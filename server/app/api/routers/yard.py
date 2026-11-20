"""堆场管理接口"""

import json

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import Optional
from pydantic import BaseModel

from app.config import settings
from app.database import get_db
from app.models import Yard, CalibrationData, SurveyPoint, VehicleBayTransform, LaneCorrection, Vehicle
from sqlalchemy import func
from app.schemas import ApiResponse
from app.utils.constants import MQTT_TOPIC_CONTROL
from app.utils.logger import get_logger
from app.utils.calibration_mode import resolve_calibration_mode
from app.utils.yard_id import validate_yard_id
from app.utils.yard_geometry import is_electrical_bay, normalize_bay_side
from app.api.routers.survey_rtg import (
    SURVEY_MODE_RTG,
    SURVEY_MODE_RTK,
    SURVEY_MODES,
    apply_bay_count_change,
    is_rtg_yard,
    mapping_point_count,
    notify_yard_map_updated,
    rtg_ready,
    survey_mode_of,
)

logger = get_logger(__name__)
router = APIRouter(prefix="/api/yard", tags=["yard"])

_mqtt_client = None


def set_mqtt_client(client):
    global _mqtt_client
    _mqtt_client = client


# ==================== 请求模型 ====================

class YardCreateRequest(BaseModel):
    yard_id: str
    name: str
    origin_lat: float
    origin_lon: float
    origin_alt: float = 0.0
    bay_spacing: float = 6.0
    bay_count: int = 0
    bay_half_width_m: float = 0.3
    boundary: Optional[list] = None
    # rtk 手持逐贝位；rtg_ends 大车采头尾、其余自动生成。更新时不传表示不改。
    survey_mode: Optional[str] = None


# ==================== 堆场 CRUD ====================

@router.get("/all_origins", response_model=ApiResponse)
async def get_all_yards(db: Session = Depends(get_db)):
    """获取所有堆场列表（含 mapped 状态）"""
    try:
        yards = db.query(Yard).all()
        data = []
        for y in yards:
            rtk_count = db.query(func.count(SurveyPoint.id)).filter(
                SurveyPoint.yard_id == y.yard_id,
                SurveyPoint.point_type == "mapping",
            ).scalar() or 0
            if is_rtg_yard(y):
                # RTG 采点：头尾四个点齐全才算采点完成
                has_rtk = rtg_ready(db, y)
                y.mapped = has_rtk
            else:
                has_rtk = rtk_count > 0
                if has_rtk and not y.mapped:
                    y.mapped = True
            data.append({
                "yard_id": y.yard_id,
                "name": y.name,
                "origin": [y.origin_lon, y.origin_lat, y.origin_alt],
                "bay_spacing": y.bay_spacing,
                "total_bays": y.bay_count,
                "bay_half_width_m": y.bay_half_width_m if y.bay_half_width_m is not None else 0.3,
                "boundary": y.boundary or [],
                "y_axis_mode": y.y_axis_mode or "fit",
                "y_axis_points": y.y_axis_points,
                "survey_mode": survey_mode_of(y),
                "mapped": has_rtk or y.mapped,
                "rtk_point_count": rtk_count,
            })
        db.commit()
        return ApiResponse(code=200, message="获取成功", data=data)
    except Exception as e:
        logger.error(f"获取堆场列表失败: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


def _parse_yard_request(req: YardCreateRequest):
    """校验并规范化新建/更新请求。返回 (yard_id, half, boundary_payload)。"""
    yard_id = validate_yard_id(req.yard_id)
    half = float(req.bay_half_width_m if req.bay_half_width_m is not None else 0.3)
    if half <= 0:
        raise HTTPException(status_code=400, detail="贝位半宽必须大于 0")
    from app.utils.yard_geometry import normalize_boundary
    boundary = normalize_boundary(req.boundary)
    if len(boundary) < 3:
        raise HTTPException(status_code=400, detail="堆场区域至少需要 3 个顶点")
    boundary_payload = [{"lat": lat, "lon": lon} for lat, lon in boundary]
    if req.survey_mode is not None and req.survey_mode not in SURVEY_MODES:
        raise HTTPException(status_code=400, detail="采点方式只能是 rtk 或 rtg_ends")
    if req.survey_mode == SURVEY_MODE_RTG and int(req.bay_count or 0) < 2:
        raise HTTPException(status_code=400, detail="RTG 采点需要先填写贝位总数，且至少为 2")
    return yard_id, half, boundary_payload


@router.post("/create", response_model=ApiResponse)
async def create_yard(req: YardCreateRequest, db: Session = Depends(get_db)):
    """新建堆场。场号已存在时返回 409，不覆盖。"""
    try:
        yard_id, half, boundary_payload = _parse_yard_request(req)
        existing = db.query(Yard).filter(Yard.yard_id == yard_id).first()
        if existing:
            raise HTTPException(status_code=409, detail=f"场地 {yard_id} 已存在，请用编辑修改")
        yard = Yard(
            survey_mode=req.survey_mode or SURVEY_MODE_RTK,
            yard_id=yard_id,
            name=req.name,
            origin_lat=req.origin_lat,
            origin_lon=req.origin_lon,
            origin_alt=req.origin_alt,
            bay_spacing=req.bay_spacing,
            bay_count=req.bay_count,
            bay_half_width_m=half,
            boundary=boundary_payload,
            mapped=False,
        )
        db.add(yard)
        db.commit()
        logger.info(f"堆场新建: {yard_id}")
        return ApiResponse(code=200, message="创建成功", data={"yard_id": yard_id})
    except HTTPException:
        db.rollback()
        raise
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        db.rollback()
        logger.error(f"新建堆场失败: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@router.post("/update", response_model=ApiResponse)
async def update_yard(req: YardCreateRequest, db: Session = Depends(get_db)):
    """更新已有堆场。场号不存在时返回 404。不改 mapped，不清贝位点。"""
    try:
        yard_id, half, boundary_payload = _parse_yard_request(req)
        yard = db.query(Yard).filter(Yard.yard_id == yard_id).first()
        if not yard:
            raise HTTPException(status_code=404, detail=f"场地 {yard_id} 不存在")
        old_mode = survey_mode_of(yard)
        new_mode = req.survey_mode or old_mode
        if new_mode != old_mode and mapping_point_count(db, yard_id) > 0:
            raise HTTPException(
                status_code=409,
                detail="该场地已有贝位点，不能直接切换采点方式，请先清空贝位点",
            )
        old_count = int(yard.bay_count or 0)
        yard.survey_mode = new_mode
        yard.name = req.name
        yard.origin_lat = req.origin_lat
        yard.origin_lon = req.origin_lon
        yard.origin_alt = req.origin_alt
        if new_mode != SURVEY_MODE_RTG:
            yard.bay_spacing = req.bay_spacing
        yard.bay_count = req.bay_count
        yard.bay_half_width_m = half
        yard.boundary = boundary_payload
        warnings = []
        if new_mode == SURVEY_MODE_RTG:
            if int(req.bay_count or 0) < 2:
                raise HTTPException(status_code=400, detail="RTG 采点需要先填写贝位总数，且至少为 2")
            warnings = apply_bay_count_change(db, yard, old_count)
        db.commit()
        if warnings:
            notify_yard_map_updated(db, yard_id)
        logger.info(f"堆场更新: {yard_id}")
        return ApiResponse(
            code=200, message="保存成功", data={"yard_id": yard_id, "warnings": warnings},
        )
    except HTTPException:
        db.rollback()
        raise
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        db.rollback()
        logger.error(f"更新堆场失败: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


class YardForceRequest(BaseModel):
    vehicle_id: str
    yard_id: Optional[str] = None  # 空 = 清除偏好；重叠时恢复面积最小


@router.post("/force", response_model=ApiResponse)
async def force_active_yard(req: YardForceRequest, db: Session = Depends(get_db)):
    """
    设置某车的场地偏好（发给中控 SET_ACTIVE_YARD）。
    仅当车辆同时落在多个场多边形内时用于消歧；场外或单场忽略。
    yard_id 为空则清除偏好，重叠时仍取面积最小。
    """
    vehicle_id = (req.vehicle_id or "").strip()
    if not vehicle_id:
        raise HTTPException(status_code=400, detail="需要 vehicle_id")
    vehicle = db.query(Vehicle).filter(Vehicle.vehicle_id == vehicle_id).first()
    if vehicle is None:
        raise HTTPException(status_code=404, detail=f"车辆 {vehicle_id} 不存在")

    yard_id = (req.yard_id or "").strip() or None
    if yard_id is not None:
        try:
            yard_id = validate_yard_id(yard_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        yard = db.query(Yard).filter(Yard.yard_id == yard_id).first()
        if yard is None:
            raise HTTPException(status_code=404, detail=f"场地 {yard_id} 不存在")

    if not _mqtt_client or not _mqtt_client.is_connected():
        raise HTTPException(status_code=503, detail="MQTT 未连接，无法通知中控")

    topic = MQTT_TOPIC_CONTROL.format(vehicle_id=vehicle_id)
    payload = json.dumps(
        {"cmd": "SET_ACTIVE_YARD", "data": {"yard_id": yard_id}},
        ensure_ascii=False,
    )
    _mqtt_client.publish(topic, payload, qos=settings.MQTT_QOS_CONTROL)
    logger.info(f"已下发场地偏好: {topic} yard={yard_id or '清除'}")
    return ApiResponse(
        code=200,
        message=(
            "已清除场地偏好，重叠时按面积最小"
            if yard_id is None
            else f"已设置场地偏好为 {yard_id}（仅重叠区生效）"
        ),
        data={"vehicle_id": vehicle_id, "yard_id": yard_id, "forced": yard_id is not None},
    )


class YardAxisRequest(BaseModel):
    yard_id: str
    mode: str = "fit"  # fit | two_point
    source: str = "bays"  # bays | manual
    from_bay: Optional[int] = None
    to_bay: Optional[int] = None
    from_lat: Optional[float] = None
    from_lon: Optional[float] = None
    to_lat: Optional[float] = None
    to_lon: Optional[float] = None


@router.post("/y_axis", response_model=ApiResponse)
async def set_yard_axis(req: YardAxisRequest, db: Session = Depends(get_db)):
    """贝位采点时选择 +Y：全部拟合，或两点（已采集贝位 / 手填坐标）。"""
    try:
        yard_id = validate_yard_id(req.yard_id)
        yard = db.query(Yard).filter(Yard.yard_id == yard_id).first()
        if yard is None:
            raise HTTPException(status_code=404, detail=f"堆场 {yard_id} 不存在")
        if is_rtg_yard(yard):
            raise HTTPException(
                status_code=400,
                detail="该场地用 RTG 采点，+Y 固定为头贝位到尾贝位，不能手动设置",
            )
        mode = (req.mode or "fit").strip().lower()
        if mode not in ("fit", "two_point"):
            raise HTTPException(status_code=400, detail="Y 轴方式只能是拟合或两点")
        spec = None
        if mode == "two_point":
            source = (req.source or "bays").strip().lower()
            if source == "manual":
                if None in (req.from_lat, req.from_lon, req.to_lat, req.to_lon):
                    raise HTTPException(status_code=400, detail="请填写两个点的纬度和经度")
                spec = {
                    "source": "manual",
                    "from": {"lat": float(req.from_lat), "lon": float(req.from_lon)},
                    "to": {"lat": float(req.to_lat), "lon": float(req.to_lon)},
                }
            else:
                if not req.from_bay or not req.to_bay:
                    raise HTTPException(status_code=400, detail="请选择两个已采集的贝位")
                if int(req.from_bay) == int(req.to_bay):
                    raise HTTPException(status_code=400, detail="两个贝位不能相同")
                have = {
                    int(p.bay_no)
                    for p in db.query(SurveyPoint).filter(
                        SurveyPoint.yard_id == yard_id,
                        SurveyPoint.point_type == "mapping",
                        SurveyPoint.bay_no.isnot(None),
                    ).all()
                    if is_electrical_bay(p)
                }
                missing = [n for n in (int(req.from_bay), int(req.to_bay)) if n not in have]
                if missing:
                    raise HTTPException(status_code=400, detail=f"贝位 {missing[0]} 尚未采集")
                spec = {
                    "source": "bays",
                    "from_bay": int(req.from_bay),
                    "to_bay": int(req.to_bay),
                }
            from app.utils.yard_geometry import en_meters, resolve_y_axis
            en_pts = []
            for p in db.query(SurveyPoint).filter(
                SurveyPoint.yard_id == yard_id,
                SurveyPoint.point_type == "mapping",
                SurveyPoint.bay_no.isnot(None),
            ).all():
                if not is_electrical_bay(p):
                    continue
                east, north = en_meters(p.lat, p.lon, yard.origin_lat, yard.origin_lon)
                en_pts.append((int(p.bay_no), east, north))
            if resolve_y_axis(en_pts, mode, spec, yard.origin_lat, yard.origin_lon) is None:
                raise HTTPException(status_code=400, detail="两点距离过近，无法确定 +Y")
        yard.y_axis_mode = mode
        yard.y_axis_points = spec
        db.commit()
        return ApiResponse(code=200, message="Y 轴已保存", data={
            "yard_id": yard_id,
            "y_axis_mode": mode,
            "y_axis_points": spec,
        })
    except HTTPException:
        db.rollback()
        raise
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        db.rollback()
        logger.error(f"保存 Y 轴失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 底图 / 校准 ====================

@router.get("/map", response_model=ApiResponse)
async def get_yard_map(
    yard_id: str,
    vehicle_id: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """获取堆场底图 / 校准状态（含 survey_points）"""
    try:
        yard = db.query(Yard).filter(Yard.yard_id == yard_id).first()
        if not yard:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"堆场 {yard_id} 不存在"
            )

        # 本车校准（仅传 vehicle_id 时查询，避免未选车时误返回他车 m_matrix）
        calibration = None
        if vehicle_id:
            calibration = (
                db.query(CalibrationData)
                .filter(
                    CalibrationData.yard_id == yard_id,
                    CalibrationData.vehicle_id == vehicle_id,
                )
                .order_by(CalibrationData.updated_at.desc())
                .first()
            )

        # RTK 真值（仅 mapping）
        survey_points = db.query(SurveyPoint).filter(
            SurveyPoint.yard_id == yard_id,
            SurveyPoint.point_type == "mapping",
        ).order_by(SurveyPoint.bay_no.asc()).all()

        transform_list = []
        if vehicle_id:
            transforms = db.query(VehicleBayTransform).filter(
                VehicleBayTransform.vehicle_id == vehicle_id,
                VehicleBayTransform.yard_id == yard_id,
            ).order_by(VehicleBayTransform.bay_no.asc()).all()
            transform_list = [
                {
                    "bay_no": t.bay_no,
                    "ref": {"lat": t.ref_lat, "lon": t.ref_lon, "alt": t.ref_alt},
                    "measured": {
                        "lat": t.meas_lat, "lon": t.meas_lon, "alt": t.meas_alt,
                        "local_x": t.meas_local_x, "local_y": t.meas_local_y, "local_z": t.meas_local_z,
                    },
                    "delta": {"lat": t.delta_lat, "lon": t.delta_lon, "alt": t.delta_alt},
                }
                for t in transforms
            ]

        lane_corrections = []
        if vehicle_id:
            rows = db.query(LaneCorrection).filter(
                LaneCorrection.vehicle_id == vehicle_id,
                LaneCorrection.yard_id == yard_id,
            ).order_by(LaneCorrection.y_m.asc()).all()
            lane_corrections = [
                {
                    "id": row.id,
                    "y_m": row.y_m,
                    "elec1_m": row.elec1_m,
                    "elec2_m": row.elec2_m,
                    "diesel1_m": row.diesel1_m,
                    "diesel2_m": row.diesel2_m,
                }
                for row in rows
            ]

        cal_mode = "none"
        if vehicle_id:
            cal_mode = resolve_calibration_mode(db, vehicle_id, yard_id)

        return ApiResponse(
            code=200,
            message="获取成功",
            data={
                "yard_id": yard_id,
                "vehicle_id": vehicle_id,
                "calibration_status": calibration.status if calibration else "none",
                "calibration_mode": cal_mode,
                "has_rtk_map": (
                    rtg_ready(db, yard) if is_rtg_yard(yard) else len(survey_points) > 0
                ),
                "survey_mode": survey_mode_of(yard),
                "rtk_point_count": len(survey_points),
                "m_matrix": calibration.m_matrix if calibration else None,
                "source_vehicle_id": (
                    calibration.source_vehicle_id if calibration else None
                ),
                "transform_list": transform_list,
                "lane_corrections": lane_corrections,
                "survey_points": [
                    {
                        "id": p.id,
                        "point_type": p.point_type,
                        "bay_no": p.bay_no,
                        "bay_side": normalize_bay_side(p.bay_side),
                        "lat": p.lat, "lon": p.lon, "alt": p.alt,
                        "local_x": p.local_x, "local_y": p.local_y, "local_z": p.local_z,
                        "source": p.source or "rtk",
                        "vehicle_id": p.vehicle_id,
                        "std_m": p.std_m,
                    }
                    for p in survey_points
                ],
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取堆场地图失败: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
