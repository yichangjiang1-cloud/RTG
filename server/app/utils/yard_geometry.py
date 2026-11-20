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


def from_yard_xy(x: float, y: float, y_east: float, y_north: float) -> Point:
    """堆场 X、Y → 东、北（to_yard_xy 的逆）。"""
    east = float(x) * float(y_north) + float(y) * float(y_east)
    north = -float(x) * float(y_east) + float(y) * float(y_north)
    return east, north


def yard_delta_to_en(
    dx: float,
    dy: float,
    axis: Optional[Point],
) -> Point:
    """
    堆场 ΔX/ΔY → 东/北增量。
    axis 为 +Y 单位向量 (east, north)；None 时与 YardTransform 未拟合一致：X=北、Y=东。
    """
    if axis is None:
        return float(dy), float(dx)
    return from_yard_xy(float(dx), float(dy), float(axis[0]), float(axis[1]))


def _ecef_to_latlon(x: float, y: float, z: float) -> Point:
    """ECEF → (lat_deg, lon_deg)，Bowring 迭代。"""
    lon = math.atan2(y, x)
    p = math.sqrt(x * x + y * y)
    lat = math.atan2(z, p * (1.0 - _E2))
    for _ in range(10):
        n = _A / math.sqrt(1.0 - _E2 * math.sin(lat) ** 2)
        lat_new = math.atan2(z + _E2 * n * math.sin(lat), p)
        if abs(lat_new - lat) < 1e-12:
            lat = lat_new
            break
        lat = lat_new
    return math.degrees(lat), math.degrees(lon)


def en_to_wgs84(east: float, north: float, lat0: float, lon0: float) -> Point:
    """相对原点的东、北 → WGS84（高程按原点椭球，与 en_meters 互逆）。"""
    lat_r = math.radians(float(lat0))
    lon_r = math.radians(float(lon0))
    x0, y0, z0 = _ecef(float(lat0), float(lon0))
    sl, cl = math.sin(lon_r), math.cos(lon_r)
    sp, cp = math.sin(lat_r), math.cos(lat_r)
    e, n = float(east), float(north)
    dx = -sl * e - sp * cl * n
    dy = cl * e - sp * sl * n
    dz = cp * n
    return _ecef_to_latlon(x0 + dx, y0 + dy, z0 + dz)


def shift_wgs84_by_yard(
    lat: float,
    lon: float,
    dx: float,
    dy: float,
    origin_lat: float,
    origin_lon: float,
    axis: Optional[Point],
) -> Point:
    """把一点沿堆场 X/Y 平移，返回新的 (lat, lon)。"""
    e0, n0 = en_meters(float(lat), float(lon), float(origin_lat), float(origin_lon))
    de, dn = yard_delta_to_en(dx, dy, axis)
    return en_to_wgs84(e0 + de, n0 + dn, float(origin_lat), float(origin_lon))


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


def lerp_bay_point(
    head: Tuple[float, float, float],
    tail: Tuple[float, float, float],
    bay_no: int,
    bay_count: int,
) -> Tuple[float, float, float]:
    """
    头贝位 1、尾贝位 bay_count，按贝位号等分，返回第 bay_no 号的 (lat, lon, alt)。
    N 个贝位只有 N-1 个间隔。范围几百米时，经纬度线性插值与真直线只差毫米级。
    """
    n = int(bay_count)
    if n < 2:
        raise ValueError("贝位数至少为 2")
    t = (int(bay_no) - 1) / float(n - 1)
    return (
        head[0] + t * (tail[0] - head[0]),
        head[1] + t * (tail[1] - head[1]),
        head[2] + t * (tail[2] - head[2]),
    )


def monotonic_violation(bay_ys: Sequence[Tuple[int, float]]) -> Optional[str]:
    """沿 +Y 的位置必须随贝位号严格增大。"""
    ordered = sorted(bay_ys, key=lambda item: item[0])
    for i in range(len(ordered) - 1):
        left, right = ordered[i], ordered[i + 1]
        if float(right[1]) <= float(left[1]) + 1e-6:
            return (
                f"贝位 {left[0]} 与 {right[0]} 沿堆场方向顺序反了或重合"
                f"（{left[1]:.3f} m → {right[1]:.3f} m）"
            )
    return None


def axis_bearing_deg(axis: Point) -> float:
    """+Y 的方位角，正北 0°，顺时针为正。axis 是 (east, north) 单位向量。"""
    return math.degrees(math.atan2(float(axis[0]), float(axis[1]))) % 360.0


def bearing_diff_deg(a: float, b: float) -> float:
    """两个方位角之差，取 0~180。"""
    diff = abs(float(a) - float(b)) % 360.0
    return 360.0 - diff if diff > 180.0 else diff


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


def parallel_reference_x(xs: Sequence[float]) -> Optional[float]:
    """与 +Y 平行的贝位线在堆场坐标中的 X。至少两个有限值，取平均。"""
    vals: List[float] = []
    for raw in xs:
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value):
            vals.append(value)
    if len(vals) < 2:
        return None
    return sum(vals) / len(vals)


def polygon_centroid(polygon: Sequence[Point]) -> Optional[Point]:
    """多边形重心。面积过小时退回顶点平均。"""
    n = len(polygon)
    if n < 3:
        return None
    acc = cx = cy = 0.0
    for i in range(n):
        x1, y1 = polygon[i]
        x2, y2 = polygon[(i + 1) % n]
        cross = x1 * y2 - x2 * y1
        acc += cross
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    if abs(acc) < 1e-12:
        return (
            sum(p[0] for p in polygon) / n,
            sum(p[1] for p in polygon) / n,
        )
    acc *= 0.5
    return cx / (6.0 * acc), cy / (6.0 * acc)


def line_right_normal(direction: Point) -> Point:
    """沿贝位号增大方向看去的右侧。+Y 的右侧是 +X。"""
    dx, dy = float(direction[0]), float(direction[1])
    return dy, -dx


def line_offset_m(px: float, py: float, origin: Point, direction: Point) -> float:
    """点相对贝位线、沿右侧法向的有符号距离。线的右侧为正。"""
    nx, ny = line_right_normal(direction)
    return (float(px) - float(origin[0])) * nx + (float(py) - float(origin[1])) * ny


def line_deviation_m(
    px: float,
    py: float,
    origin: Point,
    direction: Point,
    centroid: Point,
) -> float:
    """点到贝位线的横向偏离，米。朝多边形中心一侧为负。"""
    offset = line_offset_m(px, py, origin, direction)
    inside = line_offset_m(centroid[0], centroid[1], origin, direction)
    if inside > 1e-6:
        return -offset
    return offset


def lane_deviation_m(tire_x: float, centroid_x: float, x_ref: float = 0.0) -> float:
    """轮胎相对一条与 +Y 平行的贝位线的横向偏离，米。朝场内为负。"""
    offset = float(tire_x) - float(x_ref)
    if float(centroid_x) - float(x_ref) > 1e-6:
        return -offset
    return offset


def bracket_bay_line(
    along_y: float,
    points: Sequence[Point],
) -> Optional[Tuple[Point, Point]]:
    """沿堆场 Y 找出夹住 along_y 的两个贝位点，返回过这两点的直线。"""
    ordered = sorted(
        ((float(x), float(y)) for x, y in points),
        key=lambda item: (item[1], item[0]),
    )
    if len(ordered) < 2:
        return None
    index = len(ordered) - 2
    for i in range(len(ordered) - 1):
        if float(along_y) <= ordered[i + 1][1]:
            index = i
            break
    x0, y0 = ordered[index]
    x1, y1 = ordered[index + 1]
    dx = x1 - x0
    dy = y1 - y0
    norm = math.hypot(dx, dy)
    if norm < 0.05:
        return None
    return (x0, y0), (dx / norm, dy / norm)


def lateral_to_survey_m(
    tire_x: float,
    tire_y: float,
    point_x: float,
    point_y: float,
    origin: Point,
    direction: Point,
    centroid: Point,
) -> Tuple[float, float]:
    """轮胎中心相对实测点的横向距离和沿贝位距离。横向符号与贝位偏差相同。"""
    lateral = line_deviation_m(
        tire_x, tire_y, origin, direction, centroid,
    ) - line_deviation_m(
        point_x, point_y, origin, direction, centroid,
    )
    dx, dy = float(direction[0]), float(direction[1])
    along = (float(tire_x) - float(point_x)) * dx + (float(tire_y) - float(point_y)) * dy
    return lateral, along


def tire_survey_offset_m(
    tire_x: float,
    tire_y: float,
    point_x: float,
    point_y: float,
    side_points: Sequence[Point],
    fallback_points: Sequence[Point],
    centroid: Point,
    fallback_x: float,
) -> Tuple[float, float]:
    """用这只轮胎自己的相邻贝位线，算它到实测点的横向距离。线不够时改用与 +Y 平行的参考线。"""
    line = bracket_bay_line(tire_y, side_points)
    if line is None:
        line = bracket_bay_line(tire_y, fallback_points)
    if line is None:
        lateral = lane_deviation_m(tire_x, centroid[0], fallback_x) - lane_deviation_m(
            point_x, centroid[0], fallback_x,
        )
        return lateral, float(tire_y) - float(point_y)
    origin, direction = line
    return lateral_to_survey_m(
        tire_x, tire_y, point_x, point_y, origin, direction, centroid,
    )
