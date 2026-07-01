"""杆臂反算（与中控 src/solver/l_arm_calibration.py 公式一致）"""

from __future__ import annotations

import math
from typing import Sequence, Tuple

_A = 6378137.0
_F = 1 / 298.257223563
_E2 = 1 - (1 - _F) ** 2


def _wgs84_to_ecef(lat_deg: float, lon_deg: float, alt_m: float) -> Tuple[float, float, float]:
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)
    n = _A / math.sqrt(1 - _E2 * math.sin(lat) ** 2)
    x = (n + alt_m) * math.cos(lat) * math.cos(lon)
    y = (n + alt_m) * math.cos(lat) * math.sin(lon)
    z = (n * (1 - _E2) + alt_m) * math.sin(lat)
    return x, y, z


def _euler_to_rotation_matrix(roll_deg: float, pitch_deg: float, heading_deg: float):
    phi = math.radians(roll_deg)
    theta = math.radians(pitch_deg)
    psi = math.radians(heading_deg)
    cx, sx = math.cos(phi), math.sin(phi)
    cy, sy = math.cos(theta), math.sin(theta)
    cz, sz = math.cos(psi), math.sin(psi)
    Rx = ((1, 0, 0), (0, cx, -sx), (0, sx, cx))
    Ry = ((cy, 0, sy), (0, 1, 0), (-sy, 0, cy))
    Rz = ((cz, -sz, 0), (sz, cz, 0), (0, 0, 1))

    def matmul(a, b):
        return tuple(
            tuple(sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3))
            for i in range(3)
        )

    return matmul(matmul(Rz, Ry), Rx)


def _mat_vec(R, v: Sequence[float]) -> Tuple[float, float, float]:
    return tuple(sum(R[i][j] * v[j] for j in range(3)) for i in range(3))


def _transpose(R) -> Tuple[Tuple[float, float, float], ...]:
    return tuple(tuple(R[j][i] for j in range(3)) for i in range(3))


def compute_l_arm_from_ecef(
    antenna_ecef: Sequence[float],
    lpoint_ecef: Sequence[float],
    roll_deg: float,
    pitch_deg: float,
    heading_deg: float,
) -> Tuple[float, float, float]:
    R = _euler_to_rotation_matrix(roll_deg, pitch_deg, heading_deg)
    delta = (
        antenna_ecef[0] - lpoint_ecef[0],
        antenna_ecef[1] - lpoint_ecef[1],
        antenna_ecef[2] - lpoint_ecef[2],
    )
    return _mat_vec(_transpose(R), delta)


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
    ax, ay, az = _wgs84_to_ecef(antenna_lat, antenna_lon, antenna_alt)
    lx, ly, lz = _wgs84_to_ecef(lpoint_lat, lpoint_lon, lpoint_alt)
    R = _euler_to_rotation_matrix(roll_deg, pitch_deg, heading_deg)
    delta = (ax - lx, ay - ly, az - lz)
    arm = _mat_vec(_transpose(R), delta)
    return arm
