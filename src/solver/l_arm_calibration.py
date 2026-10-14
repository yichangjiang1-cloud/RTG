"""
从现场测量作业点 + 实时天线坐标与姿态，反算车体坐标系杆臂 L_arm。

正解（运行）：P_lpoint = P_antenna − R · L_arm
反算（标定）：L_arm = Rᵀ · (P_antenna − P_lpoint)
"""

from __future__ import annotations

import numpy as np
from typing import Sequence, Tuple

from src.solver.attitude import body_to_enu_rotation, euler_to_rotation_matrix
from src.solver.coordinate_transform import ecef_to_enu, ecef_to_wgs84, wgs84_to_ecef

_ECEF_NORM_MIN = 1.0e6


def compute_l_arm_from_ecef(
    antenna_ecef: Sequence[float],
    lpoint_ecef: Sequence[float],
    roll_deg: float,
    pitch_deg: float,
    heading_deg: float,
) -> np.ndarray:
    """两点 + 姿态 → 车体系杆臂 [dx, dy, dz]（X 前 Y 左 Z 上）。"""
    ant = np.asarray(antenna_ecef, dtype=float)
    lp = np.asarray(lpoint_ecef, dtype=float)
    if float(np.linalg.norm(ant)) > _ECEF_NORM_MIN:
        lat, lon, alt = ecef_to_wgs84(float(lp[0]), float(lp[1]), float(lp[2]))
        e, n, u = ecef_to_enu(float(ant[0]), float(ant[1]), float(ant[2]), lat, lon, alt)
        delta_enu = np.array([e, n, u], dtype=float)
        r_be = body_to_enu_rotation(roll_deg, pitch_deg, heading_deg)
        return r_be.T @ delta_enu
    r = euler_to_rotation_matrix(roll_deg, pitch_deg, heading_deg)
    return r.T @ (ant - lp)


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
