"""
逐贝残差插值：在贝位锚点之间线性过渡 delta，供空白行驶区补偿。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from src.solver.yard_geometry import is_electrical_bay


@dataclass
class BayAnchor:
    bay_no: int
    x: float
    delta_x: float
    delta_y: float
    delta_z: float


@dataclass
class BayPosition:
    """RTK 建图贝位在堆场 LYCS 平面上的位置。X 北、Y 东。"""

    bay_no: int
    x: float
    y: float = 0.0


def build_bay_positions_from_survey_points(
    survey_points: Sequence[dict],
    transform=None,
    bay_spacing: float = 6.0,
) -> List[BayPosition]:
    """
    从前端建图 survey_points 构建贝位折线（按 bay_no 排序，不按北向重排）。
    平面坐标优先用 local_x/local_y，否则由 WGS84 投影到 LYCS。
    """
    positions: List[BayPosition] = []
    spacing = max(float(bay_spacing), 0.1)
    for pt in survey_points:
        if not is_electrical_bay(pt):
            continue
        bay_no = pt.get("bay_no")
        if bay_no is None:
            continue
        x = pt.get("local_x")
        y = pt.get("local_y")
        if (x is None or y is None) and transform is not None:
            lat = float(pt.get("lat", 0.0))
            lon = float(pt.get("lon", 0.0))
            alt = float(pt.get("alt") or 0.0)
            if abs(lat) > 1e-9 or abs(lon) > 1e-9:
                px, py, _ = transform.wgs84_to_lycs(lat, lon, alt)
                if x is None:
                    x = px
                if y is None:
                    y = py
        if x is None:
            x = float(int(bay_no) - 1) * spacing
        if y is None:
            y = 0.0
        positions.append(BayPosition(int(bay_no), float(x), float(y)))
    positions.sort(key=lambda p: p.bay_no)
    return positions


def _segment_projection(
    px: float, py: float, left: BayPosition, right: BayPosition,
) -> Tuple[float, float]:
    """作业点投到贝位线段上。返回 (t, 距离平方)。t<=0 在起点侧，t>=1 在终点侧。"""
    sx = right.x - left.x
    sy = right.y - left.y
    len2 = sx * sx + sy * sy
    if len2 <= 1e-12:
        dx = px - left.x
        dy = py - left.y
        return 0.0, dx * dx + dy * dy
    t = ((px - left.x) * sx + (py - left.y) * sy) / len2
    if t <= 0.0:
        dx = px - left.x
        dy = py - left.y
    elif t >= 1.0:
        dx = px - right.x
        dy = py - right.y
    else:
        dx = px - (left.x + t * sx)
        dy = py - (left.y + t * sy)
    return t, dx * dx + dy * dy


def resolve_bay_no_next(
    lpoint_x: float,
    positions: Sequence[BayPosition],
    lpoint_y: float = 0.0,
) -> int:
    """
    PLC 当前贝位：沿 RTK 贝位折线（按贝位号顺序）判断，平面 X/Y 都参与。

    正好落在某贝位上返回该贝；落在两贝线段之间返回下一贝。
    折线起点之前返回第一贝，终点之后返回最后一贝。无 RTK 表时返回 0。
    """
    if not positions:
        return 0
    ordered = sorted(positions, key=lambda p: p.bay_no)
    if len(ordered) == 1:
        return ordered[0].bay_no

    best_dist = float("inf")
    best_bay = ordered[0].bay_no
    for i in range(len(ordered) - 1):
        left, right = ordered[i], ordered[i + 1]
        t, dist2 = _segment_projection(lpoint_x, lpoint_y, left, right)
        bay = left.bay_no if t <= 1e-8 else right.bay_no
        if dist2 < best_dist - 1e-9:
            best_dist = dist2
            best_bay = bay
    return best_bay


def geodetic_delta_to_local(
    delta_lon: float,
    delta_lat: float,
    delta_alt: float,
    ref_lat_deg: float,
) -> Tuple[float, float, float]:
    """将经纬高差近似换算为局部米制偏移（小范围够用）。"""
    lat_rad = math.radians(ref_lat_deg)
    m_per_deg_lat = 111_320.0
    m_per_deg_lon = 111_320.0 * math.cos(lat_rad)
    return (
        delta_lon * m_per_deg_lon,
        delta_lat * m_per_deg_lat,
        delta_alt,
    )


def build_anchors_from_transform_list(
    transform_list: Sequence[dict],
    ref_lat_deg: float,
    bay_spacing: float,
) -> List[BayAnchor]:
    """从 API transform_list 构建按 X 排序的残差锚点。"""
    anchors: List[BayAnchor] = []
    for item in transform_list:
        bay_no = item.get("bay_no")
        if bay_no is None:
            continue
        meas = item.get("measured") or {}
        delta = item.get("delta") or {}
        x = meas.get("local_x")
        if x is None:
            x = float(bay_no - 1) * bay_spacing
        dx, dy, dz = geodetic_delta_to_local(
            float(delta.get("lon") or 0.0),
            float(delta.get("lat") or 0.0),
            float(delta.get("alt") or 0.0),
            ref_lat_deg,
        )
        anchors.append(BayAnchor(int(bay_no), float(x), dx, dy, dz))
    anchors.sort(key=lambda a: a.x)
    return anchors


def interpolate_residual(
    x: float,
    anchors: Sequence[BayAnchor],
) -> Tuple[float, float, float]:
    """
    按大车 X 在相邻贝位锚点间线性插值残差 (dx, dy, dz)。
    无锚点时返回 (0,0,0)；单锚点返回该点残差。
    """
    if not anchors:
        return 0.0, 0.0, 0.0
    if len(anchors) == 1:
        a = anchors[0]
        return a.delta_x, a.delta_y, a.delta_z

    if x <= anchors[0].x:
        a = anchors[0]
        return a.delta_x, a.delta_y, a.delta_z
    if x >= anchors[-1].x:
        a = anchors[-1]
        return a.delta_x, a.delta_y, a.delta_z

    for i in range(len(anchors) - 1):
        left, right = anchors[i], anchors[i + 1]
        if left.x <= x <= right.x:
            span = right.x - left.x
            if span <= 1e-6:
                return left.delta_x, left.delta_y, left.delta_z
            t = (x - left.x) / span
            return (
                left.delta_x + t * (right.delta_x - left.delta_x),
                left.delta_y + t * (right.delta_y - left.delta_y),
                left.delta_z + t * (right.delta_z - left.delta_z),
            )

    a = anchors[-1]
    return a.delta_x, a.delta_y, a.delta_z
