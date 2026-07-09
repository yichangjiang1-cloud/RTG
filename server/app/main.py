"""FastAPI 应用主入口"""

from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, status
from fastapi.middleware.cors import CORSMiddleware
import asyncio
import json

from app.config import settings
from app.database import init_db
from app.utils.logger import setup_logging, get_logger
from app.mqtt.client import MqttClient
from app.mqtt.handlers import MqttMessageHandler
from app.api.ws import WebSocketManager
from app.api.routers import vehicle, yard, survey, health, calibration

logger = None
mqtt_client = None
ws_manager = None
mqtt_handler = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    global logger, mqtt_client, ws_manager, mqtt_handler

    # 启动事件
    logger = setup_logging()
    logger.info("=" * 60)
    logger.info(f"启动 {settings.APP_NAME} v{settings.APP_VERSION}")
    logger.info("=" * 60)

    # 初始化数据库
    try:
        init_db()
        logger.info("数据库初始化完成")
    except Exception as e:
        logger.error(f"数据库初始化失败: {e}")
        raise

    # 初始化 WebSocket 管理器
    ws_manager = WebSocketManager()
    logger.info("WebSocket 管理器已初始化")

    # 初始化 MQTT 客户端
    mqtt_client = MqttClient()
    mqtt_handler = MqttMessageHandler(ws_manager)

    # 获取当前事件循环，用于在 paho-mqtt 的同步回调中调度异步任务
    loop = asyncio.get_running_loop()

    def sync_mqtt_handler(topic: str, data: dict):
        """paho-mqtt 同步回调 → 调度到 asyncio 事件循环执行真正的异步 handler"""
        logger.debug(f"[MQTT] 收到消息 {topic}")
        asyncio.run_coroutine_threadsafe(
            mqtt_handler.handle_message(topic, data), loop
        )

    mqtt_client.set_message_handler(sync_mqtt_handler)

    mqtt_client.connect()
    logger.info("MQTT 客户端已启动（后台自动重连）")

    # 暴露全局引用给各 router 模块
    health.set_mqtt_client(mqtt_client)
    health.set_ws_manager(ws_manager)
    vehicle.set_mqtt_client(mqtt_client)
    survey.set_mqtt_client(mqtt_client)

    logger.info(f"监听端口: {settings.BACKEND_HOST}:{settings.BACKEND_PORT}")
    logger.info("=" * 60)

    yield

    # 关闭事件
    logger.info("=" * 60)
    logger.info("正在关闭应用...")
    if mqtt_client:
        mqtt_client.disconnect()
    logger.info("应用已关闭")
    logger.info("=" * 60)

# 创建 FastAPI 应用
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    lifespan=lifespan,
)

# CORS 中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(vehicle.router)
app.include_router(yard.router)
app.include_router(survey.router)
app.include_router(calibration.router)
app.include_router(health.router)

# ==================== WebSocket 端点 ====================

@app.websocket("/ws/realtime")
async def websocket_endpoint(websocket: WebSocket, vehicle_id: str = Query(...)):
    """
    WebSocket 实时数据推送端点

    Args:
        websocket: WebSocket 连接
        vehicle_id: 车辆 ID
    """
    if not ws_manager:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await ws_manager.handle_websocket(websocket, vehicle_id)

# ==================== 健康检查端点 ====================

@app.get("/")
async def root():
    """根路由"""
    return {
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "status": "running",
    }

@app.get("/api/version")
async def get_version():
    """获取版本信息"""
    return {
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "backend_url": f"http://{settings.BACKEND_HOST}:{settings.BACKEND_PORT}",
        "mqtt_broker": f"{settings.MQTT_BROKER_HOST}:{settings.MQTT_BROKER_PORT}",
    }

# ==================== 错误处理 ====================

@app.exception_handler(Exception)
async def general_exception_handler(request, exc):
    """全局异常处理"""
    logger.error(f"未处理异常: {exc}")
    return {
        "code": 500,
        "message": "内部服务器错误",
        "detail": str(exc) if settings.DEBUG else "internal error",
    }

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.BACKEND_HOST,
        port=settings.BACKEND_PORT,
        reload=settings.DEBUG,
        log_level=settings.LOG_LEVEL.lower(),
    )
