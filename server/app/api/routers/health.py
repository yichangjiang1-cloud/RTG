"""健康检查接口"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text
from datetime import datetime

from app.database import get_db
from app.config import settings
from app.schemas import HealthCheckResponse, ApiResponse
from app.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(tags=["health"])

# MQTT 客户端引用
mqtt_client = None
ws_manager = None

def set_mqtt_client(client):
    """设置 MQTT 客户端"""
    global mqtt_client
    mqtt_client = client

def set_ws_manager(manager):
    """设置 WebSocket 管理器"""
    global ws_manager
    ws_manager = manager

@router.get("/api/health", response_model=ApiResponse)
async def health_check(db: Session = Depends(get_db)):
    """健康检查"""
    try:
        # 检查数据库连接
        db_connected = True
        try:
            db.execute(text("SELECT 1"))
        except Exception:
            db_connected = False

        # 检查 MQTT 连接
        mqtt_connected = mqtt_client.is_connected() if mqtt_client else False

        # 确定整体状态
        if mqtt_connected and db_connected:
            status = "healthy"
        elif mqtt_connected or db_connected:
            status = "degraded"
        else:
            status = "unhealthy"

        return ApiResponse(
            code=200,
            message="获取成功",
            data={
                "status": status,
                "timestamp": datetime.utcnow().isoformat(),
                "mqtt_connected": mqtt_connected,
                "db_connected": db_connected,
                "version": settings.APP_VERSION
            }
        )
    except Exception as e:
        logger.error(f"健康检查失败: {e}")
        return ApiResponse(
            code=500,
            message="健康检查失败",
            data={
                "status": "unhealthy",
                "timestamp": datetime.utcnow().isoformat(),
                "error": str(e)
            }
        )

@router.get("/api/health/mqtt", response_model=ApiResponse)
async def mqtt_health():
    """MQTT 连接状态检查"""
    try:
        connected = mqtt_client.is_connected() if mqtt_client else False
        return ApiResponse(
            code=200,
            message="获取成功",
            data={
                "connected": connected,
                "broker": f"{settings.MQTT_BROKER_HOST}:{settings.MQTT_BROKER_PORT}",
                "timestamp": datetime.utcnow().isoformat()
            }
        )
    except Exception as e:
        logger.error(f"MQTT 健康检查失败: {e}")
        return ApiResponse(
            code=500,
            message="检查失败",
            data={"error": str(e)}
        )

@router.get("/api/health/db", response_model=ApiResponse)
async def db_health(db: Session = Depends(get_db)):
    """数据库连接状态检查"""
    try:
        db.execute(text("SELECT 1"))
        return ApiResponse(
            code=200,
            message="获取成功",
            data={
                "connected": True,
                "database": settings.DATABASE_URL,
                "timestamp": datetime.utcnow().isoformat()
            }
        )
    except Exception as e:
        logger.error(f"数据库健康检查失败: {e}")
        return ApiResponse(
            code=500,
            message="检查失败",
            data={"error": str(e), "connected": False}
        )

@router.get("/api/status/websocket", response_model=ApiResponse)
async def websocket_status():
    """WebSocket 连接状态"""
    try:
        if ws_manager:
            return ApiResponse(
                code=200,
                message="获取成功",
                data={
                    "total_connections": ws_manager.get_connection_count(),
                    "active_vehicles": ws_manager.get_active_vehicles(),
                    "timestamp": datetime.utcnow().isoformat()
                }
            )
        else:
            return ApiResponse(
                code=200,
                message="获取成功",
                data={
                    "total_connections": 0,
                    "active_vehicles": [],
                    "timestamp": datetime.utcnow().isoformat()
                }
            )
    except Exception as e:
        logger.error(f"WebSocket 状态检查失败: {e}")
        return ApiResponse(
            code=500,
            message="检查失败",
            data={"error": str(e)}
        )
