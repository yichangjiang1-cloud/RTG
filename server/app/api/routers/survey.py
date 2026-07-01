"""打点管理接口"""

import json
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import SurveyPoint, CalibrationData, VehicleBayTransform, Yard
from app.schemas import SurveyConfirmRequest, SurveyFinalizeRequest, SurveyBatchImportRequest, ApiResponse
from app.utils.calibration_matrix import compute_transform_matrix
from app.utils.calibration_mode import (
    resolve_calibration_mode,
    get_reference_vehicle_id,
)
from app.utils.constants import MQTT_TOPIC_CONTROL
from app.utils.logger import get_logger

RTK_VEHICLE_ID = "RTK_HANDHELD"

logger = get_logger(__name__)
router = APIRouter(prefix="/api/survey", tags=["survey"])

_mqtt_client = None


def set_mqtt_client(client):
    """设置 MQTT 客户端（由 main.py 启动时调用）"""
    global _mqtt_client
    _mqtt_client = client


def _notify_calibration_finalized(vehicle_id: str, yard_id: str) -> None:
    """通知中控：校准 finalize 完成，触发堆场备份同步。"""
    if not _mqtt_client or not _mqtt_client.is_connected():
        logger.warning("MQTT 未连接，无法通知中控校准完成")
        return
    topic = MQTT_TOPIC_CONTROL.format(vehicle_id=vehicle_id)
    payload = json.dumps(
        {
            "cmd": "CALIBRATION_FINALIZED",
            "data": {
                "vehicle_id": vehicle_id,
                "yard_id": yard_id,
                "status": "completed",
            },
        },
        ensure_ascii=False,
    )
    _mqtt_client.publish(topic, payload, qos=settings.MQTT_QOS_CONTROL)
    logger.info(f"已下发校准完成指令: {topic} yard={yard_id}")


def _parse_bay_no(bay_id: str) -> int | None:
    """
    安全解析贝位号：支持 "Bay_01", "bay01", "1" 等各种格式
    返回整数序号，解析失败返回 None
    """
    if not bay_id:
        return None
    # 直接是数字字符串
    if bay_id.isdigit():
        return int(bay_id)
    # 提取字符串中的第一个数字段，如 "Bay_01" → 1
    m = re.search(r'(\d+)', bay_id)
    if m:
        return int(m.group(1))
    return None


def _rtk_bay_numbers(db: Session, yard_id: str) -> set[int]:
    rows = db.query(SurveyPoint.bay_no).filter(
        SurveyPoint.yard_id == yard_id,
        SurveyPoint.point_type == "mapping",
        SurveyPoint.bay_no.isnot(None),
    ).all()
    return {r[0] for r in rows}


def _required_calibration_bays(db: Session, vehicle_id: str, yard_id: str) -> tuple[int, int]:
    """
    返回 (最少需采贝位数, RTK 贝位总数)。
    校准1 full：需覆盖全部 RTK 贝位；校准2 verify：至少 3 个（不足 3 个 RTK 则全部）。
    """
    rtk_bays = _rtk_bay_numbers(db, yard_id)
    rtk_count = len(rtk_bays)
    if rtk_count == 0:
        return 0, 0

    others_done = db.query(CalibrationData).filter(
        CalibrationData.yard_id == yard_id,
        CalibrationData.status == "completed",
        CalibrationData.vehicle_id != vehicle_id,
    ).count()
    if others_done > 0:
        return min(3, rtk_count), rtk_count
    return rtk_count, rtk_count


def _mark_yard_rtk_ready(db: Session, yard_id: str) -> None:
    """有 RTK 打点后标记场地真值就绪"""
    count = db.query(SurveyPoint).filter(
        SurveyPoint.yard_id == yard_id,
        SurveyPoint.point_type == "mapping",
    ).count()
    if count > 0:
        yard = db.query(Yard).filter(Yard.yard_id == yard_id).first()
        if yard:
            yard.mapped = True


def _upsert_mapping_point(db: Session, req: SurveyConfirmRequest, bay_no: int | None):
    existing = None
    if bay_no is not None:
        existing = db.query(SurveyPoint).filter(
            SurveyPoint.yard_id == req.yard_id,
            SurveyPoint.point_type == "mapping",
            SurveyPoint.bay_no == bay_no,
        ).first()
    if existing:
        existing.lat = req.lat
        existing.lon = req.lon
        existing.alt = req.alt
        return existing
    sp = SurveyPoint(
        yard_id=req.yard_id,
        point_type="mapping",
        bay_no=bay_no,
        lat=req.lat,
        lon=req.lon,
        alt=req.alt,
    )
    db.add(sp)
    return sp


def _upsert_calibration_transform(
    db: Session, req: SurveyConfirmRequest, bay_no: int | None
) -> VehicleBayTransform:
    if bay_no is None:
        raise HTTPException(status_code=400, detail="无法解析贝位编号")

    rtk = db.query(SurveyPoint).filter(
        SurveyPoint.yard_id == req.yard_id,
        SurveyPoint.point_type == "mapping",
        SurveyPoint.bay_no == bay_no,
    ).first()
    if not rtk:
        raise HTTPException(
            status_code=400,
            detail=f"贝位 {req.bay_id} 尚无 RTK 真值，请先完成 RTK 建图",
        )

    cal_mode = resolve_calibration_mode(db, req.vehicle_id, req.yard_id)
    ref_lat, ref_lon, ref_alt = rtk.lat, rtk.lon, rtk.alt

    # 校准2：reference = 首车底图同贝坐标（001 的 measured）
    if cal_mode == "verify":
        ref_vid = get_reference_vehicle_id(db, req.yard_id, req.vehicle_id)
        if not ref_vid:
            raise HTTPException(
                status_code=400,
                detail="校准2 需要参考车已完成全场校准，请先完成首车 finalize",
            )
        ref_row = db.query(VehicleBayTransform).filter(
            VehicleBayTransform.vehicle_id == ref_vid,
            VehicleBayTransform.yard_id == req.yard_id,
            VehicleBayTransform.bay_no == bay_no,
        ).first()
        if not ref_row:
            raise HTTPException(
                status_code=400,
                detail=f"参考车 {ref_vid} 在贝位 {bay_no} 尚无底图数据，请先完成首车全场校准",
            )
        ref_lat = ref_row.meas_lat
        ref_lon = ref_row.meas_lon
        ref_alt = ref_row.meas_alt

    delta_lat = req.lat - ref_lat
    delta_lon = req.lon - ref_lon
    delta_alt = req.alt - ref_alt

    row = db.query(VehicleBayTransform).filter(
        VehicleBayTransform.vehicle_id == req.vehicle_id,
        VehicleBayTransform.yard_id == req.yard_id,
        VehicleBayTransform.bay_no == bay_no,
    ).first()
    if row:
        row.ref_lat, row.ref_lon, row.ref_alt = ref_lat, ref_lon, ref_alt
        row.meas_lat, row.meas_lon, row.meas_alt = req.lat, req.lon, req.alt
        row.meas_local_x = req.local_x
        row.meas_local_y = req.local_y
        row.meas_local_z = req.local_z
        row.delta_lat, row.delta_lon, row.delta_alt = delta_lat, delta_lon, delta_alt
        return row

    row = VehicleBayTransform(
        vehicle_id=req.vehicle_id,
        yard_id=req.yard_id,
        bay_no=bay_no,
        ref_lat=ref_lat,
        ref_lon=ref_lon,
        ref_alt=ref_alt,
        meas_lat=req.lat,
        meas_lon=req.lon,
        meas_alt=req.alt,
        meas_local_x=req.local_x,
        meas_local_y=req.local_y,
        meas_local_z=req.local_z,
        delta_lat=delta_lat,
        delta_lon=delta_lon,
        delta_alt=delta_alt,
    )
    db.add(row)
    return row


@router.post("/confirm", response_model=ApiResponse)
async def confirm_survey_point(
    req: SurveyConfirmRequest,
    db: Session = Depends(get_db)
):
    """确认打点：mapping=RTK 真值；calibration=车载对比并写入逐贝转换列表"""
    try:
        logger.info(
            f"打点确认: {req.vehicle_id} - 堆场: {req.yard_id} - 贝位: {req.bay_id} - 模式: {req.mode}"
        )

        bay_no = _parse_bay_no(req.bay_id)

        if req.mode == "mapping":
            if req.vehicle_id != RTK_VEHICLE_ID:
                raise HTTPException(
                    status_code=400,
                    detail="场地真值仅允许 RTK 录入，请使用 RTK 手动或 CSV 导入",
                )
            survey_point = _upsert_mapping_point(db, req, bay_no)
            _mark_yard_rtk_ready(db, req.yard_id)
            db.commit()
            rtk_count = db.query(SurveyPoint).filter(
                SurveyPoint.yard_id == req.yard_id,
                SurveyPoint.point_type == "mapping",
            ).count()
            yard = db.query(Yard).filter(Yard.yard_id == req.yard_id).first()
            return ApiResponse(
                code=200,
                message="RTK 真值保存成功",
                data={
                    "vehicle_id": req.vehicle_id,
                    "yard_id": req.yard_id,
                    "bay_id": req.bay_id,
                    "bay_no": bay_no,
                    "survey_id": survey_point.id,
                    "rtk_point_count": rtk_count,
                    "mapped": bool(yard and yard.mapped),
                },
            )

        if req.mode == "calibration":
            transform = _upsert_calibration_transform(db, req, bay_no)
            cal = db.query(CalibrationData).filter(
                CalibrationData.vehicle_id == req.vehicle_id,
                CalibrationData.yard_id == req.yard_id,
            ).first()
            if not cal:
                cal = CalibrationData(
                    vehicle_id=req.vehicle_id,
                    yard_id=req.yard_id,
                    status="pending",
                )
                db.add(cal)
            elif cal.status == "completed":
                # 覆盖/追加采点后需重新 finalize
                cal.status = "pending"
                cal.m_matrix = None
            db.commit()
            return ApiResponse(
                code=200,
                message="校准采点已写入转换列表",
                data={
                    "vehicle_id": req.vehicle_id,
                    "yard_id": req.yard_id,
                    "bay_id": req.bay_id,
                    "bay_no": bay_no,
                    "transform_id": transform.id,
                    "delta": {
                        "lat": transform.delta_lat,
                        "lon": transform.delta_lon,
                        "alt": transform.delta_alt,
                    },
                },
            )

        raise HTTPException(status_code=400, detail=f"未知 mode: {req.mode}")
    except Exception as e:
        db.rollback()
        logger.error(f"打点确认失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.post("/finalize", response_model=ApiResponse)
async def finalize_survey(
    req: SurveyFinalizeRequest,
    db: Session = Depends(get_db)
):
    """完成 B 车校准，写入 CalibrationData 并标记为 completed"""
    try:
        logger.info(f"完成校准: {req.vehicle_id} - 堆场: {req.yard_id}")

        yard = db.query(Yard).filter(Yard.yard_id == req.yard_id).first()
        if not yard:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"堆场 {req.yard_id} 不存在",
            )

        rtk_bays = _rtk_bay_numbers(db, req.yard_id)
        if not rtk_bays:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="场地尚无 RTK 真值，请先在场地建图录入至少一个贝位",
            )

        transforms = db.query(VehicleBayTransform).filter(
            VehicleBayTransform.vehicle_id == req.vehicle_id,
            VehicleBayTransform.yard_id == req.yard_id,
        ).order_by(VehicleBayTransform.bay_no.asc()).all()

        if not transforms:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="尚未采集任何校准贝位，请先对 RTK 贝位执行 confirm(mode=calibration)",
            )

        sampled_bays = {t.bay_no for t in transforms}
        missing_rtk = sampled_bays - rtk_bays
        if missing_rtk:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"贝位 {sorted(missing_rtk)} 无 RTK 真值，无法完成校准",
            )

        required, rtk_count = _required_calibration_bays(
            db, req.vehicle_id, req.yard_id
        )
        if len(sampled_bays) < required:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"校准采点不足：需至少 {required} 个贝位"
                    f"（本场 RTK 共 {rtk_count} 个），当前 {len(sampled_bays)} 个"
                ),
            )

        if required == rtk_count and sampled_bays != rtk_bays:
            missing = sorted(rtk_bays - sampled_bays)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"校准1 需覆盖全部 RTK 贝位，尚未采集: {missing}",
            )

        # 查找或创建校准记录
        calibration = db.query(CalibrationData).filter(
            CalibrationData.vehicle_id == req.vehicle_id,
            CalibrationData.yard_id == req.yard_id,
        ).first()

        cal_mode = resolve_calibration_mode(db, req.vehicle_id, req.yard_id)
        # 若本车已有 completed 记录，按 payload 中的 mode 或重新判定
        mode_in_payload = (req.calibration_data or {}).get("mode")
        if mode_in_payload in ("full", "verify"):
            cal_mode = mode_in_payload
        elif len(sampled_bays) < len(rtk_bays):
            cal_mode = "verify"

        transform_list = [
            {
                "bay_no": t.bay_no,
                "ref": {"lat": t.ref_lat, "lon": t.ref_lon, "alt": t.ref_alt},
                "measured": {
                    "lat": t.meas_lat,
                    "lon": t.meas_lon,
                    "alt": t.meas_alt,
                    "local_x": t.meas_local_x,
                    "local_y": t.meas_local_y,
                    "local_z": t.meas_local_z,
                },
                "delta": {"lat": t.delta_lat, "lon": t.delta_lon, "alt": t.delta_alt},
            }
            for t in transforms
        ]
        payload = req.calibration_data or {}
        payload["mode"] = cal_mode
        payload["transform_list"] = transform_list
        payload["steps"] = len(transforms)

        source_vehicle_id = None
        matrix = None
        residual = None

        if cal_mode == "verify":
            source_vehicle_id = get_reference_vehicle_id(
                db, req.yard_id, req.vehicle_id
            )
            if not source_vehicle_id:
                raise HTTPException(
                    status_code=400,
                    detail="校准2 缺少参考车，请先完成首车全场校准",
                )
            ref_transforms = db.query(VehicleBayTransform).filter(
                VehicleBayTransform.vehicle_id == source_vehicle_id,
                VehicleBayTransform.yard_id == req.yard_id,
                VehicleBayTransform.bay_no.in_(sorted(sampled_bays)[:3]),
            ).order_by(VehicleBayTransform.bay_no.asc()).all()
            if len(ref_transforms) < 3:
                raise HTTPException(
                    status_code=400,
                    detail="参考车在采点贝位上缺少底图数据，无法求 M",
                )
            by_bay = {t.bay_no: t for t in ref_transforms}
            measured_xy = []
            reference_xy = []
            for bay in sorted(sampled_bays)[:3]:
                cur = next(x for x in transforms if x.bay_no == bay)
                ref_t = by_bay.get(bay)
                if not ref_t:
                    raise HTTPException(
                        status_code=400,
                        detail=f"参考车在贝位 {bay} 无底图数据",
                    )
                if cur.meas_local_x is None or cur.meas_local_y is None:
                    raise HTTPException(
                        status_code=400,
                        detail=f"贝位 {bay} 缺少 local_x/local_y，无法求 M",
                    )
                if ref_t.meas_local_x is None or ref_t.meas_local_y is None:
                    raise HTTPException(
                        status_code=400,
                        detail=f"参考车贝位 {bay} 缺少 local_x/local_y",
                    )
                measured_xy.append((cur.meas_local_x, cur.meas_local_y))
                reference_xy.append((ref_t.meas_local_x, ref_t.meas_local_y))
            matrix, residual = compute_transform_matrix(measured_xy, reference_xy)
            payload["matrix"] = matrix
            payload["calibration_residual"] = residual
            payload["source_vehicle_id"] = source_vehicle_id

        if calibration:
            calibration.status = "completed"
            calibration.m_matrix = payload
            calibration.source_vehicle_id = source_vehicle_id
        else:
            calibration = CalibrationData(
                vehicle_id=req.vehicle_id,
                yard_id=req.yard_id,
                m_matrix=payload,
                status="completed",
                source_vehicle_id=source_vehicle_id,
            )
            db.add(calibration)

        db.commit()

        _notify_calibration_finalized(req.vehicle_id, req.yard_id)

        return ApiResponse(
            code=200,
            message="校准完成",
            data={
                "vehicle_id": req.vehicle_id,
                "yard_id": req.yard_id,
                "calibration_id": calibration.id,
                "status": "completed",
                "calibration_mode": cal_mode,
                "source_vehicle_id": source_vehicle_id,
                "calibration_residual": residual,
            }
        )
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"完成校准失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.delete("/rtk_points", response_model=ApiResponse)
async def clear_rtk_points(
    yard_id: str,
    db: Session = Depends(get_db),
):
    """清空场地的 RTK 真值（mapping 点），用于重新打点"""
    try:
        deleted = db.query(SurveyPoint).filter(
            SurveyPoint.yard_id == yard_id,
            SurveyPoint.point_type == "mapping",
        ).delete(synchronize_session=False)

        yard = db.query(Yard).filter(Yard.yard_id == yard_id).first()
        if yard:
            yard.mapped = False

        db.commit()
        logger.info(f"清空 RTK 真值: yard={yard_id}, deleted={deleted}")
        return ApiResponse(
            code=200,
            message=f"已清空 {deleted} 个 RTK 真值点",
            data={"yard_id": yard_id, "deleted": deleted},
        )
    except Exception as e:
        db.rollback()
        logger.error(f"清空 RTK 真值失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )


@router.get("/history", response_model=ApiResponse)
async def get_survey_history(
    yard_id: str,
    vehicle_id: Optional[str] = None,
    point_type: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """
    查询打点历史。
    - point_type=mapping（默认用途）：查 survey_points（RTK 真值），忽略 vehicle_id。
    - point_type=calibration：查 vehicle_bay_transforms，必须传 vehicle_id。
    """
    try:
        if point_type == "calibration":
            if not vehicle_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="point_type=calibration 时必须传 vehicle_id",
                )
            rows = (
                db.query(VehicleBayTransform)
                .filter(
                    VehicleBayTransform.vehicle_id == vehicle_id,
                    VehicleBayTransform.yard_id == yard_id,
                )
                .order_by(VehicleBayTransform.bay_no.asc())
                .all()
            )
            return ApiResponse(
                code=200,
                message="获取成功",
                data=[
                    {
                        "id": r.id,
                        "vehicle_id": vehicle_id,
                        "point_type": "calibration",
                        "bay_no": r.bay_no,
                        "bay_id": f"Bay_{r.bay_no:02d}" if r.bay_no is not None else None,
                        "lat": r.meas_lat,
                        "lon": r.meas_lon,
                        "alt": r.meas_alt,
                        "local_x": r.meas_local_x,
                        "local_y": r.meas_local_y,
                        "local_z": r.meas_local_z,
                        "ref": {
                            "lat": r.ref_lat,
                            "lon": r.ref_lon,
                            "alt": r.ref_alt,
                        },
                        "delta": {
                            "lat": r.delta_lat,
                            "lon": r.delta_lon,
                            "alt": r.delta_alt,
                        },
                        "created_at": r.updated_at.isoformat() if r.updated_at else None,
                    }
                    for r in rows
                ],
            )

        query = db.query(SurveyPoint).filter(SurveyPoint.yard_id == yard_id)
        if point_type:
            query = query.filter(SurveyPoint.point_type == point_type)

        points = query.order_by(
            SurveyPoint.bay_no.asc(), SurveyPoint.created_at.asc()
        ).all()

        return ApiResponse(
            code=200,
            message="获取成功",
            data=[
                {
                    "id": p.id,
                    "point_type": p.point_type,
                    "bay_no": p.bay_no,
                    "bay_id": f"Bay_{p.bay_no:02d}" if p.bay_no is not None else None,
                    "lat": p.lat,
                    "lon": p.lon,
                    "alt": p.alt,
                    "local_x": p.local_x,
                    "local_y": p.local_y,
                    "local_z": p.local_z,
                    "created_at": p.created_at.isoformat(),
                }
                for p in points
            ],
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取打点历史失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.post("/batch_import", response_model=ApiResponse)
async def batch_import_survey_points(
    req: SurveyBatchImportRequest,
    db: Session = Depends(get_db)
):
    """批量导入打点坐标（RTK 手持设备采集数据或 CSV 导入）"""
    try:
        if not req.points:
            raise HTTPException(status_code=400, detail="points 列表不能为空")

        logger.info(f"批量导入: yard={req.yard_id}, mode={req.mode}, count={len(req.points)}")
        created = []
        if req.mode != "mapping":
            raise HTTPException(status_code=400, detail="批量导入仅支持 RTK 真值 (mode=mapping)")

        for p in req.points:
            bay_no = _parse_bay_no(p.bay_id)
            fake_req = SurveyConfirmRequest(
                vehicle_id=RTK_VEHICLE_ID,
                yard_id=req.yard_id,
                bay_id=p.bay_id,
                mode="mapping",
                lat=p.lat,
                lon=p.lon,
                alt=p.alt,
            )
            _upsert_mapping_point(db, fake_req, bay_no)
            created.append({"bay_id": p.bay_id, "bay_no": bay_no})

        _mark_yard_rtk_ready(db, req.yard_id)
        db.commit()
        logger.info(f"批量导入完成: {len(created)} 条记录")

        return ApiResponse(
            code=200,
            message=f"成功导入 {len(created)} 个贝位坐标",
            data={"yard_id": req.yard_id, "count": len(created), "points": created}
        )
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"批量导入失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )
