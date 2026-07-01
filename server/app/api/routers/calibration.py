"""车辆场地校准：逐贝转换列表、校准模式判定"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import CalibrationData, VehicleBayTransform, SurveyPoint
from app.schemas import ApiResponse
from app.utils.logger import get_logger
from app.utils.calibration_mode import (
    rtk_point_count as _rtk_point_count,
    resolve_calibration_mode as _resolve_calibration_mode,
    get_reference_vehicle_id,
)

logger = get_logger(__name__)
router = APIRouter(prefix="/api/calibration", tags=["calibration"])

RTK_VEHICLE_ID = "RTK_HANDHELD"


def _serialize_transform(row: VehicleBayTransform) -> dict:
    return {
        "bay_no": row.bay_no,
        "bay_id": f"Bay_{row.bay_no:02d}" if row.bay_no is not None else None,
        "ref": {"lat": row.ref_lat, "lon": row.ref_lon, "alt": row.ref_alt},
        "measured": {
            "lat": row.meas_lat,
            "lon": row.meas_lon,
            "alt": row.meas_alt,
            "local_x": row.meas_local_x,
            "local_y": row.meas_local_y,
            "local_z": row.meas_local_z,
        },
        "delta": {
            "lat": row.delta_lat,
            "lon": row.delta_lon,
            "alt": row.delta_alt,
        },
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


@router.get("/mode", response_model=ApiResponse)
async def get_calibration_mode(
    vehicle_id: str,
    yard_id: str,
    db: Session = Depends(get_db),
):
    """查询当前车在该场地的校准模式（full / verify / skip）"""
    rtk_count = _rtk_point_count(db, yard_id)
    if rtk_count == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="场地尚无 RTK 真值，请先完成场地建图",
        )
    mode = _resolve_calibration_mode(db, vehicle_id, yard_id)
    ref_vehicle = None
    if mode == "verify":
        ref_vehicle = get_reference_vehicle_id(db, yard_id, vehicle_id)

    return ApiResponse(
        code=200,
        message="获取成功",
        data={
            "vehicle_id": vehicle_id,
            "yard_id": yard_id,
            "calibration_mode": mode,
            "rtk_point_count": rtk_count,
            "reference_vehicle_id": ref_vehicle,
        },
    )


@router.get("/transform_list", response_model=ApiResponse)
async def get_transform_list(
    vehicle_id: str,
    yard_id: str,
    db: Session = Depends(get_db),
):
    """获取某车在某场地的逐贝位转换列表"""
    rows = db.query(VehicleBayTransform).filter(
        VehicleBayTransform.vehicle_id == vehicle_id,
        VehicleBayTransform.yard_id == yard_id,
    ).order_by(VehicleBayTransform.bay_no.asc()).all()

    return ApiResponse(
        code=200,
        message="获取成功",
        data={
            "vehicle_id": vehicle_id,
            "yard_id": yard_id,
            "count": len(rows),
            "transform_list": [_serialize_transform(r) for r in rows],
        },
    )


@router.delete("/transform_list", response_model=ApiResponse)
async def clear_transform_list(
    vehicle_id: str,
    yard_id: str,
    db: Session = Depends(get_db),
):
    """清空本车在本场的逐贝校准记录，便于重新采点（同贝位 confirm 为覆盖，无需先清空）"""
    try:
        deleted = db.query(VehicleBayTransform).filter(
            VehicleBayTransform.vehicle_id == vehicle_id,
            VehicleBayTransform.yard_id == yard_id,
        ).delete(synchronize_session=False)

        cal = db.query(CalibrationData).filter(
            CalibrationData.vehicle_id == vehicle_id,
            CalibrationData.yard_id == yard_id,
        ).first()
        if cal:
            cal.status = "pending"
            cal.m_matrix = None

        db.commit()
        logger.info(
            f"清空校准: vehicle={vehicle_id} yard={yard_id} transforms={deleted}"
        )
        return ApiResponse(
            code=200,
            message=f"已清空 {deleted} 条逐贝校准记录",
            data={
                "vehicle_id": vehicle_id,
                "yard_id": yard_id,
                "deleted": deleted,
                "calibration_status": "pending",
            },
        )
    except Exception as e:
        db.rollback()
        logger.error(f"清空校准失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )
