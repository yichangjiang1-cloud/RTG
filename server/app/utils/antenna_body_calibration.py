"""
四天线 ECEF → 车体系安装坐标标定（后端 API 独立实现，与中控公式一致）。

约定：
  - 上水接收机：GGA[0]=电气房(a1)，GGA[1]=动力房(a2)
  - 下水接收机：GGA[0]=电气房(b1)，GGA[1]=动力房(b2)
  - 车体系：X 指向上水，Y 指向电气房，Z 上；原点 a1
"""

from __future__ import annotations

import math
from typing import Dict, List, Tuple, Union

_A = 6378137.0
_F = 1 / 298.257223563
_E2 = 1 - (1 - _F) ** 2

Ecef = Tuple[float, float, float]
BodyDict = Dict[str, List[float]]
Vec3 = Tuple[float, float, float]


def _wgs84_to_ecef(lat_deg: float, lon_deg: float, alt_m: float) -> Ecef:
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)
    n = _A / math.sqrt(1 - _E2 * math.sin(lat) ** 2)
    x = (n + alt_m) * math.cos(lat) * math.cos(lon)
    y = (n + alt_m) * math.cos(lat) * math.sin(lon)
    z = (n * (1 - _E2) + alt_m) * math.sin(lat)
    return x, y, z


def _ecef_to_wgs84(x: float, y: float, z: float) -> Tuple[float, float, float]:
    lon = math.atan2(y, x)
    p = math.sqrt(x ** 2 + y ** 2)
    lat = math.atan2(z, p * (1 - _E2))
    for _ in range(10):
        n = _A / math.sqrt(1 - _E2 * math.sin(lat) ** 2)
        lat_new = math.atan2(z + _E2 * n * math.sin(lat), p)
        if abs(lat_new - lat) < 1e-12:
            lat = lat_new
            break
        lat = lat_new
    n = _A / math.sqrt(1 - _E2 * math.sin(lat) ** 2)
    alt = p / math.cos(lat) - n if abs(math.cos(lat)) > 1e-10 else abs(z) / math.sin(lat) - n * (1 - _E2)
    return math.degrees(lat), math.degrees(lon), alt


def _ecef_to_enu(
    x: float, y: float, z: float,
    ref_lat_deg: float, ref_lon_deg: float, ref_alt_m: float,
) -> Vec3:
    ref_lat = math.radians(ref_lat_deg)
    ref_lon = math.radians(ref_lon_deg)
    x0, y0, z0 = _wgs84_to_ecef(ref_lat_deg, ref_lon_deg, ref_alt_m)
    dx, dy, dz = x - x0, y - y0, z - z0
    e = -math.sin(ref_lon) * dx + math.cos(ref_lon) * dy
    n = (
        -math.sin(ref_lat) * math.cos(ref_lon) * dx
        - math.sin(ref_lat) * math.sin(ref_lon) * dy
        + math.cos(ref_lat) * dz
    )
    u = (
        math.cos(ref_lat) * math.cos(ref_lon) * dx
        + math.cos(ref_lat) * math.sin(ref_lon) * dy
        + math.sin(ref_lat) * dz
    )
    return e, n, u


def _sub(a: Vec3, b: Vec3) -> Vec3:
    return a[0] - b[0], a[1] - b[1], a[2] - b[2]


def _norm(v: Vec3) -> float:
    return math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)


def _scale(v: Vec3, s: float) -> Vec3:
    return v[0] * s, v[1] * s, v[2] * s


def _cross(a: Vec3, b: Vec3) -> Vec3:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _mat_vec(m: Tuple[Vec3, Vec3, Vec3], v: Vec3) -> Vec3:
    return (
        _dot(m[0], v),
        _dot(m[1], v),
        _dot(m[2], v),
    )


def _enu_delta(ex: float, ey: float, ez: float, ref: Ecef, ref_lla: Tuple[float, float, float]) -> Vec3:
    e, n, u = _ecef_to_enu(ex, ey, ez, ref_lla[0], ref_lla[1], ref_lla[2])
    e0, n0, u0 = _ecef_to_enu(ref[0], ref[1], ref[2], ref_lla[0], ref_lla[1], ref_lla[2])
    return e - e0, n - n0, u - u0


def compute_antenna_body_from_ecef(
    a1: Ecef,
    a2: Ecef,
    b1: Ecef,
    b2: Ecef,
) -> Tuple[BodyDict, float, float]:
    """由四天线 ECEF 坐标计算车体系安装坐标及实测基线/跨距。"""
    ref_lla = _ecef_to_wgs84(*a1)
    pts = {
        "a1": (0.0, 0.0, 0.0),
        "a2": _enu_delta(*a2, a1, ref_lla),
        "b1": _enu_delta(*b1, a1, ref_lla),
        "b2": _enu_delta(*b2, a1, ref_lla),
    }

    def _mean(p: Vec3, q: Vec3) -> Vec3:
        return ((p[0] + q[0]) * 0.5, (p[1] + q[1]) * 0.5, (p[2] + q[2]) * 0.5)

    up = _mean(pts["a1"], pts["a2"])
    down = _mean(pts["b1"], pts["b2"])
    x_vec = _sub(up, down)
    baseline_m = _norm(x_vec)
    if baseline_m < 0.5:
        raise ValueError("上下水两端距离过短，无法建立车体系")
    x_hat = _scale(x_vec, 1.0 / baseline_m)
    y_raw = _sub(_mean(pts["a1"], pts["b1"]), _mean(pts["a2"], pts["b2"]))
    y_raw = _sub(y_raw, _scale(x_hat, _dot(y_raw, x_hat)))
    y_norm = _norm(y_raw)
    if y_norm < 0.3:
        raise ValueError("电气房与动力房距离过短，无法确定横向轴")
    y_hat = _scale(y_raw, 1.0 / y_norm)
    z_hat = _cross(x_hat, y_hat)
    r_be = (x_hat, y_hat, z_hat)

    body: BodyDict = {}
    for key, p in pts.items():
        body[key] = list(_mat_vec(r_be, p))

    span_m = y_norm
    return body, baseline_m, span_m


def ecef_dict_from_antennas(
    antennas: Dict[str, Dict[str, Union[float, bool]]],
) -> Tuple[Ecef, Ecef, Ecef, Ecef]:
    """从 {a1:{ecef_x,...}, ...} 提取四组 ECEF。"""

    def _one(k: str) -> Ecef:
        d = antennas.get(k) or {}
        ex = float(d.get("ecef_x", 0.0))
        ey = float(d.get("ecef_y", 0.0))
        ez = float(d.get("ecef_z", 0.0))
        if ex == 0.0 and ey == 0.0 and ez == 0.0:
            lat = float(d.get("lat", 0.0))
            lon = float(d.get("lon", 0.0))
            alt = float(d.get("alt", 0.0))
            if abs(lat) < 1e-9 and abs(lon) < 1e-9:
                raise ValueError(f"天线 {k} 无有效坐标")
            ex, ey, ez = _wgs84_to_ecef(lat, lon, alt)
        return ex, ey, ez

    return _one("a1"), _one("a2"), _one("b1"), _one("b2")


__all__ = ["compute_antenna_body_from_ecef", "ecef_dict_from_antennas"]
