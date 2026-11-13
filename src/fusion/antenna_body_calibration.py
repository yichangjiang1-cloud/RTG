"""
从四天线 ECEF 反算车体系安装坐标（标定用）。

约定（与现场接线一致）：
  - a1/a2 = 左侧接收机主天线 / 从天线，b1/b2 = 右侧
  - 主天线在下水、从天线在上水
  - 车体系：X 从下水端中心指向上水端中心，Y 从右侧指向左侧，Z 上；原点 a1
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
    pts = {
        "a1": np.zeros(3),
        "a2": _enu_delta(*a2, a1, ref_lla),
        "b1": _enu_delta(*b1, a1, ref_lla),
        "b2": _enu_delta(*b2, a1, ref_lla),
    }
    down = (pts["a1"] + pts["b1"]) * 0.5
    up = (pts["a2"] + pts["b2"]) * 0.5
    x_vec = up - down
    baseline_m = float(np.linalg.norm(x_vec))
    if baseline_m < 0.5:
        raise ValueError("上下水两端距离过短，无法建立车体系")
    x_hat = x_vec / baseline_m
    y_raw = (pts["a1"] + pts["a2"]) * 0.5 - (pts["b1"] + pts["b2"]) * 0.5
    y_raw = y_raw - x_hat * float(np.dot(y_raw, x_hat))
    y_norm = float(np.linalg.norm(y_raw))
    if y_norm < 0.3:
        raise ValueError("左右两侧距离过短，无法确定横向轴")
    y_hat = y_raw / y_norm
    z_hat = np.cross(x_hat, y_hat)
    r_be = np.column_stack([x_hat, y_hat, z_hat])

    body: BodyDict = {}
    for key, p in pts.items():
        body[key] = (r_be.T @ p).tolist()

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
            from src.solver.coordinate_transform import wgs84_to_ecef
            ex, ey, ez = wgs84_to_ecef(lat, lon, alt)
        return ex, ey, ez

    return _one("a1"), _one("a2"), _one("b1"), _one("b2")
