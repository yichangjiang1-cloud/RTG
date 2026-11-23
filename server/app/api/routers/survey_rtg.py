"""RTG 大车采头、尾贝位，其余贝位按间距自动生成。

约定：
- 头贝位 = 1 号，尾贝位 = bay_count 号。
- 一次采集同时给出电气侧、动力侧的点（各取本侧两只轮胎中心的中点）。
- +Y 固定为 头 → 尾（two_point，source=bays）。
- 来源 source：rtg_end 头尾；rtg_fix 重采的中间贝位；auto 自动生成；rtk 手持。
- 自动点只会被 auto 生成逻辑改写，不会覆盖 rtg_end / rtg_fix / rtk。
"""
from __future__ import annotations

import json
import math
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.routers import survey as survey_router
from app.config import settings
from app.database import get_db
from app.models import (
    LaneCorrection,
    SurveyPoint,
    Vehicle,
    VehicleBayTransform,
    Yard,
    get_cst_now,
)
from app.schemas import (
    ApiResponse,
    RtgCaptureRequest,
    RtgRegenerateRequest,
    RtgRestoreRequest,
    SurveyPointAdjustRequest,
)
from app.utils.constants import MQTT_TOPIC_CONTROL
from app.utils.logger import get_logger
from app.utils.survey_adjust_log import append_record as append_adjust_record
from app.utils.yard_geometry import (
    axis_bearing_deg,
    bearing_diff_deg,
    en_meters,
    lerp_bay_point,
    monotonic_violation,
    normalize_bay_side,
    shift_wgs84_by_yard,
    to_yard_xy,
)

logger = get_logger(__name__)
router = APIRouter(prefix="/api/survey/rtg", tags=["survey-rtg"])

SOURCE_RTK = "rtk"
SOURCE_END = "rtg_end"
SOURCE_FIX = "rtg_fix"
SOURCE_AUTO = "auto"

SURVEY_MODE_RTK = "rtk"
SURVEY_MODE_RTG = "rtg_ends"
SURVEY_MODES = (SURVEY_MODE_RTK, SURVEY_MODE_RTG)

SIDES = ("elec", "diesel")
SIDE_LABEL = {"elec": "电气侧", "diesel": "动力侧"}

MAX_FAR_FROM_ORIGIN_M = 5000.0  # 采集点离堆场原点超过这个距离，当成垃圾数据
MAX_STD_M = 0.10  # 采集窗口标准差超过这个值直接拒绝（除非 force）
MIN_SHIFT_LIMIT_M = 1.0  # 重采与原值允许的最小差距
SHIFT_LIMIT_RATIO = 0.4  # 重采与原值允许相差的间距比例
AXIS_CHANGE_DEG = 0.1  # +Y 方位变化超过它就算 +Y 变了
SIDE_AXIS_DIFF_WARN_DEG = 0.5  # 两侧头尾方向相差警告
SPACING_WARN_MIN_M = 2.0
SPACING_WARN_MAX_M = 25.0


# ==================== 基础查询 ====================

def survey_mode_of(yard: Yard) -> str:
    mode = (yard.survey_mode or SURVEY_MODE_RTK).strip().lower()
    return mode if mode in SURVEY_MODES else SURVEY_MODE_RTK


def is_rtg_yard(yard: Yard) -> bool:
    return survey_mode_of(yard) == SURVEY_MODE_RTG


def _get_yard(db: Session, yard_id: str) -> Yard:
    yard = db.query(Yard).filter(Yard.yard_id == yard_id).first()
    if yard is None:
        raise HTTPException(status_code=404, detail=f"堆场 {yard_id} 不存在")
    return yard


def _require_rtg_yard(yard: Yard) -> int:
    if not is_rtg_yard(yard):
        raise HTTPException(
            status_code=400,
            detail=f"场地 {yard.yard_id} 的采点方式是手持 RTK，不能用 RTG 采点",
        )
    n = int(yard.bay_count or 0)
    if n < 2:
        raise HTTPException(status_code=400, detail="RTG 采点至少需要 2 个贝位，请先在场地里填写贝位总数")
    return n


def _rows(db: Session, yard_id: str) -> List[SurveyPoint]:
    db.flush()
    return db.query(SurveyPoint).filter(
        SurveyPoint.yard_id == yard_id,
        SurveyPoint.point_type == "mapping",
        SurveyPoint.bay_no.isnot(None),
    ).all()


def _find(rows: List[SurveyPoint], bay_no: int, side: str) -> Optional[SurveyPoint]:
    for p in rows:
        if int(p.bay_no) == int(bay_no) and normalize_bay_side(p.bay_side) == side:
            return p
    return None


def _source(p: SurveyPoint) -> str:
    return p.source or SOURCE_RTK


def mapping_point_count(db: Session, yard_id: str) -> int:
    return len(_rows(db, yard_id))


def rtg_ready(db: Session, yard: Yard) -> bool:
    """头、尾两侧四个点都在，才算采点完成。"""
    n = int(yard.bay_count or 0)
    if n < 2:
        return False
    rows = _rows(db, yard.yard_id)
    return all(_find(rows, bay, side) is not None for side in SIDES for bay in (1, n))


# ==================== 点的写入 ====================

def _upsert(
    db: Session,
    yard_id: str,
    bay_no: int,
    side: str,
    lat: float,
    lon: float,
    alt: float,
    source: str,
    vehicle_id: Optional[str] = None,
    frame_count: Optional[int] = None,
    std_m: Optional[float] = None,
) -> SurveyPoint:
    rows = _rows(db, yard_id)
    row = _find(rows, bay_no, side)
    if row is None:
        row = SurveyPoint(
            yard_id=yard_id,
            point_type="mapping",
            bay_no=int(bay_no),
            bay_side=side,
            lat=lat,
            lon=lon,
            alt=alt,
            source=source,
            vehicle_id=vehicle_id,
            frame_count=frame_count if frame_count is not None else 0,
            std_m=std_m,
        )
        db.add(row)
    else:
        row.lat = lat
        row.lon = lon
        row.alt = alt
        row.bay_side = side
        row.source = source
        row.vehicle_id = vehicle_id
        if frame_count is not None:
            row.frame_count = frame_count
        row.std_m = std_m
        row.updated_at = get_cst_now()
    db.flush()
    return row


def regenerate_auto(db: Session, yard: Yard) -> Dict[str, int]:
    """
    按头尾重新生成中间贝位的自动点。
    只改写 source=auto 的点。头尾缺一个时，该侧已有的自动点会被清掉。
    """
    n = int(yard.bay_count or 0)
    created = updated = removed = 0
    rows = _rows(db, yard.yard_id)
    for p in list(rows):
        if _source(p) == SOURCE_AUTO and (n < 3 or int(p.bay_no) < 2 or int(p.bay_no) > n - 1):
            db.delete(p)
            removed += 1
    db.flush()
    if n >= 3:
        rows = _rows(db, yard.yard_id)
        for side in SIDES:
            head = _find(rows, 1, side)
            tail = _find(rows, n, side)
            ready = (
                head is not None and tail is not None
                and _source(head) != SOURCE_AUTO and _source(tail) != SOURCE_AUTO
            )
            for k in range(2, n):
                cur = _find(rows, k, side)
                if not ready:
                    if cur is not None and _source(cur) == SOURCE_AUTO:
                        db.delete(cur)
                        removed += 1
                    continue
                if cur is not None and _source(cur) != SOURCE_AUTO:
                    continue
                lat, lon, alt = lerp_bay_point(
                    (head.lat, head.lon, head.alt), (tail.lat, tail.lon, tail.alt), k, n,
                )
                if cur is None:
                    db.add(SurveyPoint(
                        yard_id=yard.yard_id,
                        point_type="mapping",
                        bay_no=k,
                        bay_side=side,
                        lat=lat,
                        lon=lon,
                        alt=alt,
                        source=SOURCE_AUTO,
                        frame_count=0,
                    ))
                    created += 1
                else:
                    cur.lat, cur.lon, cur.alt = lat, lon, alt
                    cur.updated_at = get_cst_now()
                    updated += 1
    db.flush()
    return {"created": created, "updated": updated, "removed": removed}


# ==================== +Y ====================

def _en_group(rows: List[SurveyPoint], side: str, yard: Yard):
    return survey_router._en_of(
        survey_router._points_on_side(rows, side), yard.origin_lat, yard.origin_lon,
    )


def current_axis(db: Session, yard: Yard):
    """按场地当前保存的 +Y 方式算出的单位向量 (east, north)，算不出返回 None。"""
    rows = _rows(db, yard.yard_id)
    elec = _en_group(rows, "elec", yard)
    diesel = _en_group(rows, "diesel", yard)
    return survey_router._yard_axis(yard, elec, diesel)


def pin_axis(db: Session, yard: Yard):
    """+Y 固定为 头 → 尾（电气侧）。返回新的 +Y，头尾太近时返回 None。"""
    n = int(yard.bay_count or 0)
    yard.y_axis_mode = "two_point"
    yard.y_axis_points = {"source": "bays", "from_bay": 1, "to_bay": n}
    db.flush()
    return current_axis(db, yard)


def _axis_dependents(db: Session, yard_id: str) -> Tuple[int, int]:
    lanes = db.query(LaneCorrection).filter(LaneCorrection.yard_id == yard_id).count()
    transforms = db.query(VehicleBayTransform).filter(
        VehicleBayTransform.yard_id == yard_id
    ).count()
    return lanes, transforms


# ==================== 校验与摘要 ====================

def _bay_ys(rows: List[SurveyPoint], side: str, yard: Yard, axis) -> List[Tuple[int, float]]:
    out = []
    for bay, east, north in _en_group(rows, side, yard):
        out.append((bay, to_yard_xy(east, north, axis[0], axis[1])[1]))
    return out


def validate_points(db: Session, yard: Yard) -> List[str]:
    """校验间距与顺序，失败抛 400。返回软警告。"""
    survey_router._reject_close_bays(db, yard.yard_id)
    rows = _rows(db, yard.yard_id)
    axis = current_axis(db, yard)
    if axis is not None:
        for side in SIDES:
            message = monotonic_violation(_bay_ys(rows, side, yard, axis))
            if message:
                raise HTTPException(status_code=400, detail=f"{SIDE_LABEL[side]}{message}")
    return list(survey_router._pair_warnings(db, yard.yard_id))


def _head_tail_en(rows: List[SurveyPoint], side: str, yard: Yard):
    n = int(yard.bay_count or 0)
    head = _find(rows, 1, side)
    tail = _find(rows, n, side)
    if head is None or tail is None:
        return None
    he = en_meters(head.lat, head.lon, yard.origin_lat, yard.origin_lon)
    te = en_meters(tail.lat, tail.lon, yard.origin_lat, yard.origin_lon)
    return he, te


def _spacing_m(rows: List[SurveyPoint], yard: Yard) -> Optional[float]:
    pair = _head_tail_en(rows, "elec", yard)
    n = int(yard.bay_count or 0)
    if pair is None or n < 2:
        return None
    (he, hn), (te, tn) = pair
    return math.hypot(te - he, tn - hn) / float(n - 1)


def summarize(db: Session, yard: Yard) -> dict:
    n = int(yard.bay_count or 0)
    rows = _rows(db, yard.yard_id)
    counts = {SOURCE_RTK: 0, SOURCE_END: 0, SOURCE_FIX: 0, SOURCE_AUTO: 0}
    for p in rows:
        counts[_source(p)] = counts.get(_source(p), 0) + 1
    ends = {
        side: {
            "head": _find(rows, 1, side) is not None,
            "tail": _find(rows, n, side) is not None,
        }
        for side in SIDES
    }
    warnings: List[str] = []
    pair = _head_tail_en(rows, "elec", yard)
    distance = spacing = bearing = None
    if pair is not None:
        (he, hn), (te, tn) = pair
        distance = math.hypot(te - he, tn - hn)
        spacing = distance / float(n - 1) if n >= 2 else None
        if distance > 1e-6:
            bearing = axis_bearing_deg(((te - he) / distance, (tn - hn) / distance))
        if spacing is not None and not (SPACING_WARN_MIN_M <= spacing <= SPACING_WARN_MAX_M):
            warnings.append(
                f"按头尾算出的贝位间距是 {spacing:.2f} m，不在常见范围 "
                f"{SPACING_WARN_MIN_M:.0f}~{SPACING_WARN_MAX_M:.0f} m，请确认贝位总数和头尾位置"
            )
    diesel_pair = _head_tail_en(rows, "diesel", yard)
    if pair is not None and diesel_pair is not None and bearing is not None:
        (dhe, dhn), (dte, dtn) = diesel_pair
        d_dist = math.hypot(dte - dhe, dtn - dhn)
        if d_dist > 1e-6:
            d_bearing = axis_bearing_deg(((dte - dhe) / d_dist, (dtn - dhn) / d_dist))
            diff = bearing_diff_deg(bearing, d_bearing)
            if diff > SIDE_AXIS_DIFF_WARN_DEG:
                warnings.append(
                    f"电气侧、动力侧头尾连线方向相差 {diff:.2f}°，请核对两次采集"
                )
    axis = current_axis(db, yard) if pair is not None else None
    return {
        "yard_id": yard.yard_id,
        "survey_mode": survey_mode_of(yard),
        "bay_count": n,
        "ready": rtg_ready(db, yard),
        "ends": ends,
        "counts": counts,
        "point_count": len(rows),
        "head_tail_distance_m": distance,
        "bay_spacing_m": spacing,
        "y_axis_bearing_deg": None if axis is None else axis_bearing_deg(axis),
        "y_axis_mode": yard.y_axis_mode,
        "y_axis_points": yard.y_axis_points,
        "shift_limit_m": max(MIN_SHIFT_LIMIT_M, SHIFT_LIMIT_RATIO * spacing)
        if spacing is not None else MIN_SHIFT_LIMIT_M,
        "warnings": warnings,
    }


def _refresh_yard_flags(db: Session, yard: Yard) -> None:
    """mapped 只在头尾四点齐全时为真；间距回写成头尾距离 / (N-1)。"""
    yard.mapped = rtg_ready(db, yard)
    spacing = _spacing_m(_rows(db, yard.yard_id), yard)
    if spacing is not None and yard.mapped:
        yard.bay_spacing = round(spacing, 4)


# ==================== 通知中控 ====================

def notify_yard_map_updated(db: Session, yard_id: str) -> int:
    """向所有车辆发 YARD_MAP_UPDATED，让中控重新同步贝位点。返回发出条数。"""
    client = getattr(survey_router, "_mqtt_client", None)
    if not client or not client.is_connected():
        logger.warning("MQTT 未连接，贝位点已保存，中控需重新同步后生效")
        return 0
    sent = 0
    payload = json.dumps(
        {"cmd": "YARD_MAP_UPDATED", "data": {"yard_id": yard_id}},
        ensure_ascii=False,
    )
    for vehicle in db.query(Vehicle).all():
        topic = MQTT_TOPIC_CONTROL.format(vehicle_id=vehicle.vehicle_id)
        client.publish(topic, payload, qos=settings.MQTT_QOS_CONTROL)
        sent += 1
    logger.info(f"已下发贝位点更新: yard={yard_id} 车辆数={sent}")
    return sent


# ==================== 手动改坐标 / XY 平移 ====================

def _resolve_adjust_sides(bay_side: str) -> List[str]:
    raw = (bay_side or "both").strip().lower()
    if raw in ("both", "all", "两侧"):
        return list(SIDES)
    side = normalize_bay_side(raw)
    return [side]


def _source_after_adjust(yard: Yard, bay_no: int, prev_source: Optional[str]) -> str:
    """手动改写后的来源：中间贝位标成 rtg_fix（可恢复自动）；头尾仍是 rtg_end；RTK 场保持 rtk。"""
    if not is_rtg_yard(yard):
        return SOURCE_RTK
    n = int(yard.bay_count or 0)
    if int(bay_no) in (1, n):
        return SOURCE_END
    if prev_source == SOURCE_END:
        return SOURCE_END
    return SOURCE_FIX


def apply_adjust(db: Session, req: SurveyPointAdjustRequest) -> dict:
    """
    手动写经纬度，或沿堆场 X/Y 平移贝位点。
    RTG 场：改头尾会重钉 +Y 并重生 auto；改中间点会标成 rtg_fix，不被 auto 覆盖。
    """
    yard = _get_yard(db, req.yard_id)
    bay_no = int(req.bay_no)
    if bay_no < 1:
        raise HTTPException(status_code=400, detail="贝位号无效")
    sides = _resolve_adjust_sides(req.bay_side)
    mode = (req.mode or "").strip().lower()
    if mode not in ("set", "nudge"):
        raise HTTPException(status_code=400, detail="mode 只能是 set 或 nudge")

    rows = _rows(db, yard.yard_id)
    targets = {side: _find(rows, bay_no, side) for side in sides}
    missing = [SIDE_LABEL[s] for s, p in targets.items() if p is None]
    if missing and mode == "nudge":
        raise HTTPException(
            status_code=404,
            detail=f"贝位 {bay_no} 缺少{'/'.join(missing)}点，无法平移（请先采点或手动写入）",
        )

    updates: Dict[str, Tuple[float, float, float]] = {}
    if mode == "set":
        if len(sides) == 2:
            for side, payload in (("elec", req.elec), ("diesel", req.diesel)):
                if payload is None:
                    raise HTTPException(status_code=400, detail=f"两侧写入需要提供 {side} 坐标")
                _check_geo(SIDE_LABEL[side], payload.lat, payload.lon, payload.alt)
                updates[side] = (float(payload.lat), float(payload.lon), float(payload.alt))
        else:
            side = sides[0]
            lat = req.lat if req.lat is not None else (getattr(getattr(req, side, None), "lat", None))
            lon = req.lon if req.lon is not None else (getattr(getattr(req, side, None), "lon", None))
            alt = req.alt if req.alt is not None else (getattr(getattr(req, side, None), "alt", None))
            side_payload = req.elec if side == "elec" else req.diesel
            if side_payload is not None:
                lat, lon, alt = side_payload.lat, side_payload.lon, side_payload.alt
            if lat is None or lon is None:
                raise HTTPException(status_code=400, detail="请提供 lat/lon")
            alt_v = 0.0 if alt is None else float(alt)
            _check_geo(SIDE_LABEL[side], float(lat), float(lon), alt_v)
            updates[side] = (float(lat), float(lon), alt_v)
            if targets[side] is None:
                # 允许新建单侧点（RTK 或补一侧）
                pass
    else:
        dx, dy = float(req.dx_m), float(req.dy_m)
        if not (math.isfinite(dx) and math.isfinite(dy)):
            raise HTTPException(status_code=400, detail="dx_m/dy_m 无效")
        if abs(dx) < 1e-9 and abs(dy) < 1e-9:
            raise HTTPException(status_code=400, detail="平移量不能为 0")
        if abs(dx) > 50 or abs(dy) > 50:
            raise HTTPException(status_code=400, detail="单次平移不超过 50 m")
        axis = current_axis(db, yard)
        for side in sides:
            cur = targets[side]
            assert cur is not None
            nlat, nlon = shift_wgs84_by_yard(
                cur.lat, cur.lon, dx, dy,
                yard.origin_lat, yard.origin_lon, axis,
            )
            updates[side] = (nlat, nlon, float(cur.alt or 0.0))

    # 离原点过远检查（用更新后坐标；若改的是头电气会先暂存）
    origin_lat, origin_lon = float(yard.origin_lat), float(yard.origin_lon)
    head_elec_set = (
        is_rtg_yard(yard)
        and bay_no == 1
        and "elec" in updates
    )
    if head_elec_set:
        origin_lat, origin_lon = updates["elec"][0], updates["elec"][1]

    for side, (lat, lon, alt) in updates.items():
        east, north = en_meters(lat, lon, origin_lat, origin_lon)
        if math.hypot(east, north) > MAX_FAR_FROM_ORIGIN_M:
            raise HTTPException(
                status_code=400,
                detail=f"{SIDE_LABEL[side]}修正后离堆场原点超过 {MAX_FAR_FROM_ORIGIN_M / 1000:.0f} km",
            )

    # 大位移二次确认（相对原点前的旧位置）
    if mode in ("set", "nudge") and not req.force:
        spacing = _spacing_m(rows, yard) or float(yard.bay_spacing or 6.0)
        limit = max(MIN_SHIFT_LIMIT_M, SHIFT_LIMIT_RATIO * spacing)
        far = []
        for side, (lat, lon, _alt) in updates.items():
            cur = targets[side]
            if cur is None:
                continue
            ce, cn = en_meters(cur.lat, cur.lon, yard.origin_lat, yard.origin_lon)
            ne, nn = en_meters(lat, lon, yard.origin_lat, yard.origin_lon)
            shift = math.hypot(ne - ce, nn - cn)
            if shift > limit:
                far.append(f"{SIDE_LABEL[side]}移动 {shift:.2f} m")
        if far:
            raise HTTPException(
                status_code=409,
                detail=f"贝位 {bay_no} {'，'.join(far)}，超过 {limit:.2f} m，请确认后强制保存",
            )

    prev_axis = current_axis(db, yard) if is_rtg_yard(yard) and rtg_ready(db, yard) else None
    if prev_axis is None:
        prev_axis = current_axis(db, yard)
    origin_lat0 = float(yard.origin_lat)
    origin_lon0 = float(yard.origin_lon)
    origin_alt0 = float(yard.origin_alt or 0.0)
    origin_updated = False
    warnings: List[str] = []

    def _lycs_of(lat: float, lon: float, alt: float):
        if prev_axis is None:
            return None
        east, north = en_meters(lat, lon, origin_lat0, origin_lon0)
        x, y = to_yard_xy(east, north, prev_axis[0], prev_axis[1])
        return {
            "x": round(float(x), 4),
            "y": round(float(y), 4),
            "z": round(float(alt) - origin_alt0, 4),
        }

    before_snapshot: Dict[str, Any] = {}
    for side in sides:
        cur = targets[side]
        if cur is None:
            before_snapshot[side] = None
            continue
        before_snapshot[side] = {
            "lat": float(cur.lat),
            "lon": float(cur.lon),
            "alt": float(cur.alt or 0.0),
            "lycs": _lycs_of(float(cur.lat), float(cur.lon), float(cur.alt or 0.0)),
            "source": _source(cur),
        }

    if head_elec_set:
        yard.origin_lat = updates["elec"][0]
        yard.origin_lon = updates["elec"][1]
        yard.origin_alt = updates["elec"][2]
        origin_updated = True
        db.flush()

    for side, (lat, lon, alt) in updates.items():
        prev = targets[side]
        source = _source_after_adjust(yard, bay_no, _source(prev) if prev else None)
        _upsert(
            db, yard.yard_id, bay_no, side, lat, lon, alt, source,
            vehicle_id=prev.vehicle_id if prev else None,
            frame_count=prev.frame_count if prev else 0,
            std_m=None,
        )

    stats = {"created": 0, "updated": 0, "removed": 0}
    if is_rtg_yard(yard):
        n = int(yard.bay_count or 0)
        is_end = bay_no in (1, n)
        rows = _rows(db, yard.yard_id)
        # 仅改头/尾才重钉 +Y 并重算 auto；改中间只动本贝（标成 rtg_fix）
        if is_end and _find(rows, 1, "elec") is not None and _find(rows, n, "elec") is not None:
            new_axis = pin_axis(db, yard)
            if new_axis is None:
                raise HTTPException(status_code=400, detail="头贝位和尾贝位距离过近，无法确定 +Y")
            if prev_axis is not None:
                moved = bearing_diff_deg(axis_bearing_deg(prev_axis), axis_bearing_deg(new_axis))
                if moved > AXIS_CHANGE_DEG:
                    lanes, transforms = _axis_dependents(db, yard.yard_id)
                    if (lanes or transforms) and not req.force:
                        raise HTTPException(
                            status_code=409,
                            detail=(
                                f"+Y 方向将改变 {moved:.2f}°，该场地已有 {lanes} 条停留点修正、"
                                f"{transforms} 条校准贝位记录，改变后它们会失效，请确认后强制保存"
                            ),
                        )
                    if lanes or transforms:
                        warnings.append(
                            f"+Y 已改变 {moved:.2f}°，已有的停留点修正和校准记录需要重做"
                        )
            stats = regenerate_auto(db, yard)
        warnings.extend(validate_points(db, yard))
        _refresh_yard_flags(db, yard)
        summary = summarize(db, yard)
        warnings.extend(summary["warnings"])
        summary["end_bay_edit"] = is_end
    else:
        survey_router._reject_close_bays(db, yard.yard_id)
        survey_router._mark_yard_rtk_ready(db, yard.yard_id)
        warnings.extend(survey_router._pair_warnings(db, yard.yard_id))
        summary = {"yard_id": yard.yard_id, "survey_mode": survey_mode_of(yard)}

    if origin_updated:
        warnings.insert(
            0,
            f"堆场原点已更新为电气侧 1 号点 "
            f"({yard.origin_lat:.7f}, {yard.origin_lon:.7f}, {yard.origin_alt:.3f} m)",
        )

    n_bays = int(yard.bay_count or 0)
    is_end_bay = bay_no in (1, n_bays) if n_bays >= 1 else bay_no == 1
    after_snapshot: Dict[str, Any] = {}
    side_deltas: Dict[str, Any] = {}
    # 用修正前原点+轴算前后 LYCS，便于直接读出本次 dx/dy（堆场米）
    for side, (lat, lon, alt) in updates.items():
        after_lycs = _lycs_of(lat, lon, alt)
        after_snapshot[side] = {
            "lat": float(lat),
            "lon": float(lon),
            "alt": float(alt),
            "lycs": after_lycs,
        }
        before = before_snapshot.get(side) or {}
        before_lycs = before.get("lycs") if isinstance(before, dict) else None
        if before_lycs and after_lycs:
            side_deltas[side] = {
                "dx_m": round(float(after_lycs["x"]) - float(before_lycs["x"]), 4),
                "dy_m": round(float(after_lycs["y"]) - float(before_lycs["y"]), 4),
                "dz_m": round(float(after_lycs["z"]) - float(before_lycs["z"]), 4),
            }
        else:
            side_deltas[side] = {
                "dx_m": float(req.dx_m) if mode == "nudge" else None,
                "dy_m": float(req.dy_m) if mode == "nudge" else None,
                "dz_m": None,
            }

    audit = {
        "saved_at": get_cst_now().isoformat(timespec="seconds"),
        "yard_id": yard.yard_id,
        "bay_no": bay_no,
        "is_end_bay": is_end_bay,
        "mode": mode,
        "bay_side": (req.bay_side or "both").strip().lower(),
        "sides": list(sides),
        "force": bool(req.force),
        "request_dx_m": float(req.dx_m) if mode == "nudge" else 0.0,
        "request_dy_m": float(req.dy_m) if mode == "nudge" else 0.0,
        "before": before_snapshot,
        "after": after_snapshot,
        "delta_lycs_m": side_deltas,
        "origin_updated": origin_updated,
        "generated": stats,
    }
    try:
        append_adjust_record(yard.yard_id, audit)
    except Exception as e:
        logger.warning(f"贝位修正审计落盘失败 yard={yard.yard_id} bay={bay_no}: {e}")

    return {
        "mode": mode,
        "bay_no": bay_no,
        "sides": sides,
        "updates": {
            side: {"lat": lat, "lon": lon, "alt": alt}
            for side, (lat, lon, alt) in updates.items()
        },
        "dx_m": float(req.dx_m) if mode == "nudge" else 0.0,
        "dy_m": float(req.dy_m) if mode == "nudge" else 0.0,
        "delta_lycs_m": side_deltas,
        "generated": stats,
        "summary": summary,
        "warnings": warnings,
        "origin_updated": origin_updated,
        "audit": audit,
    }


# ==================== 业务入口 ====================

def _check_geo(label: str, lat: float, lon: float, alt: float) -> None:
    if not all(math.isfinite(float(v)) for v in (lat, lon, alt)):
        raise HTTPException(status_code=400, detail=f"{label}坐标不是有效数字")
    if abs(lat) > 90 or abs(lon) > 180:
        raise HTTPException(status_code=400, detail=f"{label}经纬度超出范围")


def apply_capture(db: Session, req: RtgCaptureRequest) -> dict:
    """写入一次采集。失败抛 HTTPException，调用方负责回滚。"""
    yard = _get_yard(db, req.yard_id)
    n = _require_rtg_yard(yard)
    role = (req.role or "").strip().lower()
    if role == "head":
        bay_no, source = 1, SOURCE_END
    elif role == "tail":
        bay_no, source = n, SOURCE_END
    elif role == "fix":
        if req.bay_no is None or not (2 <= int(req.bay_no) <= n - 1):
            raise HTTPException(
                status_code=400,
                detail=f"重采只用于中间贝位 2~{n - 1}；头尾请用 head / tail",
            )
        bay_no, source = int(req.bay_no), SOURCE_FIX
        if not rtg_ready(db, yard):
            raise HTTPException(status_code=400, detail="请先采完头贝位和尾贝位")
    else:
        raise HTTPException(status_code=400, detail="role 只能是 head / tail / fix")

    points = {"elec": req.elec, "diesel": req.diesel}
    for side in SIDES:
        p = points[side]
        _check_geo(SIDE_LABEL[side], p.lat, p.lon, p.alt)

    # 头贝位（1 号）：堆场原点 = 电气侧两只轮胎中心的中点
    origin_updated = False
    if role == "head":
        yard.origin_lat = float(req.elec.lat)
        yard.origin_lon = float(req.elec.lon)
        yard.origin_alt = float(req.elec.alt)
        origin_updated = True
        db.flush()

    for side in SIDES:
        p = points[side]
        east, north = en_meters(p.lat, p.lon, yard.origin_lat, yard.origin_lon)
        if math.hypot(east, north) > MAX_FAR_FROM_ORIGIN_M:
            raise HTTPException(
                status_code=400,
                detail=f"{SIDE_LABEL[side]}采集点离堆场原点超过 {MAX_FAR_FROM_ORIGIN_M / 1000:.0f} km，数据异常",
            )
    if req.std_m is not None:
        if not math.isfinite(float(req.std_m)) or req.std_m < 0:
            raise HTTPException(status_code=400, detail="标准差无效")
        if req.std_m > MAX_STD_M and not req.force:
            raise HTTPException(
                status_code=409,
                detail=f"采集窗口内位置波动 {req.std_m * 100:.1f} cm，超过 {MAX_STD_M * 100:.0f} cm，建议重采",
            )

    rows = _rows(db, yard.yard_id)
    warnings: List[str] = []

    # 重采：与原来的差距过大要二次确认
    if role == "fix":
        spacing = _spacing_m(rows, yard) or float(yard.bay_spacing or 6.0)
        limit = max(MIN_SHIFT_LIMIT_M, SHIFT_LIMIT_RATIO * spacing)
        far = []
        for side in SIDES:
            cur = _find(rows, bay_no, side)
            if cur is None:
                continue
            ce, cn = en_meters(cur.lat, cur.lon, yard.origin_lat, yard.origin_lon)
            ne, nn = en_meters(points[side].lat, points[side].lon, yard.origin_lat, yard.origin_lon)
            shift = math.hypot(ne - ce, nn - cn)
            if shift > limit:
                far.append(f"{SIDE_LABEL[side]}相差 {shift:.2f} m")
        if far and not req.force:
            raise HTTPException(
                status_code=409,
                detail=f"贝位 {bay_no} 与原位置{'，'.join(far)}，超过 {limit:.2f} m，请确认后强制替换",
            )

    prev_axis = current_axis(db, yard) if rtg_ready(db, yard) else None

    for side in SIDES:
        p = points[side]
        _upsert(
            db, yard.yard_id, bay_no, side, p.lat, p.lon, p.alt, source,
            vehicle_id=req.vehicle_id,
            frame_count=int(req.frame_count) if req.frame_count else None,
            std_m=req.std_m,
        )

    rows = _rows(db, yard.yard_id)
    elec_head, elec_tail = _find(rows, 1, "elec"), _find(rows, n, "elec")
    if elec_head is not None and elec_tail is not None:
        new_axis = pin_axis(db, yard)
        if new_axis is None:
            raise HTTPException(status_code=400, detail="头贝位和尾贝位距离过近，无法确定 +Y")
        if prev_axis is not None and role in ("head", "tail"):
            moved = bearing_diff_deg(axis_bearing_deg(prev_axis), axis_bearing_deg(new_axis))
            if moved > AXIS_CHANGE_DEG:
                lanes, transforms = _axis_dependents(db, yard.yard_id)
                if (lanes or transforms) and not req.force:
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            f"+Y 方向将改变 {moved:.2f}°，该场地已有 {lanes} 条停留点修正、"
                            f"{transforms} 条校准贝位记录，改变后它们会失效，请确认后强制保存"
                        ),
                    )
                if lanes or transforms:
                    warnings.append(
                        f"+Y 已改变 {moved:.2f}°，已有的停留点修正和校准记录需要重做"
                    )

    stats = regenerate_auto(db, yard)
    warnings.extend(validate_points(db, yard))
    _refresh_yard_flags(db, yard)
    summary = summarize(db, yard)
    warnings.extend(summary["warnings"])
    if origin_updated:
        warnings.insert(
            0,
            f"堆场原点已设为电气侧头贝位轮胎中点 "
            f"({yard.origin_lat:.7f}, {yard.origin_lon:.7f}, {yard.origin_alt:.3f} m)",
        )
    return {
        "role": role,
        "bay_no": bay_no,
        "generated": stats,
        "summary": summary,
        "warnings": warnings,
        "origin_updated": origin_updated,
        "origin": {
            "lat": yard.origin_lat,
            "lon": yard.origin_lon,
            "alt": yard.origin_alt,
        } if origin_updated else None,
    }


def apply_restore_auto(db: Session, yard_id: str, bay_no: int) -> dict:
    yard = _get_yard(db, yard_id)
    n = _require_rtg_yard(yard)
    if not (2 <= int(bay_no) <= n - 1):
        raise HTTPException(status_code=400, detail=f"只能恢复中间贝位 2~{n - 1}")
    rows = _rows(db, yard.yard_id)
    targets = [
        p for p in rows
        if int(p.bay_no) == int(bay_no) and _source(p) == SOURCE_FIX
    ]
    if not targets:
        raise HTTPException(status_code=404, detail=f"贝位 {bay_no} 没有 RTG 重采点，不需要恢复")
    for p in targets:
        db.delete(p)
    db.flush()
    stats = regenerate_auto(db, yard)
    warnings = validate_points(db, yard)
    _refresh_yard_flags(db, yard)
    summary = summarize(db, yard)
    warnings.extend(summary["warnings"])
    return {"bay_no": int(bay_no), "generated": stats, "summary": summary, "warnings": warnings}


def apply_regenerate(db: Session, yard_id: str) -> dict:
    yard = _get_yard(db, yard_id)
    _require_rtg_yard(yard)
    rows = _rows(db, yard.yard_id)
    n = int(yard.bay_count or 0)
    if _find(rows, 1, "elec") is not None and _find(rows, n, "elec") is not None:
        if pin_axis(db, yard) is None:
            raise HTTPException(status_code=400, detail="头贝位和尾贝位距离过近，无法确定 +Y")
    stats = regenerate_auto(db, yard)
    warnings = validate_points(db, yard)
    _refresh_yard_flags(db, yard)
    summary = summarize(db, yard)
    warnings.extend(summary["warnings"])
    return {"generated": stats, "summary": summary, "warnings": warnings}


def apply_bay_count_change(db: Session, yard: Yard, old_count: int) -> List[str]:
    """
    RTG 采点的场地改了贝位总数：尾贝位的点改记到新的尾号，自动点重新生成。
    重采过的中间贝位保持原号，超出新范围时拒绝。
    """
    new_count = int(yard.bay_count or 0)
    if new_count < 2:
        raise HTTPException(status_code=400, detail="RTG 采点至少需要 2 个贝位")
    if int(old_count) == new_count:
        return []
    rows = _rows(db, yard.yard_id)
    if not rows:
        return []
    fixed_out = sorted(
        int(p.bay_no) for p in rows
        if _source(p) == SOURCE_FIX and int(p.bay_no) >= new_count
    )
    if fixed_out:
        raise HTTPException(
            status_code=400,
            detail=f"贝位 {fixed_out[:8]} 有 RTG 重采点，超出新的贝位总数 {new_count}，请先恢复自动后再改",
        )
    for p in list(rows):
        if _source(p) == SOURCE_AUTO:
            db.delete(p)
    db.flush()
    for p in _rows(db, yard.yard_id):
        if _source(p) == SOURCE_END and int(p.bay_no) == int(old_count) and int(old_count) != 1:
            p.bay_no = new_count
            p.updated_at = get_cst_now()
    db.flush()
    if yard.y_axis_mode == "two_point" and isinstance(yard.y_axis_points, dict):
        yard.y_axis_points = {
            "source": "bays",
            "from_bay": 1,
            "to_bay": new_count,
        }
        db.flush()
    stats = regenerate_auto(db, yard)
    warnings = validate_points(db, yard)
    _refresh_yard_flags(db, yard)
    warnings.insert(
        0,
        f"贝位总数已从 {old_count} 改为 {new_count}，尾贝位点已改记为 {new_count} 号，"
        f"自动点已重新生成（新增 {stats['created']}）。重采过的贝位请核对",
    )
    return warnings


# ==================== 接口 ====================

@router.post("/capture", response_model=ApiResponse)
def capture(req: RtgCaptureRequest, db: Session = Depends(get_db)):
    """大车一次采集：头 / 尾 / 重采某个中间贝位。同时写电气、动力两侧。"""
    try:
        result = apply_capture(db, req)
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"RTG 采集失败: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
    notify_yard_map_updated(db, req.yard_id)
    role_name = {"head": "头贝位", "tail": "尾贝位", "fix": f"贝位 {result['bay_no']}"}[result["role"]]
    return ApiResponse(code=200, message=f"{role_name}已保存", data=result)


@router.post("/restore_auto", response_model=ApiResponse)
def restore_auto(req: RtgRestoreRequest, db: Session = Depends(get_db)):
    """删除某个中间贝位的 RTG 重采点，恢复按头尾生成的位置。"""
    try:
        result = apply_restore_auto(db, req.yard_id, req.bay_no)
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"恢复自动点失败: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
    notify_yard_map_updated(db, req.yard_id)
    return ApiResponse(code=200, message=f"贝位 {req.bay_no} 已恢复自动位置", data=result)


@router.post("/regenerate", response_model=ApiResponse)
def regenerate(req: RtgRegenerateRequest, db: Session = Depends(get_db)):
    """按当前头尾重新生成全部自动点。"""
    try:
        result = apply_regenerate(db, req.yard_id)
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"重新生成失败: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
    notify_yard_map_updated(db, req.yard_id)
    return ApiResponse(code=200, message="已重新生成", data=result)


@router.get("/status", response_model=ApiResponse)
def rtg_status(yard_id: str, db: Session = Depends(get_db)):
    yard = _get_yard(db, yard_id)
    if not is_rtg_yard(yard):
        return ApiResponse(
            code=200,
            message="手持 RTK 场地",
            data={"yard_id": yard.yard_id, "survey_mode": survey_mode_of(yard)},
        )
    return ApiResponse(code=200, message="获取成功", data=summarize(db, yard))
