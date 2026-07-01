"""WebSocket 连接管理"""

from fastapi import WebSocket, WebSocketDisconnect, status
from typing import Dict, Set, Optional
import asyncio
import json
from app.utils.logger import get_logger
from datetime import datetime

logger = get_logger(__name__)

class WebSocketManager:
    """WebSocket 连接管理器"""

    def __init__(self):
        # 按 vehicle_id 组织连接
        self.active_connections: Dict[str, Set[WebSocket]] = {}
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket, vehicle_id: str):
        """
        连接客户端

        Args:
            websocket: WebSocket 连接对象
            vehicle_id: 车辆 ID
        """
        await websocket.accept()

        async with self._lock:
            if vehicle_id not in self.active_connections:
                self.active_connections[vehicle_id] = set()
            self.active_connections[vehicle_id].add(websocket)

        logger.info(f"WebSocket 连接: {vehicle_id} (总连接数: {len(self.active_connections[vehicle_id])})")

    async def disconnect(self, websocket: WebSocket, vehicle_id: str):
        """
        断开客户端连接

        Args:
            websocket: WebSocket 连接对象
            vehicle_id: 车辆 ID
        """
        async with self._lock:
            if vehicle_id in self.active_connections:
                self.active_connections[vehicle_id].discard(websocket)
                if not self.active_connections[vehicle_id]:
                    del self.active_connections[vehicle_id]

        logger.info(f"WebSocket 断开: {vehicle_id}")

    async def broadcast_to_vehicle(self, vehicle_id: str, message: Dict):
        """
        广播消息给指定车辆的所有客户端

        Args:
            vehicle_id: 车辆 ID
            message: 要发送的消息字典
        """
        if vehicle_id not in self.active_connections:
            return

        # 添加时间戳
        if "timestamp" not in message:
            message["timestamp"] = datetime.utcnow().isoformat()

        payload = json.dumps(message, default=str)

        # 获取当前连接列表的副本
        async with self._lock:
            connections = list(self.active_connections.get(vehicle_id, []))

        # 发送消息给所有连接
        disconnected = []
        for websocket in connections:
            try:
                await websocket.send_text(payload)
            except Exception as e:
                logger.error(f"发送 WebSocket 消息失败: {e}")
                disconnected.append(websocket)

        # 清理断开的连接
        async with self._lock:
            for ws in disconnected:
                if vehicle_id in self.active_connections:
                    self.active_connections[vehicle_id].discard(ws)

    async def broadcast_to_all(self, message: Dict):
        """
        广播消息给所有连接的客户端

        Args:
            message: 要发送的消息字典
        """
        async with self._lock:
            vehicle_ids = list(self.active_connections.keys())

        for vehicle_id in vehicle_ids:
            await self.broadcast_to_vehicle(vehicle_id, message)

    async def handle_websocket(self, websocket: WebSocket, vehicle_id: str):
        """
        处理 WebSocket 连接

        Args:
            websocket: WebSocket 连接对象
            vehicle_id: 车辆 ID
        """
        await self.connect(websocket, vehicle_id)
        try:
            while True:
                # 接收客户端消息（保活）
                data = await websocket.receive_text()
                # 可以在此处理客户端发送的命令
                logger.debug(f"收到客户端消息: {vehicle_id} - {data}")
        except WebSocketDisconnect:
            await self.disconnect(websocket, vehicle_id)
        except Exception as e:
            logger.error(f"WebSocket 异常: {e}")
            await self.disconnect(websocket, vehicle_id)

    def get_connection_count(self, vehicle_id: Optional[str] = None) -> int:
        """
        获取连接数

        Args:
            vehicle_id: 车辆 ID（None 表示所有连接）

        Returns:
            连接数
        """
        if vehicle_id:
            return len(self.active_connections.get(vehicle_id, set()))
        return sum(len(conns) for conns in self.active_connections.values())

    def get_active_vehicles(self) -> list:
        """获取所有活跃的车辆 ID"""
        return list(self.active_connections.keys())
