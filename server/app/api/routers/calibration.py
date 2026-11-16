"""车辆场地校准：逐贝转换列表、校准模式判定"""

import json

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import CalibrationData, VehicleBayTransform
from app.schemas import ApiResponse
from app.utils.logger import get_logger
from app.utils.calibration_mode import (
    rtk_point_count as _rtk_point_count,
    resolve_calibration_mode as _resolve_calibration_mode,
    get_reference_vehicle_id,
)
from app.utils.constants import MQTT_TOPIC_CONTROL

logger = get_logger(__name__)
router = APIRouter(prefix="/api/calibration", tags=["calibration"])

RTK_VEHICLE_ID = "RTK_HANDHELD"
_mqtt_client = None


def set_mqtt_client(client):
    """设置 MQTT 客户端（由 main.py 启动时调用）"""
    global _mqtt_client
    _mqtt_client = client


def _notify_calibration_changed(vehicle_id: str, yard_id: str) -> None:
    """通知中控重新拉取堆场，使删除后的采点立即退出定位。"""
    if not _mqtt_client or not _mqtt_client.is_connected():
        logger.warning("MQTT 未连接，采点已删除，中控需重新同步后生效")
        return
    topic = MQTT_TOPIC_CONTROL.format(vehicle_id=vehicle_id)
    payload = json.dumps(
        {
            "cmd": "CALIBRATION_FINALIZED",
            "data": {"vehicle_id": vehicle_id, "yard_id": yard_id},
        },
        ensure_ascii=False,
    )
    _mqtt_client.publish(topic, payload, qos=settings.MQTT_QOS_CONTROL)
    logger.info(f"已通知中控刷新校准: {topic} yard={yard_id}")

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


@router.delete("/transform_list/{bay_no}", response_model=ApiResponse)
async def delete_transform_bay(
    bay_no: int,
    vehicle_id: str,
    yard_id: str,
    db: Session = Depends(get_db),
):
    """删除本车在本场的一个贝位采点。其余贝位保留，稍后可重新确认这一贝。"""
    if bay_no <= 0:
        raise HTTPException(status_code=400, detail="贝位编号无效")
    row = db.query(VehicleBayTransform).filter(
        VehicleBayTransform.vehicle_id == vehicle_id,
        VehicleBayTransform.yard_id == yard_id,
        VehicleBayTransform.bay_no == bay_no,
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"贝位 {bay_no} 没有采点")
    db.delete(row)
    db.flush()
    remaining = db.query(VehicleBayTransform).filter(
        VehicleBayTransform.vehicle_id == vehicle_id,
        VehicleBayTransform.yard_id == yard_id,
    ).count()
    cal_status = None
    if remaining == 0:
        cal = db.query(CalibrationData).filter(
            CalibrationData.vehicle_id == vehicle_id,
            CalibrationData.yard_id == yard_id,
        ).first()
        if cal:
            cal.status = "pending"
            cal.m_matrix = None
            cal_status = "pending"
    db.commit()
    _notify_calibration_changed(vehicle_id, yard_id)
    logger.info(f"删除校准采点: vehicle={vehicle_id} yard={yard_id} bay={bay_no} remaining={remaining}")
    return ApiResponse(
        code=200,
        message=f"已删除贝位 {bay_no} 的采点",
        data={
            "vehicle_id": vehicle_id,
            "yard_id": yard_id,
            "bay_no": bay_no,
            "remaining": remaining,
            "calibration_status": cal_status,
        },
    )
