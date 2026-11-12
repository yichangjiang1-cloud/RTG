"""堆场多边形、贝位拟合轴、贝位带。纯 Python，服务端校验共用同一份算法。"""
from __future__ import annotations

import math
from typing import Iterable, List, Optional, Sequence, Tuple

Point = Tuple[float, float]
BayEN = Tuple[int, float, float]  # bay_no, east_m, north_m

_DIESEL_SIDE = {"diesel", "dsl", "power", "动力", "动力侧", "动力房"}


def is_electrical_bay(point) -> bool:
    """未标侧别的旧点算电气侧。动力侧不参与 +Y 和当前贝位号。"""
    if isinstance(point, dict):
        raw = point.get("bay_side", point.get("side", ""))
    else:
        raw = getattr(point, "bay_side", "")
    return str(raw or "").strip().lower() not in _DIESEL_SIDE


def electrical_bays(points):
    return [p for p in (points or []) if is_electrical_bay(p)]


def normalize_bay_side(value) -> str:
    return "elec" if is_electrical_bay({"bay_side": value}) else "diesel"

# 与定位解算的 WGS84 ECEF→ENU 同一套，拟合 +Y 不再用固定 111320 米/度
_A = 6378137.0
_F = 1 / 298.257223563
_E2 = 1 - (1 - _F) ** 2


def _ecef(lat_deg: float, lon_deg: float) -> Tuple[float, float, float]:
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)
    n = _A / math.sqrt(1.0 - _E2 * math.sin(lat) ** 2)
    return (
        n * math.cos(lat) * math.cos(lon),
        n * math.cos(lat) * math.sin(lon),
        n * (1.0 - _E2) * math.sin(lat),
    )


def en_meters(lat: float, lon: float, lat0: float, lon0: float) -> Point:
    """相对原点的东、北。与定位解算的 ECEF→ENU 同一套椭球。"""
    x, y, z = _ecef(float(lat), float(lon))
    x0, y0, z0 = _ecef(float(lat0), float(lon0))
    dx, dy, dz = x - x0, y - y0, z - z0
    lat_r = math.radians(float(lat0))
    lon_r = math.radians(float(lon0))
    sl, cl = math.sin(lon_r), math.cos(lon_r)
    sp, cp = math.sin(lat_r), math.cos(lat_r)
    east = -sl * dx + cl * dy
    north = -sp * cl * dx - sp * sl * dy + cp * dz
    return east, north


def point_in_polygon(x: float, y: float, polygon: Sequence[Point]) -> bool:
    """射线法。多边形按顺序首尾相连，不必重复第一个点。"""
    n = len(polygon)
    if n < 3:
        return False
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        intersects = ((yi > y) != (yj > y)) and (
            x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-15) + xi
        )
        if intersects:
            inside = not inside
        j = i
    return inside


def fit_y_axis(points: Sequence[BayEN]) -> Optional[Point]:
    """
    用全部贝位点拟合 +Y。返回 (east, north) 单位向量。
    +Y 指向贝位号增大的方向。少于 2 个不同贝位号时返回 None。
    """
    if len(points) < 2:
        return None
    bays = [float(p[0]) for p in points]
    if max(bays) - min(bays) < 1e-6:
        return None
    n = float(len(points))
    mean_b = sum(bays) / n
    mean_e = sum(p[1] for p in points) / n
    mean_n = sum(p[2] for p in points) / n
    var_b = sum((b - mean_b) ** 2 for b in bays)
    if var_b < 1e-12:
        return None
    de = sum((b - mean_b) * (p[1] - mean_e) for b, p in zip(bays, points)) / var_b
    dn = sum((b - mean_b) * (p[2] - mean_n) for b, p in zip(bays, points)) / var_b
    norm = math.hypot(de, dn)
    if norm < 1e-9:
        return None
    return de / norm, dn / norm


def to_yard_xy(east: float, north: float, y_east: float, y_north: float) -> Point:
    """
    东、北 → 堆场 X、Y。
    +Y 为拟合方向，+X 在朝 +Y 看时的右侧（右手系，Z 朝上）。
    """
    x = east * y_north - north * y_east
    y = east * y_east + north * y_north
    return x, y


def resolve_bay_band(
    y: float,
    bay_ys: Sequence[Tuple[int, float]],
    half_width_m: float,
) -> int:
    """Y 落在某贝位 ±半宽内则返回该贝位号，否则 0。重叠时取距中心更近的。"""
    half = max(float(half_width_m), 0.0)
    best_bay = 0
    best_dist = half + 1.0
    for bay_no, bay_y in bay_ys:
        dist = abs(float(y) - float(bay_y))
        if dist <= half + 1e-9 and dist < best_dist:
            best_dist = dist
            best_bay = int(bay_no)
    return best_bay


def spacing_violation(
    bay_ys: Sequence[Tuple[int, float]],
    half_width_m: float,
) -> Optional[str]:
    """相邻贝位号沿 Y 的间距必须不小于 2 倍半宽。"""
    ordered = sorted(bay_ys, key=lambda item: item[0])
    need = 2.0 * max(float(half_width_m), 0.0)
    for i in range(len(ordered) - 1):
        left, right = ordered[i], ordered[i + 1]
        dist = abs(float(right[1]) - float(left[1]))
        if dist < need - 1e-6:
            return (
                f"贝位 {left[0]} 与 {right[0]} 沿堆场方向间距 {dist:.3f} m，"
                f"小于 {need:.3f} m（2×半宽）"
            )
    return None


def polygon_area(polygon: Sequence[Point]) -> float:
    n = len(polygon)
    if n < 3:
        return 0.0
    acc = 0.0
    j = n - 1
    for i in range(n):
        acc += polygon[j][0] * polygon[i][1] - polygon[i][0] * polygon[j][1]
        j = i
    return abs(acc) * 0.5


def normalize_boundary(raw) -> List[Point]:
    """接受 [{lat, lon}] 或 [[lat, lon], ...]。"""
    points: List[Point] = []
    if not isinstance(raw, Iterable) or isinstance(raw, (str, bytes)):
        return points
    for item in raw:
        if isinstance(item, dict):
            lat = item.get("lat")
            lon = item.get("lon")
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            lat, lon = item[0], item[1]
        else:
            continue
        if lat is None or lon is None:
            continue
        points.append((float(lat), float(lon)))
    return points


def two_point_y_axis(
    east1: float, north1: float, east2: float, north2: float,
) -> Optional[Point]:
    """+Y 从第一点指向第二点。两点过近时返回 None。"""
    de = float(east2) - float(east1)
    dn = float(north2) - float(north1)
    norm = math.hypot(de, dn)
    if norm < 0.05:
        return None
    return de / norm, dn / norm


def resolve_y_axis(
    points: Sequence[BayEN],
    mode: Optional[str],
    spec: Optional[dict],
    origin_lat: float,
    origin_lon: float,
) -> Optional[Point]:
    """
    fit：全部贝位点拟合，+Y 指向贝位号增大的方向。
    two_point：spec.source=bays 用两个贝位号，source=manual 用两个经纬度。
    +Y 都是从第一点指向第二点。两点方式缺数据时不回退到拟合。
    """
    if (mode or "fit").strip().lower() != "two_point":
        return fit_y_axis(points)
    spec = spec if isinstance(spec, dict) else {}
    source = str(spec.get("source") or "bays").strip().lower()
    if source == "manual":
        start = spec.get("from") if isinstance(spec.get("from"), dict) else {}
        end = spec.get("to") if isinstance(spec.get("to"), dict) else {}
        try:
            e1, n1 = en_meters(float(start["lat"]), float(start["lon"]), origin_lat, origin_lon)
            e2, n2 = en_meters(float(end["lat"]), float(end["lon"]), origin_lat, origin_lon)
        except (KeyError, TypeError, ValueError):
            return None
        return two_point_y_axis(e1, n1, e2, n2)
    by_bay = {int(bay): (east, north) for bay, east, north in points}
    try:
        start_bay = int(spec["from_bay"])
        end_bay = int(spec["to_bay"])
    except (KeyError, TypeError, ValueError):
        return None
    if start_bay not in by_bay or end_bay not in by_bay or start_bay == end_bay:
        return None
    e1, n1 = by_bay[start_bay]
    e2, n2 = by_bay[end_bay]
    return two_point_y_axis(e1, n1, e2, n2)
