"""
大车姿态解算。

模式：
  - hdt_tra_height_roll（UB482 两主点）：航向/俯仰来自 HDT/TRA(NMEA)；
    横滚由左右主天线高差 + roll_span_m 估算。保留 dual_gga 作兼容回退。
  - dual_receiver / four_antenna（旧四 GGA）：双基线 + Kabsch，保留备用。
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
# 两主点估横滚时，左右间距过小则放弃
MIN_ROLL_SPAN_M = 1.0


@dataclass
class GantryAttitudeConfig:
    """来自 vehicle_params.yaml → gantry.attitude"""

    # hdt_tra_height_roll | dual_receiver | four_antenna
    mode: str = "hdt_tra_height_roll"
    baseline_m: float = 1.0  # 前后定向基线长（安装信息）
    roll_span_m: float = 23.47  # 左右主天线间距，用于 Δh→roll
    roll_alt0_m: float = 0.0  # 车体水平时 (alt_left - alt_right) 零点
    antenna_body: Dict[str, List[float]] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, block: Optional[dict], w_span: float = 23.47) -> "GantryAttitudeConfig":
        if not isinstance(block, dict):
            block = {}
        mode = str(block.get("mode", "hdt_tra_height_roll"))
        baseline_m = float(block.get("baseline_m", 1.0))
        roll_span_m = float(block.get("roll_span_m", w_span if w_span > 0 else 23.47))
        roll_alt0_m = float(block.get("roll_alt0_m", 0.0))
        body = block.get("antenna_body")
        if not isinstance(body, dict) or len(body) < 4:
            body = default_antenna_body(w_span, max(baseline_m, 1.0))
        else:
            body = {
                k: [float(x) for x in v[:3]]
                for k, v in body.items()
                if isinstance(v, (list, tuple))
            }
        return cls(
            mode=mode,
            baseline_m=baseline_m,
            roll_span_m=roll_span_m,
            roll_alt0_m=roll_alt0_m,
            antenna_body=body,
        )


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
    pitch = math.degrees(math.atan2(u2, horiz))
    return heading, pitch


def derive_roll_from_dual_master_height(
    ga1: GnssRaw,
    ga2: GnssRaw,
    roll_span_m: float,
    roll_alt0_m: float = 0.0,
) -> Optional[float]:
    """
    左右主天线椭球高差 → 横滚（度）。
    约定：左高右低为正横滚（符号需现场核对）。
    roll ≈ atan((alt_L - alt_R - alt0) / D)
    """
    if roll_span_m < MIN_ROLL_SPAN_M:
        return None
    if ga1.altitude is None or ga2.altitude is None:
        return None
    dh = float(ga1.altitude) - float(ga2.altitude) - float(roll_alt0_m)
    return math.degrees(math.atan2(dh, float(roll_span_m)))


def _pick_nmea_heading_pitch(
    ga1: Optional[GnssRaw],
    ga2: Optional[GnssRaw],
    ga1_live: bool,
    ga2_live: bool,
) -> Optional[Tuple[float, float, str]]:
    """优先左机 HDT/TRA，其次右机。"""
    for live, g, tag in (
        (ga1_live, ga1, "nmea_a1"),
        (ga2_live, ga2, "nmea_a2"),
    ):
        if not live or g is None or g.heading is None:
            continue
        return (
            normalize_heading_deg(float(g.heading)),
            float(g.pitch or 0.0),
            tag,
        )
    return None


def _collect_four_ecef(
    ga1: Optional[GnssRaw],
    ga2: Optional[GnssRaw],
) -> Optional[Dict[str, Tuple[float, float, float]]]:
    if ga1 is None or not ga1.has_antenna2:
        return None
    if ga2 is None or not ga2.has_antenna2:
        return None
    return {
        "a1": (ga1.ecef_x, ga1.ecef_y, ga1.ecef_z),
        "a2": (ga1.antenna2_ecef_x, ga1.antenna2_ecef_y, ga1.antenna2_ecef_z),
        "b1": (ga2.ecef_x, ga2.ecef_y, ga2.ecef_z),
        "b2": (ga2.antenna2_ecef_x, ga2.antenna2_ecef_y, ga2.antenna2_ecef_z),
    }


def build_gantry_antennas_payload(
    ga1: Optional[dict],
    ga2: Optional[dict],
) -> Optional[Dict[str, dict]]:
    """
    组装天线点供遥测/标定。
    - 两主点布局：a1=左主(电气)，柴油参考用右主 b1；a2/b2 仅在有副 GGA 时填充。
    - 旧四 GGA：左右各主+副。
    """
    if not ga1 or not ga1.get("is_live"):
        return None

    def _primary(g: dict) -> dict:
        return {
            "lat": float(g.get("lat", 0.0)),
            "lon": float(g.get("lon", 0.0)),
            "alt": float(g.get("alt", 0.0)),
            "ecef_x": float(g.get("ecef_x", 0.0)),
            "ecef_y": float(g.get("ecef_y", 0.0)),
            "ecef_z": float(g.get("ecef_z", 0.0)),
            "valid": True,
        }

    def _secondary(g: dict) -> dict:
        ok = bool(g.get("has_antenna2"))
        return {
            "lat": float(g.get("antenna2_lat", 0.0)),
            "lon": float(g.get("antenna2_lon", 0.0)),
            "alt": float(g.get("antenna2_alt", 0.0)),
            "ecef_x": float(g.get("antenna2_ecef_x", 0.0)),
            "ecef_y": float(g.get("antenna2_ecef_y", 0.0)),
            "ecef_z": float(g.get("antenna2_ecef_z", 0.0)),
            "valid": ok,
        }

    out: Dict[str, dict] = {
        "a1": {**_primary(ga1), "role": "elec", "receiver": "left"},
        "a2": {**_secondary(ga1), "role": "diesel", "receiver": "left"},
    }
    if ga2 and ga2.get("is_live"):
        out["b1"] = {**_primary(ga2), "role": "diesel", "receiver": "right"}
        out["b2"] = {**_secondary(ga2), "role": "diesel", "receiver": "right"}
        if not ga1.get("has_antenna2"):
            out["a2"] = {
                **_primary(ga2),
                "role": "diesel",
                "receiver": "right",
                "valid": True,
            }
    return out


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


def _pick_gantry_heading(
    hp_a: Tuple[float, float],
    hp_b: Tuple[float, float],
) -> float:
    """两机双 GGA 航向平均（最短弧）。"""
    a, b = hp_a[0], hp_b[0]
    mid = a + 0.5 * _circular_diff_deg(a, b)
    return normalize_heading_deg(mid)


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
    """大车级姿态（写入 gantry_a1 通道供 solver 使用）。"""
    mode = (cfg.mode or "hdt_tra_height_roll").strip().lower()

    # ---- UB482：HDT/TRA + 两主点高差横滚 ----
    if mode in ("hdt_tra_height_roll", "two_master", "evk"):
        hp = _pick_nmea_heading_pitch(ga1, ga2, ga1_live, ga2_live)
        if hp is None:
            return None
        heading, pitch, src = hp
        roll = 0.0
        roll_src = "0"
        if ga1_live and ga2_live and ga1 is not None and ga2 is not None:
            r = derive_roll_from_dual_master_height(
                ga1, ga2, cfg.roll_span_m, cfg.roll_alt0_m
            )
            if r is not None:
                roll = r
                roll_src = "height"
        return GantryAttitudeResult(
            heading, pitch, roll, f"{src}+roll_{roll_src}"
        )

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

    if mode == "four_antenna" and four_ecef is not None:
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

    if hp_a is not None:
        roll = _nmea_roll(ga1)
        return GantryAttitudeResult(
            normalize_heading_deg(hp_a[0]),
            hp_a[1],
            roll if roll is not None else 0.0,
            "nmea" if roll is not None else "dual_gga",
        )

    if ga1.heading is not None:
        return GantryAttitudeResult(
            normalize_heading_deg(float(ga1.heading or 0.0)),
            float(ga1.pitch or 0.0),
            float(ga1.roll or 0.0),
            "nmea",
        )
    return None
