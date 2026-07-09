"""
从四天线 ECEF 反算车体系安装坐标（标定用）。

约定（与现场一致）：
  - 左接收机 gantry_a1：GGA[0]=电气房(a1)，GGA[1]=柴油机(a2)
  - 右接收机 gantry_a2：GGA[0]=电气房(b1)，GGA[1]=柴油机(b2)
  - 车体系：X 前（a1→a2），Y 左，Z 上；原点 a1
"""

from __future__ import annotations

import math
from typing import Dict, List, Tuple, Union

import numpy as np

from src.solver.coordinate_transform import ecef_to_enu, ecef_to_wgs84

Ecef = Tuple[float, float, float]
BodyDict = Dict[str, List[float]]


def _enu_delta(ex: float, ey: float, ez: float, ref: Ecef, ref_lla: Tuple[float, float, float]) -> np.ndarray:
    e, n, u = ecef_to_enu(ex, ey, ez, ref_lla[0], ref_lla[1], ref_lla[2])
    e0, n0, u0 = ecef_to_enu(ref[0], ref[1], ref[2], ref_lla[0], ref_lla[1], ref_lla[2])
    return np.array([e - e0, n - n0, u - u0], dtype=float)


def compute_antenna_body_from_ecef(
    a1: Ecef,
    a2: Ecef,
    b1: Ecef,
    b2: Ecef,
) -> Tuple[BodyDict, float, float]:
    """
    由四天线 ECEF 坐标计算车体系安装坐标及实测基线/跨距。

    :return: (antenna_body, baseline_m, span_m)
    """
    ref_lla = ecef_to_wgs84(*a1)
    p_a1 = np.zeros(3)
    p_a2 = _enu_delta(*a2, a1, ref_lla)
    p_b1 = _enu_delta(*b1, a1, ref_lla)
    p_b2 = _enu_delta(*b2, a1, ref_lla)

    baseline_m = float(np.linalg.norm(p_a2 - p_a1))
    if baseline_m < 0.5:
        raise ValueError("左接收机双天线基线过短，无法建立车体系")

    x_hat = (p_a2 - p_a1) / baseline_m
    z_up = np.array([0.0, 0.0, 1.0])
    y_hat = np.cross(z_up, x_hat)
    y_norm = float(np.linalg.norm(y_hat))
    if y_norm < 1e-6:
        raise ValueError("四天线共线，无法确定横向轴")
    y_hat /= y_norm
    z_hat = np.cross(x_hat, y_hat)
    r_be = np.column_stack([x_hat, y_hat, z_hat])

    body: BodyDict = {}
    for key, p in (("a1", p_a1), ("a2", p_a2), ("b1", p_b1), ("b2", p_b2)):
        body[key] = (r_be.T @ p).tolist()

    span_m = float(abs(body["b1"][1]))
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
            from src.solver.coordinate_transform import wgs84_to_ecef
            ex, ey, ez = wgs84_to_ecef(lat, lon, alt)
        return ex, ey, ez

    return _one("a1"), _one("a2"), _one("b1"), _one("b2")
