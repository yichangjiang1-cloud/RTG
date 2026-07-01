"""
逐贝残差插值：在贝位锚点之间线性过渡 delta，供空白行驶区补偿。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple


@dataclass
class BayAnchor:
    bay_no: int
    x: float
    delta_x: float
    delta_y: float
    delta_z: float


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
