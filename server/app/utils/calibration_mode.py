"""校准模式与参考车判定（survey / calibration 共用）"""

from sqlalchemy.orm import Session

from app.models import CalibrationData, SurveyPoint


def rtk_point_count(db: Session, yard_id: str) -> int:
    return db.query(SurveyPoint).filter(
        SurveyPoint.yard_id == yard_id,
        SurveyPoint.point_type == "mapping",
    ).count()


def resolve_calibration_mode(db: Session, vehicle_id: str, yard_id: str) -> str:
    mine = db.query(CalibrationData).filter(
        CalibrationData.vehicle_id == vehicle_id,
        CalibrationData.yard_id == yard_id,
    ).first()
    if mine and mine.status == "completed":
        return "skip"

    # 场地首台完成校准的车永远是「校准1 full」（含重校 pending 阶段）
    first_done = (
        db.query(CalibrationData)
        .filter(
            CalibrationData.yard_id == yard_id,
            CalibrationData.status == "completed",
        )
        .order_by(CalibrationData.updated_at.asc())
        .first()
    )
    if first_done and first_done.vehicle_id == vehicle_id:
        return "full"

    others_done = db.query(CalibrationData).filter(
        CalibrationData.yard_id == yard_id,
        CalibrationData.status == "completed",
        CalibrationData.vehicle_id != vehicle_id,
    ).count()
    return "verify" if others_done > 0 else "full"


def get_reference_vehicle_id(db: Session, yard_id: str, vehicle_id: str) -> str | None:
    """校准2：返回已完成校准的参考车（首车优先最早 completed）。"""
    ref = (
        db.query(CalibrationData)
        .filter(
            CalibrationData.yard_id == yard_id,
            CalibrationData.status == "completed",
            CalibrationData.vehicle_id != vehicle_id,
        )
        .order_by(CalibrationData.updated_at.asc())
        .first()
    )
    return ref.vehicle_id if ref else None
