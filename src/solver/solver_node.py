"""
解算节点：solver_node
订阅 fusion_node 输出的融合状态，执行完整解算流程：
  1. 自动识别当前堆场
  2. 坐标系变换（WGS84 → LYCS）
  3. L-Point 刚体变换（大车 + 小车）
  4. B 车矩阵修正（如已完成校准）
  5. 输出 PositionResult 到 EventBus
"""
from __future__ import annotations

import logging
import math
import time
import numpy as np
from typing import Optional

from src.core.event_bus import get_bus, EventBus
from src.models.positioning import (
    PositionResult, GantryResult, TrolleyResult, DataSource
)
from src.models.gnss_data import FixQuality
from src.solver.lpoint_solver import LPointSolver
from src.solver.yard_manager import YardManager
from src.solver.calibration import BVehicleCalibrator
from src.solver.calibration_context import CalibrationContextManager
from src.solver.bay_residual import (
    interpolate_residual,
)
from src.solver.coordinate_transform import ecef_to_enu, ecef_to_wgs84
from src.solver.yard_geometry import (
    diesel_bays,
    applied_lane_shifts,
    bracket_bay_line,
    deviation_adjust_along_line,
    deviation_adjust_to_dx,
    electrical_bays,
    en_meters,
    fit_bay_line,
    lane_deviation_m,
    line_deviation_m,
    normalize_boundary,
    parallel_reference_x,
    polygon_centroid,
    resolve_bay_band,
    resolve_y_axis,
)
from src.solver.side_layout import (
    antenna_ecef_from_dict,
    parse_side_layout,
    split_sides,
)
from src.fusion.gantry_attitude import normalize_tire_attitude
from src.fusion.gnss_pair import (
    DEFAULT_MAX_PAIR_SKEW_S,
    DEFAULT_TIRE_PAIR_HOLD_S,
    apply_pair_skew,
    merge_held_tires,
)
from src.solver.motion_speed import (
    DEADBAND_MPS,
    MIN_DISPLACEMENT_M,
    PAIR_DT,
    MotionSpeedState,
    hold_gnss_speed,
    shortest_arc_deg,
    update_motion_speed,
    relative_planar_speed,
    use_gantry_speed_sample,
)
from src.solver.gantry_frame import lycs_to_gantry_body
from src.utils.config_loader import get_config

logger = logging.getLogger(__name__)


def _trolley_origin_from_cfg(cfg) -> tuple[str, Optional[tuple[float, float, float]]]:
    """读取小车零点配置：auto 或 manual + origin_body（大车车体系）。"""
    block = cfg.get("trolley") if isinstance(cfg.get("trolley"), dict) else {}
    mode = str(block.get("origin_mode", "auto"))
    ob = block.get("origin_body")
    if mode == "manual" and isinstance(ob, (list, tuple)) and len(ob) >= 3:
        return "manual", (float(ob[0]), float(ob[1]), float(ob[2]))
    return "auto", None


def _read_named_arm(cfg, flat_key: str):
    """读取一套杆臂。缺字段或单 l_arm 都返回 None，不参与解算。"""
    gantry_block = cfg.get("gantry") if isinstance(cfg.get("gantry"), dict) else {}
    arm = cfg.get(f"gantry.{flat_key}") or gantry_block.get(flat_key) or cfg.get(flat_key)
    if not arm or len(arm) < 3:
        return None
    return [float(x) for x in arm[:3]]


def _arm_len(arm) -> float:
    if not arm or len(arm) < 3:
        return 0.0
    return math.hypot(float(arm[0]), float(arm[1]), float(arm[2]))


def _fmt_tire_x(pt) -> str:
    """日志用：只打轮胎的堆场 X，缺值时打 None。"""
    return "None" if not pt else f"{pt[0]:.3f}"


def _end_arm(cfg, end_key: str, side_key: str) -> list:
    """一端单独标定过就用这一端。接近 0 的结果不当成杆臂，仍用该侧主天线那一套。"""
    side = _read_named_arm(cfg, side_key) or [0.0, 0.0, 0.0]
    end = _read_named_arm(cfg, end_key)
    if end is not None and _arm_len(end) >= 0.05:
        return end
    return side


def _gantry_l_arms_from_cfg(cfg) -> tuple[list, list, list, list]:
    """电气下游、电气上游、动力下游、动力上游。缺省为零，不用旧的单杆臂。"""
    return (
        _end_arm(cfg, "l_arm_elec_down", "l_arm_elec"),
        _end_arm(cfg, "l_arm_elec_up", "l_arm_elec"),
        _end_arm(cfg, "l_arm_diesel_down", "l_arm_diesel"),
        _end_arm(cfg, "l_arm_diesel_up", "l_arm_diesel"),
    )


class SolverNode:
    """
    解算节点：将融合状态转换为可下发给 PLC 的最终定位结果。

    数据流：
      fusion_node → TOPIC_FUSED_STATE → SolverNode → TOPIC_POSITION_RESULT
    """

    def __init__(
        self,
        yard_manager: YardManager,
        calibration_ctx: Optional[CalibrationContextManager] = None,
        bus: Optional[EventBus] = None,
    ):
        cfg = get_config()
        self._bus = bus or get_bus()
        self._yard_manager = yard_manager
        self._calibration_ctx = calibration_ctx or CalibrationContextManager()

        # 四个轮胎各一根杆臂。没单独标定的一端先用该侧下游（主天线）那一套。
        elec_down, elec_up, diesel_down, diesel_up = _gantry_l_arms_from_cfg(cfg)
        self._solver_elec_down = LPointSolver(elec_down)
        self._solver_elec_up = LPointSolver(elec_up)
        self._solver_diesel_down = LPointSolver(diesel_down)
        self._solver_diesel_up = LPointSolver(diesel_up)

        self._offline_timeout_s = max(
            0.1, float(cfg.get("gnss.offline_timeout_s", 0.5))
        )
        self._max_pair_skew_s = max(
            0.05, float(cfg.get("gnss.max_pair_skew_s", DEFAULT_MAX_PAIR_SKEW_S))
        )
        self._tire_pair_hold_s = max(
            0.0, float(cfg.get("gnss.tire_pair_hold_s", DEFAULT_TIRE_PAIR_HOLD_S))
        )
        self._last_good_tires_ecef: Optional[dict] = None
        self._last_good_tires_at: float = 0.0
        self._require_rtk_fixed = bool(cfg.get("gnss.require_rtk_fixed", True))

        # 小车零点（大车车体系，锚定 L-Point，随大车移动）
        origin_mode, origin_body = _trolley_origin_from_cfg(cfg)
        self._trolley_origin_mode = origin_mode
        self._trolley_origin_body: Optional[tuple[float, float, float]] = origin_body
        self._trolley_pending_capture = False

        # B 车校准器（每个堆场独立）
        self._calibrators: dict[str, BVehicleCalibrator] = {}

        # 大车速度
        self._last_gantry_x: Optional[float] = None
        self._last_gantry_y: Optional[float] = None
        self._last_raw_gantry_x: Optional[float] = None
        self._last_raw_gantry_y: Optional[float] = None
        self._gantry_motion = MotionSpeedState()
        self._last_gantry_time: float = 0.0
        self._last_gantry_any_time: float = 0.0
        self._last_gantry_heading: float = 0.0
        self._last_speed_log: float = 0.0
        self._last_speed_log_time: float = 0.0
        self._last_upstream_heading: Optional[float] = None
        layout = cfg.get("receiver_layout") if isinstance(cfg.get("receiver_layout"), dict) else {}
        self._side = parse_side_layout(layout)
        att_block = cfg.get("gantry.attitude", {})
        if not isinstance(att_block, dict):
            gantry_block = cfg.get("gantry") if isinstance(cfg.get("gantry"), dict) else {}
            att_block = gantry_block.get("attitude") if isinstance(gantry_block.get("attitude"), dict) else {}
        # 主通道统一整车姿态（忽略配置里遗留的 per_side）
        self._tire_attitude = "rigid"
        self._last_elec_y: Optional[tuple[float, float]] = None
        self._last_diesel_y: Optional[tuple[float, float]] = None
        self._last_trolley_x: Optional[tuple[float, float]] = None

        # 小车相对速度历史 (t, rel_x, rel_y)
        self._trolley_rel_hist: list[tuple[float, float, float]] = []
        self._trolley_last_c2: Optional[tuple[float, float, float]] = None

        # 订阅融合状态
        self._bus.subscribe(EventBus.TOPIC_FUSED_STATE, self._on_fused_state)
        self._yard_missing_logged = False
        self._solve_place = ""
        logger.info("SolverNode 初始化完成 tire_attitude=%s", self._tire_attitude)

    # ------------------------------------------------------------------
    # 回调：处理融合状态
    # ------------------------------------------------------------------
    def _on_fused_state(self, state: dict) -> None:
        """
        接收来自 fusion_node 的融合状态字典。

        期望字段：
          gantry_a1: {ecef_x, ecef_y, ecef_z, roll, pitch, heading, timestamp}
          gantry_a2: {ecef_x, ecef_y, ecef_z, roll, pitch, heading}
          trolley:   {ecef_x, ecef_y, ecef_z, roll, pitch, heading, timestamp}
          source:    DataSource 枚举值
        """
        try:
            result = self._solve(state)
            if result:
                self._bus.publish(EventBus.TOPIC_POSITION_RESULT, result)
        except Exception as exc:
            logger.exception(f"解算异常: {exc}")

    def _note_solve_place(self, place: str) -> None:
        """场内/场外只在状态变化时记一条，避免每帧刷屏。"""
        if place == self._solve_place:
            return
        previous = self._solve_place or "启动"
        self._solve_place = place
        if place.startswith("场外"):
            logger.warning("解算位置: %s → %s", previous, place)
        else:
            logger.info("解算位置: %s → %s", previous, place)

    def _sync_side(self, block: dict) -> None:
        layout = block.get("receiver_layout")
        if isinstance(layout, dict):
            self._side = parse_side_layout(layout)

    def _sides(self, ga1, ga2, recv1: bool, recv2: bool):
        return split_sides(self._side, ga1, ga2, recv1, recv2)

    def _upstream_heading(self, ga1: dict, ga2: dict, recv1: bool, recv2: bool) -> Optional[float]:
        """航向从下水端两天线中点指向上水端两天线中点。"""
        elec, diesel, elec_live, diesel_live, elec_down, diesel_down = self._sides(
            ga1, ga2, recv1, recv2,
        )
        if not (elec_live and diesel_live):
            return self._last_upstream_heading
        up_e = antenna_ecef_from_dict(elec, not elec_down)
        down_e = antenna_ecef_from_dict(elec, elec_down)
        up_d = antenna_ecef_from_dict(diesel, not diesel_down)
        down_d = antenna_ecef_from_dict(diesel, diesel_down)
        if up_e is None or down_e is None or up_d is None or down_d is None:
            return self._last_upstream_heading
        up = tuple((up_e[i] + up_d[i]) * 0.5 for i in range(3))
        down = tuple((down_e[i] + down_d[i]) * 0.5 for i in range(3))
        lat, lon, alt = ecef_to_wgs84(*down)
        east, north, _up = ecef_to_enu(*up, lat, lon, alt)
        if math.hypot(east, north) < 1.0:
            return self._last_upstream_heading
        heading = math.degrees(math.atan2(east, north)) % 360.0
        if heading >= 359.95:
            heading = 0.0
        self._last_upstream_heading = heading
        return heading

    def _side_hpr(
        self,
        side_dual: Optional[dict],
        roll: float,
        pitch: float,
        heading: float,
        primary: Optional[dict],
    ) -> tuple[float, float, float]:
        """
        分侧：航向、俯仰取本侧双 GGA（已扣除模板安装俯仰）；横滚返回共用值。
        侧双 GGA 无效时航向回退整车 primary，俯仰回退整车俯仰，再回退共享 heading。
        """
        side_ok = (
            isinstance(side_dual, dict)
            and side_dual.get("valid", True)
            and side_dual.get("heading") is not None
        )
        if side_ok:
            side_heading = float(side_dual["heading"])
        elif isinstance(primary, dict) and primary.get("heading") is not None:
            side_heading = float(primary["heading"])
        else:
            side_heading = float(heading)
        side_pitch = side_dual.get("pitch") if side_ok else None
        side_pitch = float(pitch) if side_pitch is None else float(side_pitch)
        return side_heading, side_pitch, float(roll)

    def _four_tire_ecef(
        self,
        ga1,
        ga2,
        roll,
        pitch,
        heading,
        recv1,
        recv2,
        attitude_compare: Optional[dict] = None,
        tire_attitude: Optional[str] = None,
    ) -> dict:
        """每个轮胎用自己那一端的杆臂。per_side 时电气/动力各用本侧航向俯仰。"""
        elec, diesel, elec_live, diesel_live, elec_down, diesel_down = self._sides(
            ga1, ga2, recv1, recv2,
        )
        out = {"elec1": None, "elec2": None, "diesel1": None, "diesel2": None}
        mode = normalize_tire_attitude(
            tire_attitude if tire_attitude is not None else self._tire_attitude
        )
        cmp = attitude_compare if isinstance(attitude_compare, dict) else {}
        primary = cmp.get("primary") if isinstance(cmp.get("primary"), dict) else {}

        if mode == "per_side":
            he, pe, re = self._side_hpr(cmp.get("elec_dual"), roll, pitch, heading, primary)
            hd, pd, rd = self._side_hpr(cmp.get("diesel_dual"), roll, pitch, heading, primary)
        else:
            he = hd = float(heading)
            pe = pd = float(pitch)
            re = rd = float(roll)

        def solve(solver, g, live, primary_ant, h, p, r):
            if not live:
                return None
            ecef = antenna_ecef_from_dict(g, primary_ant)
            if ecef is None:
                return None
            return tuple(solver.solve(np.array(ecef), r, p, h))

        elec_primary = self._solver_elec_down if elec_down else self._solver_elec_up
        elec_secondary = self._solver_elec_up if elec_down else self._solver_elec_down
        diesel_primary = self._solver_diesel_down if diesel_down else self._solver_diesel_up
        diesel_secondary = self._solver_diesel_up if diesel_down else self._solver_diesel_down
        # 主通道：分侧航向俯仰（或 rigid 整车）+ 共用横滚
        out["elec1"] = solve(elec_primary, elec, elec_live, True, he, pe, re)
        out["elec2"] = solve(elec_secondary, elec, elec_live, False, he, pe, re)
        out["diesel1"] = solve(diesel_primary, diesel, diesel_live, True, hd, pd, rd)
        out["diesel2"] = solve(diesel_secondary, diesel, diesel_live, False, hd, pd, rd)
        # 对比：四天线 Kabsch 刚体整车 H/P/R（有解才算）
        four = cmp.get("four_antenna") if isinstance(cmp.get("four_antenna"), dict) else None
        if (
            four
            and four.get("valid", True)
            and four.get("heading") is not None
            and four.get("pitch") is not None
            and four.get("roll") is not None
        ):
            hr = float(four["heading"])
            pr = float(four["pitch"])
            rr = float(four["roll"])
            out["elec1_rigid"] = solve(elec_primary, elec, elec_live, True, hr, pr, rr)
            out["elec2_rigid"] = solve(elec_secondary, elec, elec_live, False, hr, pr, rr)
            out["diesel1_rigid"] = solve(diesel_primary, diesel, diesel_live, True, hr, pr, rr)
            out["diesel2_rigid"] = solve(diesel_secondary, diesel, diesel_live, False, hr, pr, rr)
        else:
            out["elec1_rigid"] = None
            out["elec2_rigid"] = None
            out["diesel1_rigid"] = None
            out["diesel2_rigid"] = None
        return out

    def _tire_centroid_wgs84(self, tires: dict):
        pts = [tires[k] for k in ("elec1", "elec2", "diesel1", "diesel2")]
        if any(p is None for p in pts):
            return None
        mean = tuple(sum(p[i] for p in pts) / 4.0 for i in range(3))
        return ecef_to_wgs84(*mean)

    def _recompute_centers(self, gantry: GantryResult) -> None:
        def mid(a, a_ok, b, b_ok):
            if not (a_ok and b_ok):
                return None
            return tuple((a[i] + b[i]) * 0.5 for i in range(3))

        elec = mid(gantry.tire_elec1, gantry.tire_elec1_valid, gantry.tire_elec2, gantry.tire_elec2_valid)
        diesel = mid(
            gantry.tire_diesel1, gantry.tire_diesel1_valid,
            gantry.tire_diesel2, gantry.tire_diesel2_valid,
        )
        gantry.a1_valid = elec is not None
        gantry.a2_valid = diesel is not None
        if elec is not None:
            gantry.center_elec_side, gantry.center_elec_y = elec[0], elec[1]
        if diesel is not None:
            gantry.center_engine_side, gantry.center_engine_y = diesel[0], diesel[1]
        ready = (
            gantry.tire_elec1_valid and gantry.tire_elec2_valid
            and gantry.tire_diesel1_valid and gantry.tire_diesel2_valid
        )
        if ready:
            xs = [gantry.tire_elec1, gantry.tire_elec2, gantry.tire_diesel1, gantry.tire_diesel2]
            gantry.lpoint_x = sum(p[0] for p in xs) / 4.0
            gantry.lpoint_y = sum(p[1] for p in xs) / 4.0
            gantry.lpoint_z = sum(p[2] for p in xs) / 4.0

    def _fill_center_geo(self, gantry: GantryResult, tires_ecef: dict) -> None:
        """两侧轮胎中点的 WGS84，供前端 RTG 采点。不依赖堆场 +Y。"""
        for side, lat_a, lon_a, alt_a, ok_flag in (
            ("elec", "center_elec_lat", "center_elec_lon", "center_elec_alt", "a1_valid"),
            ("diesel", "center_engine_lat", "center_engine_lon", "center_engine_alt", "a2_valid"),
        ):
            a = tires_ecef.get(f"{side}1")
            b = tires_ecef.get(f"{side}2")
            if a is None or b is None or not getattr(gantry, ok_flag):
                continue
            mid = tuple((a[i] + b[i]) * 0.5 for i in range(3))
            lat, lon, alt = ecef_to_wgs84(*mid)
            setattr(gantry, lat_a, float(lat))
            setattr(gantry, lon_a, float(lon))
            setattr(gantry, alt_a, float(alt))

    def _shift_tires(self, gantry: GantryResult, dx: float, dy: float, dz: float = 0.0) -> None:
        for name, flag in (
            ("tire_elec1", "tire_elec1_valid"),
            ("tire_elec2", "tire_elec2_valid"),
            ("tire_diesel1", "tire_diesel1_valid"),
            ("tire_diesel2", "tire_diesel2_valid"),
            ("tire_elec1_rigid", "tire_elec1_rigid_valid"),
            ("tire_elec2_rigid", "tire_elec2_rigid_valid"),
            ("tire_diesel1_rigid", "tire_diesel1_rigid_valid"),
            ("tire_diesel2_rigid", "tire_diesel2_rigid_valid"),
        ):
            if not getattr(gantry, flag):
                continue
            x, y, z = getattr(gantry, name)
            setattr(gantry, name, (x + dx, y + dy, z + dz))
        self._recompute_centers(gantry)

    def _fill_lane(self, gantry: GantryResult, transform, yard_id: str) -> None:
        gantry.lane_mm = [0, 0, 0, 0]
        gantry.leg_offsets_rigid = [0.0, 0.0, 0.0, 0.0]
        if transform is None or not transform.axis_ready:
            return
        meta = self._yard_manager.get_yard_meta(yard_id or "") or {}
        ring = normalize_boundary(meta.get("boundary"))
        if len(ring) < 3:
            return
        verts = [
            transform.wgs84_to_lycs(lat, lon, float(transform.origin_alt))[:2]
            for lat, lon in ring
        ]
        center = polygon_centroid(verts)
        if center is None:
            return
        elec_pts = self._side_bay_xy(transform, yard_id, "elec")
        diesel_pts = self._side_bay_xy(transform, yard_id, "diesel")
        x_elec = self._parallel_fallback_x(yard_id, elec_pts)
        tires = (
            (gantry.tire_elec1, bool(gantry.tire_elec1_valid), False),
            (gantry.tire_elec2, bool(gantry.tire_elec2_valid), False),
            (gantry.tire_diesel1, bool(gantry.tire_diesel1_valid), True),
            (gantry.tire_diesel2, bool(gantry.tire_diesel2_valid), True),
        )
        mm = []
        for tire, ok, on_diesel in tires:
            if not ok:
                mm.append(0)
                continue
            meters = self._tire_lane_m(
                tire[0], tire[1], on_diesel, elec_pts, diesel_pts, center, x_elec,
            )
            mm.append(int(round(meters * 1000.0)))
        gantry.lane_mm = mm
        gantry.leg_offsets = [v / 1000.0 for v in mm]

        rigid_specs = (
            (gantry.tire_elec1_rigid, bool(gantry.tire_elec1_rigid_valid), False),
            (gantry.tire_elec2_rigid, bool(gantry.tire_elec2_rigid_valid), False),
            (gantry.tire_diesel1_rigid, bool(gantry.tire_diesel1_rigid_valid), True),
            (gantry.tire_diesel2_rigid, bool(gantry.tire_diesel2_rigid_valid), True),
        )
        rigid_m = []
        for tire, ok, on_diesel in rigid_specs:
            if not ok:
                rigid_m.append(0.0)
                continue
            rigid_m.append(float(self._tire_lane_m(
                tire[0], tire[1], on_diesel, elec_pts, diesel_pts, center, x_elec,
            )))
        gantry.leg_offsets_rigid = rigid_m
        self._log_lane(gantry, mm)

    def _log_lane(self, gantry, mm) -> None:
        """诊断用：把字段 12-15 和四个轮胎的堆场 X、姿态一起打出来（每 1 s 最多一条）。"""
        now = time.monotonic()
        if now - getattr(self, "_last_lane_log_t", 0.0) < 1.0:
            return
        self._last_lane_log_t = now
        logger.info(
            "lane e1=%d e2=%d d1=%d d2=%d | tireX %s %s %s %s | roll=%.3f pitch=%.3f hdg=%.2f",
            mm[0], mm[1], mm[2], mm[3],
            _fmt_tire_x(gantry.tire_elec1), _fmt_tire_x(gantry.tire_elec2),
            _fmt_tire_x(gantry.tire_diesel1), _fmt_tire_x(gantry.tire_diesel2),
            gantry.roll, gantry.pitch, gantry.heading,
        )

    def _side_reference_x(self, transform, yard_id: str, side: str) -> Optional[float]:
        """该侧贝位点投影到当前堆场坐标后的平均 X。不足两个点时返回 None。"""
        cal = self._calibration_ctx.get(yard_id or "")
        points = cal.survey_points if cal else []
        chosen = electrical_bays(points) if side == "elec" else diesel_bays(points)
        label = "电气侧" if side == "elec" else "动力侧"
        xs = []
        for point in chosen:
            if not isinstance(point, dict):
                continue
            lat = float(point.get("lat") or 0.0)
            lon = float(point.get("lon") or 0.0)
            if abs(lat) < 1e-9 and abs(lon) < 1e-9:
                continue
            x, _y, _z = transform.wgs84_to_lycs(lat, lon, float(point.get("alt") or 0.0))
            xs.append(x)
        fitted = parallel_reference_x(xs)
        stamp = (yard_id, None if fitted is None else round(fitted, 3), len(xs))
        attr = "_elec_line_stamp" if side == "elec" else "_diesel_mean_x_stamp"
        if stamp != getattr(self, attr, None):
            setattr(self, attr, stamp)
            if fitted is None:
                logger.info("%s贝位线不足（%d 个点），偏差仍相对过原点的 +Y", label, len(xs))
            else:
                logger.info("%s贝位线 X=%.3f m（%d 个点，平行于 +Y）", label, fitted, len(xs))
        return fitted

    def _diesel_bay_line(self, transform, yard_id: str):
        """动力侧贝位点自己拟合的直线。点不够时返回 None，偏差改用电气侧线。"""
        cal = self._calibration_ctx.get(yard_id or "")
        points = cal.survey_points if cal else []
        samples = []
        for point in diesel_bays(points):
            if not isinstance(point, dict) or point.get("bay_no") is None:
                continue
            lat = float(point.get("lat") or 0.0)
            lon = float(point.get("lon") or 0.0)
            if abs(lat) < 1e-9 and abs(lon) < 1e-9:
                continue
            x, y, _z = transform.wgs84_to_lycs(lat, lon, float(point.get("alt") or 0.0))
            samples.append((float(point["bay_no"]), x, y))
        fitted = fit_bay_line(samples)
        if fitted is None:
            stamp = (yard_id, None, len(samples))
            angle = None
        else:
            origin, direction = fitted
            angle = math.degrees(math.atan2(direction[0], direction[1]))
            stamp = (
                yard_id,
                round(origin[0], 3),
                round(origin[1], 3),
                round(angle, 2),
                len(samples),
            )
        if stamp != getattr(self, "_diesel_line_stamp", None):
            self._diesel_line_stamp = stamp
            if fitted is None:
                logger.info("动力侧贝位线不足（%d 个点），偏差改用电气侧贝位线", len(samples))
            else:
                logger.info(
                    "动力侧贝位线过 (%.3f, %.3f)，与 +Y 夹角 %.2f°（%d 个点）",
                    origin[0], origin[1], angle, len(samples),
                )
        return fitted

    def _side_bay_xy(self, transform, yard_id: str, side: str):
        """该侧贝位点在堆场坐标里的 (X, Y)。每个作业点再用自己的 Y 选相邻两点。"""
        cal = self._calibration_ctx.get(yard_id or "")
        points = cal.survey_points if cal else []
        chosen = electrical_bays(points) if side == "elec" else diesel_bays(points)
        samples = []
        for point in chosen:
            if not isinstance(point, dict) or point.get("bay_no") is None:
                continue
            lat = float(point.get("lat") or 0.0)
            lon = float(point.get("lon") or 0.0)
            if abs(lat) < 1e-9 and abs(lon) < 1e-9:
                continue
            x, y, _z = transform.wgs84_to_lycs(lat, lon, float(point.get("alt") or 0.0))
            samples.append((float(x), float(y)))
        label = "电气侧" if side == "elec" else "动力侧"
        stamp = (yard_id, len(samples))
        attr = "_elec_segment_stamp" if side == "elec" else "_diesel_segment_stamp"
        if stamp != getattr(self, attr, None):
            setattr(self, attr, stamp)
            if len(samples) < 2 and side == "elec":
                logger.info("%s贝位点不足（%d 个），偏差仍相对过原点的 +Y", label, len(samples))
            elif len(samples) < 2:
                logger.info("%s贝位点不足（%d 个），偏差改用电气侧相邻贝位连线", label, len(samples))
            else:
                logger.info("%s偏差按相邻贝位点连线计算（%d 个点）", label, len(samples))
        return samples

    def _parallel_fallback_x(self, yard_id: str, elec_pts) -> float:
        fitted = parallel_reference_x([point[0] for point in elec_pts])
        return 0.0 if fitted is None else float(fitted)

    def _tire_reference_line(self, along_y: float, on_diesel: bool, elec_pts, diesel_pts):
        """这个作业点所在的那两个贝位点连线。动力侧点不够时用电气侧。"""
        own = diesel_pts if on_diesel else elec_pts
        line = bracket_bay_line(along_y, own)
        if line is None and on_diesel:
            line = bracket_bay_line(along_y, elec_pts)
        return line

    def _tire_lane_m(
        self, px, py, on_diesel, elec_pts, diesel_pts, center, x_elec,
    ) -> float:
        line = self._tire_reference_line(py, on_diesel, elec_pts, diesel_pts)
        if line is None:
            return lane_deviation_m(px, center[0], x_elec)
        origin, direction = line
        return line_deviation_m(px, py, origin, direction, center)

    def _side_fix(self, ga1, ga2, recv1, recv2):
        elec, diesel, elec_live, diesel_live, _ed, _dd = self._sides(ga1, ga2, recv1, recv2)

        def pair(g, live):
            if not live or not g:
                return 0, 0
            primary = int(g.get("fix_quality") or 0)
            secondary = int(g.get("antenna2_fix_quality") or primary)
            return primary, secondary

        ep, es = pair(elec, elec_live)
        dp, ds = pair(diesel, diesel_live)
        return ep, es, dp, ds

    def _apply_bay_axis(self, transform, survey_pts, yard_meta=None) -> None:
        pts = []
        for p in electrical_bays(survey_pts):
            bay = p.get("bay_no")
            if bay is None:
                continue
            lat = float(p.get("lat") or 0.0)
            lon = float(p.get("lon") or 0.0)
            if abs(lat) < 1e-9 and abs(lon) < 1e-9:
                continue
            east, north = en_meters(lat, lon, transform.origin_lat, transform.origin_lon)
            pts.append((int(bay), east, north))
        meta = yard_meta or {}
        axis = resolve_y_axis(
            pts,
            meta.get("y_axis_mode"),
            meta.get("y_axis_points"),
            transform.origin_lat,
            transform.origin_lon,
        )
        if axis:
            transform.set_y_axis(*axis)
        else:
            transform.reset_axis()

    def _axis_speed(self, slot: str, value: Optional[float], now: float) -> float:
        prev = getattr(self, slot)
        speed = 0.0
        if value is not None and prev is not None:
            dt = now - prev[1]
            if 0.05 < dt < 2.0:
                speed = (value - prev[0]) / dt
        if value is not None:
            setattr(self, slot, (value, now))
        return speed

    # ------------------------------------------------------------------
    # 核心解算
    # ------------------------------------------------------------------
    def _outside_result(
        self, now, ga1, ga2, tly, recv1, recv2, tly_live, ref, ref_rid,
        gantry_antennas=None, attitude_compare=None, gnss_pair_align=None,
    ) -> PositionResult:
        """中点不在任何场内，或只有一个轮胎中心在场内：场号空，场内外和场类型发 0。

        作业点没有堆场坐标，但接收机在线状态和定位质量仍要带上，
        否则前端总状态是 RTK FIXED，接收机列表却全是无数据。
        """
        hdg_src = ga1 if recv1 else ga2
        gantry = GantryResult(
            timestamp=now,
            recv1_live=recv1,
            recv2_live=recv2,
            heading=float(hdg_src.get("heading") or 0.0),
            pitch=float(hdg_src.get("pitch") or 0.0),
            roll=float(hdg_src.get("roll") or 0.0),
        )
        trolley = TrolleyResult(timestamp=now, is_valid=bool(tly_live))
        return PositionResult(
            timestamp=now,
            yard_id="",
            in_yard=False,
            gantry=gantry,
            trolley=trolley,
            latitude=float(ref.get("lat", 0.0)),
            longitude=float(ref.get("lon", 0.0)),
            altitude=float(ref.get("alt", 0.0)),
            ref_ecef_x=float(ref.get("ecef_x", 0.0)),
            ref_ecef_y=float(ref.get("ecef_y", 0.0)),
            ref_ecef_z=float(ref.get("ecef_z", 0.0)),
            ref_receiver_id=ref_rid,
            antenna2_latitude=float(ga2.get("lat", 0.0)) if recv2 else 0.0,
            antenna2_longitude=float(ga2.get("lon", 0.0)) if recv2 else 0.0,
            antenna2_altitude=float(ga2.get("alt", 0.0)) if recv2 else 0.0,
            antenna2_valid=bool(recv2),
            gnss_fix_quality=int((ga1 if recv1 else ga2).get("fix_quality", 0)),
            fix_quality_left=int(ga1.get("fix_quality", 0)) if recv1 else 0,
            fix_quality_left_ant2=int(ga1.get("antenna2_fix_quality", 0)) if recv1 else 0,
            fix_quality_right=int(ga2.get("fix_quality", 0)) if recv2 else 0,
            fix_quality_right_ant2=int(ga2.get("antenna2_fix_quality", 0)) if recv2 else 0,
            fix_quality_trolley=int(tly.get("fix_quality", 0)) if tly_live else 0,
            fix_quality_trolley_ant2=int(tly.get("antenna2_fix_quality", 0)) if tly_live else 0,
            satellites_left=int(ga1.get("num_satellites", 0)) if recv1 else 0,
            satellites_right=int(ga2.get("num_satellites", 0)) if recv2 else 0,
            satellites_trolley=int(tly.get("num_satellites", 0)) if tly_live else 0,
            hdop_left=float(ga1.get("hdop", 99.9)) if recv1 else 99.9,
            hdop_right=float(ga2.get("hdop", 99.9)) if recv2 else 99.9,
            hdop_trolley=float(tly.get("hdop", 99.9)) if tly_live else 99.9,
            gantry_antennas=gantry_antennas or {},
            gantry_attitude_compare=attitude_compare or {},
            gnss_pair_align=dict(gnss_pair_align or {}),
            current_bay_no=0,
        )

    def _solve(self, state: dict) -> Optional[PositionResult]:
        """
        完整解算流程，返回 PositionResult。
        """
        now = time.time()
        ga1 = state.get("gantry_a1", {})
        ga2 = state.get("gantry_a2", {})
        tly = state.get("trolley", {})

        stale = self._offline_timeout_s
        recv1_live = _is_receiver_live(ga1, now, stale, self._require_rtk_fixed)
        recv2_live = _is_receiver_live(ga2, now, stale, self._require_rtk_fixed)
        tly_live = _is_receiver_live(tly, now, stale)
        recv1_live, recv2_live, _, pair_dt, pair_src = apply_pair_skew(
            recv1_live,
            recv2_live,
            float(ga1.get("last_gnss_time", 0.0)),
            float(ga2.get("last_gnss_time", 0.0)),
            self._max_pair_skew_s,
            utc_sod1=ga1.get("utc_sod"),
            utc_sod2=ga2.get("utc_sod"),
        )
        pair_align = state.get("gnss_pair_align") or {
            "dt_s": None if pair_dt is None else round(float(pair_dt), 4),
            "source": pair_src,
            "dropped": False,
            "max_skew_s": self._max_pair_skew_s,
        }
        # 大车任一侧在线即可出帧（单侧作业点仍可算）；两侧皆无则不出
        if not recv1_live and not recv2_live:
            return None

        if recv1_live:
            ref, ref_rid = ga1, "gantry_a1"
        else:
            ref, ref_rid = ga2, "gantry_a2"

        roll = pitch = 0.0
        if recv1_live:
            roll = float(ga1.get("roll") or 0.0)
            pitch = float(ga1.get("pitch") or 0.0)
        elif recv2_live:
            roll = float(ga2.get("roll") or 0.0)
            pitch = float(ga2.get("pitch") or 0.0)

        att_cmp = state.get("gantry_attitude_compare") or {}
        if not isinstance(att_cmp, dict):
            att_cmp = {}
        tire_mode = normalize_tire_attitude(
            att_cmp.get("tire_attitude") or self._tire_attitude
        )
        # 共用横滚只在 per_side 生效；rigid 保持原来的主通道横滚
        if tire_mode == "per_side" and att_cmp.get("shared_roll") is not None:
            roll = float(att_cmp["shared_roll"])

        # rigid 保持下水→上水中点航向；per_side 优先两侧双 GGA 合成航向
        upstream = self._upstream_heading(ga1, ga2, recv1_live, recv2_live)
        synth = att_cmp.get("synthetic_heading")
        if tire_mode == "per_side" and synth is not None:
            heading = float(synth)
        elif upstream is not None:
            heading = float(upstream)
        else:
            heading = float((ga1 if recv1_live else ga2).get("heading") or 0.0)

        ga1 = dict(ga1)
        ga2 = dict(ga2)
        ga1["heading"] = heading
        ga2["heading"] = heading
        ga1["roll"] = roll
        ga2["roll"] = roll

        tires_ecef = self._four_tire_ecef(
            ga1,
            ga2,
            roll,
            pitch,
            heading,
            recv1_live,
            recv2_live,
            attitude_compare=att_cmp,
            tire_attitude=tire_mode,
        )
        # 历元丢一侧 → 四轮胎暂时不齐 → 场内/场外狂跳；短时用上一帧齐整轮胎填缺
        tires_ecef, tires_held = merge_held_tires(
            tires_ecef,
            self._last_good_tires_ecef,
            self._last_good_tires_at,
            now,
            self._tire_pair_hold_s,
        )
        if isinstance(pair_align, dict):
            pair_align = dict(pair_align)
            pair_align["tires_held"] = bool(tires_held)
        center_ll = self._tire_centroid_wgs84(tires_ecef)
        if center_ll is not None and not tires_held:
            self._last_good_tires_ecef = {
                k: tires_ecef.get(k) for k in ("elec1", "elec2", "diesel1", "diesel2")
            }
            self._last_good_tires_at = now
        if not self._yard_manager._yard_meta:
            if not self._yard_missing_logged:
                logger.warning(
                    "无法识别当前堆场（yard_config 无 yards 或未同步 API），"
                    "解算与 MQTT 遥测已暂停；请配置 config/yard_config.yaml 后重启中控"
                )
                self._yard_missing_logged = True
            return None

        yard_id = self._yard_manager.detect_yard_from_point(
            center_ll[0] if center_ll else None,
            center_ll[1] if center_ll else None,
        )
        transform = self._yard_manager.get_transform(yard_id) if yard_id else None
        if transform is None:
            outside = self._outside_result(
                now, ga1, ga2, tly, recv1_live, recv2_live, tly_live, ref, ref_rid,
                state.get("gantry_antennas"),
                state.get("gantry_attitude_compare"),
                pair_align,
            )
            ep, es, dp, ds = self._side_fix(ga1, ga2, recv1_live, recv2_live)
            outside.fix_quality_elec = ep
            outside.fix_quality_elec_ant2 = es
            outside.fix_quality_diesel = dp
            outside.fix_quality_diesel_ant2 = ds
            reason = (
                "轮胎中心不齐，无法判断是否在场内"
                if center_ll is None
                else "轮胎中心不在任一堆场多边形内"
            )
            self._note_solve_place(f"场外（{reason}）")
            return outside

        self._note_solve_place(f"场内 {yard_id}")
        cal = self._calibration_ctx.get(yard_id or "")
        survey_pts = cal.survey_points if cal else []
        yard_meta = self._yard_manager.get_yard_meta(yard_id or "") or {}
        self._apply_bay_axis(transform, survey_pts, yard_meta)

        # ---- 2. 大车解算 ----
        gantry_result = self._solve_gantry(
            ga1, ga2, transform, yard_id, now, recv1_live, recv2_live, tires_ecef,
            attitude_compare=att_cmp,
            tire_attitude=tire_mode,
        )

        # ---- 3. 小车解算 ----
        trolley_result = (
            self._solve_trolley(tly, gantry_result, transform, now)
            if tly_live
            else TrolleyResult(timestamp=now, is_valid=False)
        )

        # ---- 4. 组装结果 ----
        # 姿态优先左机（融合写入主通道）；仅右机在线时用右机
        hdg_src = ga1 if recv1_live else ga2

        lp_lat, lp_lon, lp_alt = transform.lycs_to_wgs84(
            gantry_result.lpoint_x,
            gantry_result.lpoint_y,
            gantry_result.lpoint_z,
        )

        yard_meta = self._yard_manager.get_yard_meta(yard_id or "") or {}
        bay_spacing = float(yard_meta.get("bay_spacing") or 6.0)
        if bay_spacing <= 0:
            bay_spacing = 6.0

        def _ant_dict(g: dict, primary: bool) -> dict:
            if primary:
                return {
                    "lat": float(g.get("lat", 0.0)),
                    "lon": float(g.get("lon", 0.0)),
                    "alt": float(g.get("alt", 0.0)),
                    "ecef_x": float(g.get("ecef_x", 0.0)),
                    "ecef_y": float(g.get("ecef_y", 0.0)),
                    "ecef_z": float(g.get("ecef_z", 0.0)),
                }
            return {
                "lat": float(g.get("antenna2_lat", 0.0)),
                "lon": float(g.get("antenna2_lon", 0.0)),
                "alt": float(g.get("antenna2_alt", 0.0)),
                "ecef_x": float(g.get("antenna2_ecef_x", 0.0)),
                "ecef_y": float(g.get("antenna2_ecef_y", 0.0)),
                "ecef_z": float(g.get("antenna2_ecef_z", 0.0)),
            }

        fq_left = int(ga1.get("fix_quality", 0)) if recv1_live else 0
        fq_left_a2 = int(ga1.get("antenna2_fix_quality", fq_left)) if recv1_live else 0
        fq_right = int(ga2.get("fix_quality", 0)) if recv2_live else 0
        fq_right_a2 = int(ga2.get("antenna2_fix_quality", fq_right)) if recv2_live else 0
        fq_tly = int(tly.get("fix_quality", 0)) if tly_live else 0
        fq_tly_a2 = int(tly.get("antenna2_fix_quality", fq_tly)) if tly_live else 0

        cal = self._calibration_ctx.get(yard_id or "")
        survey_pts = cal.survey_points if cal else []
        yard_meta = self._yard_manager.get_yard_meta(yard_id or "") or {}
        half = float(yard_meta.get("bay_half_width_m") or 0.3)
        current_bay_no = 0
        four_tires = (
            gantry_result.tire_elec1_valid and gantry_result.tire_elec2_valid
            and gantry_result.tire_diesel1_valid and gantry_result.tire_diesel2_valid
        )
        if transform.axis_ready and four_tires:
            bay_ys = []
            for pt in electrical_bays(survey_pts):
                bay_no = pt.get("bay_no")
                if bay_no is None:
                    continue
                lat = float(pt.get("lat") or 0.0)
                lon = float(pt.get("lon") or 0.0)
                if abs(lat) < 1e-9 and abs(lon) < 1e-9:
                    continue
                _x, y, _z = transform.wgs84_to_lycs(lat, lon, float(pt.get("alt") or 0.0))
                bay_ys.append((int(bay_no), y))
            current_bay_no = resolve_bay_band(gantry_result.lpoint_y, bay_ys, half)

        result = PositionResult(
            timestamp=now,
            yard_id=yard_id or "",
            in_yard=True,
            gantry=gantry_result,
            trolley=trolley_result,
            latitude=float(ref.get("lat", 0.0)),
            longitude=float(ref.get("lon", 0.0)),
            altitude=float(ref.get("alt", 0.0)),
            ref_ecef_x=float(ref.get("ecef_x", 0.0)),
            ref_ecef_y=float(ref.get("ecef_y", 0.0)),
            ref_ecef_z=float(ref.get("ecef_z", 0.0)),
            ref_receiver_id=ref_rid,
            # 遥测/杆臂 UI 的 antenna2 = 柴油侧参考天线：
            # 优先右接收机主点；否则回退左机副 GGA（旧双 GGA 布局）
            antenna2_latitude=(
                float(ga2.get("lat", 0.0)) if recv2_live
                else float(ga1.get("antenna2_lat", 0.0))
            ),
            antenna2_longitude=(
                float(ga2.get("lon", 0.0)) if recv2_live
                else float(ga1.get("antenna2_lon", 0.0))
            ),
            antenna2_altitude=(
                float(ga2.get("alt", 0.0)) if recv2_live
                else float(ga1.get("antenna2_alt", 0.0))
            ),
            antenna2_ecef_x=(
                float(ga2.get("ecef_x", 0.0)) if recv2_live
                else float(ga1.get("antenna2_ecef_x", 0.0))
            ),
            antenna2_ecef_y=(
                float(ga2.get("ecef_y", 0.0)) if recv2_live
                else float(ga1.get("antenna2_ecef_y", 0.0))
            ),
            antenna2_ecef_z=(
                float(ga2.get("ecef_z", 0.0)) if recv2_live
                else float(ga1.get("antenna2_ecef_z", 0.0))
            ),
            antenna2_valid=bool(recv2_live or ga1.get("has_antenna2")),
            lpoint_latitude=lp_lat,
            lpoint_longitude=lp_lon,
            lpoint_altitude=lp_alt,
            gnss_fix_quality=int(state.get("fix_quality", 0)),
            imu_coasting_active=state.get("imu_coasting", False),
            imu_coasting_duration=state.get("imu_coasting_duration", 0.0),
            gantry_antennas=state.get("gantry_antennas") or {},
            yard_origin_lat=float(transform.origin_lat),
            yard_origin_lon=float(transform.origin_lon),
            yard_origin_alt=float(transform.origin_alt),
            yard_axis_east=float(transform.y_axis_east),
            yard_axis_north=float(transform.y_axis_north),
            bay_spacing_m=bay_spacing,
            current_bay_no=current_bay_no,
            fix_quality_left=fq_left,
            fix_quality_left_ant2=fq_left_a2,
            fix_quality_right=fq_right,
            fix_quality_right_ant2=fq_right_a2,
            fix_quality_trolley=fq_tly,
            fix_quality_trolley_ant2=fq_tly_a2,
            satellites_left=int(ga1.get("num_satellites", 0)) if recv1_live else 0,
            satellites_right=int(ga2.get("num_satellites", 0)) if recv2_live else 0,
            satellites_trolley=int(tly.get("num_satellites", 0)) if tly_live else 0,
            hdop_left=float(ga1.get("hdop", 99.9)) if recv1_live else 99.9,
            hdop_right=float(ga2.get("hdop", 99.9)) if recv2_live else 99.9,
            hdop_trolley=float(tly.get("hdop", 99.9)) if tly_live else 99.9,
            trolley_antenna_primary=_ant_dict(tly, True) if tly_live else {},
            trolley_antenna_secondary=_ant_dict(tly, False) if tly_live else {},
            gantry_attitude_compare=state.get("gantry_attitude_compare") or {},
            gnss_pair_align=dict(state.get("gnss_pair_align") or pair_align),
        )
        ep, es, dp, ds = self._side_fix(ga1, ga2, recv1_live, recv2_live)
        result.fix_quality_elec = ep
        result.fix_quality_elec_ant2 = es
        result.fix_quality_diesel = dp
        result.fix_quality_diesel_ant2 = ds
        result.gantry.heading = float(hdg_src.get("heading", 0.0))
        result.gantry.pitch = float(hdg_src.get("pitch", 0.0))
        result.gantry.roll = float(hdg_src.get("roll", 0.0))
        return result

    def _solve_gantry(
        self,
        ga1: dict,
        ga2: dict,
        transform,
        yard_id: str,
        now: float,
        recv1_live: bool,
        recv2_live: bool,
        tires_ecef: Optional[dict] = None,
        attitude_compare: Optional[dict] = None,
        tire_attitude: Optional[str] = None,
    ) -> GantryResult:
        """
        四个轮胎中心：电气/动力各用该侧主天线标定的杆臂，套到主天线和从天线。
        侧中心是该侧两个轮胎的中点，大车中心是四个轮胎的中点。
        """
        gantry = GantryResult(timestamp=now)
        gantry.recv1_live = recv1_live
        gantry.recv2_live = recv2_live
        gantry.a1_valid = False
        gantry.a2_valid = False
        gantry.elec_primary_down = self._side.elec_primary_down()
        gantry.diesel_primary_down = self._side.diesel_primary_down()

        roll = pitch = heading = 0.0
        if recv1_live:
            roll = float(ga1.get("roll") or 0.0)
            pitch = float(ga1.get("pitch") or 0.0)
            heading = float(ga1.get("heading") or 0.0)
        elif recv2_live:
            roll = float(ga2.get("roll") or 0.0)
            pitch = float(ga2.get("pitch") or 0.0)
            heading = float(ga2.get("heading") or 0.0)
        # 须在 _fill_lane / _log_lane 之前写入，否则诊断日志一直是 0
        gantry.heading = heading
        gantry.pitch = pitch
        gantry.roll = roll

        tires = tires_ecef or self._four_tire_ecef(
            ga1, ga2, roll, pitch, heading, recv1_live, recv2_live,
            attitude_compare=attitude_compare,
            tire_attitude=tire_attitude,
        )
        slots = (
            ("elec1", "tire_elec1", "tire_elec1_valid"),
            ("elec2", "tire_elec2", "tire_elec2_valid"),
            ("diesel1", "tire_diesel1", "tire_diesel1_valid"),
            ("diesel2", "tire_diesel2", "tire_diesel2_valid"),
        )
        for key, store, flag in slots:
            ecef = tires.get(key)
            if ecef is None:
                continue
            xyz = transform.ecef_to_lycs(*ecef)
            setattr(gantry, store, (float(xyz[0]), float(xyz[1]), float(xyz[2])))
            setattr(gantry, flag, True)
        for key, store, flag in (
            ("elec1_rigid", "tire_elec1_rigid", "tire_elec1_rigid_valid"),
            ("elec2_rigid", "tire_elec2_rigid", "tire_elec2_rigid_valid"),
            ("diesel1_rigid", "tire_diesel1_rigid", "tire_diesel1_rigid_valid"),
            ("diesel2_rigid", "tire_diesel2_rigid", "tire_diesel2_rigid_valid"),
        ):
            ecef = tires.get(key)
            if ecef is None:
                continue
            xyz = transform.ecef_to_lycs(*ecef)
            setattr(gantry, store, (float(xyz[0]), float(xyz[1]), float(xyz[2])))
            setattr(gantry, flag, True)
        self._recompute_centers(gantry)
        self._fill_center_geo(gantry, tires)
        raw_lpoint_x = gantry.lpoint_x
        raw_lpoint_y = gantry.lpoint_y

        gantry = self._apply_calibration_corrections(gantry, yard_id, transform)
        self._apply_lane_corrections(gantry, transform, yard_id)
        self._fill_lane(gantry, transform, yard_id)

        gnss_speed, course_deg = _pick_gnss_motion(ga1, ga2, recv1_live, recv2_live)

        # ── 大车速度估计（位移优先，GNSS 备用） ──
        _MAX_SPEED_DT = 2.0    # 会话间隔上限：超过视为重启/跳变
        _STATIC_DECAY = 0.55   # 静止衰减（每接受帧×0.55，约 6 帧归零）

        cur_live = int(recv1_live) + int(recv2_live)
        prev_live = getattr(self, "_last_gantry_live_count", cur_live)
        live_changed = cur_live != prev_live
        self._last_gantry_live_count = cur_live

        since_any = (
            now - self._last_gantry_any_time
            if self._last_gantry_any_time > 0
            else 1e9
        )
        pair_complete = since_any < PAIR_DT

        ref_valid = (
            self._last_raw_gantry_x is not None
            and self._last_raw_gantry_y is not None
            and self._last_gantry_time > 0
        )
        dx = dy = 0.0
        dt = 0.0
        if ref_valid:
            dx = raw_lpoint_x - self._last_raw_gantry_x
            dy = raw_lpoint_y - self._last_raw_gantry_y
            dt = now - self._last_gantry_time

        def _reset_ref():
            self._last_raw_gantry_x = raw_lpoint_x
            self._last_raw_gantry_y = raw_lpoint_y
            self._last_gantry_x = gantry.lpoint_x
            self._last_gantry_y = gantry.lpoint_y
            self._last_gantry_time = now
            self._last_gantry_heading = heading

        yaw_rate_dps = (
            shortest_arc_deg(self._last_gantry_heading, heading) / dt
            if dt > 1e-6 else 0.0
        )

        speed_r2d = transform.r2d if transform is not None else None

        def _apply_speed():
            if dt < PAIR_DT:
                # 刚锁定参考点后的配对包，间隔太短，只改参考、不算速度
                _reset_ref()
                return
            disp = math.hypot(dx, dy)
            if disp >= MIN_DISPLACEMENT_M:
                update_motion_speed(
                    self._gantry_motion, dx, dy, dt, gnss_speed, course_deg,
                    heading_deg=heading,
                    heading_prev_deg=self._last_gantry_heading,
                    r2d=speed_r2d,
                )
            elif gnss_speed >= DEADBAND_MPS:
                hold_gnss_speed(self._gantry_motion, gnss_speed, course_deg, r2d=speed_r2d)
            elif abs(self._gantry_motion.speed) >= DEADBAND_MPS:
                self._gantry_motion.speed *= _STATIC_DECAY
                from src.solver.motion_speed import _snap
                self._gantry_motion.speed = _snap(
                    self._gantry_motion.speed, DEADBAND_MPS
                )
            _reset_ref()

        if not ref_valid:
            _reset_ref()
        elif dt > _MAX_SPEED_DT or live_changed:
            _reset_ref()
        elif not use_gantry_speed_sample(cur_live, since_any, dt):
            # 双机第一包：一边新一边旧，作业点会偏。等配对包。
            # dt 过短时也只改参考（见 _apply_speed）。
            if pair_complete and dt < PAIR_DT:
                _reset_ref()
        else:
            _apply_speed()

        self._last_gantry_any_time = now

        gantry.speed = self._gantry_motion.speed
        speed_abs = abs(gantry.speed)
        moving = speed_abs >= DEADBAND_MPS or self._last_speed_log > DEADBAND_MPS
        if moving and now - self._last_speed_log_time >= 1.0:
            pos_est = (
                math.hypot(dx, dy) / dt
                if dt >= PAIR_DT else float("nan")
            )
            logger.info(
                "[速度] gantry.speed=%.4f m/s | pos≈%.4f gnss=%.4f course=%.1f | "
                "dx=%.4f dy=%.4f dt=%.3f | lpoint=(%.4f, %.4f) | recv=%d live | hdg=%.2f ω=%.1f°/s",
                gantry.speed, pos_est, gnss_speed,
                course_deg if course_deg is not None else float("nan"),
                dx, dy, dt, gantry.lpoint_x, gantry.lpoint_y, cur_live, heading,
                yaw_rate_dps,
            )
            self._last_speed_log = speed_abs
            self._last_speed_log_time = now
        gantry.data_source = ga1.get("source", DataSource.INVALID)
        gantry.heading = heading
        gantry.pitch = pitch
        gantry.roll = roll
        gantry.speed_y_elec = self._axis_speed(
            "_last_elec_y",
            gantry.center_elec_y if gantry.a1_valid else None,
            now,
        )
        gantry.speed_y_diesel = self._axis_speed(
            "_last_diesel_y",
            gantry.center_engine_y if gantry.a2_valid else None,
            now,
        )
        return gantry

    def _solve_trolley(
        self,
        tly: dict,
        gantry: GantryResult,
        transform,
        now: float,
    ) -> TrolleyResult:
        """
        小车定位：双 GGA（优先）或单 GGA → LYCS → 大车车体系（锚定 L-Point）。
        必须有大车有效 L-Point，才能输出相对横梁行程；仅小车在线时不出相对结果。
        """
        trolley = TrolleyResult(timestamp=now)
        trolley.origin_mode = self._trolley_origin_mode

        if not gantry.a1_valid:
            return trolley

        c1 = (float(tly.get("ecef_x", 0.0)), float(tly.get("ecef_y", 0.0)), float(tly.get("ecef_z", 0.0)))
        if c1[0] == 0.0 and c1[1] == 0.0 and c1[2] == 0.0:
            return trolley

        has_a2 = bool(tly.get("has_antenna2"))
        a2x = float(tly.get("antenna2_ecef_x", 0.0))
        a2y = float(tly.get("antenna2_ecef_y", 0.0))
        a2z = float(tly.get("antenna2_ecef_z", 0.0))
        if has_a2 and not (a2x == 0.0 and a2y == 0.0 and a2z == 0.0):
            c2 = (a2x, a2y, a2z)
            # 副天线无解时会被收成赤道 ECEF（约 6378137,0,0）。
            # 和主天线相距远超横梁时丢掉，避免中心被拉到几十万米。
            sep = math.hypot(c2[0] - c1[0], c2[1] - c1[1], c2[2] - c1[2])
            # 大于 40 m：空语句被收成赤道。小于 0.5 m：副天线被写成了主天线。
            # 这两种都沿用上一次正常的副天线，中心不再跳。
            if sep > 40.0 or sep < 0.5:
                if self._trolley_last_c2 is not None:
                    c2 = self._trolley_last_c2
                else:
                    c2 = c1
                    has_a2 = False
            else:
                self._trolley_last_c2 = c2
        else:
            # 单天线：中心 = 主天线（双 GGA 未开时的降级）
            c2 = c1
            has_a2 = False

        x1, y1, z1 = transform.ecef_to_lycs(*c1)
        x2, y2, z2 = transform.ecef_to_lycs(*c2)

        trolley.antenna1_x, trolley.antenna1_y, trolley.antenna1_z = x1, y1, z1
        if has_a2:
            trolley.antenna2_x, trolley.antenna2_y, trolley.antenna2_z = x2, y2, z2
        else:
            trolley.antenna2_x = trolley.antenna2_y = trolley.antenna2_z = 0.0

        cx = (x1 + x2) / 2.0
        cy = (y1 + y2) / 2.0
        cz = (z1 + z2) / 2.0
        trolley.center_x, trolley.center_y, trolley.center_z = cx, cy, cz
        trolley.speed_x = self._axis_speed("_last_trolley_x", cx, now)

        bx, by, bz = lycs_to_gantry_body(
            cx, cy, cz,
            gantry.lpoint_x, gantry.lpoint_y, gantry.lpoint_z,
            float(gantry.heading),
            r2d=transform.r2d,
        )
        trolley.body_x, trolley.body_y, trolley.body_z = bx, by, bz

        if self._trolley_pending_capture:
            self._trolley_origin_body = (bx, by, bz)
            self._trolley_origin_mode = "manual"
            self._trolley_pending_capture = False
            logger.info("小车零点已捕获 (车体系): x=%.3f y=%.3f z=%.3f", bx, by, bz)
            self._trolley_rel_hist = []
        elif self._trolley_origin_body is None and self._trolley_origin_mode == "auto":
            self._trolley_origin_body = (bx, by, bz)
            logger.info("小车自动零点锁定 (车体系): x=%.3f y=%.3f z=%.3f", bx, by, bz)
            self._trolley_rel_hist = []

        ox, oy, oz = self._trolley_origin_body or (bx, by, bz)
        trolley.origin_body_x, trolley.origin_body_y, trolley.origin_body_z = ox, oy, oz

        trolley.rel_x = bx - ox
        trolley.rel_y = by - oy
        trolley.rel_z = bz - oz
        trolley.travel_distance = trolley.rel_y

        # 相对速度 = 仪表盘 ΔX/ΔY 在约 0.4 s 内的变化率。
        # 大车移动而相对零点不动 → 0。不用 GNSS 地速。
        _REL_WINDOW_S = 0.4
        # 副天线空语句会把相对位置打飞。这一帧不拿去算速度，否则接下来
        # 0.4 秒里好位置也会显示出几百万米每秒。
        if self._trolley_rel_hist:
            _prev_t, prev_x, prev_y = self._trolley_rel_hist[-1]
            if math.hypot(trolley.rel_x - prev_x, trolley.rel_y - prev_y) > 3.0:
                self._trolley_rel_hist = []
        self._trolley_rel_hist.append((now, trolley.rel_x, trolley.rel_y))
        self._trolley_rel_hist = [
            p for p in self._trolley_rel_hist if now - p[0] <= 1.0
        ]
        older = [p for p in self._trolley_rel_hist if now - p[0] >= _REL_WINDOW_S]
        if older:
            t0, x0, y0 = older[-1]
            trolley.speed = relative_planar_speed(
                trolley.rel_x - x0, trolley.rel_y - y0, now - t0,
            )
        else:
            trolley.speed = 0.0

        trolley.is_valid = True
        trolley.data_source = tly.get("source", DataSource.INVALID)
        return trolley

    # ------------------------------------------------------------------
    # 校准接口（供 bridge_node 或工具调用）
    # ------------------------------------------------------------------
    def add_calibration_point(
        self,
        yard_id: str,
        measured_xy: tuple,
        reference_xy: tuple,
    ) -> None:
        """
        添加 B 车校准点对（**仅调试/离线工具**，生产采点走 Web API）。

        Web 流程：前端 confirm → 后端 finalize 算 M → MQTT CALIBRATION_FINALIZED → reload_calibration。
        """
        if yard_id not in self._calibrators:
            self._calibrators[yard_id] = BVehicleCalibrator()

        calibrator = self._calibrators[yard_id]
        count = calibrator.add_point(measured_xy, reference_xy)

        if calibrator.is_ready:
            M = calibrator.compute_transform()
            self._yard_manager.save_b_matrix(yard_id, M)
            logger.info(f"B 车校准完成，矩阵已保存（堆场 {yard_id}）")

    def apply_trolley_origin_config(self, trolley_cfg: dict) -> None:
        """应用小车零点配置（来自 vehicle_params / 云端）。"""
        if not isinstance(trolley_cfg, dict):
            return
        mode = str(trolley_cfg.get("origin_mode", "auto"))
        ob = trolley_cfg.get("origin_body")
        if mode == "manual" and isinstance(ob, (list, tuple)) and len(ob) >= 3:
            self._trolley_origin_mode = "manual"
            self._trolley_origin_body = (float(ob[0]), float(ob[1]), float(ob[2]))
            logger.info("小车零点已设为手动 (车体系): %s", self._trolley_origin_body)
        elif mode == "auto":
            self._trolley_origin_mode = "auto"
            self._trolley_origin_body = None
            logger.info("小车零点模式: auto（下次有效解自动锁定）")

    def request_trolley_origin_capture(self) -> None:
        """下一帧有效解时将当前位置设为车体系零点。"""
        self._trolley_pending_capture = True
        logger.info("已请求捕获小车零点（等待下一帧双 GGA）")

    def set_trolley_origin_manual(self, ox: float, oy: float, oz: float) -> None:
        """手动设定大车车体系零点坐标。"""
        self._trolley_origin_mode = "manual"
        self._trolley_origin_body = (float(ox), float(oy), float(oz))
        self._trolley_pending_capture = False
        self._trolley_rel_hist = []
        logger.info("小车零点已手动设定 (车体系): (%.3f, %.3f, %.3f)", ox, oy, oz)

    def reset_trolley_origin(self) -> None:
        """重置为 auto 模式，下一有效解重新锁定零点。"""
        self._trolley_origin_mode = "auto"
        self._trolley_origin_body = None
        self._trolley_pending_capture = False
        self._trolley_rel_hist = []
        logger.info("小车零点已重置为 auto")

    def apply_vehicle_config(self, cloud_config: dict) -> None:
        """应用云端下发的车辆静态参数到 L-Point 解算器。"""
        block = cloud_config.get("config") if isinstance(cloud_config.get("config"), dict) else cloud_config
        if not block:
            return
        elec_down, elec_up, diesel_down, diesel_up = _gantry_l_arms_from_cfg(block)
        self._solver_elec_down.update_l_arm(elec_down)
        self._solver_elec_up.update_l_arm(elec_up)
        self._solver_diesel_down.update_l_arm(diesel_down)
        self._solver_diesel_up.update_l_arm(diesel_up)
        logger.info(
            "已应用四轮胎杆臂 elec_down=%s elec_up=%s diesel_down=%s diesel_up=%s",
            elec_down, elec_up, diesel_down, diesel_up,
        )
        self._sync_side(block)
        att = block.get("gantry_attitude")
        if not isinstance(att, dict):
            gantry = block.get("gantry") if isinstance(block.get("gantry"), dict) else {}
            att = gantry.get("attitude") if isinstance(gantry.get("attitude"), dict) else {}
        # 下发配置缺该键时保持当前值，避免部分配置静默改变投轮胎方式
        if isinstance(att, dict):
            # 主通道统一整车；忽略云端 per_side
            prev = self._tire_attitude
            self._tire_attitude = "rigid"
            if att.get("tire_attitude") not in (None, "", "rigid") or prev != "rigid":
                logger.info(
                    "tire_attitude 已热更新: rigid（忽略下发=%s，原=%s）",
                    att.get("tire_attitude"),
                    prev,
                )
        trolley_cfg = block.get("trolley")
        if isinstance(trolley_cfg, dict):
            self.apply_trolley_origin_config(trolley_cfg)

    def reload_calibration(
        self,
        yard_id: str,
        vehicle_id: str,
        ref_lat_deg: float = 0.0,
        api_client=None,
        map_snapshot: Optional[dict] = None,
    ) -> None:
        """从后端或本地备份刷新本堆场校准上下文，并同步 M 到 yard_manager。"""
        state = None
        if api_client is not None:
            state = self._calibration_ctx.load_yard(
                api_client, yard_id, vehicle_id, ref_lat_deg
            )
        elif map_snapshot is not None:
            src_list = map_snapshot.get("source_transform_list")
            state = self._calibration_ctx.load_yard_from_snapshot(
                yard_id,
                vehicle_id,
                ref_lat_deg,
                map_snapshot,
                source_transform_list=src_list,
            )
        if state and state.matrix is not None and yard_id:
            self._yard_manager.save_b_matrix(yard_id, state.matrix)

    def _apply_calibration_corrections(
        self, gantry: GantryResult, yard_id: str, transform
    ) -> GantryResult:
        """
        作业修正链：
          1) 校准2：M × P_B → 001 坐标系
          2) 套用全场残差底图（首车表或 verify 时 source 车表）线性插值
        """
        cal = self._calibration_ctx.get(yard_id) if yard_id else None
        M = None
        if cal and cal.matrix is not None:
            M = cal.matrix
        elif yard_id:
            M = self._yard_manager.get_b_matrix(yard_id)

        if M is not None:
            ox, oy = gantry.lpoint_x, gantry.lpoint_y
            nx, ny = _apply_matrix(M, ox, oy)
            self._shift_tires(gantry, nx - ox, ny - oy)

        anchors = []
        if cal:
            if cal.mode == "verify" and cal.source_anchors:
                anchors = cal.source_anchors
            elif cal.source_anchors:
                anchors = cal.source_anchors
            elif cal.own_anchors:
                anchors = cal.own_anchors

        if anchors:
            ref_lat = cal.ref_lat_deg if cal else 0.0
            if ref_lat == 0.0 and transform is not None:
                ref_lat = transform.origin_lat
            rdx, rdy, dz = interpolate_residual(gantry.lpoint_x, anchors)
            self._shift_tires(gantry, -rdx, -rdy, -dz)

        return gantry

    def _lane_frame(self, transform, yard_id: str):
        """场内方向和两侧贝位线。没有场界时无法判断朝内朝外，返回 None。"""
        meta = self._yard_manager.get_yard_meta(yard_id or "") or {}
        ring = normalize_boundary(meta.get("boundary"))
        if len(ring) < 3:
            return None
        verts = [
            transform.wgs84_to_lycs(lat, lon, float(transform.origin_alt))[:2]
            for lat, lon in ring
        ]
        center = polygon_centroid(verts)
        if center is None:
            return None
        elec_pts = self._side_bay_xy(transform, yard_id, "elec")
        diesel_pts = self._side_bay_xy(transform, yard_id, "diesel")
        x_elec = self._parallel_fallback_x(yard_id, elec_pts)
        return (float(center[0]), float(center[1])), elec_pts, diesel_pts, x_elec

    def _apply_lane_corrections(self, gantry: GantryResult, transform, yard_id: str) -> None:
        """任意停留点的横向修正。只落在该点前后两个贝位之间，每个轮胎用自己的沿贝位位置。"""
        if transform is None or not getattr(transform, "axis_ready", False) or not yard_id:
            return
        cal = self._calibration_ctx.get(yard_id)
        points = cal.lane_corrections if cal else []
        if not points:
            return
        frame = self._lane_frame(transform, yard_id)
        if frame is None:
            return
        centroid, elec_pts, diesel_pts, x_elec = frame
        elec_ys = [point[1] for point in elec_pts]
        diesel_ys = [point[1] for point in diesel_pts]
        if len(elec_ys) < 2:
            elec_ys = diesel_ys
        if len(diesel_ys) < 2:
            diesel_ys = elec_ys
        specs = (
            ("tire_elec1", "tire_elec1_valid", 0, False, elec_ys),
            ("tire_elec2", "tire_elec2_valid", 1, False, elec_ys),
            ("tire_diesel1", "tire_diesel1_valid", 2, True, diesel_ys),
            ("tire_diesel2", "tire_diesel2_valid", 3, True, diesel_ys),
            ("tire_elec1_rigid", "tire_elec1_rigid_valid", 0, False, elec_ys),
            ("tire_elec2_rigid", "tire_elec2_rigid_valid", 1, False, elec_ys),
            ("tire_diesel1_rigid", "tire_diesel1_rigid_valid", 2, True, diesel_ys),
            ("tire_diesel2_rigid", "tire_diesel2_rigid_valid", 3, True, diesel_ys),
        )
        moved = False
        for name, flag, index, on_diesel, bay_ys in specs:
            if not getattr(gantry, flag):
                continue
            tire = getattr(gantry, name)
            dev = applied_lane_shifts(tire[1], points, bay_ys)[index]
            line = self._tire_reference_line(
                tire[1], on_diesel, elec_pts, diesel_pts,
            )
            if line is not None:
                dx, dy = deviation_adjust_along_line(
                    dev, line[0], line[1], centroid,
                )
            else:
                dx = deviation_adjust_to_dx(dev, centroid[0], x_elec)
                dy = 0.0
            if abs(dx) < 1e-6 and abs(dy) < 1e-6:
                continue
            x, y, z = getattr(gantry, name)
            setattr(gantry, name, (x + dx, y + dy, z))
            moved = True
        if moved:
            self._recompute_centers(gantry)


def _is_receiver_live(
    g: dict, now: float, stale_s: float = 0.5, require_fixed: bool = False,
) -> bool:
    if not g:
        return False
    last = float(g.get("last_gnss_time", 0.0))
    if last <= 0 or (now - last) > stale_s:
        return False
    fq = int(g.get("fix_quality", 0))
    if not require_fixed:
        return fq > int(FixQuality.INVALID)
    if fq != int(FixQuality.RTK_FIXED):
        return False
    if g.get("has_antenna2"):
        return int(g.get("antenna2_fix_quality", 0)) == int(FixQuality.RTK_FIXED)
    return True


def _pick_gnss_motion(
    ga1: dict,
    ga2: dict,
    a1_live: bool,
    a2_live: bool,
) -> tuple[float, float | None]:
    """取有实时数据接收机的 GNSS 地速与航迹角（两侧平均，避免取 max 偏高）。"""
    speeds: list[float] = []
    courses: list[float] = []
    if a1_live:
        spd = float(ga1.get("speed_mps", 0.0))
        if spd > 0:
            speeds.append(spd)
            courses.append(float(ga1.get("course_deg", 0.0)))
    if a2_live:
        spd = float(ga2.get("speed_mps", 0.0))
        if spd > 0:
            speeds.append(spd)
            courses.append(float(ga2.get("course_deg", 0.0)))
    if not speeds:
        return 0.0, None
    mag = sum(speeds) / len(speeds)
    # 航迹角：取更接近平均地速的那一侧，避免两侧航迹差 180° 时乱平均
    best_i = min(range(len(speeds)), key=lambda i: abs(speeds[i] - mag))
    return mag, courses[best_i]


def _apply_matrix(M: np.ndarray, x: float, y: float):
    """用 3×3 齐次矩阵变换 2D 点。"""
    p = M @ np.array([x, y, 1.0])
    return float(p[0]), float(p[1])

