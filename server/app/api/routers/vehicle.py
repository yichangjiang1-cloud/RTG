"""车辆管理接口"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from typing import List, Optional
import json
import math

from app.database import get_db
from app.models import Vehicle, VehicleConfig
from app.schemas import (
    VehicleResponse, VehicleCreate, VehicleInitRequest,
    VehicleConfigResponse, VehicleConfigCreate, VehicleConfigCloneRequest,
    LArmComputeRequest, ApiResponse,
    AntennaBodyComputeRequest, TrolleyOriginRequest,
)
from app.utils.l_arm_calibration import compute_l_arm_from_wgs84
from app.utils.antenna_body_calibration import (
    compute_antenna_body_from_ecef,
    ecef_dict_from_antennas,
)
from app.mqtt.presence import presence
from app.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api/vehicle", tags=["vehicle"])

# MQTT 客户端引用（由 main.py lifespan 注入）
_mqtt_client = None

def set_mqtt_client(client):
    """设置 MQTT 客户端（由 main.py 启动时调用）"""
    global _mqtt_client
    _mqtt_client = client


def _arm3(arm):
    if not isinstance(arm, (list, tuple)) or len(arm) < 3:
        return None
    return [float(x) for x in arm[:3]]


def _align_calibration_arms(cal, elec, diesel, extra=None):
    """标定记录里的杆臂改成当前四套杆臂。extra 的键是 elec_up 这类记录名。"""
    if not isinstance(cal, dict):
        return cal
    out = dict(cal)
    pairs = {"elec": elec, "diesel": diesel}
    if isinstance(extra, dict):
        pairs.update(extra)
    for key, arm in pairs.items():
        arm3 = _arm3(arm)
        if arm3 and isinstance(out.get(key), dict):
            rec = dict(out[key])
            rec["l_arm"] = arm3
            out[key] = rec
    return out


def _arm_from_record(cal, key):
    if not isinstance(cal, dict):
        return None
    rec = cal.get(key)
    if not isinstance(rec, dict):
        return None
    return _arm3(rec.get("l_arm"))


def _write_arms(config: VehicleConfig, src) -> None:
    """四个轮胎杆臂落库。下游同时写入旧的 l_arm_elec / l_arm_diesel。"""
    cal = getattr(src, "l_arm_calibration", None)
    down_e = _arm3(getattr(src, "l_arm_elec_down", None)) or _arm3(getattr(src, "l_arm_elec", None))
    down_d = _arm3(getattr(src, "l_arm_diesel_down", None)) or _arm3(getattr(src, "l_arm_diesel", None))
    up_e = _arm3(getattr(src, "l_arm_elec_up", None)) or _arm_from_record(cal, "elec_up")
    up_d = _arm3(getattr(src, "l_arm_diesel_up", None)) or _arm_from_record(cal, "diesel_up")
    config.l_arm_elec = down_e
    config.l_arm_elec_down = down_e
    config.l_arm_diesel = down_d
    config.l_arm_diesel_down = down_d
    config.l_arm_elec_up = up_e
    config.l_arm_diesel_up = up_d
    config.l_arm_calibration = _align_calibration_arms(
        cal,
        down_e,
        down_d,
        {
            "elec_up": up_e,
            "elec_down": down_e,
            "diesel_up": up_d,
            "diesel_down": down_d,
        },
    )


def _arms_payload(config: VehicleConfig) -> dict:
    cal = config.l_arm_calibration if isinstance(config.l_arm_calibration, dict) else {}
    def pick(column, record_key, fallback=None):
        arm = _arm3(getattr(config, column, None))
        return arm or _arm_from_record(cal, record_key) or fallback
    down_e = pick("l_arm_elec_down", "elec_down", _arm3(config.l_arm_elec))
    down_d = pick("l_arm_diesel_down", "diesel_down", _arm3(config.l_arm_diesel))
    return {
        "l_arm_elec": down_e,
        "l_arm_diesel": down_d,
        "l_arm_elec_down": down_e,
        "l_arm_diesel_down": down_d,
        "l_arm_elec_up": pick("l_arm_elec_up", "elec_up"),
        "l_arm_diesel_up": pick("l_arm_diesel_up", "diesel_up"),
        "l_arm_calibration": config.l_arm_calibration,
    }


def _sanitize_attitude(att):
    """只保留四天线 / 双接收机，去掉跨距类旧字段；主通道姿态固定 rigid。"""
    if not isinstance(att, dict):
        return att
    out = dict(att)
    mode = str(out.get("mode") or "").strip().lower()
    out["mode"] = "dual_receiver" if mode in ("dual_receiver", "dual") else "four_antenna"
    out["tire_attitude"] = "rigid"
    out.pop("roll_span_m", None)
    body_cal = out.get("body_calibration")
    if isinstance(body_cal, dict):
        body_cal = dict(body_cal)
        body_cal.pop("span_m", None)
        out["body_calibration"] = body_cal
    return out


def _drop_legacy_fields(config: VehicleConfig) -> None:
    config.l_arm = None
    config.height = None
    config.w_span = None


def _publish_vehicle_config_update(vehicle_id: str, config: VehicleConfig) -> None:
    """保存配置后通知中控机热更新，并触发 vehicle_params.yaml 本地备份。"""
    if not _mqtt_client or not _mqtt_client.is_connected():
        logger.warning("MQTT 未连接，无法下发 UPDATE_CONFIG（中控需重启后从 API 同步）")
        return
    payload = {
        "cmd": "UPDATE_CONFIG",
        "data": {
            **_arms_payload(config),
            "antennas": config.antennas,
            "gantry_attitude": config.gantry_attitude,
            "receiver_layout": config.receiver_layout,
            "ntrip": getattr(config, "ntrip", None),
            "trolley": config.trolley,
        },
    }
    topic = f"rtg/{vehicle_id}/control"
    _mqtt_client.publish(topic, json.dumps(payload, ensure_ascii=False, default=str), qos=1)
    logger.info(f"已下发配置更新: {topic} (含 l_arm_calibration={config.l_arm_calibration is not None})")


def _publish_trolley_origin(vehicle_id: str, payload: dict) -> None:
    if not _mqtt_client or not _mqtt_client.is_connected():
        logger.warning("MQTT 未连接，无法下发 SET_TROLLEY_ORIGIN")
        return
    topic = f"rtg/{vehicle_id}/control"
    body = {"cmd": "SET_TROLLEY_ORIGIN", "data": payload}
    _mqtt_client.publish(topic, json.dumps(body, ensure_ascii=False, default=str), qos=1)
    logger.info(f"已下发小车零点: {topic} action={payload.get('action')}")


class HeadingDeadbandRequest(BaseModel):
    vehicle_id: str
    enabled: bool = False
    ref_deg: Optional[float] = None
    band_deg: Optional[float] = Field(default=None, ge=0.0, le=10.0)


@router.post("/heading-deadband", response_model=ApiResponse)
async def set_heading_deadband(req: HeadingDeadbandRequest, db: Session = Depends(get_db)):
    """
    仪表盘航向死区纠偏：|Δ|≤band 用 ref；超出则 ref±(|Δ|-band)。
    只影响轮胎/杆臂投影，遥测显示仍为真实航向。
    """
    vehicle_id = (req.vehicle_id or "").strip()
    if not vehicle_id:
        raise HTTPException(status_code=400, detail="需要 vehicle_id")
    vehicle = db.query(Vehicle).filter(Vehicle.vehicle_id == vehicle_id).first()
    if vehicle is None:
        raise HTTPException(status_code=404, detail=f"车辆 {vehicle_id} 不存在")
    if not _mqtt_client or not _mqtt_client.is_connected():
        raise HTTPException(status_code=503, detail="MQTT 未连接，无法通知中控")

    data = {"enabled": bool(req.enabled)}
    if req.ref_deg is not None:
        data["ref_deg"] = float(req.ref_deg) % 360.0
    if req.band_deg is not None:
        data["band_deg"] = float(req.band_deg)

    # 落库：merge 到 vehicle_configs.heading_deadband，供重启/云同步恢复
    cfg = db.query(VehicleConfig).filter(VehicleConfig.vehicle_id == vehicle_id).first()
    hdb = dict(getattr(cfg, "heading_deadband", None) or {}) if cfg else {}
    hdb["enabled"] = bool(req.enabled)
    if req.ref_deg is not None:
        hdb["ref_deg"] = float(req.ref_deg) % 360.0
    if req.band_deg is not None:
        hdb["band_deg"] = float(req.band_deg)
    if cfg is not None:
        cfg.heading_deadband = hdb
        db.commit()

    topic = f"rtg/{vehicle_id}/control"
    body = {"cmd": "SET_HEADING_DEADBAND", "data": data}
    _mqtt_client.publish(topic, json.dumps(body, ensure_ascii=False, default=str), qos=1)
    logger.info("已下发航向死区并落库: %s %s", topic, data)
    return ApiResponse(code=200, message="航向死区已下发", data={"vehicle_id": vehicle_id, **hdb})


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
                _write_arms(config, req.params)
                config.antennas = req.params.antennas
                if req.params.gantry_attitude is not None:
                    config.gantry_attitude = _sanitize_attitude(req.params.gantry_attitude)
                _drop_legacy_fields(config)
            else:
                config = VehicleConfig(
                    vehicle_id=req.new_vehicle_id,
                    antennas=req.params.antennas,
                    gantry_attitude=_sanitize_attitude(req.params.gantry_attitude),
                )
                _write_arms(config, req.params)
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

def _is_temp_id(vehicle_id: str) -> bool:
    return str(vehicle_id or "").upper().startswith("RTG-NEW")


@router.get("/list", response_model=ApiResponse)
async def list_vehicles(db: Session = Depends(get_db)):
    """已命名车辆 + 当前 MQTT 在线设备（含未初始化）。status 由心跳/遥测判定。"""
    try:
        vehicles = db.query(Vehicle).all()
        result = []
        listed_ids = set()
        for v in vehicles:
            config = db.query(VehicleConfig).filter(
                VehicleConfig.vehicle_id == v.vehicle_id
            ).first()
            online = presence.is_online(v.vehicle_id) or (
                bool(v.temp_id) and presence.is_online(v.temp_id)
            )
            last_iso = (
                presence.last_seen_iso(v.vehicle_id)
                or (presence.last_seen_iso(v.temp_id) if v.temp_id else None)
                or (v.updated_at.isoformat() if v.updated_at else None)
            )
            listed_ids.add(v.vehicle_id)
            if v.temp_id:
                listed_ids.add(v.temp_id)
            result.append({
                "id": v.id,
                "vehicle_id": v.vehicle_id,
                "temp_id": v.temp_id,
                "sn": v.sn or "",
                "label": v.label or v.vehicle_id,
                "is_initialized": v.initialized,
                "status": "online" if online else "offline",
                "last_seen": last_iso,
            })

        for vid in presence.online_ids():
            if vid in listed_ids:
                continue
            result.append({
                "id": None,
                "vehicle_id": vid,
                "temp_id": vid if _is_temp_id(vid) else None,
                "sn": "",
                "label": vid,
                "is_initialized": not _is_temp_id(vid),
                "status": "online",
                "last_seen": presence.last_seen_iso(vid),
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
                    **_arms_payload(config),
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
    if req.side not in ("elec", "diesel", "elec_up", "elec_down", "diesel_up", "diesel_down"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="side 须为 elec_up、elec_down、diesel_up 或 diesel_down",
        )
    try:
        a = req.antenna
        lp = req.lpoint
        att = req.attitude
        dx, dy, dz = compute_l_arm_from_wgs84(
            a.lat, a.lon, a.alt,
            lp.lat, lp.lon, lp.alt,
            att.roll, att.pitch, att.heading,
        )
        if req.antenna_ecef is not None:
            from app.utils.l_arm_calibration import compute_l_arm_from_ecef, _wgs84_to_ecef
            lp_ecef = _wgs84_to_ecef(lp.lat, lp.lon, lp.alt)
            ex, ey, ez = compute_l_arm_from_ecef(
                (req.antenna_ecef.x, req.antenna_ecef.y, req.antenna_ecef.z),
                lp_ecef,
                att.roll, att.pitch, att.heading,
            )
            # 从天线的 ECEF 有时和经纬高不是同一个点，用它会把杆臂算成 0。
            # 两者差过 1 米就用页面上看到的经纬高。
            if math.dist((dx, dy, dz), (ex, ey, ez)) <= 1.0:
                dx, dy, dz = ex, ey, ez
            else:
                logger.warning(
                    "杆臂反算改用经纬高: side=%s ECEF结果=%s 经纬高结果=%s",
                    req.side, (ex, ey, ez), (dx, dy, dz),
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


@router.post("/compute_antenna_body", response_model=ApiResponse)
async def compute_antenna_body(req: AntennaBodyComputeRequest):
    """
    由四天线实时 ECEF 反算车体系安装坐标。
    约定：a1/a2=左接收机 GGA[0]/GGA[1]，b1/b2=右接收机 GGA[0]/GGA[1]。
    """
    try:
        a1, a2, b1, b2 = ecef_dict_from_antennas(req.antennas)
        body, baseline_m, span_m = compute_antenna_body_from_ecef(a1, a2, b1, b2)
        return ApiResponse(
            code=200,
            message="四天线安装坐标标定成功",
            data={
                "antenna_body": body,
                "baseline_m": baseline_m,
                "span_m": span_m,
            },
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error(f"四天线标定失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )


@router.post("/trolley_origin", response_model=ApiResponse)
async def set_trolley_origin(req: TrolleyOriginRequest, db: Session = Depends(get_db)):
    """
    设置小车零点（大车车体系，锚定 L-Point，随大车移动）。
    action: capture=当前位置设为零点 | manual=手动 origin_body | reset=恢复 auto
    """
    action = req.action
    if action not in ("capture", "manual", "reset"):
        raise HTTPException(status_code=400, detail="action 须为 capture | manual | reset")

    config = db.query(VehicleConfig).filter(
        VehicleConfig.vehicle_id == req.vehicle_id
    ).first()
    if not config:
        raise HTTPException(status_code=404, detail=f"车辆 {req.vehicle_id} 无配置记录")

    trolley = dict(config.trolley or {})
    if action == "manual":
        if not req.origin_body or len(req.origin_body) < 3:
            raise HTTPException(status_code=400, detail="manual 模式须提供 origin_body [x,y,z]")
        trolley = {
            "origin_mode": "manual",
            "origin_body": [float(x) for x in req.origin_body[:3]],
        }
        config.trolley = trolley
        db.commit()
        _publish_trolley_origin(req.vehicle_id, {
            "action": "manual",
            "origin_body": trolley["origin_body"],
            "trolley": trolley,
        })
    elif action == "capture":
        _publish_trolley_origin(req.vehicle_id, {"action": "capture"})
    else:
        trolley = {"origin_mode": "auto"}
        config.trolley = trolley
        db.commit()
        _publish_trolley_origin(req.vehicle_id, {"action": "reset", "trolley": trolley})

    return ApiResponse(
        code=200,
        message="小车零点指令已下发",
        data={"vehicle_id": req.vehicle_id, "action": action, "trolley": config.trolley},
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
            _write_arms(config, req)
            config.antennas = req.antennas
            config.gantry_attitude = _sanitize_attitude(req.gantry_attitude)
            config.receiver_layout = req.receiver_layout
            config.ntrip = req.ntrip
            config.trolley = req.trolley
            _drop_legacy_fields(config)
        else:
            config = VehicleConfig(
                vehicle_id=req.vehicle_id,
                antennas=req.antennas,
                gantry_attitude=_sanitize_attitude(req.gantry_attitude),
                receiver_layout=req.receiver_layout,
                ntrip=req.ntrip,
                trolley=req.trolley,
            )
            _write_arms(config, req)
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
                **_arms_payload(config),
                "antennas": config.antennas,
                "gantry_attitude": config.gantry_attitude,
                "heading_deadband": getattr(config, "heading_deadband", None),
                "receiver_layout": config.receiver_layout,
                "ntrip": getattr(config, "ntrip", None),
                "trolley": config.trolley,
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
            _write_arms(target_config, source_config)
            target_config.antennas = source_config.antennas
            target_config.gantry_attitude = _sanitize_attitude(source_config.gantry_attitude)
            target_config.receiver_layout = source_config.receiver_layout
            target_config.ntrip = source_config.ntrip
            target_config.trolley = source_config.trolley
            _drop_legacy_fields(target_config)
        else:
            target_config = VehicleConfig(
                vehicle_id=req.new_vehicle_id,
                antennas=source_config.antennas,
                gantry_attitude=_sanitize_attitude(source_config.gantry_attitude),
                receiver_layout=source_config.receiver_layout,
                ntrip=source_config.ntrip,
                trolley=source_config.trolley,
            )
            _write_arms(target_config, source_config)
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
