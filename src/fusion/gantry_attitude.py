"""
大车四天线 / 双接收机姿态解算。

策略（与 fusion_node 配合）：
  - 仅接收机 A（gantry_a1）在线：heading/pitch 来自 A 的双 GGA；roll 仅 NMEA 有则采用，否则 0。
  - A + B 同时在线：A/B 各自双 GGA 求 heading/pitch 后取平均；roll 由四天线刚体定姿（Kabsch）解算。
  - mode=four_antenna 且配置了 antenna_body：四点定姿同时输出 heading/pitch/roll（覆盖双基线平均）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

from src.models.gnss_data import GnssRaw
from src.solver.attitude import rotation_matrix_to_euler
from src.solver.coordinate_transform import ecef_to_enu

# 双 GGA 基线低于此水平长度时不解算航向/俯仰，回退 NMEA（PASHR/HDT）
MIN_DUAL_GGA_BASELINE_M = 0.5


@dataclass
class GantryAttitudeConfig:
    """来自 vehicle_params.yaml → gantry.attitude"""

    mode: str = "dual_receiver"  # dual_receiver | four_antenna
    baseline_m: float = 20.0
    antenna_body: Dict[str, List[float]] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, block: Optional[dict], w_span: float = 23.47) -> "GantryAttitudeConfig":
        if not isinstance(block, dict):
            block = {}
        mode = str(block.get("mode", "dual_receiver"))
        baseline_m = float(block.get("baseline_m", 20.0))
        body = block.get("antenna_body")
        if not isinstance(body, dict) or len(body) < 4:
            body = default_antenna_body(w_span, baseline_m)
        else:
            body = {k: [float(x) for x in v[:3]] for k, v in body.items() if isinstance(v, (list, tuple))}
        return cls(mode=mode, baseline_m=baseline_m, antenna_body=body)


@dataclass
class GantryAttitudeResult:
    heading_deg: float
    pitch_deg: float
    roll_deg: float
    source: str


def default_antenna_body(w_span: float, baseline_m: float) -> Dict[str, List[float]]:
    """
    车体系：X 前、Y 左、Z 上（与 attitude.py / L_arm 一致）。
    A1 为原点；A2 沿 +X；B1/B2 在 -Y 侧（右列，Y 左为正）。
    """
    return {
        "a1": [0.0, 0.0, 0.0],
        "a2": [baseline_m, 0.0, 0.0],
        "b1": [0.0, -float(w_span), 0.0],
        "b2": [baseline_m, -float(w_span), 0.0],
    }


def normalize_heading_deg(heading_deg: float) -> float:
    """归一化到 [0, 360)；北向附近 360° 与 0° 统一为 0° 便于显示。"""
    h = float(heading_deg) % 360.0
    if h >= 359.95:
        return 0.0
    return h


def _circular_diff_deg(a: float, b: float) -> float:
    """最短角差 a→b，范围 (-180, 180]。"""
    return (b - a + 180.0) % 360.0 - 180.0


def _resolve_baseline_heading(heading_deg: float, ref_heading: Optional[float]) -> float:
    """
    双 GGA 基线航向存在 180° 模糊；用 PASHR/NMEA 参考航向消歧。
    """
    h = normalize_heading_deg(heading_deg)
    if ref_heading is None:
        return h
    ref = normalize_heading_deg(ref_heading)
    if abs(_circular_diff_deg(h, ref)) > 90.0:
        h = normalize_heading_deg(h + 180.0)
    return h


def derive_heading_pitch_from_dual_gga(
    gnss: GnssRaw,
    ref_heading: Optional[float] = None,
) -> Optional[Tuple[float, float]]:
    """同机双 GGA 基线 → heading / pitch（roll 不可观）。"""
    if not gnss.has_antenna2:
        return None
    e2, n2, u2 = ecef_to_enu(
        gnss.antenna2_ecef_x,
        gnss.antenna2_ecef_y,
        gnss.antenna2_ecef_z,
        gnss.latitude,
        gnss.longitude,
        gnss.altitude,
    )
    horiz = math.hypot(e2, n2)
    if horiz < MIN_DUAL_GGA_BASELINE_M:
        return None
    if horiz < 1e-6 and abs(u2) < 1e-6:
        return None
    ref_hdg = ref_heading
    if ref_hdg is None and gnss.heading is not None:
        ref_hdg = gnss.heading
    heading = _resolve_baseline_heading(
        math.degrees(math.atan2(e2, n2)), ref_hdg
    )
    pitch = math.degrees(math.atan2(u2, max(horiz, 1e-9)))
    return heading, pitch


def _circular_mean_deg(a: float, b: float) -> float:
    """仅当两角接近时取均值；相差过大时返回第一路（左列参考）。"""
    if abs(_circular_diff_deg(a, b)) > 45.0:
        return normalize_heading_deg(a)
    x = math.cos(math.radians(a)) + math.cos(math.radians(b))
    y = math.sin(math.radians(a)) + math.sin(math.radians(b))
    if abs(x) < 1e-12 and abs(y) < 1e-12:
        return normalize_heading_deg(a)
    return normalize_heading_deg(math.degrees(math.atan2(y, x)))


def _pick_gantry_heading(hp_a: Tuple[float, float], hp_b: Optional[Tuple[float, float]]) -> float:
    """大车航向以左列 gantry_a1 双 GGA 为准，右列仅作一致性校验。"""
    h_a = normalize_heading_deg(hp_a[0])
    if hp_b is None:
        return h_a
    h_b = normalize_heading_deg(hp_b[0])
    if abs(_circular_diff_deg(h_a, h_b)) > 45.0:
        return h_a
    return _circular_mean_deg(h_a, h_b)


def _gnss_primary_ecef(gnss: GnssRaw) -> Tuple[float, float, float]:
    return float(gnss.ecef_x), float(gnss.ecef_y), float(gnss.ecef_z)


def _gnss_secondary_ecef(gnss: GnssRaw) -> Optional[Tuple[float, float, float]]:
    if not gnss.has_antenna2:
        return None
    return (
        float(gnss.antenna2_ecef_x),
        float(gnss.antenna2_ecef_y),
        float(gnss.antenna2_ecef_z),
    )


def _collect_four_ecef(
    ga1: Optional[GnssRaw],
    ga2: Optional[GnssRaw],
) -> Optional[Dict[str, Tuple[float, float, float]]]:
    """左列 a1/a2（电气/柴油），右列 b1/b2。"""
    if ga1 is None or ga2 is None:
        return None
    a2 = _gnss_secondary_ecef(ga1)
    b1 = _gnss_primary_ecef(ga2)
    b2 = _gnss_secondary_ecef(ga2)
    if a2 is None or b2 is None:
        return None
    return {
        "a1": _gnss_primary_ecef(ga1),
        "a2": a2,
        "b1": b1,
        "b2": b2,
    }


def build_gantry_antennas_payload(
    ga1: Optional[dict],
    ga2: Optional[dict],
) -> Optional[Dict[str, dict]]:
    """
    从融合状态 dict 组装四天线 WGS84/ECEF（供遥测与标定）。
    ga1=左接收机，ga2=右接收机；主天线=电气房 GGA[0]，副天线=柴油机 GGA[1]。
    """
    if not ga1 or not ga2:
        return None
    if not ga1.get("is_live") or not ga2.get("is_live"):
        return None
    if not ga1.get("has_antenna2") or not ga2.get("has_antenna2"):
        return None

    def _pt(primary: bool, g: dict) -> dict:
        if primary:
            return {
                "lat": float(g.get("lat", 0.0)),
                "lon": float(g.get("lon", 0.0)),
                "alt": float(g.get("alt", 0.0)),
                "ecef_x": float(g.get("ecef_x", 0.0)),
                "ecef_y": float(g.get("ecef_y", 0.0)),
                "ecef_z": float(g.get("ecef_z", 0.0)),
                "valid": True,
            }
        return {
            "lat": float(g.get("antenna2_lat", 0.0)),
            "lon": float(g.get("antenna2_lon", 0.0)),
            "alt": float(g.get("antenna2_alt", 0.0)),
            "ecef_x": float(g.get("antenna2_ecef_x", 0.0)),
            "ecef_y": float(g.get("antenna2_ecef_y", 0.0)),
            "ecef_z": float(g.get("antenna2_ecef_z", 0.0)),
            "valid": True,
        }

    return {
        "a1": {**_pt(True, ga1), "role": "elec", "receiver": "left"},
        "a2": {**_pt(False, ga1), "role": "diesel", "receiver": "left"},
        "b1": {**_pt(True, ga2), "role": "elec", "receiver": "right"},
        "b2": {**_pt(False, ga2), "role": "diesel", "receiver": "right"},
    }


def _kabsch_rotation(body_pts: np.ndarray, enu_pts: np.ndarray) -> Optional[np.ndarray]:
    """求 R：body → ENU，使 sum|R @ p_body - p_enu|² 最小。"""
    if body_pts.shape != enu_pts.shape or body_pts.shape[0] < 3:
        return None
    c_b = body_pts.mean(axis=0)
    c_e = enu_pts.mean(axis=0)
    p = body_pts - c_b
    q = enu_pts - c_e
    h = p.T @ q
    u, _, vt = np.linalg.svd(h)
    r = vt.T @ u.T
    if np.linalg.det(r) < 0:
        vt[-1, :] *= -1
        r = vt.T @ u.T
    return r


def solve_four_antenna_attitude(
    ecef_by_id: Dict[str, Tuple[float, float, float]],
    body_by_id: Dict[str, List[float]],
    ref_lat: float,
    ref_lon: float,
    ref_alt: float,
) -> Optional[GantryAttitudeResult]:
    """四天线 ECEF + 车体系安装坐标 → 完整欧拉角。"""
    order = ("a1", "a2", "b1", "b2")
    body_rows, enu_rows = [], []
    for key in order:
        if key not in ecef_by_id or key not in body_by_id:
            return None
        ex, ey, ez = ecef_by_id[key]
        e, n, u = ecef_to_enu(ex, ey, ez, ref_lat, ref_lon, ref_alt)
        body_rows.append(body_by_id[key])
        enu_rows.append([e, n, u])

    body = np.array(body_rows, dtype=float)
    enu = np.array(enu_rows, dtype=float)
    r_be = _kabsch_rotation(body, enu)
    if r_be is None:
        return None

    roll, pitch, heading = rotation_matrix_to_euler(r_be)
    return GantryAttitudeResult(
        heading_deg=normalize_heading_deg(float(heading)),
        pitch_deg=float(pitch),
        roll_deg=float(roll),
        source="four_antenna",
    )


def _nmea_roll(gnss: Optional[GnssRaw]) -> Optional[float]:
    if gnss is None or gnss.roll is None:
        return None
    return float(gnss.roll)


def compute_gantry_attitude(
    ga1: Optional[GnssRaw],
    ga2: Optional[GnssRaw],
    ga1_live: bool,
    ga2_live: bool,
    cfg: GantryAttitudeConfig,
    ref_lat: float,
    ref_lon: float,
    ref_alt: float,
) -> Optional[GantryAttitudeResult]:
    """
    大车级姿态（供 solver 使用的 gantry_a1 通道）。
    """
    if not ga1_live or ga1 is None:
        return None

    hp_a = derive_heading_pitch_from_dual_gga(ga1)
    ref_for_b = hp_a[0] if hp_a is not None else ga1.heading
    hp_b = (
        derive_heading_pitch_from_dual_gga(ga2, ref_heading=ref_for_b)
        if ga2_live and ga2
        else None
    )

    four_ecef = _collect_four_ecef(ga1, ga2 if ga2_live else None)

    if cfg.mode == "four_antenna" and four_ecef is not None:
        full = solve_four_antenna_attitude(
            four_ecef, cfg.antenna_body, ref_lat, ref_lon, ref_alt
        )
        if full is not None:
            return full

    if ga2_live and ga2 is not None and hp_a is not None and hp_b is not None:
        heading = _pick_gantry_heading(hp_a, hp_b)
        pitch = (hp_a[1] + hp_b[1]) / 2.0
        roll = 0.0
        source = "dual_receiver"

        if four_ecef is not None:
            rigid = solve_four_antenna_attitude(
                four_ecef, cfg.antenna_body, ref_lat, ref_lon, ref_alt
            )
            if rigid is not None:
                roll = rigid.roll_deg
                source = "dual_receiver+4ant_roll"

        return GantryAttitudeResult(
            normalize_heading_deg(heading), pitch, roll, source
        )

    # 仅接收机 A
    if hp_a is not None:
        roll = _nmea_roll(ga1)
        return GantryAttitudeResult(
            normalize_heading_deg(hp_a[0]),
            hp_a[1],
            roll if roll is not None else 0.0,
            "nmea" if roll is not None else "dual_gga",
        )

    # 无双 GGA：NMEA 回退
    if ga1.heading is not None:
        return GantryAttitudeResult(
            normalize_heading_deg(float(ga1.heading or 0.0)),
            float(ga1.pitch or 0.0),
            float(ga1.roll or 0.0),
            "nmea",
        )
    return None
