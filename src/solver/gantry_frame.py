"""
大车（门架）车体坐标系变换。

原点锚定在大车 L-Point；X 沿大车航向，Y 沿横梁（小车行程方向），Z 向上。
大车移动/转向时，同一物理点在车体系下坐标不变，世界系 (LYCS) 坐标随之变化。
"""

from __future__ import annotations

import math
from typing import Tuple

Vec3 = Tuple[float, float, float]


def lycs_to_gantry_body(
    px: float,
    py: float,
    pz: float,
    ref_x: float,
    ref_y: float,
    ref_z: float,
    heading_deg: float,
) -> Vec3:
    """LYCS 点 → 大车车体系（相对 L-Point，按航向旋转）。"""
    dx = px - ref_x
    dy = py - ref_y
    dz = pz - ref_z
    h = math.radians(heading_deg)
    c, s = math.cos(h), math.sin(h)
    bx = dx * c + dy * s
    by = -dx * s + dy * c
    return bx, by, dz


def gantry_body_travel_component(by: float) -> float:
    """沿横梁行程分量（车体系 Y）。"""
    return by
