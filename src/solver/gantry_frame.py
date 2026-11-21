"""
大车（门架）车体坐标系变换。

原点锚定在大车 L-Point；X 沿大车航向，Y 沿横梁（小车行程方向），Z 向上。
大车移动/转向时，同一物理点在车体系下坐标不变，世界系 (LYCS) 坐标随之变化。
"""

from __future__ import annotations

import math
from typing import Tuple

Vec3 = Tuple[float, float, float]


# 未拟合贝位轴时的默认 LYCS：X = 北，Y = 东
DEFAULT_R2D = ((0.0, 1.0), (1.0, 0.0))


def heading_axes_lycs(
    heading_deg: float,
    r2d=None,
) -> Tuple[Tuple[float, float], Tuple[float, float]]:
    """
    航向在 LYCS 平面中的前向单位向量与右向单位向量。

    r2d 为 CoordinateTransform 的 2×2 矩阵（ENU(E,N) → LYCS(X,Y)）。
    拟合贝位轴后 +Y 沿贝位，车头通常与 +Y 重合，不能再假设 X=北、Y=东。
    """
    R = DEFAULT_R2D if r2d is None else r2d
    h = math.radians(heading_deg)
    s, c = math.sin(h), math.cos(h)
    # ENU(E,N)：前向 (sinh, cosh)，车右侧 (cosh, -sinh)
    fx = R[0][0] * s + R[0][1] * c
    fy = R[1][0] * s + R[1][1] * c
    rx = R[0][0] * c - R[0][1] * s
    ry = R[1][0] * c - R[1][1] * s
    return (fx, fy), (rx, ry)


def lycs_to_gantry_body(
    px: float,
    py: float,
    pz: float,
    ref_x: float,
    ref_y: float,
    ref_z: float,
    heading_deg: float,
    r2d=None,
) -> Vec3:
    """
    LYCS 点 → 大车车体系（相对 L-Point，按航向旋转）。

    bx：沿车头（前向）；by：车右侧为正（与原默认约定一致）。
    同一刚体点在车体系下坐标不随航向变化。
    """
    dx = px - ref_x
    dy = py - ref_y
    dz = pz - ref_z
    (fx, fy), (rx, ry) = heading_axes_lycs(heading_deg, r2d)
    bx = dx * fx + dy * fy
    by = dx * rx + dy * ry
    return bx, by, dz


def gantry_body_travel_component(by: float) -> float:
    """沿横梁行程分量（车体系 Y）。"""
    return by
