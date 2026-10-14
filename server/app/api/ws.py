"""WebSocket 连接管理"""

from fastapi import WebSocket, WebSocketDisconnect
from typing import Dict, Set, Optional
import asyncio
import json
import time
from app.utils.logger import get_logger
from app.config import settings
from datetime import datetime

logger = get_logger(__name__)


class WebSocketManager:
    """WebSocket 连接管理器"""

    def __init__(self):
        self.active_connections: Dict[str, Set[WebSocket]] = {}
        self._lock = asyncio.Lock()
        self._last_tel_log: Dict[str, float] = {}

    async def connect(self, websocket: WebSocket, vehicle_id: str):
        await websocket.accept()

        async with self._lock:
            if vehicle_id not in self.active_connections:
                self.active_connections[vehicle_id] = set()
            self.active_connections[vehicle_id].add(websocket)

        logger.info(
            "WebSocket 连接: %s (总连接数: %s)",
            vehicle_id,
            len(self.active_connections[vehicle_id]),
        )

        try:
            await websocket.send_text(json.dumps({
                "type": "connected",
                "vehicle_id": vehicle_id,
                "data": {"message": "websocket_ready"},
                "timestamp": datetime.utcnow().isoformat(),
            }))
        except Exception as e:
            logger.warning("WebSocket 欢迎帧发送失败: %s", e)

    async def disconnect(self, websocket: WebSocket, vehicle_id: str):
        async with self._lock:
            if vehicle_id in self.active_connections:
                self.active_connections[vehicle_id].discard(websocket)
                if not self.active_connections[vehicle_id]:
                    del self.active_connections[vehicle_id]
        logger.info("WebSocket 断开: %s", vehicle_id)

    def _log_outbound(self, vehicle_id: str, message: dict, payload: str) -> None:
        if not getattr(settings, "WS_FRAME_LOG", False):
            return
        msg_type = message.get("type", "?")
        n = len(self.active_connections.get(vehicle_id, []))
        if msg_type == "telemetry":
            now = time.time()
            if now - self._last_tel_log.get(vehicle_id, 0) < 1.0:
                return
            self._last_tel_log[vehicle_id] = now
            data = message.get("data") or {}
            loc = data.get("local_coord") or {}
            logger.info(
                "WS→ %s telemetry clients=%s bytes=%s status=%s lycs=(%s,%s,%s)",
                vehicle_id,
                n,
                len(payload),
                data.get("status"),
                loc.get("x"),
                loc.get("y"),
                loc.get("z"),
            )
        elif msg_type == "status":
            ev = (message.get("data") or {}).get("event", "")
            if ev == "heartbeat":
                logger.debug(
                    "WS→ %s status/heartbeat clients=%s bytes=%s",
                    vehicle_id, n, len(payload),
                )
            else:
                logger.info(
                    "WS→ %s status/%s clients=%s bytes=%s",
                    vehicle_id, ev, n, len(payload),
                )
        else:
            logger.info(
                "WS→ %s type=%s clients=%s bytes=%s",
                vehicle_id, msg_type, n, len(payload),
            )

    async def broadcast_to_vehicle(self, vehicle_id: str, message: Dict):
        if vehicle_id not in self.active_connections:
            return

        if "timestamp" not in message:
            message["timestamp"] = datetime.utcnow().isoformat()

        payload = json.dumps(message, default=str)
        self._log_outbound(vehicle_id, message, payload)

        async with self._lock:
            connections = list(self.active_connections.get(vehicle_id, []))

        disconnected = []
        for websocket in connections:
            try:
                await websocket.send_text(payload)
            except Exception as e:
                logger.error("发送 WebSocket 消息失败: %s", e)
                disconnected.append(websocket)

        async with self._lock:
            for ws in disconnected:
                if vehicle_id in self.active_connections:
                    self.active_connections[vehicle_id].discard(ws)

    async def broadcast_to_all(self, message: Dict):
        async with self._lock:
            vehicle_ids = list(self.active_connections.keys())

        for vehicle_id in vehicle_ids:
            await self.broadcast_to_vehicle(vehicle_id, message)

    async def handle_websocket(self, websocket: WebSocket, vehicle_id: str):
        await self.connect(websocket, vehicle_id)
        try:
            while True:
                data = await websocket.receive_text()
                logger.debug("收到客户端消息: %s - %s", vehicle_id, data)
        except WebSocketDisconnect:
            await self.disconnect(websocket, vehicle_id)
        except Exception as e:
            logger.error("WebSocket 异常: %s", e)
            await self.disconnect(websocket, vehicle_id)

    def get_connection_count(self, vehicle_id: Optional[str] = None) -> int:
        if vehicle_id:
            return len(self.active_connections.get(vehicle_id, set()))
        return sum(len(conns) for conns in self.active_connections.values())

    def get_active_vehicles(self) -> list:
        return list(self.active_connections.keys())
