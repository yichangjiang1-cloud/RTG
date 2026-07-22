"""
融合节点：fusion_node
订阅 driver_node 输出的 GNSS/IMU 原始数据，运行 EKF，
输出融合后的位姿状态到 EventBus（TOPIC_FUSED_STATE）。

数据流：
  TOPIC_GNSS_RAW (GnssRaw)  ──┐
                               ├─→ FusionNode(EKF) ──→ TOPIC_FUSED_STATE (dict)
  TOPIC_IMU_RAW  (ImuRaw)   ──┘

丢星补偿：
  GNSS 中断 < 10s 时，切换到 IMU 惯导补位模式（ImuMechanization）。
  超过阈值后标记 DataSource.INVALID，bridge_node 停止下发。
"""

import logging
import math
import time
import threading
import numpy as np
from typing import Optional, Dict

from src.core.event_bus import get_bus, EventBus
from src.models.gnss_data import GnssRaw, FixQuality
from src.models.imu_data import ImuRaw
from src.models.positioning import DataSource
from src.fusion.ekf import EKF
from src.fusion.imu_mechanization import ImuMechanization
from src.solver.coordinate_transform import wgs84_to_ecef, ecef_to_enu
from src.solver.attitude import euler_to_rotation_matrix
from src.fusion.gantry_attitude import (
    GantryAttitudeConfig,
    build_gantry_antennas_payload,
    compute_gantry_attitude,
    derive_heading_pitch_from_dual_gga,
    normalize_heading_deg,
)
from src.utils.config_loader import get_config

logger = logging.getLogger(__name__)


class ReceiverState:
    """
    单台接收机的 EKF 融合状态封装。
    gantry_a1=左侧(A1+A2)、gantry_a2=右侧(B1+B2)、trolley 各独立一套。
    """

    def __init__(self, receiver_id: str, ekf_params: dict):
        self.receiver_id = receiver_id
        self.ekf = EKF(**ekf_params)
        self.mech = ImuMechanization()

        # 最新融合结果（供 solver_node 消费）
        self.last_gnss: Optional[GnssRaw] = None
        self.last_imu: Optional[ImuRaw] = None

        # 当前融合位姿（NED 相对于参考点，米）
        self.pos_ned = np.zeros(3)
        self.vel_ned = np.zeros(3)
        self.roll_deg: float = 0.0
        self.pitch_deg: float = 0.0
        self.heading_deg: float = 0.0

        # GNSS 状态追踪
        self.last_gnss_time: float = 0.0
        self.gnss_outage_start: float = 0.0
        self.is_coasting: bool = False

        # NED 参考点（首次 GNSS 定位后锁定）
        self.ref_lat: Optional[float] = None
        self.ref_lon: Optional[float] = None
        self.ref_alt: Optional[float] = None

        # ECEF 坐标（最终输出给 solver）
        self.ecef_x: float = 0.0
        self.ecef_y: float = 0.0
        self.ecef_z: float = 0.0
        self.lat: float = 0.0
        self.lon: float = 0.0
        self.fix_quality: int = 0

        self._lock = threading.Lock()


class FusionNode:
    """
    融合节点：GNSS + IMU EKF 融合，输出平滑位姿。

    三台接收机独立融合，共享同一路 IMU 数据。
    """

    def __init__(self, bus: Optional[EventBus] = None):
        cfg = get_config()
        self._bus = bus or get_bus()

        # EKF 参数（从配置读取）
        ekf_params = {
            "sigma_pos":        cfg.get("ekf.sigma_pos",        0.05),
            "sigma_vel":        cfg.get("ekf.sigma_vel",        0.01),
            "sigma_att":        cfg.get("ekf.sigma_att",        0.001),
            "sigma_gyro_bias":  cfg.get("ekf.sigma_gyro_bias",  1e-5),
            "sigma_accel_bias": cfg.get("ekf.sigma_accel_bias", 1e-4),
            "sigma_gnss_h":     cfg.get("ekf.sigma_gnss_h",     0.03),
            "sigma_gnss_v":     cfg.get("ekf.sigma_gnss_v",     0.05),
        }

        # 丢星补偿策略参数
        self._gnss_outage_max   = cfg.get("ekf.gnss_outage_max_s",       10.0)
        self._coasting_pos_limit = cfg.get("ekf.imu_coasting_pos_limit", 0.25)

        # 三台接收机各自的状态
        self._states: Dict[str, ReceiverState] = {
            rid: ReceiverState(rid, ekf_params)
            for rid in ("gantry_a1", "gantry_a2", "trolley")
        }

        # 最新 IMU 数据（共享给所有接收机）
        self._last_imu: Optional[ImuRaw] = None
        self._last_imu_time: float = 0.0

        w_span = float(cfg.get("w_span", 23.47))
        att_block = cfg.get("gantry.attitude", {})
        self._gantry_att_cfg = GantryAttitudeConfig.from_dict(att_block, w_span=w_span)
        self._last_gantry_att_source: str = ""

        # 订阅事件
        self._bus.subscribe(EventBus.TOPIC_GNSS_RAW, self._on_gnss)
        self._bus.subscribe(EventBus.TOPIC_IMU_RAW, self._on_imu)

        logger.info(
            "FusionNode 初始化完成，管理 %d 台接收机，大车姿态模式=%s",
            len(self._states),
            self._gantry_att_cfg.mode,
        )

    def update_gantry_attitude_config(self, cloud_config: dict) -> None:
        """热更新四天线 / 双接收机姿态参数。"""
        block = cloud_config.get("config") if isinstance(cloud_config.get("config"), dict) else cloud_config
        if not isinstance(block, dict):
            return
        w_span = float(block.get("w_span", 23.47))
        att = block.get("gantry_attitude")
        if not isinstance(att, dict):
            att = block.get("gantry", {}).get("attitude") if isinstance(block.get("gantry"), dict) else {}
        self._gantry_att_cfg = GantryAttitudeConfig.from_dict(att, w_span=w_span)
        logger.info("大车姿态配置已热更新: mode=%s", self._gantry_att_cfg.mode)

    # ------------------------------------------------------------------
    # 回调：GNSS 原始数据到来
    # ------------------------------------------------------------------
    @staticmethod
    def _format_optional_angle(val: Optional[float]) -> str:
        return f"{val:.2f}°" if val is not None else "—"

    def _log_fusion_attitude(
        self,
        rid: str,
        source: str,
        heading: float,
        pitch: float,
        roll: float,
        gnss: GnssRaw,
    ) -> None:
        """打印融合后的航向/姿态及来源。"""
        nmea_hdg = self._format_optional_angle(gnss.heading)
        nmea_ptc = self._format_optional_angle(gnss.pitch)
        nmea_rol = self._format_optional_angle(gnss.roll)
        ant2 = ""
        if gnss.has_antenna2:
            ant2 = (
                f" | 双GGA基线 A2=({gnss.antenna2_latitude:.8f},"
                f"{gnss.antenna2_longitude:.8f},{gnss.antenna2_altitude:.3f}m)"
            )
        logger.debug(
            "[%s] 融合姿态 | 来源=%s | heading=%.2f° pitch=%.2f° roll=%.2f° | "
            "NMEA解析 hdg=%s pitch=%s roll=%s%s",
            rid,
            source,
            heading,
            pitch,
            roll,
            nmea_hdg,
            nmea_ptc,
            nmea_rol,
            ant2,
        )

    def _on_gnss(self, gnss: GnssRaw) -> None:
        """
        处理单台接收机的 GNSS 数据。

        1. 更新 EKF（GNSS 位置量测更新）
        2. 输出融合状态
        """
        rid = gnss.receiver_id
        state = self._states.get(rid)
        if state is None:
            return

        if not gnss.is_valid:
            # 无效定位时触发/继续惯导补位
            self._handle_gnss_outage(state, gnss)
            return

        now = time.time()
        with state._lock:
            # 首次定位：锁定 NED 参考点
            if state.ref_lat is None:
                state.ref_lat  = gnss.latitude
                state.ref_lon  = gnss.longitude
                state.ref_alt  = gnss.altitude
                # 初始化 EKF 标称状态
                state.pos_ned  = np.zeros(3)
                state.vel_ned  = np.array([0.0, 0.0, 0.0])
                if rid == "trolley":
                    state.roll_deg = 0.0
                    state.pitch_deg = 0.0
                    state.heading_deg = 0.0
                else:
                    state.roll_deg    = gnss.roll    or 0.0
                    state.pitch_deg   = gnss.pitch   or 0.0
                    state.heading_deg = gnss.heading or 0.0
                logger.info("[%s] GNSS 首次定位，建立 NED 参考点 (%.7f, %.7f, %.3f)",
                            rid, gnss.latitude, gnss.longitude, gnss.altitude)

            # GNSS 量测 → NED
            E_gnss, N_gnss, U_gnss = ecef_to_enu(
                gnss.ecef_x, gnss.ecef_y, gnss.ecef_z,
                state.ref_lat, state.ref_lon, state.ref_alt,
            )
            gnss_ned = np.array([N_gnss, E_gnss, -U_gnss])  # NED: U→-D

            # 若从惯导补位恢复，检查位置连续性
            if state.is_coasting:
                jump = np.linalg.norm(gnss_ned - state.pos_ned)
                logger.info("[%s] GNSS 恢复，惯导期间位置漂移 %.4f m", rid, jump)
                state.is_coasting = False

            # EKF 量测更新
            correction = state.ekf.update_gnss_position(gnss_ned, state.pos_ned)

            # 误差回代：修正标称状态
            state.pos_ned += correction[0:3]
            state.vel_ned += correction[3:6]
            att_corr = correction[6:9]   # rad
            state.roll_deg    += math.degrees(att_corr[0])
            state.pitch_deg   += math.degrees(att_corr[1])
            state.heading_deg += math.degrees(att_corr[2])
            state.heading_deg = normalize_heading_deg(state.heading_deg)

            state.ekf.reset_state()

            # 大车左右接收机：姿态在 _publish 中按双机/四天线联合解算；小车仅双 GGA 定位置
            if rid in ("gantry_a1", "gantry_a2"):
                hp = derive_heading_pitch_from_dual_gga(gnss)
                if hp is not None:
                    state.heading_deg = normalize_heading_deg(hp[0])
                    state.pitch_deg = hp[1]
                    state.roll_deg = float(gnss.roll or 0.0)
                elif gnss.heading is not None:
                    state.heading_deg = normalize_heading_deg(float(gnss.heading))
                    state.pitch_deg = float(gnss.pitch or 0.0)
                    state.roll_deg = float(gnss.roll or 0.0)

            # 更新 ECEF 输出
            state.ecef_x = gnss.ecef_x
            state.ecef_y = gnss.ecef_y
            state.ecef_z = gnss.ecef_z
            state.lat    = gnss.latitude
            state.lon    = gnss.longitude
            state.fix_quality = int(gnss.fix_quality)
            state.last_gnss      = gnss
            state.last_gnss_time = now

            # 同步初始化 IMU 推算器（以备丢星）
            state.mech.initialize(
                pos_ned    = tuple(state.pos_ned),
                vel_ned    = tuple(state.vel_ned),
                roll_deg   = state.roll_deg,
                pitch_deg  = state.pitch_deg,
                heading_deg= state.heading_deg,
                gyro_bias  = state.ekf.get_gyro_bias(),
                accel_bias = state.ekf.get_accel_bias(),
            )

        # 发布本台接收机融合结果（只要有新 GNSS 就触发一次发布）
        self._publish()

    # ------------------------------------------------------------------
    # 回调：IMU 数据到来
    # ------------------------------------------------------------------
    def _on_imu(self, imu: ImuRaw) -> None:
        """
        IMU 数据更新：
          1. 驱动 EKF 预测步（更新协方差）
          2. 推进惯导补位积分（若正在丢星）
        """
        now = time.time()
        dt  = now - self._last_imu_time if self._last_imu_time > 0 else 0.005
        dt  = max(min(dt, 0.1), 0.001)   # 限制在 1ms ~ 100ms
        self._last_imu      = imu
        self._last_imu_time = now

        gyro  = np.array([imu.gyro_x,  imu.gyro_y,  imu.gyro_z])
        accel = np.array([imu.accel_x, imu.accel_y, imu.accel_z])

        for state in self._states.values():
            with state._lock:
                # EKF 预测：构建 F、Q
                R_bn = euler_to_rotation_matrix(
                    state.roll_deg, state.pitch_deg, state.heading_deg
                )
                # 导航系比力（去重力后）
                import src.fusion.imu_mechanization as _m
                accel_bias = state.ekf.get_accel_bias()
                gyro_bias  = state.ekf.get_gyro_bias()
                a_nav = R_bn @ (accel - accel_bias) - np.array([0, 0, _m.GRAVITY])

                F = state.ekf.build_F(a_nav, R_bn, dt)
                Q = state.ekf.build_Q(dt)
                state.ekf.predict(F, Q)

                # 若正在惯导补位，推进积分
                if state.is_coasting and state.mech.is_initialized:
                    state.mech.propagate(gyro, accel, dt)
                    mech_s = state.mech.state
                    # 将惯导位置更新到标称位置
                    state.pos_ned = np.array([mech_s.pos_n, mech_s.pos_e, mech_s.pos_d])
                    state.vel_ned = np.array([mech_s.vel_n, mech_s.vel_e, mech_s.vel_d])
                    state.roll_deg    = mech_s.roll
                    state.pitch_deg   = mech_s.pitch
                    state.heading_deg = mech_s.heading
                    # 回推 ECEF（用于 solver）
                    self._update_ecef_from_ned(state)

    def _handle_gnss_outage(self, state: ReceiverState, gnss: GnssRaw) -> None:
        """
        GNSS 无效时的丢星处理。
        - < 10s：切换到惯导补位
        - ≥ 10s：标记 INVALID，停止补位
        """
        now = time.time()
        outage_duration = now - state.last_gnss_time if state.last_gnss_time > 0 else 0.0

        if not state.is_coasting:
            state.is_coasting      = True
            state.gnss_outage_start = now
            logger.warning("[%s] GNSS 丢失，切换惯导补位", state.receiver_id)

        if outage_duration > self._gnss_outage_max:
            state.fix_quality = int(FixQuality.INVALID)
            logger.error("[%s] GNSS 中断超过 %.1fs，位置不可信",
                         state.receiver_id, self._gnss_outage_max)

    def _update_ecef_from_ned(self, state: ReceiverState) -> None:
        """将 NED 标称位置回推为 ECEF 坐标（用于 solver 消费）。"""
        if state.ref_lat is None:
            return
        # NED → ENU
        N, E, D = state.pos_ned
        # ENU → ECEF（通过参考点偏移）
        ref_lat_r = math.radians(state.ref_lat)
        ref_lon_r = math.radians(state.ref_lon)
        X0, Y0, Z0 = wgs84_to_ecef(state.ref_lat, state.ref_lon, state.ref_alt)
        # 旋转：ENU → ECEF
        R_enu2ecef = np.array([
            [-math.sin(ref_lon_r),
             -math.sin(ref_lat_r) * math.cos(ref_lon_r),
              math.cos(ref_lat_r) * math.cos(ref_lon_r)],
            [ math.cos(ref_lon_r),
             -math.sin(ref_lat_r) * math.sin(ref_lon_r),
              math.cos(ref_lat_r) * math.sin(ref_lon_r)],
            [ 0,
              math.cos(ref_lat_r),
              math.sin(ref_lat_r)],
        ])
        dxyz = R_enu2ecef @ np.array([E, N, -D])   # NED → ENU: U=-D, swap N/E
        state.ecef_x = X0 + dxyz[0]
        state.ecef_y = Y0 + dxyz[1]
        state.ecef_z = Z0 + dxyz[2]

    # ------------------------------------------------------------------
    # 发布融合状态
    # ------------------------------------------------------------------
    def _publish(self) -> None:
        """
        将三台接收机的融合状态组装成字典发布到 EventBus。
        只要大车 a1 有效就发布。
        """
        s_a1 = self._states["gantry_a1"]
        s_a2 = self._states["gantry_a2"]
        s_tly = self._states["trolley"]

        def _state_dict(s: ReceiverState) -> dict:
            with s._lock:
                coasting_dur = s.mech.state.coasting_duration if s.is_coasting else 0.0
                alt = 0.0
                speed_mps = 0.0
                course_deg = 0.0
                num_sats = 0
                hdop = 99.9
                if s.last_gnss is not None:
                    alt = float(s.last_gnss.altitude)
                    speed_mps = float(s.last_gnss.speed_mps)
                    course_deg = float(s.last_gnss.course_deg)
                    num_sats = int(s.last_gnss.num_satellites)
                    hdop = float(s.last_gnss.hdop)
                elif s.ref_alt is not None:
                    alt = float(s.ref_alt)
                now_ts = time.time()
                is_live = (
                    s.last_gnss_time > 0
                    and (now_ts - s.last_gnss_time) < 2.0
                    and s.fix_quality > int(FixQuality.INVALID)
                )
                has_antenna2 = False
                a2_ecef = (0.0, 0.0, 0.0)
                a2_lat = a2_lon = a2_alt = 0.0
                if s.last_gnss is not None and s.last_gnss.has_antenna2:
                    has_antenna2 = True
                    a2_ecef = (
                        float(s.last_gnss.antenna2_ecef_x),
                        float(s.last_gnss.antenna2_ecef_y),
                        float(s.last_gnss.antenna2_ecef_z),
                    )
                    a2_lat = float(s.last_gnss.antenna2_latitude or 0.0)
                    a2_lon = float(s.last_gnss.antenna2_longitude or 0.0)
                    a2_alt = float(s.last_gnss.antenna2_altitude or 0.0)
                return {
                    "ecef_x":   s.ecef_x,
                    "ecef_y":   s.ecef_y,
                    "ecef_z":   s.ecef_z,
                    "has_antenna2": has_antenna2,
                    "antenna2_ecef_x": a2_ecef[0],
                    "antenna2_ecef_y": a2_ecef[1],
                    "antenna2_ecef_z": a2_ecef[2],
                    "antenna2_lat": a2_lat,
                    "antenna2_lon": a2_lon,
                    "antenna2_alt": a2_alt,
                    "lat":      s.lat,
                    "lon":      s.lon,
                    "alt":      alt,
                    "speed_mps": speed_mps,
                    "course_deg": course_deg,
                    "last_gnss_time": s.last_gnss_time,
                    "num_satellites": num_sats,
                    "hdop": hdop,
                    "is_live": is_live,
                    "roll":     s.roll_deg,
                    "pitch":    s.pitch_deg,
                    "heading":  s.heading_deg,
                    "fix_quality": s.fix_quality,
                    "antenna2_fix_quality": (
                        int(s.last_gnss.antenna2_fix_quality)
                        if s.last_gnss is not None and has_antenna2
                        else 0
                    ),
                    "is_coasting": s.is_coasting,
                    "coasting_duration": coasting_dur,
                    "source": (
                        DataSource.IMU_COASTING if s.is_coasting
                        else DataSource.GNSS_RTK_FIXED if s.fix_quality == int(FixQuality.RTK_FIXED)
                        else DataSource.GNSS_RTK_FLOAT if s.fix_quality == int(FixQuality.RTK_FLOAT)
                        else DataSource.INVALID
                    ),
                }

        with s_a1._lock:
            a1_ready = s_a1.ref_lat is not None
        with s_a2._lock:
            a2_ready = s_a2.ref_lat is not None
        if not a1_ready and not a2_ready:
            return

        d_a1 = _state_dict(s_a1)
        d_a2 = _state_dict(s_a2)
        d_tly = _state_dict(s_tly)
        self._apply_gantry_attitude(s_a1, s_a2, d_a1, d_a2)
        ref = d_a1 if d_a1.get("is_live") else (d_a2 if d_a2.get("is_live") else d_a1)
        gantry_antennas = build_gantry_antennas_payload(d_a1, d_a2)

        fused = {
            "gantry_a1": d_a1,
            "gantry_a2": d_a2,
            "trolley":   d_tly,
            "gantry_antennas": gantry_antennas,
            "fix_quality":          ref.get("fix_quality", 0),
            "imu_coasting":         ref.get("is_coasting", False),
            "imu_coasting_duration": ref.get("coasting_duration", 0.0),
            "source": ref.get("source", DataSource.INVALID),
        }
        logger.debug(
            "发布融合状态 | a1 live=%s hdg=%.2f° pitch=%.2f° roll=%.2f° (%s) | "
            "a2 live=%s hdg=%.2f° | ref_fix=%s coasting=%s",
            d_a1.get("is_live"),
            d_a1.get("heading", 0.0),
            d_a1.get("pitch", 0.0),
            d_a1.get("roll", 0.0),
            d_a1.get("attitude_source", "-"),
            d_a2.get("is_live"),
            d_a2.get("heading", 0.0),
            ref.get("fix_quality", 0),
            fused.get("imu_coasting"),
        )
        self._bus.publish(EventBus.TOPIC_FUSED_STATE, fused)

    def _apply_gantry_attitude(
        self,
        s_a1: ReceiverState,
        s_a2: ReceiverState,
        d_a1: dict,
        d_a2: dict,
    ) -> None:
        """双接收机联合姿态 → 写入 gantry_a1（solver 主通道）。"""
        ga1 = s_a1.last_gnss
        ga2 = s_a2.last_gnss
        if ga1 is None or s_a1.ref_lat is None:
            return

        att = compute_gantry_attitude(
            ga1,
            ga2,
            bool(d_a1.get("is_live")),
            bool(d_a2.get("is_live")),
            self._gantry_att_cfg,
            s_a1.ref_lat,
            s_a1.ref_lon,
            s_a1.ref_alt,
        )
        if att is None:
            return

        d_a1["heading"] = att.heading_deg
        d_a1["pitch"] = att.pitch_deg
        d_a1["roll"] = att.roll_deg
        d_a1["attitude_source"] = att.source

        with s_a1._lock:
            s_a1.heading_deg = att.heading_deg
            s_a1.pitch_deg = att.pitch_deg
            s_a1.roll_deg = att.roll_deg

        if att.source != self._last_gantry_att_source:
            self._last_gantry_att_source = att.source
            logger.info(
                "[gantry] 联合姿态 | 来源=%s | hdg=%.2f° pitch=%.2f° roll=%.2f° | "
                "a1_live=%s a2_live=%s",
                att.source,
                att.heading_deg,
                att.pitch_deg,
                att.roll_deg,
                d_a1.get("is_live"),
                d_a2.get("is_live"),
            )