"""堆场管理接口"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import Optional
from pydantic import BaseModel

from app.database import get_db
from app.models import Yard, CalibrationData, SurveyPoint, VehicleBayTransform
from sqlalchemy import func
from app.schemas import ApiResponse
from app.utils.logger import get_logger
from app.utils.calibration_mode import resolve_calibration_mode

logger = get_logger(__name__)
router = APIRouter(prefix="/api/yard", tags=["yard"])


# ==================== 请求模型 ====================

class YardCreateRequest(BaseModel):
    yard_id: str
    name: str
    origin_lat: float
    origin_lon: float
    origin_alt: float = 0.0
    heading_deg: float = 0.0
    bay_spacing: float = 6.0
    bay_count: int = 0


class GenerateMapRequest(BaseModel):
    yard_id: str
    vehicle_id: str


class SaveMapRequest(BaseModel):
    yard_id: str
    vehicle_id: str
    m_matrix: dict


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
                "heading": y.heading_deg,
                "bay_spacing": y.bay_spacing,
                "total_bays": y.bay_count,
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
        yard = db.query(Yard).filter(Yard.yard_id == req.yard_id).first()
        if yard:
            yard.name = req.name
            yard.origin_lat = req.origin_lat
            yard.origin_lon = req.origin_lon
            yard.origin_alt = req.origin_alt
            yard.heading_deg = req.heading_deg
            yard.bay_spacing = req.bay_spacing
            yard.bay_count = req.bay_count
        else:
            yard = Yard(
                yard_id=req.yard_id,
                name=req.name,
                origin_lat=req.origin_lat,
                origin_lon=req.origin_lon,
                origin_alt=req.origin_alt,
                heading_deg=req.heading_deg,
                bay_spacing=req.bay_spacing,
                bay_count=req.bay_count,
                mapped=False,
            )
            db.add(yard)
        db.commit()
        logger.info(f"堆场保存: {req.yard_id}")
        return ApiResponse(code=200, message="保存成功", data={"yard_id": req.yard_id})
    except Exception as e:
        db.rollback()
        logger.error(f"保存堆场失败: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


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
                "survey_points": [
                    {
                        "id": p.id,
                        "point_type": p.point_type,
                        "bay_no": p.bay_no,
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


@router.post("/generate_map", response_model=ApiResponse)
async def generate_yard_map(
    req: GenerateMapRequest,
    db: Session = Depends(get_db)
):
    """触发中控机生成堆场底图（A 车建图指令）"""
    try:
        logger.info(f"生成堆场底图: {req.yard_id} 车辆: {req.vehicle_id}")
        return ApiResponse(
            code=200,
            message="底图生成命令已发送",
            data={"yard_id": req.yard_id, "vehicle_id": req.vehicle_id}
        )
    except Exception as e:
        logger.error(f"生成堆场底图失败: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@router.post("/save_map", response_model=ApiResponse)
async def save_yard_map(
    req: SaveMapRequest,
    db: Session = Depends(get_db)
):
    """保存 A 车底图校准矩阵，并将堆场标记为已建图"""
    try:
        calibration = db.query(CalibrationData).filter(
            CalibrationData.yard_id == req.yard_id,
            CalibrationData.vehicle_id == req.vehicle_id
        ).first()

        if calibration:
            calibration.m_matrix = req.m_matrix
            calibration.status = "completed"
        else:
            calibration = CalibrationData(
                vehicle_id=req.vehicle_id,
                yard_id=req.yard_id,
                m_matrix=req.m_matrix,
                status="completed"
            )
            db.add(calibration)

        yard = db.query(Yard).filter(Yard.yard_id == req.yard_id).first()
        if yard:
            yard.mapped = True

        db.commit()
        logger.info(f"保存底图矩阵: {req.yard_id}，堆场已标记为已建图")

        return ApiResponse(
            code=200,
            message="底图保存成功",
            data={"yard_id": req.yard_id}
        )
    except Exception as e:
        db.rollback()
        logger.error(f"保存校准矩阵失败: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
