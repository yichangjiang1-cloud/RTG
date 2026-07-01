"""
从现场测量作业点 + 实时天线坐标与姿态，反算车体坐标系杆臂 L_arm。

正解（运行）：P_lpoint = P_antenna − R · L_arm
反算（标定）：L_arm = Rᵀ · (P_antenna − P_lpoint)
"""

from __future__ import annotations

import numpy as np
from typing import Sequence, Tuple

from src.solver.attitude import euler_to_rotation_matrix
from src.solver.coordinate_transform import wgs84_to_ecef


def compute_l_arm_from_ecef(
    antenna_ecef: Sequence[float],
    lpoint_ecef: Sequence[float],
    roll_deg: float,
    pitch_deg: float,
    heading_deg: float,
) -> np.ndarray:
    """ECEF 下两点 + 姿态 → 车体系杆臂 [dx, dy, dz]。"""
    R = euler_to_rotation_matrix(roll_deg, pitch_deg, heading_deg)
    delta = np.asarray(antenna_ecef, dtype=float) - np.asarray(lpoint_ecef, dtype=float)
    return R.T @ delta


def compute_l_arm_from_wgs84(
    antenna_lat: float,
    antenna_lon: float,
    antenna_alt: float,
    lpoint_lat: float,
    lpoint_lon: float,
    lpoint_alt: float,
    roll_deg: float,
    pitch_deg: float,
    heading_deg: float,
) -> Tuple[float, float, float]:
    """WGS84 两点 + 姿态 → 车体系杆臂 (dx, dy, dz)。"""
    ax, ay, az = wgs84_to_ecef(antenna_lat, antenna_lon, antenna_alt)
    lx, ly, lz = wgs84_to_ecef(lpoint_lat, lpoint_lon, lpoint_alt)
    arm = compute_l_arm_from_ecef((ax, ay, az), (lx, ly, lz), roll_deg, pitch_deg, heading_deg)
    return float(arm[0]), float(arm[1]), float(arm[2])
