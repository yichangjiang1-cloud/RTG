"""
大车姿态解算。

模式（由 vehicle 配置 gantry.attitude.mode 选择，仪表盘主通道跟随）：
  - four_antenna：四天线齐时 Kabsch 刚体定姿（需 antenna_body）
  - dual_receiver：左右双 GGA 基线航向/俯仰合成；横滚可借四天线刚体

旧模式 hdt_tra_height_roll / two_master / evk 读入时映射为 four_antenna。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

from src.models.gnss_data import GnssRaw
from src.solver.attitude import body_enu_to_nmea_euler
from src.solver.coordinate_transform import ecef_to_enu

# 双 GGA 基线低于此水平长度时不解算航向/俯仰
MIN_DUAL_GGA_BASELINE_M = 0.5
# 投轮胎用的分侧航向/俯仰对基线噪声敏感（0.6 m 基线上 1 cm 高差≈0.95°，经 ~21 m 杆臂放大 35 cm），
# 基线短于此值时该侧不参与分侧投影，回退整车主姿态
MIN_SIDE_TIRE_BASELINE_M = 2.0
# 两主点估横滚时，左右间距过小则放弃
MIN_ROLL_SPAN_M = 1.0

_LEGACY_TWO_MASTER_MODES = frozenset(
    {"hdt_tra_height_roll", "two_master", "evk", "nmea_height"}
)


def normalize_attitude_mode(mode: Optional[str]) -> str:
    """归一化为 four_antenna | dual_receiver。"""
    m = (mode or "").strip().lower()
    if m in ("dual_receiver", "dual"):
        return "dual_receiver"
    if m in _LEGACY_TWO_MASTER_MODES:
        return "four_antenna"
    if m == "four_antenna":
        return "four_antenna"
    return "four_antenna"


def normalize_tire_attitude(mode: Optional[str]) -> str:
    """归一化为 per_side | rigid。仅显式写 per_side 才开启，缺省/未知值一律 rigid。"""
    m = (mode or "").strip().lower()
    if m == "per_side":
        return "per_side"
    return "rigid"


@dataclass
class GantryAttitudeConfig:
    """来自 vehicle_params.yaml → gantry.attitude"""

    # four_antenna | dual_receiver
    mode: str = "four_antenna"
    # per_side：电气/动力各用本侧双 GGA 航向俯仰；rigid：整车一套姿态（缺省）
    tire_attitude: str = "rigid"
    # 分侧原始双 GGA 俯仰的静态零偏（度）：{"elec": x, "diesel": y}；缺省则该侧用整车俯仰
    side_pitch_zero_deg: Dict[str, float] = field(default_factory=dict)
    baseline_m: float = 1.0  # 前后定向基线长（四天线安装坐标参考）
    # 已废弃：横滚间距改为由两主 GGA 实时计算；保留字段仅兼容旧配置/四天线参考布局
    roll_span_m: float = 0.0
    roll_alt0_m: float = 0.0  # 车体水平时左右主天线高差零点（安装高度差）
    antenna_body: Dict[str, List[float]] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, block: Optional[dict], w_span: float = 0.0) -> "GantryAttitudeConfig":
        if not isinstance(block, dict):
            block = {}
        _ = w_span  # 已废弃，不再作为横滚间距
        mode = normalize_attitude_mode(str(block.get("mode", "four_antenna")))
        tire_attitude = normalize_tire_attitude(block.get("tire_attitude"))
        baseline_m = float(block.get("baseline_m", 1.0))
        roll_span_m = 0.0
        roll_alt0_m = float(block.get("roll_alt0_m", 0.0))
        body = block.get("antenna_body")
        if not isinstance(body, dict) or len(body) < 4:
            span_ref = roll_span_m if roll_span_m > 1.0 else max(baseline_m, 1.0)
            body = default_antenna_body(span_ref, max(baseline_m, 1.0))
        else:
            body = {
                k: [float(x) for x in v[:3]]
                for k, v in body.items()
                if isinstance(v, (list, tuple))
            }
        zeros: Dict[str, float] = {}
        raw_zero = block.get("side_pitch_zero_deg")
        if isinstance(raw_zero, dict):
            for side in ("elec", "diesel"):
                if raw_zero.get(side) is not None:
                    zeros[side] = float(raw_zero[side])
        return cls(
            mode=mode,
            tire_attitude=tire_attitude,
            side_pitch_zero_deg=zeros,
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
    # False：仅有 HDT 航向，无 PASHR/TRA 俯仰横滚（勿把缺失当成 0°）
    pitch_roll_valid: bool = True

    def to_dict(self) -> dict:
        return {
            "heading": self.heading_deg,
            "pitch": self.pitch_deg if self.pitch_roll_valid else None,
            "roll": self.roll_deg if self.pitch_roll_valid else None,
            "source": self.source,
            "pitch_roll_valid": self.pitch_roll_valid,
            "valid": True,
        }


@dataclass
class GantryAttitudeCompare:
    """
    大车双路姿态：
      - primary 严格跟随配置 mode（four_antenna / dual_receiver）
      - 所选模式无解时回退另一路
      - four_antenna / dual_receiver 两路并行供对比
      - elec_dual / diesel_dual：分侧双 GGA 航向俯仰（投轮胎用）
      - shared_roll / synthetic_heading：共用横滚与两侧合成航向
    """

    primary: Optional[GantryAttitudeResult]
    four_antenna: Optional[GantryAttitudeResult]
    dual_receiver: Optional[GantryAttitudeResult]
    delta: Optional[Dict[str, float]]
    four_ready: bool
    primary_method: str = "four_antenna"  # four_antenna | dual_receiver
    nmea_height: Optional[GantryAttitudeResult] = None  # 已废弃，恒 None
    nmea: Optional[GantryAttitudeResult] = None  # 优选一侧 PASHR/HDT（兼容）
    nmea_a1: Optional[GantryAttitudeResult] = None  # 左机 gantry_a1 原值
    nmea_a2: Optional[GantryAttitudeResult] = None  # 右机 gantry_a2 原值
    a1_dual: Optional[GantryAttitudeResult] = None
    a2_dual: Optional[GantryAttitudeResult] = None
    elec_dual: Optional[GantryAttitudeResult] = None
    diesel_dual: Optional[GantryAttitudeResult] = None
    shared_roll: Optional[float] = None
    shared_roll_source: str = "0"
    synthetic_heading: Optional[float] = None
    synthetic_pitch: Optional[float] = None
    tire_attitude: str = "per_side"

    def to_dict(self) -> dict:
        delta = self.delta
        other = (
            self.dual_receiver
            if self.primary_method == "four_antenna"
            else self.four_antenna
        )
        if delta is None and self.primary is not None and other is not None:
            delta = _attitude_delta(self.primary, other)

        def _side(r: Optional[GantryAttitudeResult], roll: Optional[float]) -> dict:
            """valid 表示确实拿到该侧双 GGA 分侧姿态；否则下游按整车主姿态回退。"""
            if r is None:
                return {
                    "heading": None,
                    "pitch": None,
                    "pitch_raw": None,
                    "roll": roll,
                    "baseline_m": None,
                    "source": "",
                    "valid": False,
                }
            return {
                "heading": r.heading_deg,
                "pitch": r.pitch_deg,
                "pitch_raw": getattr(r, "pitch_raw_deg", r.pitch_deg),
                "roll": roll,
                "baseline_m": getattr(r, "baseline_m", None),
                "source": r.source,
                "valid": True,
            }

        return {
            "primary": self.primary.to_dict() if self.primary else None,
            "four_antenna": self.four_antenna.to_dict() if self.four_antenna else None,
            "dual_receiver": (
                self.dual_receiver.to_dict() if self.dual_receiver else None
            ),
            "nmea_height": None,
            "nmea": self.nmea.to_dict() if self.nmea else None,
            "nmea_a1": self.nmea_a1.to_dict() if self.nmea_a1 else None,
            "nmea_a2": self.nmea_a2.to_dict() if self.nmea_a2 else None,
            "delta": delta,
            "four_ready": self.four_ready,
            "primary_method": self.primary_method,
            "configured_mode": self.primary_method,
            "a1_dual": _side(self.a1_dual, self.shared_roll),
            "a2_dual": _side(self.a2_dual, self.shared_roll),
            "elec_dual": _side(self.elec_dual, self.shared_roll),
            "diesel_dual": _side(self.diesel_dual, self.shared_roll),
            "shared_roll": self.shared_roll,
            "shared_roll_source": self.shared_roll_source,
            "synthetic_heading": self.synthetic_heading,
            "synthetic_pitch": self.synthetic_pitch,
            "tire_attitude": self.tire_attitude,
        }


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


def horizontal_distance_master_gga(ga1: GnssRaw, ga2: GnssRaw) -> Optional[float]:
    """左右主天线水平距离（米），由两主 GGA ECEF 直接计算。"""
    if (
        ga1.ecef_x == 0.0 and ga1.ecef_y == 0.0 and ga1.ecef_z == 0.0
    ) or (
        ga2.ecef_x == 0.0 and ga2.ecef_y == 0.0 and ga2.ecef_z == 0.0
    ):
        return None
    if ga1.latitude is None or ga1.longitude is None or ga1.altitude is None:
        return None
    e, n, _u = ecef_to_enu(
        ga2.ecef_x, ga2.ecef_y, ga2.ecef_z,
        float(ga1.latitude), float(ga1.longitude), float(ga1.altitude),
    )
    horiz = math.hypot(e, n)
    if horiz < MIN_ROLL_SPAN_M:
        return None
    return horiz


def derive_roll_from_dual_master_height(
    ga1: GnssRaw,
    ga2: GnssRaw,
    roll_span_m: float = 0.0,
    roll_alt0_m: float = 0.0,
) -> Optional[float]:
    """
    左右主 GGA → 横滚（度）。

    水平间距 D 优先由两主 GGA ECEF 实时计算；仅当 GGA 间距不可用且
    配置了 roll_span_m（兼容旧配置）时才用手填值。
    高差用 ENU 的 U（右相对左），再减安装零点 roll_alt0。
    约定：左高右低为正横滚（符号需现场核对）。
    roll ≈ atan((u - alt0) / D)
    """
    if ga1.latitude is None or ga1.longitude is None or ga1.altitude is None:
        return None
    if ga2.ecef_x == 0.0 and ga2.ecef_y == 0.0 and ga2.ecef_z == 0.0:
        return None

    e, n, u = ecef_to_enu(
        ga2.ecef_x, ga2.ecef_y, ga2.ecef_z,
        float(ga1.latitude), float(ga1.longitude), float(ga1.altitude),
    )
    # 左相对右的高差：ga1 为原点时 u 为右相对左的高度 → 取 -u 得左减右
    dh = -float(u) - float(roll_alt0_m)

    horiz = math.hypot(e, n)
    if horiz >= MIN_ROLL_SPAN_M:
        d = horiz
    elif float(roll_span_m) >= MIN_ROLL_SPAN_M:
        d = float(roll_span_m)
    else:
        return None
    return math.degrees(math.atan2(dh, d))


def _pick_nmea_heading_pitch(
    ga1: Optional[GnssRaw],
    ga2: Optional[GnssRaw],
    ga1_live: bool,
    ga2_live: bool,
) -> Optional[Tuple[float, float, str]]:
    """从有 HDT/TRA 的一侧取航向/俯仰（不假设必在左机；混接时通常为 EVK 侧）。"""
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


_nmea_att_sticky_tag: Optional[str] = None


def _nmea_pr_score(g: GnssRaw) -> float:
    return abs(float(g.pitch or 0.0)) + abs(float(g.roll or 0.0))


def _raw_nmea_from_gnss(
    g: Optional[GnssRaw],
    live: bool,
    tag: str,
) -> Optional[GantryAttitudeResult]:
    """单台接收机 NMEA 姿态原值（有航向才出；俯仰/横滚可缺）。"""
    if not live or g is None or g.heading is None:
        return None
    pr_ok = g.pitch is not None and g.roll is not None
    return GantryAttitudeResult(
        heading_deg=normalize_heading_deg(float(g.heading)),
        pitch_deg=float(g.pitch) if pr_ok else 0.0,
        roll_deg=float(g.roll) if pr_ok else 0.0,
        source=tag,
        pitch_roll_valid=pr_ok,
    )


def _pick_nmea_attitude(
    ga1: Optional[GnssRaw],
    ga2: Optional[GnssRaw],
    ga1_live: bool,
    ga2_live: bool,
) -> Optional[GantryAttitudeResult]:
    """接收机姿态原值，不经四天线刚体。

    优先取同一台机上带俯仰+横滚的完整句（PASHR/TRA/HPR）。
    两侧都有完整值时取 |P|+|R| 更大的一侧（避免 a1 常年 0° 压过 a2 真实双天线姿态），
    并用 sticky 减少帧间来回切源。
    """
    global _nmea_att_sticky_tag

    full: list[tuple[GnssRaw, str]] = []
    heading_only: list[tuple[GnssRaw, str]] = []
    for live, g, tag in (
        (ga1_live, ga1, "nmea_a1"),
        (ga2_live, ga2, "nmea_a2"),
    ):
        if not live or g is None or g.heading is None:
            continue
        if g.pitch is not None and g.roll is not None:
            full.append((g, tag))
        else:
            heading_only.append((g, tag))

    if full:
        best = max(full, key=lambda it: _nmea_pr_score(it[0]))
        chosen = best
        if _nmea_att_sticky_tag:
            sticky = next((it for it in full if it[1] == _nmea_att_sticky_tag), None)
            if sticky is not None:
                # sticky 仍可用，且不比最优差超过 0.2° 时保持，避免噪声切源
                if _nmea_pr_score(best[0]) - _nmea_pr_score(sticky[0]) < 0.2:
                    chosen = sticky
        _nmea_att_sticky_tag = chosen[1]
        g, tag = chosen
        return GantryAttitudeResult(
            heading_deg=normalize_heading_deg(float(g.heading)),
            pitch_deg=float(g.pitch),
            roll_deg=float(g.roll),
            source=tag,
            pitch_roll_valid=True,
        )

    _nmea_att_sticky_tag = None
    if heading_only:
        g, tag = heading_only[0]
        return GantryAttitudeResult(
            heading_deg=normalize_heading_deg(float(g.heading)),
            pitch_deg=0.0,
            roll_deg=0.0,
            source=tag,
            pitch_roll_valid=False,
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

    roll, _pitch_x, _heading_x = body_enu_to_nmea_euler(r_be)
    # 航向、俯仰沿天线摆放方向：下水主天线（a1、b1）→ 上水从天线（a2、b2）。
    # 不读安装表的 +X。表的 +X 在「一侧一台接收机」时会落到横梁上，航向会变成 138°。
    down = (enu[0] + enu[2]) * 0.5
    up = (enu[1] + enu[3]) * 0.5
    delta = up - down
    horiz = float(math.hypot(delta[0], delta[1]))
    span = float(math.hypot(horiz, delta[2]))
    if horiz >= 1.0 and span > 1e-6:
        heading = math.degrees(math.atan2(float(delta[0]), float(delta[1]))) % 360.0
        if heading >= 359.95:
            heading = 0.0
        pitch = math.degrees(math.asin(max(-1.0, min(1.0, float(delta[2]) / span))))
    else:
        heading = _heading_x
        pitch = _pitch_x
    return GantryAttitudeResult(
        heading_deg=normalize_heading_deg(heading),
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


def compute_nmea_height_roll_attitude(
    ga1: Optional[GnssRaw],
    ga2: Optional[GnssRaw],
    ga1_live: bool,
    ga2_live: bool,
    cfg: GantryAttitudeConfig,
) -> Optional[GantryAttitudeResult]:
    """
    UB482 / 混接姿态对比路：
    航向/俯仰 ← 任一侧 HDT/TRA/HPR（通常 EVK）；
    横滚 ← 左右主 GGA 高差 / 水平间距（间距由 GGA 计算）。
    """
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
            roll_src = "gga"
    return GantryAttitudeResult(
        heading, pitch, roll, f"{src}+roll_{roll_src}"
    )


def compute_four_antenna_attitude_from_gga(
    ga1: Optional[GnssRaw],
    ga2: Optional[GnssRaw],
    ga2_live: bool,
    cfg: GantryAttitudeConfig,
    ref_lat: float,
    ref_lon: float,
    ref_alt: float,
) -> Optional[GantryAttitudeResult]:
    """四 GGA 刚体定姿（a1/a2/b1/b2 + 安装坐标 Kabsch）。"""
    four_ecef = _collect_four_ecef(ga1, ga2 if ga2_live else None)
    if four_ecef is None:
        return None
    body = cfg.antenna_body
    if not body or len(body) < 4:
        return None
    return solve_four_antenna_attitude(
        four_ecef, body, ref_lat, ref_lon, ref_alt
    )


def _attitude_delta(
    primary: GantryAttitudeResult,
    other: GantryAttitudeResult,
) -> Dict[str, float]:
    """对比路 − 主通道 的角差（heading 用最短弧）。"""
    return {
        "heading_deg": _circular_diff_deg(primary.heading_deg, other.heading_deg),
        "pitch_deg": other.pitch_deg - primary.pitch_deg,
        "roll_deg": other.roll_deg - primary.roll_deg,
    }



def compute_dual_receiver_attitude(
    ga1: Optional[GnssRaw],
    ga2: Optional[GnssRaw],
    ga1_live: bool,
    ga2_live: bool,
    cfg: GantryAttitudeConfig,
    ref_lat: float,
    ref_lon: float,
    ref_alt: float,
) -> Optional[GantryAttitudeResult]:
    """双接收机：左右双 GGA 航向/俯仰；横滚优先四天线刚体。"""
    if not ga1_live or ga1 is None:
        if ga2_live and ga2 is not None:
            hp_b = derive_heading_pitch_from_dual_gga(ga2)
            if hp_b is not None:
                return GantryAttitudeResult(
                    normalize_heading_deg(hp_b[0]),
                    hp_b[1],
                    float(ga2.roll or 0.0),
                    "dual_gga_a2",
                )
        return None

    hp_a = derive_heading_pitch_from_dual_gga(ga1)
    ref_for_b = hp_a[0] if hp_a is not None else ga1.heading
    hp_b = (
        derive_heading_pitch_from_dual_gga(ga2, ref_heading=ref_for_b)
        if ga2_live and ga2
        else None
    )
    four_ecef = _collect_four_ecef(ga1, ga2 if ga2_live else None)

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
        roll = 0.0
        source = "dual_gga_a1"
        if four_ecef is not None:
            rigid = solve_four_antenna_attitude(
                four_ecef, cfg.antenna_body, ref_lat, ref_lon, ref_alt
            )
            if rigid is not None:
                roll = rigid.roll_deg
                source = "dual_gga_a1+4ant_roll"
        return GantryAttitudeResult(
            normalize_heading_deg(hp_a[0]), hp_a[1], roll, source
        )
    return None


def _side_dual_from_gga(
    gnss: Optional[GnssRaw],
    live: bool,
    tag: str,
    primary_down: bool = True,
) -> Optional[GantryAttitudeResult]:
    """
    单侧双 GGA → 航向/俯仰（横滚留空，由 shared_roll 共用）。

    基线短于 MIN_SIDE_TIRE_BASELINE_M 返回 None（回退整车姿态）。
    航向统一为"下游端→上游端"（与 SolverNode._upstream_heading 一致）：
      primary_down=True  主天线在下游，主→副即下游→上游，直接用；
      primary_down=False 主天线在上游（receiver_layout 的 *_primary_end: up），
                         主→副是上游→下游，按配置反向：航向 +180°、俯仰取反。
    这是按配置换算，不是对安装错误的自动纠正。
    """
    if not live or gnss is None or not gnss.has_antenna2:
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
    if horiz < MIN_SIDE_TIRE_BASELINE_M:
        return None
    heading = normalize_heading_deg(math.degrees(math.atan2(e2, n2)))
    pitch = math.degrees(math.atan2(u2, horiz))
    if not primary_down:
        heading = normalize_heading_deg(heading + 180.0)
        pitch = -pitch
    out = GantryAttitudeResult(heading, pitch, 0.0, tag)
    out.baseline_m = horiz  # type: ignore[attr-defined]
    return out


def _pair_mount_pitch_deg(
    body: Dict[str, List[float]],
    first: str,
    second: str,
    primary_down: bool = True,
) -> Optional[float]:
    """
    车体系中一对天线（主→副）基线自身的俯仰角（安装几何，不含大车姿态）。
    与 _side_dual_from_gga 的符号约定一致：primary_down=False 时取反。
    """
    if not body or first not in body or second not in body:
        return None
    a = body[first]
    b = body[second]
    dx = float(b[0]) - float(a[0])
    dy = float(b[1]) - float(a[1])
    dz = float(b[2]) - float(a[2])
    horiz = math.hypot(dx, dy)
    if horiz < 1e-6:
        return None
    m = math.degrees(math.atan2(dz, horiz))
    return m if primary_down else -m


def _calibrate_side_pitch(
    side: Optional[GantryAttitudeResult],
    shared_pitch: Optional[float],
    mount_pitch: Optional[float] = None,
) -> Optional[GantryAttitudeResult]:
    """
    分侧俯仰：本侧双 GGA 测得的基线俯仰 − 本侧安装基线俯仰（车体系模板）= 大车俯仰。
    不再取四天线中点平均。模板缺失时退回整车俯仰。原始侧俯仰保存在 pitch_raw_deg 供诊断。
    """
    if side is None:
        return None
    raw = side.pitch_deg
    if mount_pitch is not None:
        pitch = float(raw) - float(mount_pitch)
    elif shared_pitch is not None:
        pitch = float(shared_pitch)
    else:
        pitch = raw
    out = GantryAttitudeResult(side.heading_deg, pitch, side.roll_deg, side.source)
    out.pitch_raw_deg = raw  # type: ignore[attr-defined]
    out.mount_pitch_deg = mount_pitch  # type: ignore[attr-defined]
    out.baseline_m = getattr(side, "baseline_m", None)  # type: ignore[attr-defined]
    return out


def _compute_shared_roll(
    ga1: Optional[GnssRaw],
    ga2: Optional[GnssRaw],
    ga1_live: bool,
    ga2_live: bool,
    cfg: GantryAttitudeConfig,
    four: Optional[GantryAttitudeResult],
) -> Tuple[float, str]:
    """
    共用横滚：优先四天线刚体（与杆臂标定同一口径）；四天线不可用时退回左右主天线高差，
    再不行为 0。高差零点 roll_alt0_m 只影响后一种回退路径。
    """
    if four is not None:
        return float(four.roll_deg), "four_antenna"
    if ga1_live and ga2_live and ga1 is not None and ga2 is not None:
        r = derive_roll_from_dual_master_height(
            ga1, ga2, cfg.roll_span_m, cfg.roll_alt0_m
        )
        if r is not None:
            return float(r), "dual_master_height"
    return 0.0, "0"


def _synthetic_heading(
    elec: Optional[GantryAttitudeResult],
    diesel: Optional[GantryAttitudeResult],
    primary: Optional[GantryAttitudeResult],
) -> Optional[float]:
    """两侧双 GGA 航向圆周平均；单侧缺失则用有的一侧；都无则用 primary。"""
    if elec is not None and diesel is not None:
        return normalize_heading_deg(
            _pick_gantry_heading(
                (elec.heading_deg, elec.pitch_deg),
                (diesel.heading_deg, diesel.pitch_deg),
            )
        )
    if elec is not None:
        return normalize_heading_deg(elec.heading_deg)
    if diesel is not None:
        return normalize_heading_deg(diesel.heading_deg)
    if primary is not None:
        return normalize_heading_deg(primary.heading_deg)
    return None


def _synthetic_pitch(
    elec: Optional[GantryAttitudeResult],
    diesel: Optional[GantryAttitudeResult],
    primary: Optional[GantryAttitudeResult],
) -> Optional[float]:
    """两侧分侧俯仰（已扣安装俯仰）取平均；单侧缺失用有的一侧；都无则用 primary。"""
    if elec is not None and diesel is not None:
        return 0.5 * (float(elec.pitch_deg) + float(diesel.pitch_deg))
    if elec is not None:
        return float(elec.pitch_deg)
    if diesel is not None:
        return float(diesel.pitch_deg)
    if primary is not None:
        return float(primary.pitch_deg)
    return None


def compute_gantry_attitude_compare(
    ga1: Optional[GnssRaw],
    ga2: Optional[GnssRaw],
    ga1_live: bool,
    ga2_live: bool,
    cfg: GantryAttitudeConfig,
    ref_lat: float,
    ref_lon: float,
    ref_alt: float,
    elec_left: bool = True,
    a1_primary_down: bool = True,
    a2_primary_down: bool = True,
) -> GantryAttitudeCompare:
    """并行解算两路姿态；primary 跟随 cfg.mode；附带分侧双 GGA 与合成航向。"""
    mode = normalize_attitude_mode(cfg.mode)
    tire_attitude = normalize_tire_attitude(cfg.tire_attitude)
    four_ready = (
        ga1 is not None
        and ga1.has_antenna2
        and ga2_live
        and ga2 is not None
        and ga2.has_antenna2
    )
    four = None
    if four_ready:
        four = compute_four_antenna_attitude_from_gga(
            ga1, ga2, ga2_live, cfg, ref_lat, ref_lon, ref_alt
        )
    dual = compute_dual_receiver_attitude(
        ga1, ga2, ga1_live, ga2_live, cfg, ref_lat, ref_lon, ref_alt
    )

    if mode == "four_antenna":
        if four is not None:
            primary, primary_method = four, "four_antenna"
        else:
            primary, primary_method = dual, "dual_receiver"
    else:
        if dual is not None:
            primary, primary_method = dual, "dual_receiver"
        else:
            primary, primary_method = four, "four_antenna"

    other = dual if primary_method == "four_antenna" else four
    delta = (
        _attitude_delta(primary, other)
        if primary is not None and other is not None
        else None
    )

    a1_dual = _side_dual_from_gga(
        ga1, ga1_live, "dual_gga_a1", primary_down=a1_primary_down
    )
    a2_dual = _side_dual_from_gga(
        ga2, ga2_live, "dual_gga_a2", primary_down=a2_primary_down
    )
    body = cfg.antenna_body or {}
    # 左接收机(a)、右接收机(b) 各自的安装基线俯仰；用来扣掉模板里的高度差
    a_mount = _pair_mount_pitch_deg(body, "a1", "a2", a1_primary_down)
    b_mount = _pair_mount_pitch_deg(body, "b1", "b2", a2_primary_down)
    if elec_left:
        elec_dual, diesel_dual = a1_dual, a2_dual
        elec_mount, diesel_mount = a_mount, b_mount
    else:
        elec_dual, diesel_dual = a2_dual, a1_dual
        elec_mount, diesel_mount = b_mount, a_mount
    shared_pitch = primary.pitch_deg if primary is not None else None
    elec_dual = _calibrate_side_pitch(elec_dual, shared_pitch, elec_mount)
    diesel_dual = _calibrate_side_pitch(diesel_dual, shared_pitch, diesel_mount)

    shared_roll, shared_roll_source = _compute_shared_roll(
        ga1, ga2, ga1_live, ga2_live, cfg, four
    )
    synth = _synthetic_heading(elec_dual, diesel_dual, primary)
    synth_pitch = _synthetic_pitch(elec_dual, diesel_dual, primary)

    return GantryAttitudeCompare(
        primary=primary,
        four_antenna=four,
        dual_receiver=dual,
        delta=delta,
        four_ready=four_ready,
        primary_method=primary_method or mode,
        nmea_height=None,
        nmea=_pick_nmea_attitude(ga1, ga2, ga1_live, ga2_live),
        nmea_a1=_raw_nmea_from_gnss(ga1, ga1_live, "nmea_a1"),
        nmea_a2=_raw_nmea_from_gnss(ga2, ga2_live, "nmea_a2"),
        a1_dual=a1_dual,
        a2_dual=a2_dual,
        elec_dual=elec_dual,
        diesel_dual=diesel_dual,
        shared_roll=shared_roll,
        shared_roll_source=shared_roll_source,
        synthetic_heading=synth,
        synthetic_pitch=synth_pitch,
        tire_attitude=tire_attitude,
    )


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
    """按配置 mode 解算大车姿态（回退路径与 compare 一致）。"""
    return compute_gantry_attitude_compare(
        ga1, ga2, ga1_live, ga2_live, cfg, ref_lat, ref_lon, ref_alt
    ).primary
