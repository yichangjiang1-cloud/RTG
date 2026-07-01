"""车辆管理接口"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List
import json

from app.database import get_db
from app.models import Vehicle, VehicleConfig
from app.schemas import (
    VehicleResponse, VehicleCreate, VehicleInitRequest,
    VehicleConfigResponse, VehicleConfigCreate, VehicleConfigCloneRequest,
    LArmComputeRequest, ApiResponse,
)
from app.utils.l_arm_calibration import compute_l_arm_from_wgs84
from app.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api/vehicle", tags=["vehicle"])

# MQTT 客户端引用（由 main.py lifespan 注入）
_mqtt_client = None

def set_mqtt_client(client):
    """设置 MQTT 客户端（由 main.py 启动时调用）"""
    global _mqtt_client
    _mqtt_client = client


def _publish_vehicle_config_update(vehicle_id: str, config: VehicleConfig) -> None:
    """保存配置后通知中控机热更新，并触发 vehicle_params.yaml 本地备份。"""
    if not _mqtt_client or not _mqtt_client.is_connected():
        logger.warning("MQTT 未连接，无法下发 UPDATE_CONFIG（中控需重启后从 API 同步）")
        return
    payload = {
        "cmd": "UPDATE_CONFIG",
        "data": {
            "l_arm": config.l_arm,
            "l_arm_elec": config.l_arm_elec,
            "l_arm_diesel": config.l_arm_diesel,
            "height": config.height,
            "w_span": config.w_span,
            "antennas": config.antennas,
            "l_arm_calibration": config.l_arm_calibration,
        },
    }
    topic = f"rtg/{vehicle_id}/control"
    _mqtt_client.publish(topic, json.dumps(payload, ensure_ascii=False, default=str), qos=1)
    logger.info(f"已下发配置更新: {topic} (含 l_arm_calibration={config.l_arm_calibration is not None})")

@router.post("/init", response_model=ApiResponse)
async def init_vehicle(
    req: VehicleInitRequest,
    db: Session = Depends(get_db)
):
    """初始化新设备"""
    try:
        # 查找临时 ID 的设备
        vehicle = db.query(Vehicle).filter(
            Vehicle.temp_id == req.temp_id
        ).first()

        if not vehicle:
            # 创建新设备
            vehicle = Vehicle(
                vehicle_id=req.new_vehicle_id,
                temp_id=req.temp_id,
                label=req.label,
                initialized=True
            )
            db.add(vehicle)
        else:
            # 更新现有设备
            vehicle.vehicle_id = req.new_vehicle_id
            vehicle.label = req.label
            vehicle.initialized = True

        db.commit()
        logger.info(f"设备初始化完成: {req.new_vehicle_id}")

        # 保存车辆参数
        if req.params:
            config = db.query(VehicleConfig).filter(
                VehicleConfig.vehicle_id == req.new_vehicle_id
            ).first()

            if config:
                config.l_arm = req.params.l_arm
                config.l_arm_elec = req.params.l_arm_elec
                config.l_arm_diesel = req.params.l_arm_diesel
                config.height = req.params.height
                config.w_span = req.params.w_span
                config.antennas = req.params.antennas
            else:
                config = VehicleConfig(
                    vehicle_id=req.new_vehicle_id,
                    l_arm=req.params.l_arm,
                    l_arm_elec=req.params.l_arm_elec,
                    l_arm_diesel=req.params.l_arm_diesel,
                    height=req.params.height,
                    w_span=req.params.w_span,
                    antennas=req.params.antennas
                )
                db.add(config)

            db.commit()

        # 通过 MQTT 下发 CMD_INIT_IDENTITY 给中控机
        # 中控机订阅 rtg/{temp_id}/control，收到后更新自身 vehicle_id
        if _mqtt_client and _mqtt_client.is_connected():
            control_topic = f"rtg/{req.temp_id}/control"
            control_payload = json.dumps({
                "cmd": "CMD_INIT_IDENTITY",
                "data": {
                    "new_vehicle_id": req.new_vehicle_id,
                    "label": req.label or req.new_vehicle_id,
                }
            }, ensure_ascii=False)
            _mqtt_client.publish(
                control_topic, control_payload,
                qos=1,
            )
            logger.info(f"已下发初始化指令: {control_topic} -> {req.new_vehicle_id}")
        else:
            logger.warning("MQTT 未连接，无法下发初始化指令给中控机")

        return ApiResponse(
            code=200,
            message="设备初始化成功",
            data={"vehicle_id": req.new_vehicle_id}
        )
    except Exception as e:
        logger.error(f"设备初始化失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@router.get("/list", response_model=ApiResponse)
async def list_vehicles(db: Session = Depends(get_db)):
    """获取车辆列表（已初始化 + 待初始化）"""
    try:
        vehicles = db.query(Vehicle).all()
        result = []
        for v in vehicles:
            # 关联查询车辆参数（获取 height / w_span）
            config = db.query(VehicleConfig).filter(
                VehicleConfig.vehicle_id == v.vehicle_id
            ).first()
            result.append({
                "id": v.id,
                "vehicle_id": v.vehicle_id,
                "temp_id": v.temp_id,
                "sn": v.sn or "",
                "label": v.label or v.vehicle_id,
                "is_initialized": v.initialized,   # 与前端字段保持一致
                "status": "online",                 # 实际状态由 MQTT 心跳维护，此处默认值
                "height": config.height if config else None,
                "span": config.w_span if config else None,
                "last_seen": v.updated_at.isoformat() if v.updated_at else None,
            })
        return ApiResponse(
            code=200,
            message="获取成功",
            data=result
        )
    except Exception as e:
        logger.error(f"获取车辆列表失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@router.get("/{vehicle_id}", response_model=ApiResponse)
async def get_vehicle(vehicle_id: str, db: Session = Depends(get_db)):
    """获取车辆详情"""
    try:
        vehicle = db.query(Vehicle).filter(
            Vehicle.vehicle_id == vehicle_id
        ).first()

        if not vehicle:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="车辆不存在"
            )

        config = db.query(VehicleConfig).filter(
            VehicleConfig.vehicle_id == vehicle_id
        ).first()

        return ApiResponse(
            code=200,
            message="获取成功",
            data={
                "vehicle_id": vehicle.vehicle_id,
                "temp_id": vehicle.temp_id,
                "sn": vehicle.sn or "",
                "initialized": vehicle.initialized,
                "config": {
                    "l_arm": config.l_arm,
                    "l_arm_elec": config.l_arm_elec,
                    "l_arm_diesel": config.l_arm_diesel,
                    "height": config.height,
                    "w_span": config.w_span,
                    "antennas": config.antennas
                } if config else None
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取车辆详情失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@router.post("/compute_l_arm", response_model=ApiResponse)
async def compute_l_arm(req: LArmComputeRequest):
    """
    由现场测量的作业点 WGS84 + 实时天线 WGS84 + 姿态反算车体系杆臂。
  标定公式：L_arm = Rᵀ · (P_antenna − P_lpoint)
    """
    if req.side not in ("elec", "diesel"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="side 须为 elec 或 diesel",
        )
    try:
        a = req.antenna
        lp = req.lpoint
        att = req.attitude
        if req.antenna_ecef is not None:
            from app.utils.l_arm_calibration import compute_l_arm_from_ecef, _wgs84_to_ecef
            lp_ecef = _wgs84_to_ecef(lp.lat, lp.lon, lp.alt)
            dx, dy, dz = compute_l_arm_from_ecef(
                (req.antenna_ecef.x, req.antenna_ecef.y, req.antenna_ecef.z),
                lp_ecef,
                att.roll, att.pitch, att.heading,
            )
        else:
            dx, dy, dz = compute_l_arm_from_wgs84(
                a.lat, a.lon, a.alt,
                lp.lat, lp.lon, lp.alt,
                att.roll, att.pitch, att.heading,
            )
        return ApiResponse(
            code=200,
            message="杆臂反算成功",
            data={
                "side": req.side,
                "l_arm": [dx, dy, dz],
                "attitude_used": {
                    "roll": att.roll,
                    "pitch": att.pitch,
                    "heading": att.heading,
                },
            },
        )
    except Exception as e:
        logger.error(f"杆臂反算失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )


@router.post("/config", response_model=ApiResponse)
async def save_vehicle_config(
    req: VehicleConfigCreate,
    db: Session = Depends(get_db)
):
    """保存车辆参数（要求车辆已存在于vehicles表）"""
    try:
        # 检查 vehicle_id 是否存在于 vehicles 表
        vehicle = db.query(Vehicle).filter(
            Vehicle.vehicle_id == req.vehicle_id
        ).first()

        if not vehicle:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"车辆 {req.vehicle_id} 不存在，请先调用 /api/vehicle/init 初始化或使用 /api/vehicle/config/clone 复制配置"
            )

        config = db.query(VehicleConfig).filter(
            VehicleConfig.vehicle_id == req.vehicle_id
        ).first()

        if config:
            config.l_arm = req.l_arm
            config.l_arm_elec = req.l_arm_elec
            config.l_arm_diesel = req.l_arm_diesel
            config.height = req.height
            config.w_span = req.w_span
            config.antennas = req.antennas
            config.l_arm_calibration = req.l_arm_calibration
        else:
            config = VehicleConfig(**req.dict())
            db.add(config)

        db.commit()
        logger.info(f"保存车辆配置: {req.vehicle_id}")
        _publish_vehicle_config_update(req.vehicle_id, config)

        return ApiResponse(
            code=200,
            message="配置保存成功",
            data={"vehicle_id": req.vehicle_id}
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"保存车辆配置失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@router.get("/config/{vehicle_id}", response_model=ApiResponse)
async def get_vehicle_config(vehicle_id: str, db: Session = Depends(get_db)):
    """获取车辆参数"""
    try:
        config = db.query(VehicleConfig).filter(
            VehicleConfig.vehicle_id == vehicle_id
        ).first()

        if not config:
            return ApiResponse(
                code=200,
                message="获取成功",
                data=None
            )

        return ApiResponse(
            code=200,
            message="获取成功",
            data={
                "vehicle_id": config.vehicle_id,
                "l_arm": config.l_arm,
                "l_arm_elec": config.l_arm_elec,
                "l_arm_diesel": config.l_arm_diesel,
                "height": config.height,
                "w_span": config.w_span,
                "antennas": config.antennas,
                "l_arm_calibration": config.l_arm_calibration,
                "created_at": config.created_at.isoformat(),
                "updated_at": config.updated_at.isoformat()
            }
        )
    except Exception as e:
        logger.error(f"获取车辆配置失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@router.post("/config/clone", response_model=ApiResponse)
async def clone_vehicle_config(
    req: VehicleConfigCloneRequest,
    db: Session = Depends(get_db)
):
    """复制已有车型的配置到新车（用于同型号车快速部署）"""
    try:
        # 检查源车辆是否存在
        source_config = db.query(VehicleConfig).filter(
            VehicleConfig.vehicle_id == req.source_vehicle_id
        ).first()

        if not source_config:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"源车辆 {req.source_vehicle_id} 的配置不存在"
            )

        # 检查目标车辆是否已存在
        target_vehicle = db.query(Vehicle).filter(
            Vehicle.vehicle_id == req.new_vehicle_id
        ).first()

        if not target_vehicle:
            # 自动创建目标车辆记录
            target_vehicle = Vehicle(
                vehicle_id=req.new_vehicle_id,
                initialized=False
            )
            db.add(target_vehicle)
            db.commit()
            logger.info(f"自动创建车辆记录: {req.new_vehicle_id}")

        # 检查目标配置是否已存在
        target_config = db.query(VehicleConfig).filter(
            VehicleConfig.vehicle_id == req.new_vehicle_id
        ).first()

        if target_config:
            target_config.l_arm = source_config.l_arm
            target_config.l_arm_elec = source_config.l_arm_elec
            target_config.l_arm_diesel = source_config.l_arm_diesel
            target_config.height = source_config.height
            target_config.w_span = source_config.w_span
            target_config.antennas = source_config.antennas
        else:
            target_config = VehicleConfig(
                vehicle_id=req.new_vehicle_id,
                l_arm=source_config.l_arm,
                l_arm_elec=source_config.l_arm_elec,
                l_arm_diesel=source_config.l_arm_diesel,
                height=source_config.height,
                w_span=source_config.w_span,
                antennas=source_config.antennas
            )
            db.add(target_config)

        db.commit()
        logger.info(f"复制配置: {req.source_vehicle_id} -> {req.new_vehicle_id}")

        return ApiResponse(
            code=200,
            message="配置复制成功",
            data={
                "source_vehicle_id": req.source_vehicle_id,
                "new_vehicle_id": req.new_vehicle_id
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"复制车辆配置失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )
