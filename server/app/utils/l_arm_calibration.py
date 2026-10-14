"""杆臂反算（与中控 src/solver/l_arm_calibration.py 公式一致）"""

from __future__ import annotations

import math
from typing import Sequence, Tuple

_A = 6378137.0
_F = 1 / 298.257223563
_E2 = 1 - (1 - _F) ** 2
_ECEF_NORM_MIN = 1.0e6


def _wgs84_to_ecef(lat_deg: float, lon_deg: float, alt_m: float) -> Tuple[float, float, float]:
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)
    n = _A / math.sqrt(1 - _E2 * math.sin(lat) ** 2)
    x = (n + alt_m) * math.cos(lat) * math.cos(lon)
    y = (n + alt_m) * math.cos(lat) * math.sin(lon)
    z = (n * (1 - _E2) + alt_m) * math.sin(lat)
    return x, y, z


def _ecef_to_wgs84(x: float, y: float, z: float) -> Tuple[float, float, float]:
    """Bowring 一次迭代，足够杆臂米级精度。"""
    lon = math.atan2(y, x)
    p = math.hypot(x, y)
    lat = math.atan2(z, p * (1 - _E2))
    for _ in range(8):
        sin_lat = math.sin(lat)
        n = _A / math.sqrt(1 - _E2 * sin_lat * sin_lat)
        lat = math.atan2(z + _E2 * n * sin_lat, p)
    sin_lat = math.sin(lat)
    n = _A / math.sqrt(1 - _E2 * sin_lat * sin_lat)
    alt = p / math.cos(lat) - n
    return math.degrees(lat), math.degrees(lon), alt


def _ecef_to_enu(
    x: float, y: float, z: float,
    ref_lat_deg: float, ref_lon_deg: float, ref_alt_m: float,
) -> Tuple[float, float, float]:
    ref_lat = math.radians(ref_lat_deg)
    ref_lon = math.radians(ref_lon_deg)
    x0, y0, z0 = _wgs84_to_ecef(ref_lat_deg, ref_lon_deg, ref_alt_m)
    dx, dy, dz = x - x0, y - y0, z - z0
    slat, clat = math.sin(ref_lat), math.cos(ref_lat)
    slon, clon = math.sin(ref_lon), math.cos(ref_lon)
    east = -slon * dx + clon * dy
    north = -slat * clon * dx - slat * slon * dy + clat * dz
    up = clat * clon * dx + clat * slon * dy + slat * dz
    return east, north, up


def _euler_to_rotation_matrix(roll_deg: float, pitch_deg: float, heading_deg: float):
    phi = math.radians(roll_deg)
    theta = math.radians(pitch_deg)
    psi = math.radians(heading_deg)
    cx, sx = math.cos(phi), math.sin(phi)
    cy, sy = math.cos(theta), math.sin(theta)
    cz, sz = math.cos(psi), math.sin(psi)
    rx = ((1, 0, 0), (0, cx, -sx), (0, sx, cx))
    ry = ((cy, 0, sy), (0, 1, 0), (-sy, 0, cy))
    rz = ((cz, -sz, 0), (sz, cz, 0), (0, 0, 1))
    return _matmul(_matmul(rz, ry), rx)


def _body_to_enu_rotation(roll_deg: float, pitch_deg: float, heading_deg: float):
    phi = math.radians(roll_deg)
    theta = math.radians(pitch_deg)
    psi = math.radians(heading_deg)
    cx, sx = math.cos(phi), math.sin(phi)
    cy, sy = math.cos(theta), math.sin(theta)
    cz, sz = math.cos(psi), math.sin(psi)
    rx = ((1, 0, 0), (0, cx, -sx), (0, sx, cx))
    ry = ((cy, 0, sy), (0, 1, 0), (-sy, 0, cy))
    rz = ((sz, -cz, 0), (cz, sz, 0), (0, 0, 1))
    return _matmul(_matmul(rz, ry), rx)


def _matmul(a, b):
    return tuple(
        tuple(sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3))
        for i in range(3)
    )


def _mat_vec(r, v: Sequence[float]) -> Tuple[float, float, float]:
    return tuple(sum(r[i][j] * v[j] for j in range(3)) for i in range(3))


def _transpose(r) -> Tuple[Tuple[float, float, float], ...]:
    return tuple(tuple(r[j][i] for j in range(3)) for i in range(3))


def compute_l_arm_from_ecef(
    antenna_ecef: Sequence[float],
    lpoint_ecef: Sequence[float],
    roll_deg: float,
    pitch_deg: float,
    heading_deg: float,
) -> Tuple[float, float, float]:
    if math.hypot(antenna_ecef[0], antenna_ecef[1], antenna_ecef[2]) > _ECEF_NORM_MIN:
        lat, lon, alt = _ecef_to_wgs84(lpoint_ecef[0], lpoint_ecef[1], lpoint_ecef[2])
        e, n, u = _ecef_to_enu(
            antenna_ecef[0], antenna_ecef[1], antenna_ecef[2], lat, lon, alt,
        )
        r_be = _body_to_enu_rotation(roll_deg, pitch_deg, heading_deg)
        return _mat_vec(_transpose(r_be), (e, n, u))
    r = _euler_to_rotation_matrix(roll_deg, pitch_deg, heading_deg)
    delta = (
        antenna_ecef[0] - lpoint_ecef[0],
        antenna_ecef[1] - lpoint_ecef[1],
        antenna_ecef[2] - lpoint_ecef[2],
    )
    return _mat_vec(_transpose(r), delta)


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
    return compute_l_arm_from_ecef((ax, ay, az), (lx, ly, lz), roll_deg, pitch_deg, heading_deg)
