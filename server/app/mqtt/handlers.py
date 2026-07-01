"""MQTT 消息处理器"""

import re
from typing import Dict, Any
from app.database import SessionLocal
from app.models import Vehicle
from app.utils.logger import get_logger

logger = get_logger(__name__)


class MqttMessageHandler:
    """
    MQTT 消息处理器。
    由 main.py 通过 asyncio.run_coroutine_threadsafe 调度，运行在 asyncio 事件循环中。
    """

    def __init__(self, ws_manager=None):
        self.ws_manager = ws_manager

    async def handle_message(self, topic: str, data: Dict[str, Any]):
        """根据 topic 分发到对应处理器"""
        try:
            if "telemetry" in topic:
                await self._handle_telemetry(topic, data)
            elif "status" in topic:
                await self._handle_status(topic, data)
            elif "discovery" in topic:
                await self._handle_discovery(topic, data)
            else:
                logger.warning(f"未知 MQTT 主题: {topic}")
        except Exception as e:
            logger.error(f"处理 MQTT 消息异常: {e}")

    # ------------------------------------------------------------------
    # telemetry: 中控机 → 后端 → 前端 WebSocket
    # ------------------------------------------------------------------
    async def _handle_telemetry(self, topic: str, data: Dict[str, Any]):
        match = re.match(r"rtg/(.+)/telemetry", topic)
        if not match:
            logger.warning(f"遥测主题格式错误: {topic}")
            return

        vehicle_id = match.group(1)
        logger.debug(f"[遥测] {vehicle_id}: 收到定位数据")

        if self.ws_manager:
            await self.ws_manager.broadcast_to_vehicle(vehicle_id, {
                "type": "telemetry",
                "data": data
            })

    # ------------------------------------------------------------------
    # status: 中控机 → 后端 → 前端 WebSocket
    # ------------------------------------------------------------------
    async def _handle_status(self, topic: str, data: Dict[str, Any]):
        match = re.match(r"rtg/(.+)/status", topic)
        if not match:
            logger.warning(f"状态主题格式错误: {topic}")
            return

        vehicle_id = match.group(1)
        logger.info(f"[状态] {vehicle_id}: {data.get('event', 'unknown')}")

        if self.ws_manager:
            await self.ws_manager.broadcast_to_vehicle(vehicle_id, {
                "type": "status",
                "data": data
            })

    # ------------------------------------------------------------------
    # discovery: 新设备上线 → 写入 DB → WebSocket 广播给所有前端
    # ------------------------------------------------------------------
    async def _handle_discovery(self, topic: str, data: Dict[str, Any]):
        temp_id = data.get("temp_id")
        if not temp_id:
            logger.warning("[发现] discovery 消息缺少 temp_id，忽略")
            return

        sn = data.get("sn", "")
        logger.info(f"[发现] 新设备上线: temp_id={temp_id}, sn={sn}")

        # 写入 vehicles 表（去重：同 temp_id 不重复插入）
        db = SessionLocal()
        try:
            existing = db.query(Vehicle).filter(
                Vehicle.temp_id == temp_id
            ).first()

            if existing:
                logger.info(f"[发现] 设备 {temp_id} 已存在于数据库，跳过插入")
            else:
                vehicle = Vehicle(
                    vehicle_id=temp_id,
                    temp_id=temp_id,
                    sn=sn,
                    initialized=False,
                )
                db.add(vehicle)
                db.commit()
                logger.info(f"[发现] 已将 {temp_id} 写入 vehicles 表 (initialized=False)")
        except Exception as e:
            db.rollback()
            logger.error(f"[发现] 写入数据库失败: {e}")
        finally:
            db.close()

        # 通过 WebSocket 广播给所有已连接的前端，通知有新设备
        if self.ws_manager:
            await self.ws_manager.broadcast_to_all({
                "type": "discovery",
                "data": {
                    "temp_id": temp_id,
                    "sn": sn,
                }
            })
            logger.info("[发现] 已通过 WebSocket 广播新设备通知")
