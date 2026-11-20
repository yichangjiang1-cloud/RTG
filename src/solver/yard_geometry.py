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


def diesel_bays(points):
    return [p for p in (points or []) if not is_electrical_bay(p)]


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

# 与 coordinate_transform 同一套 WGS84，避免拟合 +Y 和定位解算各用一套米制
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


def yard_delta_to_en(dx, dy, axis):
    """堆场 ΔX/ΔY → 东/北。axis=None 时 X=北、Y=东。"""
    if axis is None:
        return float(dy), float(dx)
    return from_yard_xy(float(dx), float(dy), float(axis[0]), float(axis[1]))


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


def fit_bay_line(
    points: Sequence[Tuple[float, float, float]],
) -> Optional[Tuple[Point, Point]]:
    """
    用一侧的贝位点拟合该侧贝位线。
    输入 (贝位号, 堆场 X, 堆场 Y)。返回直线上一点，以及指向贝位号增大方向的单位向量。
    贝位号没有拉开，或点重合时返回 None。
    """
    samples = [(float(bay), float(x), float(y)) for bay, x, y in points]
    direction = fit_y_axis(samples)
    if direction is None:
        return None
    n = float(len(samples))
    origin = (
        sum(p[1] for p in samples) / n,
        sum(p[2] for p in samples) / n,
    )
    return origin, direction


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
    """
    点到拟合贝位线的横向偏离，米。
    朝多边形中心一侧为负，另一侧为正。线与 +Y 平行时与 lane_deviation_m 一致。
    """
    offset = line_offset_m(px, py, origin, direction)
    inside = line_offset_m(centroid[0], centroid[1], origin, direction)
    if inside > 1e-6:
        return -offset
    return offset


def deviation_adjust_along_line(
    delta_dev_m: float,
    origin: Point,
    direction: Point,
    centroid: Point,
) -> Point:
    """把与字段 12–15 同号的修正量换成沿贝位线法向的位移。朝场内为负。"""
    nx, ny = line_right_normal(direction)
    inside = line_offset_m(centroid[0], centroid[1], origin, direction)
    step = -float(delta_dev_m) if inside > 1e-6 else float(delta_dev_m)
    return step * nx, step * ny


def lane_deviation_m(tire_x: float, centroid_x: float, x_ref: float = 0.0) -> float:
    """
    轮胎相对一条与 +Y 平行的贝位线的横向偏离，米。
    x_ref 是该线的堆场 X。过原点的 +Y 才是 0。
    朝多边形中心一侧为负，另一侧为正。
    """
    offset = float(tire_x) - float(x_ref)
    if float(centroid_x) - float(x_ref) > 1e-6:
        return -offset
    return offset


def lane_deviations_mm(
    tires: Sequence[Tuple[float, bool]],
    centroid_x: float,
    elec_x: Optional[float] = None,
    diesel_x: Optional[float] = None,
) -> List[int]:
    """
    四个轮胎的横向偏离，毫米。顺序：电气主、电气从、动力主、动力从。
    elec_x、diesel_x 是各自贝位线的堆场 X。缺电气线时用过原点的 +Y；
    缺动力线时改用电气侧贝位线。
    """
    x_elec = 0.0 if elec_x is None else float(elec_x)
    x_diesel = x_elec if diesel_x is None else float(diesel_x)
    refs = (x_elec, x_elec, x_diesel, x_diesel)
    out: List[int] = []
    for (tire_x, ok), x_ref in zip(tires, refs):
        if not ok:
            out.append(0)
            continue
        out.append(int(round(lane_deviation_m(tire_x, centroid_x, x_ref) * 1000.0)))
    return out


def deviation_adjust_to_dx(delta_dev_m: float, centroid_x: float, x_ref: float) -> float:
    """把与字段 12–15 同号的修正量换成堆场 X 位移。朝场内为负。"""
    if float(centroid_x) - float(x_ref) > 1e-6:
        return -float(delta_dev_m)
    return float(delta_dev_m)


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


def bracket_bay_line(
    along_y: float,
    points: Sequence[Point],
) -> Optional[Tuple[Point, Point]]:
    """
    沿堆场 Y 找出夹住 along_y 的两个贝位点，返回过这两点的直线。
    落在最前一个贝位之前用第一段，落在最后一个之后用最后一段。
    两点过近时返回 None。
    """
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


def bay_line_ends(points: Sequence[Point]) -> Optional[Tuple[float, float]]:
    """贝位线沿堆场 Y 的起点和终点。不足两个点，或挤在一起时返回 None。"""
    ys = [float(point[1]) for point in points]
    if len(ys) < 2:
        return None
    y0, y1 = min(ys), max(ys)
    if y1 - y0 < 0.05:
        return None
    return y0, y1


def _usable_lane_points(
    points: Sequence[Tuple[float, Sequence[float]]],
) -> List[Tuple[float, Tuple[float, float, float, float]]]:
    usable: List[Tuple[float, Tuple[float, float, float, float]]] = []
    for item in points:
        if item is None or len(item) < 2 or item[1] is None or len(item[1]) < 4:
            continue
        usable.append((float(item[0]), tuple(float(v) for v in item[1][:4])))
    usable.sort(key=lambda row: row[0])
    return usable


def anchored_lane_points(
    points: Sequence[Tuple[float, Sequence[float]]],
    y_start: float,
    y_end: float,
    end_match_m: float = 0.5,
) -> List[Tuple[float, Tuple[float, float, float, float]]]:
    """
    纠偏点夹在贝位线两端之间。两端没有单独记下修正时，修正量是 0。
    记下的点落在端点附近时，用记下的值，不再另加一个 0。
    """
    zeros = (0.0, 0.0, 0.0, 0.0)
    usable = _usable_lane_points(points)
    start = float(y_start)
    end = float(y_end)

    def _covered(y: float) -> bool:
        return any(abs(py - y) <= end_match_m for py, _shifts in usable)

    if not _covered(start):
        usable.append((start, zeros))
    if not _covered(end):
        usable.append((end, zeros))
    usable.sort(key=lambda row: row[0])
    return usable


_BAY_MATCH_M = 0.5


def _resolve_bay_ys(bay_ys, y_end: Optional[float] = None) -> List[float]:
    """贝位沿堆场 Y 的位置。传入两个数时当作只有首尾两个贝位。过近的点并成一个。"""
    if bay_ys is None:
        return []
    if isinstance(bay_ys, (int, float)) and not isinstance(bay_ys, bool):
        if y_end is None:
            return []
        raw = [float(bay_ys), float(y_end)]
    else:
        raw = [float(v) for v in bay_ys]
        if isinstance(y_end, (int, float)) and not isinstance(y_end, bool):
            raw.append(float(y_end))
    ordered = sorted(v for v in raw if math.isfinite(v))
    out: List[float] = []
    for value in ordered:
        if not out or value - out[-1] >= 0.05:
            out.append(value)
    return out


def applied_lane_shifts(
    y: float,
    points: Sequence[Tuple[float, Sequence[float]]],
    bay_ys=None,
    y_end: Optional[float] = None,
) -> Tuple[float, float, float, float]:
    """
    纠偏只落在记下的点前后两个贝位之间。
    这两个贝位没有单独记下时修正是 0，再往外的贝位也是 0。
    点就记在某个贝上时，该贝用记下的值，前后两段都连到它。
    相邻两段各有一个纠偏点时，中间那个贝位仍是 0。
    没有至少两个贝位时不修正。
    """
    zeros = (0.0, 0.0, 0.0, 0.0)
    bays = _resolve_bay_ys(bay_ys, y_end)
    usable = _usable_lane_points(points)
    if len(bays) < 2 or not usable:
        return zeros
    y = float(y)
    pinned: dict = {}
    pin_dist: dict = {}
    interior: dict = {}
    before: List[Tuple[float, Tuple[float, float, float, float]]] = []
    after: List[Tuple[float, Tuple[float, float, float, float]]] = []
    for py, shifts in usable:
        nearest = None
        best = _BAY_MATCH_M + 1.0
        for index, bay in enumerate(bays):
            dist = abs(py - bay)
            if dist <= _BAY_MATCH_M and dist < best:
                best = dist
                nearest = index
        if nearest is not None:
            if nearest not in pin_dist or best < pin_dist[nearest]:
                pinned[nearest] = shifts
                pin_dist[nearest] = best
            continue
        if py < bays[0]:
            before.append((py, shifts))
        elif py > bays[-1]:
            after.append((py, shifts))
        else:
            for index in range(len(bays) - 1):
                if bays[index] < py < bays[index + 1]:
                    interior.setdefault(index, []).append((py, shifts))
                    break

    def _at_bay(index: int) -> Tuple[float, float, float, float]:
        return pinned.get(index, zeros)

    def _on_segment(index: int, at: float) -> Tuple[float, float, float, float]:
        left = (bays[index], _at_bay(index))
        right = (bays[index + 1], _at_bay(index + 1))
        mids = interior.get(index, [])
        if not mids and left[1] == zeros and right[1] == zeros:
            return zeros
        return interpolate_lane_shifts(at, [left, *mids, right])

    if y < bays[0]:
        if not before:
            return zeros
        anchors = [*before, (bays[0], _at_bay(0))]
        if y < min(py for py, _shifts in anchors):
            return zeros
        return interpolate_lane_shifts(y, anchors)
    if y > bays[-1]:
        if not after:
            return zeros
        anchors = [(bays[-1], _at_bay(len(bays) - 1)), *after]
        if y > max(py for py, _shifts in anchors):
            return zeros
        return interpolate_lane_shifts(y, anchors)
    for index, bay in enumerate(bays):
        if abs(y - bay) <= 1e-6:
            return _at_bay(index)
    for index in range(len(bays) - 1):
        if bays[index] < y < bays[index + 1]:
            return _on_segment(index, y)
    return zeros


def interpolate_lane_shifts(
    y: float,
    points: Sequence[Tuple[float, Sequence[float]]],
) -> Tuple[float, float, float, float]:
    """
    按沿贝位位置分段线性插值四个轮胎的修正量。
    两端之外保持最近一点，不向外延伸斜率。没有点时四个都是 0。
    """
    usable = [
        (float(py), tuple(float(v) for v in shifts[:4]))
        for py, shifts in points
        if shifts is not None and len(shifts) >= 4
    ]
    zeros = (0.0, 0.0, 0.0, 0.0)
    if not usable:
        return zeros
    usable.sort(key=lambda item: item[0])
    if len(usable) == 1 or float(y) <= usable[0][0]:
        return usable[0][1]  # type: ignore[return-value]
    if float(y) >= usable[-1][0]:
        return usable[-1][1]  # type: ignore[return-value]
    for i in range(1, len(usable)):
        y0, s0 = usable[i - 1]
        y1, s1 = usable[i]
        if float(y) <= y1:
            span = y1 - y0
            if abs(span) < 1e-9:
                return s1  # type: ignore[return-value]
            t = (float(y) - y0) / span
            return tuple(a + (b - a) * t for a, b in zip(s0, s1))  # type: ignore[return-value]
    return usable[-1][1]  # type: ignore[return-value]


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
