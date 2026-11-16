"""堆场管理接口"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import Optional
from pydantic import BaseModel

from app.database import get_db
from app.models import Yard, CalibrationData, SurveyPoint, VehicleBayTransform, LaneCorrection
from sqlalchemy import func
from app.schemas import ApiResponse
from app.utils.logger import get_logger
from app.utils.calibration_mode import resolve_calibration_mode
from app.utils.yard_id import validate_yard_id
from app.utils.yard_geometry import is_electrical_bay, normalize_bay_side

logger = get_logger(__name__)
router = APIRouter(prefix="/api/yard", tags=["yard"])


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
                "mapped": has_rtk or y.mapped,
                "rtk_point_count": rtk_count,
            })
        db.commit()
        return ApiResponse(code=200, message="获取成功", data=data)
    except Exception as e:
        logger.error(f"获取堆场列表失败: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@router.post("/create", response_model=ApiResponse)
async def create_yard(req: YardCreateRequest, db: Session = Depends(get_db)):
    """新建或更新堆场信息"""
    try:
        yard_id = validate_yard_id(req.yard_id)
        half = float(req.bay_half_width_m if req.bay_half_width_m is not None else 0.3)
        if half <= 0:
            raise HTTPException(status_code=400, detail="贝位半宽必须大于 0")
        from app.utils.yard_geometry import normalize_boundary
        boundary = normalize_boundary(req.boundary)
        if len(boundary) < 3:
            raise HTTPException(status_code=400, detail="堆场区域至少需要 3 个顶点")
        boundary_payload = [{"lat": lat, "lon": lon} for lat, lon in boundary]
        yard = db.query(Yard).filter(Yard.yard_id == yard_id).first()
        if yard:
            yard.name = req.name
            yard.origin_lat = req.origin_lat
            yard.origin_lon = req.origin_lon
            yard.origin_alt = req.origin_alt
            yard.bay_spacing = req.bay_spacing
            yard.bay_count = req.bay_count
            yard.bay_half_width_m = half
            yard.boundary = boundary_payload
        else:
            yard = Yard(
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
        logger.info(f"堆场保存: {yard_id}")
        return ApiResponse(code=200, message="保存成功", data={"yard_id": yard_id})
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        db.rollback()
        logger.error(f"保存堆场失败: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


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
                "has_rtk_map": len(survey_points) > 0,
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
