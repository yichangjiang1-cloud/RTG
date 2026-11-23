"""杆臂核对记录：落盘到 logs/lever_check/<vehicle_id>.json；兼算轮胎→贝位线偏差。"""

from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import SurveyPoint, Yard
from app.schemas import ApiResponse
from app.utils.yard_geometry import (
    bracket_bay_line,
    en_meters,
    lane_deviation_m,
    line_deviation_m,
    normalize_bay_side,
    normalize_boundary,
    parallel_reference_x,
    polygon_centroid,
    resolve_y_axis,
    to_yard_xy,
)

router = APIRouter(prefix="/api/lever-check", tags=["lever-check"])

_SAFE_VID = re.compile(r"[^A-Za-z0-9._-]+")
_MAX_RECORDS = 500


def _dir() -> Path:
    log_dir = Path(os.path.dirname(settings.LOG_FILE) or "logs")
    out = log_dir / "lever_check"
    out.mkdir(parents=True, exist_ok=True)
    return out


def _path(vehicle_id: str) -> Path:
    safe = _SAFE_VID.sub("_", (vehicle_id or "").strip()) or "unknown"
    return _dir() / f"{safe}.json"


def _read(vehicle_id: str) -> list[dict[str, Any]]:
    path = _path(vehicle_id)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    return []


def _write(vehicle_id: str, records: list[dict[str, Any]]) -> Path:
    path = _path(vehicle_id)
    path.write_text(
        json.dumps(records, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def _mapping_side_en(db: Session, yard: Yard, side: str) -> list[tuple[int, float, float]]:
    """(bay_no, east, north)。"""
    rows = db.query(SurveyPoint).filter(
        SurveyPoint.yard_id == yard.yard_id,
        SurveyPoint.point_type == "mapping",
        SurveyPoint.bay_no.isnot(None),
    ).all()
    want = normalize_bay_side(side)
    pts = []
    for p in rows:
        if normalize_bay_side(getattr(p, "bay_side", None)) != want:
            continue
        east, north = en_meters(p.lat, p.lon, yard.origin_lat, yard.origin_lon)
        pts.append((int(p.bay_no), east, north))
    return pts


def _yard_axis(yard: Yard, elec_en, diesel_en):
    axis = resolve_y_axis(
        elec_en,
        yard.y_axis_mode,
        yard.y_axis_points,
        yard.origin_lat,
        yard.origin_lon,
    )
    if axis is None and diesel_en:
        axis = resolve_y_axis(
            diesel_en, "fit", None, yard.origin_lat, yard.origin_lon,
        )
    return axis


def _en_to_bay_xy(
    group: list[tuple[int, float, float]],
    axis: tuple[float, float],
) -> list[tuple[int, float, float]]:
    """(bay_no, east, north) → (bay_no, lycs_x, lycs_y)，按 Y 排序。"""
    out = []
    for bay_no, east, north in group:
        x, y = to_yard_xy(east, north, axis[0], axis[1])
        out.append((int(bay_no), float(x), float(y)))
    out.sort(key=lambda item: (item[2], item[1], item[0]))
    return out


def bracket_bay_nos(
    along_y: float,
    bay_xy: list[tuple[int, float, float]],
) -> Optional[dict[str, Any]]:
    """
    按堆场 Y 找出夹住轮胎的两个场地贝位。
    返回 from_bay / to_bay（沿 +Y 从小到大）、各自 Y、插值比例 t∈[0,1]，
    以及更接近的贝位号 / 距离。
    """
    if len(bay_xy) < 2:
        return None
    ordered = list(bay_xy)
    index = len(ordered) - 2
    for i in range(len(ordered) - 1):
        if float(along_y) <= ordered[i + 1][2]:
            index = i
            break
    b0, _x0, y0 = ordered[index]
    b1, _x1, y1 = ordered[index + 1]
    span = float(y1) - float(y0)
    if abs(span) < 1e-6:
        t = 0.0
    else:
        t = (float(along_y) - float(y0)) / span
    t = max(0.0, min(1.0, t))
    d0 = abs(float(along_y) - float(y0))
    d1 = abs(float(along_y) - float(y1))
    if d0 <= d1:
        closer_bay, closer_dist_m, farther_bay = int(b0), d0, int(b1)
    else:
        closer_bay, closer_dist_m, farther_bay = int(b1), d1, int(b0)
    return {
        "from_bay": int(b0),
        "to_bay": int(b1),
        "from_y_m": round(float(y0), 4),
        "to_y_m": round(float(y1), 4),
        "tire_y_m": round(float(along_y), 4),
        "t": round(float(t), 4),
        "closer_bay": closer_bay,
        "closer_dist_m": round(float(closer_dist_m), 4),
        "farther_bay": farther_bay,
        "label": f"贝位 {int(b0)}–{int(b1)} 之间",
        "hint": f"贝位 {int(b0)}–{int(b1)} 之间，更接近贝位 {closer_bay}",
    }


def compute_bay_offset_m(
    db: Session,
    yard_id: str,
    lat: float,
    lon: float,
    side: str = "elec",
    alt: Optional[float] = None,
) -> Optional[dict[str, Any]]:
    """轮胎中心到对应侧贝位线的横向偏差（米）。朝场内为负，与 leg_offsets 同号。"""
    yard = db.query(Yard).filter(Yard.yard_id == yard_id).first()
    if yard is None or yard.origin_lat is None or yard.origin_lon is None:
        return None

    elec_en = _mapping_side_en(db, yard, "elec")
    diesel_en = _mapping_side_en(db, yard, "diesel")
    axis = _yard_axis(yard, elec_en, diesel_en)
    if axis is None:
        return None

    elec_bay_xy = _en_to_bay_xy(elec_en, axis)
    diesel_bay_xy = _en_to_bay_xy(diesel_en, axis)
    elec_xy = [(x, y) for _b, x, y in elec_bay_xy]
    diesel_xy = [(x, y) for _b, x, y in diesel_bay_xy]
    on_diesel = normalize_bay_side(side) == "diesel"

    ring = normalize_boundary(yard.boundary)
    if len(ring) < 3:
        return None
    verts = [
        to_yard_xy(*en_meters(lat0, lon0, yard.origin_lat, yard.origin_lon), axis[0], axis[1])
        for lat0, lon0 in ring
    ]
    center = polygon_centroid(verts)
    if center is None:
        return None

    te, tn = en_meters(lat, lon, yard.origin_lat, yard.origin_lon)
    px, py = to_yard_xy(te, tn, axis[0], axis[1])
    origin_alt = float(yard.origin_alt or 0.0)
    pz = float(alt) - origin_alt if alt is not None and math.isfinite(float(alt)) else None

    own = diesel_xy if on_diesel else elec_xy
    own_bay = diesel_bay_xy if on_diesel else elec_bay_xy
    line = bracket_bay_line(py, own)
    bay_between = bracket_bay_nos(py, own_bay)
    if line is None and on_diesel:
        line = bracket_bay_line(py, elec_xy)
        if bay_between is None:
            bay_between = bracket_bay_nos(py, elec_bay_xy)

    if line is None:
        x_ref = parallel_reference_x([p[0] for p in elec_xy])
        if x_ref is None:
            x_ref = 0.0
        offset_m = lane_deviation_m(px, center[0], x_ref)
        mode = "parallel_y"
    else:
        origin, direction = line
        offset_m = line_deviation_m(px, py, origin, direction, center)
        mode = "bracket"

    return {
        "yard_id": yard_id,
        "side": "diesel" if on_diesel else "elec",
        "offset_m": float(offset_m),
        "offset_cm": float(offset_m) * 100.0,
        "tire_lycs_m": [
            round(float(px), 4),
            round(float(py), 4),
            None if pz is None else round(float(pz), 4),
        ],
        "tire_lycs_x": float(px),
        "tire_lycs_y": float(py),
        "tire_lycs_z": None if pz is None else float(pz),
        "bay_between": bay_between,
        "mode": mode,
    }


def _enrich_bay_offsets(db: Session, record: dict[str, Any]) -> dict[str, Any]:
    yard_id = str(record.get("yard_id") or "").strip()
    if not yard_id:
        return record
    side = "elec"
    end = str(record.get("end") or "")
    if "diesel" in end:
        side = "diesel"

    def one(key_lla: str, offset_key: str, lycs_key: str, between_key: str):
        pt = record.get(key_lla)
        if not isinstance(pt, dict):
            record[offset_key] = None
            record[lycs_key] = None
            record[between_key] = None
            return
        try:
            lat = float(pt["lat"])
            lon = float(pt["lon"])
            alt = float(pt["alt"]) if pt.get("alt") is not None else None
        except (KeyError, TypeError, ValueError):
            record[offset_key] = None
            record[lycs_key] = None
            record[between_key] = None
            return
        info = compute_bay_offset_m(db, yard_id, lat, lon, side, alt)
        if info is None:
            record[offset_key] = None
            record[lycs_key] = None
            record[between_key] = None
            return
        record[offset_key] = round(float(info["offset_cm"]), 2)
        record[lycs_key] = info.get("tire_lycs_m")
        record[between_key] = info.get("bay_between")

    one(
        "tire_predicted",
        "bay_offset_predicted_cm",
        "tire_predicted_lycs_m",
        "bay_between_predicted",
    )
    one(
        "tire_measured",
        "bay_offset_measured_cm",
        "tire_measured_lycs_m",
        "bay_between_measured",
    )
    return record


class LeverCheckSaveRequest(BaseModel):
    vehicle_id: str
    record: dict[str, Any]


class LeverCheckReplaceRequest(BaseModel):
    vehicle_id: str
    records: list[dict[str, Any]] = Field(default_factory=list)


class BayOffsetRequest(BaseModel):
    yard_id: str
    lat: float
    lon: float
    alt: float = 0.0
    side: str = "elec"


class BayOffsetBatchRequest(BaseModel):
    yard_id: str
    side: str = "elec"
    points: dict[str, dict[str, float]] = Field(default_factory=dict)


class BayBetweenLycsRequest(BaseModel):
    """用堆场 LYCS 坐标查轮胎夹在哪两个贝位之间（Dashboard 用）。"""
    yard_id: str
    side: str = "elec"
    points: dict[str, dict[str, float]] = Field(default_factory=dict)


class Wgs84ToLycsRequest(BaseModel):
    yard_id: str
    lat: float
    lon: float
    alt: float = 0.0


def compute_bay_between_lycs(
    db: Session,
    yard_id: str,
    lycs_y: float,
    side: str = "elec",
) -> Optional[dict[str, Any]]:
    """按堆场 Y 返回电气/动力侧贝位夹持信息。"""
    yard = db.query(Yard).filter(Yard.yard_id == yard_id).first()
    if yard is None or yard.origin_lat is None or yard.origin_lon is None:
        return None
    elec_en = _mapping_side_en(db, yard, "elec")
    diesel_en = _mapping_side_en(db, yard, "diesel")
    axis = _yard_axis(yard, elec_en, diesel_en)
    if axis is None:
        return None
    elec_bay_xy = _en_to_bay_xy(elec_en, axis)
    diesel_bay_xy = _en_to_bay_xy(diesel_en, axis)
    on_diesel = normalize_bay_side(side) == "diesel"
    own_bay = diesel_bay_xy if on_diesel else elec_bay_xy
    bay_between = bracket_bay_nos(float(lycs_y), own_bay)
    if bay_between is None and on_diesel:
        bay_between = bracket_bay_nos(float(lycs_y), elec_bay_xy)
    if bay_between is None:
        return None
    return {
        "yard_id": yard_id,
        "side": "diesel" if on_diesel else "elec",
        "bay_between": bay_between,
    }


def wgs84_to_lycs_m(
    db: Session,
    yard_id: str,
    lat: float,
    lon: float,
    alt: float = 0.0,
) -> Optional[dict[str, Any]]:
    """WGS84 → 堆场 LYCS。只依赖原点 + Y 轴，不算贝位偏差。"""
    yard = db.query(Yard).filter(Yard.yard_id == yard_id).first()
    if yard is None or yard.origin_lat is None or yard.origin_lon is None:
        return None
    elec_en = _mapping_side_en(db, yard, "elec")
    diesel_en = _mapping_side_en(db, yard, "diesel")
    axis = _yard_axis(yard, elec_en, diesel_en)
    if axis is None:
        return None
    te, tn = en_meters(lat, lon, yard.origin_lat, yard.origin_lon)
    px, py = to_yard_xy(te, tn, axis[0], axis[1])
    pz = float(alt) - float(yard.origin_alt or 0.0)
    return {
        "yard_id": yard_id,
        "wgs84": {"lat": float(lat), "lon": float(lon), "alt": float(alt)},
        "lycs_m": [round(float(px), 4), round(float(py), 4), round(float(pz), 4)],
        "x": float(px),
        "y": float(py),
        "z": float(pz),
    }


@router.post("/wgs84-to-lycs", response_model=ApiResponse)
def convert_wgs84_to_lycs(req: Wgs84ToLycsRequest, db: Session = Depends(get_db)):
    info = wgs84_to_lycs_m(db, req.yard_id, req.lat, req.lon, req.alt)
    if info is None:
        return ApiResponse(code=400, message="无法转换（缺堆场原点或 Y 轴）")
    return ApiResponse(data=info)


@router.post("/bay-offset", response_model=ApiResponse)
def bay_offset(req: BayOffsetRequest, db: Session = Depends(get_db)):
    info = compute_bay_offset_m(db, req.yard_id, req.lat, req.lon, req.side, req.alt)
    if info is None:
        return ApiResponse(code=400, message="无法计算贝位偏差（缺堆场/边界/贝位点）")
    return ApiResponse(data=info)


@router.post("/bay-offset/batch", response_model=ApiResponse)
def bay_offset_batch(req: BayOffsetBatchRequest, db: Session = Depends(get_db)):
    out: dict[str, Any] = {}
    for name, pt in (req.points or {}).items():
        try:
            lat = float(pt["lat"])
            lon = float(pt["lon"])
            alt = float(pt["alt"]) if pt.get("alt") is not None else None
        except (KeyError, TypeError, ValueError):
            out[name] = None
            continue
        out[name] = compute_bay_offset_m(db, req.yard_id, lat, lon, req.side, alt)
    return ApiResponse(data={"yard_id": req.yard_id, "side": req.side, "offsets": out})


@router.post("/bay-between/lycs", response_model=ApiResponse)
def bay_between_lycs(req: BayBetweenLycsRequest, db: Session = Depends(get_db)):
    """Dashboard：轮胎 LYCS Y → 夹在哪两个贝位、更接近哪个。"""
    out: dict[str, Any] = {}
    for name, pt in (req.points or {}).items():
        try:
            y = float(pt["y"])
        except (KeyError, TypeError, ValueError):
            out[name] = None
            continue
        if not math.isfinite(y):
            out[name] = None
            continue
        info = compute_bay_between_lycs(db, req.yard_id, y, req.side)
        out[name] = None if info is None else info.get("bay_between")
    return ApiResponse(data={"yard_id": req.yard_id, "side": req.side, "between": out})


@router.get("/{vehicle_id}", response_model=ApiResponse)
def list_records(vehicle_id: str):
    records = _read(vehicle_id)
    return ApiResponse(
        data={
            "vehicle_id": vehicle_id,
            "path": str(_path(vehicle_id)).replace("\\", "/"),
            "records": records,
        }
    )


@router.post("/save", response_model=ApiResponse)
def save_record(req: LeverCheckSaveRequest, db: Session = Depends(get_db)):
    if not req.vehicle_id.strip():
        return ApiResponse(code=400, message="缺少 vehicle_id")
    if not isinstance(req.record, dict) or not req.record:
        return ApiResponse(code=400, message="缺少 record")
    rec = _enrich_bay_offsets(db, dict(req.record))
    records = [rec, *_read(req.vehicle_id)][:_MAX_RECORDS]
    path = _write(req.vehicle_id, records)
    return ApiResponse(
        data={
            "vehicle_id": req.vehicle_id,
            "path": str(path).replace("\\", "/"),
            "count": len(records),
            "record": rec,
        }
    )


@router.put("/{vehicle_id}", response_model=ApiResponse)
def replace_records(vehicle_id: str, req: LeverCheckReplaceRequest):
    records = [x for x in req.records if isinstance(x, dict)][:_MAX_RECORDS]
    path = _write(vehicle_id, records)
    return ApiResponse(
        data={
            "vehicle_id": vehicle_id,
            "path": str(path).replace("\\", "/"),
            "count": len(records),
        }
    )


@router.delete("/{vehicle_id}/{record_id}", response_model=ApiResponse)
def delete_record(vehicle_id: str, record_id: str):
    records = _read(vehicle_id)
    next_recs = [r for r in records if str(r.get("id")) != record_id]
    if len(next_recs) == len(records):
        return ApiResponse(code=404, message="记录不存在")
    if not next_recs:
        path = _path(vehicle_id)
        if path.is_file():
            path.unlink()
        return ApiResponse(
            data={"vehicle_id": vehicle_id, "path": str(path).replace("\\", "/"), "count": 0}
        )
    path = _write(vehicle_id, next_recs)
    return ApiResponse(
        data={
            "vehicle_id": vehicle_id,
            "path": str(path).replace("\\", "/"),
            "count": len(next_recs),
            "deleted_id": record_id,
        }
    )


@router.delete("/{vehicle_id}", response_model=ApiResponse)
def clear_records(vehicle_id: str):
    path = _path(vehicle_id)
    if path.is_file():
        path.unlink()
    return ApiResponse(data={"vehicle_id": vehicle_id, "path": str(path).replace("\\", "/"), "count": 0})
