"""
解算节点：solver_node
订阅 fusion_node 输出的融合状态，执行完整解算流程：
  1. 自动识别当前堆场
  2. 坐标系变换（WGS84 → LYCS）
  3. L-Point 刚体变换（大车 + 小车）
  4. B 车矩阵修正（如已完成校准）
  5. 输出 PositionResult 到 EventBus
"""

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
from src.solver.attitude import euler_to_rotation_matrix
from src.solver.yard_manager import YardManager
from src.solver.calibration import BVehicleCalibrator
from src.solver.calibration_context import CalibrationContextManager
from src.solver.bay_residual import interpolate_residual
from src.solver.motion_speed import (
    DEADBAND_MPS,
    MIN_DISPLACEMENT_M,
    MotionSpeedState,
    hold_gnss_speed,
    update_motion_speed,
)
from src.utils.config_loader import get_config
logger = logging.getLogger(__name__)


def _gantry_l_arms_from_cfg(cfg) -> tuple[list, list]:
    """读取大车双作业点杆臂；兼容旧版单 l_arm。"""
    legacy = cfg.get("l_arm", [0, 0, -8.45])
    gantry_block = cfg.get("gantry") if isinstance(cfg.get("gantry"), dict) else {}
    elec = (
        cfg.get("gantry.l_arm_elec")
        or gantry_block.get("l_arm_elec")
        or cfg.get("l_arm_elec")
    )
    diesel = (
        cfg.get("gantry.l_arm_diesel")
        or gantry_block.get("l_arm_diesel")
        or cfg.get("l_arm_diesel")
    )
    if not elec or len(elec) < 3:
        elec = legacy
    if not diesel or len(diesel) < 3:
        diesel = legacy
    return [float(x) for x in elec[:3]], [float(x) for x in diesel[:3]]


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

        # 大车双作业点：接收机1 的 A1→电气房侧、A2→柴油机侧，各独立杆臂
        l_arm_elec, l_arm_diesel = _gantry_l_arms_from_cfg(cfg)
        self._solver_elec = LPointSolver(l_arm_elec)
        self._solver_diesel = LPointSolver(l_arm_diesel)

        # 小车解算器（定位主天线 + 定向副天线杆臂）
        c1_arm = cfg.get("trolley.antenna_c1.L_arm", [0, 0.5, -3.1])
        c2_arm = cfg.get("trolley.antenna_c2.L_arm", [0, -0.5, -3.1])
        self._solver_c1 = LPointSolver(c1_arm)
        self._solver_c2 = LPointSolver(c2_arm)
        self._trolley_baseline_body = np.array(c2_arm, dtype=float) - np.array(c1_arm, dtype=float)

        # B 车校准器（每个堆场独立）
        self._calibrators: dict[str, BVehicleCalibrator] = {}

        # 小车行程零点（初始化后记录）
        self._trolley_origin_x: Optional[float] = None
        self._last_trolley_x: Optional[float] = None
        self._last_trolley_y: Optional[float] = None
        self._last_trolley_time: float = 0.0

        # 大车速度
        self._last_gantry_x: Optional[float] = None
        self._last_gantry_y: Optional[float] = None
        self._last_raw_gantry_x: Optional[float] = None
        self._last_raw_gantry_y: Optional[float] = None
        self._gantry_motion = MotionSpeedState()
        self._last_gantry_time: float = 0.0

        # 小车速度
        self._trolley_motion = MotionSpeedState()

        # 订阅融合状态
        self._bus.subscribe(EventBus.TOPIC_FUSED_STATE, self._on_fused_state)
        self._yard_missing_logged = False
        logger.info("SolverNode 初始化完成")

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

    # ------------------------------------------------------------------
    # 核心解算
    # ------------------------------------------------------------------
    def _solve(self, state: dict) -> Optional[PositionResult]:
        """
        完整解算流程，返回 PositionResult。
        """
        now = time.time()
        ga1 = state.get("gantry_a1", {})
        ga2 = state.get("gantry_a2", {})
        tly = state.get("trolley", {})

        recv1_live = _is_receiver_live(ga1, now)
        recv2_live = _is_receiver_live(ga2, now)
        if not recv1_live:
            return None

        ref = ga1

        # ---- 1. 识别当前堆场 ----
        yard_id = self._yard_manager.detect_yard(
            lat=ref.get("lat", 0.0),
            lon=ref.get("lon", 0.0),
        )
        transform = self._yard_manager.get_transform(yard_id)
        if transform is None:
            if not self._yard_missing_logged:
                logger.warning(
                    "无法识别当前堆场（yard_config 无 yards 或未同步 API），"
                    "解算与 MQTT 遥测已暂停；请配置 config/yard_config.yaml 后重启中控"
                )
                self._yard_missing_logged = True
            return None

        # ---- 2. 大车解算 ----
        gantry_result = self._solve_gantry(
            ga1, ga2, transform, yard_id, now, recv1_live, recv2_live
        )

        # ---- 3. 小车解算 ----
        tly_live = _is_receiver_live(tly, now)
        trolley_result = (
            self._solve_trolley(tly, transform, now)
            if tly_live
            else TrolleyResult(timestamp=now, is_valid=False)
        )

        # ---- 4. 组装结果 ----
        hdg_src = ga1
        ref_rid = "gantry_a1"

        lp_lat, lp_lon, lp_alt = transform.lycs_to_wgs84(
            gantry_result.lpoint_x,
            gantry_result.lpoint_y,
            gantry_result.lpoint_z,
        )

        result = PositionResult(
            timestamp=now,
            yard_id=yard_id or "",
            gantry=gantry_result,
            trolley=trolley_result,
            latitude=float(ref.get("lat", 0.0)),
            longitude=float(ref.get("lon", 0.0)),
            altitude=float(ref.get("alt", 0.0)),
            ref_ecef_x=float(ref.get("ecef_x", 0.0)),
            ref_ecef_y=float(ref.get("ecef_y", 0.0)),
            ref_ecef_z=float(ref.get("ecef_z", 0.0)),
            ref_receiver_id=ref_rid,
            antenna2_latitude=float(ga1.get("antenna2_lat", 0.0)),
            antenna2_longitude=float(ga1.get("antenna2_lon", 0.0)),
            antenna2_altitude=float(ga1.get("antenna2_alt", 0.0)),
            antenna2_ecef_x=float(ga1.get("antenna2_ecef_x", 0.0)),
            antenna2_ecef_y=float(ga1.get("antenna2_ecef_y", 0.0)),
            antenna2_ecef_z=float(ga1.get("antenna2_ecef_z", 0.0)),
            antenna2_valid=bool(ga1.get("has_antenna2")),
            lpoint_latitude=lp_lat,
            lpoint_longitude=lp_lon,
            lpoint_altitude=lp_alt,
            gnss_fix_quality=int(state.get("fix_quality", 0)),
            imu_coasting_active=state.get("imu_coasting", False),
            imu_coasting_duration=state.get("imu_coasting_duration", 0.0),
        )
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
    ) -> GantryResult:
        """
        大车定位：接收机1 (gantry_a1) 双天线 → 两个作业点。
          A1 主天线 GGA + l_arm_elec  → 电气房侧 L-Point
          A2 副天线 GGA + l_arm_diesel → 柴油机侧 L-Point
        不再用跨距推算另一侧作业点；接收机2 仅标记在线状态。
        """
        gantry = GantryResult(timestamp=now)
        gantry.recv1_live = recv1_live
        gantry.recv2_live = recv2_live
        gantry.a1_valid = False
        gantry.a2_valid = False

        lx_elec = ly_elec = lz_elec = 0.0
        lx_diesel = ly_diesel = lz_diesel = 0.0

        roll = pitch = heading = 0.0
        if recv1_live:
            roll = ga1.get("roll", 0.0)
            pitch = ga1.get("pitch", 0.0)
            heading = ga1.get("heading", 0.0)

            gps_a1 = np.array([ga1["ecef_x"], ga1["ecef_y"], ga1["ecef_z"]])
            lp_elec_ecef = self._solver_elec.solve(gps_a1, roll, pitch, heading)
            lx_elec, ly_elec, lz_elec = transform.ecef_to_lycs(*lp_elec_ecef)
            gantry.center_elec_side = lx_elec
            gantry.center_elec_y = ly_elec
            gantry.a1_valid = True

            if ga1.get("has_antenna2"):
                gps_a2 = np.array([
                    ga1["antenna2_ecef_x"],
                    ga1["antenna2_ecef_y"],
                    ga1["antenna2_ecef_z"],
                ])
                lp_diesel_ecef = self._solver_diesel.solve(gps_a2, roll, pitch, heading)
                lx_diesel, ly_diesel, lz_diesel = transform.ecef_to_lycs(*lp_diesel_ecef)
                gantry.center_engine_side = lx_diesel
                gantry.center_engine_y = ly_diesel
                gantry.a2_valid = True

        raw_xs, raw_ys, raw_zs = [], [], []
        if gantry.a1_valid:
            raw_xs.append(lx_elec)
            raw_ys.append(ly_elec)
            raw_zs.append(lz_elec)
        if gantry.a2_valid:
            raw_xs.append(lx_diesel)
            raw_ys.append(ly_diesel)
            raw_zs.append(lz_diesel)

        raw_lpoint_x = float(np.mean(raw_xs)) if raw_xs else 0.0
        raw_lpoint_y = float(np.mean(raw_ys)) if raw_ys else 0.0
        raw_lpoint_z = float(np.mean(raw_zs)) if raw_zs else 0.0

        gantry.lpoint_x = raw_lpoint_x
        gantry.lpoint_y = raw_lpoint_y
        gantry.lpoint_z = raw_lpoint_z

        # 门腿 Y：电气房/柴油机侧各用实测作业点 Y，不用跨距推算
        if gantry.a1_valid and gantry.a2_valid:
            gantry.leg_offsets = [ly_elec, ly_diesel, ly_elec, ly_diesel]
        elif gantry.a1_valid:
            gantry.leg_offsets = [ly_elec, ly_elec, ly_elec, ly_elec]
        elif gantry.a2_valid:
            gantry.leg_offsets = [ly_diesel, ly_diesel, ly_diesel, ly_diesel]
        else:
            gantry.leg_offsets = [0.0, 0.0, 0.0, 0.0]

        gantry = self._apply_calibration_corrections(gantry, yard_id, transform)

        gnss_speed, course_deg = _pick_gnss_motion(ga1, ga2, recv1_live, recv2_live)
        dx = dy = 0.0
        dt = 0.0
        if (
            self._last_raw_gantry_x is not None
            and self._last_raw_gantry_y is not None
            and self._last_gantry_time > 0
        ):
            dx = raw_lpoint_x - self._last_raw_gantry_x
            dy = raw_lpoint_y - self._last_raw_gantry_y
            dt = now - self._last_gantry_time

        if math.hypot(dx, dy) >= MIN_DISPLACEMENT_M and dt > 0:
            update_motion_speed(
                self._gantry_motion, dx, dy, dt, gnss_speed, course_deg
            )
            self._last_raw_gantry_x = raw_lpoint_x
            self._last_raw_gantry_y = raw_lpoint_y
            self._last_gantry_x = gantry.lpoint_x
            self._last_gantry_y = gantry.lpoint_y
            self._last_gantry_time = now
        elif gnss_speed >= DEADBAND_MPS:
            hold_gnss_speed(self._gantry_motion, gnss_speed, course_deg)
        elif self._last_raw_gantry_x is None:
            self._last_raw_gantry_x = raw_lpoint_x
            self._last_raw_gantry_y = raw_lpoint_y
            self._last_gantry_x = gantry.lpoint_x
            self._last_gantry_y = gantry.lpoint_y
            self._last_gantry_time = now

        gantry.speed = self._gantry_motion.speed
        gantry.data_source = ga1.get("source", DataSource.INVALID)
        return gantry

    def _solve_trolley(self, tly: dict, transform, now: float) -> TrolleyResult:
        """
        小车定位解算：输出两端天线在 RTG 坐标系下的坐标及行程。

        GGA 为定位主天线（c1）相位中心；定向副天线（c2）由主天线位置 +
        姿态旋转矩阵与杆臂基线向量推算，再分别做 L-Point 刚体变换。
        """
        trolley = TrolleyResult(timestamp=now)

        gps_master = np.array([tly["ecef_x"], tly["ecef_y"], tly["ecef_z"]])
        roll_c = tly.get("roll", 0.0)
        pitch_c = tly.get("pitch", 0.0)
        hdg_c = tly.get("heading", 0.0)

        R = euler_to_rotation_matrix(roll_c, pitch_c, hdg_c)
        gps_slave = gps_master + R @ self._trolley_baseline_body

        lp_c1 = self._solver_c1.solve(gps_master, roll_c, pitch_c, hdg_c)
        lp_c2 = self._solver_c2.solve(gps_slave, roll_c, pitch_c, hdg_c)

        x1, y1, z1 = transform.ecef_to_lycs(*lp_c1)
        x2, y2, z2 = transform.ecef_to_lycs(*lp_c2)

        trolley.antenna1_x, trolley.antenna1_y, trolley.antenna1_z = x1, y1, z1
        trolley.antenna2_x, trolley.antenna2_y, trolley.antenna2_z = x2, y2, z2
        trolley.center_x = (x1 + x2) / 2.0
        trolley.center_y = (y1 + y2) / 2.0
        trolley.center_z = (z1 + z2) / 2.0

        # 行程（相对初始零点）
        if self._trolley_origin_x is None:
            self._trolley_origin_x = trolley.center_x
        trolley.travel_distance = trolley.center_x - self._trolley_origin_x

        # 小车速度
        gnss_speed = float(tly.get("speed_mps", 0.0))
        course_deg = float(tly.get("course_deg", 0.0))
        dx = 0.0
        dy = 0.0
        dt = 0.0
        if (
            self._last_trolley_x is not None
            and self._last_trolley_y is not None
            and self._last_trolley_time > 0
        ):
            dx = trolley.center_x - self._last_trolley_x
            dy = trolley.center_y - self._last_trolley_y
            dt = now - self._last_trolley_time

        if math.hypot(dx, dy) >= MIN_DISPLACEMENT_M and dt > 0:
            update_motion_speed(
                self._trolley_motion,
                dx,
                dy,
                dt,
                gnss_speed,
                course_deg,
            )
            self._last_trolley_x = trolley.center_x
            self._last_trolley_y = trolley.center_y
            self._last_trolley_time = now
        elif gnss_speed >= DEADBAND_MPS:
            hold_gnss_speed(self._trolley_motion, gnss_speed, course_deg)
        elif self._last_trolley_x is None:
            self._last_trolley_x = trolley.center_x
            self._last_trolley_y = trolley.center_y
            self._last_trolley_time = now

        trolley.speed = self._trolley_motion.speed
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
        添加 B 车校准点对。满 3 点后自动执行校准。

        :param yard_id:       堆场 ID
        :param measured_xy:   RTG 实测 (x, y)（米）
        :param reference_xy:  参考真值 (x, y)（米）
        """
        if yard_id not in self._calibrators:
            self._calibrators[yard_id] = BVehicleCalibrator()

        calibrator = self._calibrators[yard_id]
        count = calibrator.add_point(measured_xy, reference_xy)

        if calibrator.is_ready:
            M = calibrator.compute_transform()
            self._yard_manager.save_b_matrix(yard_id, M)
            logger.info(f"B 车校准完成，矩阵已保存（堆场 {yard_id}）")

    def reset_trolley_origin(self) -> None:
        """重置小车行程零点。"""
        self._trolley_origin_x = None
        logger.info("小车行程零点已重置")

    def apply_vehicle_config(self, cloud_config: dict) -> None:
        """应用云端下发的车辆静态参数到 L-Point 解算器。"""
        block = cloud_config.get("config") if isinstance(cloud_config.get("config"), dict) else cloud_config
        if not block:
            return
        elec, diesel = _gantry_l_arms_from_cfg(block)
        self._solver_elec.update_l_arm(elec)
        self._solver_diesel.update_l_arm(diesel)
        logger.info("已应用大车双作业点杆臂 elec=%s diesel=%s", elec, diesel)

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
            gantry.lpoint_x, gantry.lpoint_y = _apply_matrix(M, ox, oy)
            dx = gantry.lpoint_x - ox
            dy = gantry.lpoint_y - oy
            if gantry.a1_valid:
                gantry.center_elec_side += dx
                gantry.center_elec_y += dy
            if gantry.a2_valid:
                gantry.center_engine_side += dx
                gantry.center_engine_y += dy

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
            gantry.lpoint_x -= rdx
            gantry.lpoint_y -= rdy
            gantry.lpoint_z -= dz
            if gantry.a1_valid:
                gantry.center_elec_side -= rdx
                gantry.center_elec_y -= rdy
            if gantry.a2_valid:
                gantry.center_engine_side -= rdx
                gantry.center_engine_y -= rdy

        return gantry


def _is_receiver_live(g: dict, now: float, stale_s: float = 2.0) -> bool:
    if not g:
        return False
    last = float(g.get("last_gnss_time", 0.0))
    if last <= 0 or (now - last) > stale_s:
        return False
    return FixQuality.is_rtk(g.get("fix_quality", 0))


def _pick_gnss_motion(
    ga1: dict,
    ga2: dict,
    a1_live: bool,
    a2_live: bool,
) -> tuple[float, float | None]:
    """取有实时数据接收机的 GNSS 地速与航迹角。"""
    candidates = []
    if a1_live:
        spd = float(ga1.get("speed_mps", 0.0))
        if spd > 0:
            candidates.append((spd, float(ga1.get("course_deg", 0.0))))
    if a2_live:
        spd = float(ga2.get("speed_mps", 0.0))
        if spd > 0:
            candidates.append((spd, float(ga2.get("course_deg", 0.0))))
    if not candidates:
        return 0.0, None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0]


def _apply_matrix(M: np.ndarray, x: float, y: float):
    """用 3×3 齐次矩阵变换 2D 点。"""
    p = M @ np.array([x, y, 1.0])
    return float(p[0]), float(p[1])

