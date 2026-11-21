"""
大车速度：作业点地速 = 走行 + ω×r。
中控展示的是走行（沿航向平移），必须用航向变化把转动切向扣掉。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from src.solver.gantry_frame import heading_axes_lycs

DEADBAND_MPS = 0.03
EMA_ALPHA = 0.35
MIN_DISPLACEMENT_M = 1e-4
MIN_COURSE_ALIGN = 0.3
# 超过此航向变化率视为在转；GNSS 地速又接近 0 时按原地转处理
MIN_YAW_RATE_DPS = 8.0
PAIR_DT = 0.05
FLUSH_DT = 0.18


def use_gantry_speed_sample(live_count: int, since_any: float, dt: float) -> bool:
    """
    双机先到的一包作业点会偏，不能拿来算速度。
    后到的配对包（since_any < 50ms）且与上一参考间隔足够时才用。
    """
    if dt <= 0:
        return False
    wait_for_pair = live_count >= 2 and since_any >= PAIR_DT
    if wait_for_pair and dt < FLUSH_DT:
        return False
    if dt < PAIR_DT:
        return False
    return True


@dataclass
class MotionSpeedState:
    speed: float = 0.0
    vx: float = 0.0
    vy: float = 0.0


def _snap(value: float, deadband: float) -> float:
    return 0.0 if abs(value) < deadband else value


def shortest_arc_deg(h0: float, h1: float) -> float:
    """h0→h1 最短有向转角，范围 (−180, 180]。"""
    return (h1 - h0 + 180.0) % 360.0 - 180.0


def mid_heading_deg(h0: float, h1: float) -> float:
    """两航向的最短弧中点，避免 359°→1° 平均成 180°。"""
    return (h0 + 0.5 * shortest_arc_deg(h0, h1)) % 360.0


def lycs_along_heading(dx: float, dy: float, heading_deg: float, r2d=None) -> float:
    """LYCS 位移在航向前向上的分量。航向 0=北、顺时针；r2d 为拟合后的 LYCS 轴。"""
    (fx, fy), _ = heading_axes_lycs(heading_deg, r2d)
    return dx * fx + dy * fy


def lycs_left_heading(dx: float, dy: float, heading_deg: float, r2d=None) -> float:
    """
    LYCS 位移在车右侧的分量（与原 lycs_left_heading 数值约定一致：车右为正）。
    名称沿用旧接口，供 translational_along_m 的转弦计算使用。
    """
    _, (rx, ry) = heading_axes_lycs(heading_deg, r2d)
    return dx * rx + dy * ry


def yaw_rate_rad(h0: float, h1: float, dt: float) -> float:
    """航向角速度（rad/s）。LYCS 右手系：NMEA 航向增加（顺时针）= +ωz。"""
    if dt <= 0:
        return 0.0
    return math.radians(shortest_arc_deg(h0, h1)) / dt


def translational_along_m(dx: float, dy: float, h0: float, h1: float, r2d=None) -> float:
    """
    作业点位移扣掉刚体转动后的沿航向平移（米）。

    作业点绕转心走圆弧，弦长投到新航向上是 r(cosΔψ−1)，顺逆都为负。
    用末端航向的横向分量反推转弦：along_rot = −lat · tan(Δψ/2) = r(cosΔψ−1)。
    扣掉后只剩走行。
    """
    dpsi = math.radians(shortest_arc_deg(h0, h1))
    along = lycs_along_heading(dx, dy, h1, r2d)
    if abs(dpsi) < 1e-8:
        return along
    lat = lycs_left_heading(dx, dy, h1, r2d)
    along_rot = lat * math.tan(0.5 * dpsi)
    return along - along_rot


def _signed_gnss_speed(
    state: MotionSpeedState,
    gnss_speed_mps: float,
    course_deg: float | None,
    deadband: float,
    r2d=None,
) -> float:
    """VTG/RMC 只提供标量地速；符号跟位移，航迹与位移接近正交时不用航迹翻号。"""
    if course_deg is not None and (abs(state.vx) > 1e-9 or abs(state.vy) > 1e-9):
        motion = math.hypot(state.vx, state.vy)
        if motion > 1e-9:
            along = lycs_along_heading(state.vx, state.vy, course_deg, r2d)
            align = along / motion
            if abs(align) >= MIN_COURSE_ALIGN:
                return math.copysign(gnss_speed_mps, align)
    if abs(state.speed) >= deadband:
        return math.copysign(gnss_speed_mps, state.speed)
    return gnss_speed_mps


def hold_gnss_speed(
    state: MotionSpeedState,
    gnss_speed_mps: float,
    course_deg: float | None = None,
    *,
    deadband: float = DEADBAND_MPS,
    alpha: float = EMA_ALPHA,
    r2d=None,
) -> float:
    """GNSS RMC 地速保持（位移未变、仅重复发布融合状态时）。"""
    if gnss_speed_mps < deadband:
        return state.speed

    signed = _signed_gnss_speed(state, gnss_speed_mps, course_deg, deadband, r2d)
    state.speed = alpha * signed + (1.0 - alpha) * state.speed
    state.speed = _snap(state.speed, deadband)
    return state.speed


def update_motion_speed(
    state: MotionSpeedState,
    dx: float,
    dy: float,
    dt: float,
    gnss_speed_mps: float = 0.0,
    course_deg: float | None = None,
    heading_deg: float | None = None,
    heading_prev_deg: float | None = None,
    *,
    deadband: float = DEADBAND_MPS,
    alpha: float = EMA_ALPHA,
    r2d=None,
) -> float:
    """
    大车速度 = 作业点地速去掉 ω×r 后的沿航向走行。

    有航向变化时用 translational_along_m；GNSS 有地速时以 VTG/RMC 为幅值
   （模拟器 VTG 是车体中心速度：只转=0，直行=2）。
    """
    if dt <= 0:
        state.speed = _snap(state.speed, deadband)
        return state.speed

    disp = math.hypot(dx, dy)
    omega = 0.0
    if heading_deg is not None and heading_prev_deg is not None:
        omega = yaw_rate_rad(heading_prev_deg, heading_deg, dt)

    if disp < MIN_DISPLACEMENT_M:
        if abs(math.degrees(omega)) >= MIN_YAW_RATE_DPS and gnss_speed_mps < deadband:
            state.vx = 0.0
            state.vy = 0.0
            state.speed = _snap(0.0, deadband)
            return state.speed
        return hold_gnss_speed(
            state, gnss_speed_mps, course_deg, deadband=deadband, alpha=alpha, r2d=r2d
        )

    if heading_deg is not None:
        h0 = heading_prev_deg if heading_prev_deg is not None else heading_deg
        along = translational_along_m(dx, dy, h0, heading_deg, r2d)
        vx, vy = along / dt, 0.0
        pos_speed = abs(vx)
        rotating = abs(math.degrees(omega)) >= MIN_YAW_RATE_DPS
        if rotating and gnss_speed_mps < deadband:
            # 原地转：作业点在动，但走行为 0。残差当噪声。
            vx, vy = 0.0, 0.0
            pos_speed = 0.0
            gnss_speed_mps = 0.0
    else:
        vx, vy = dx / dt, dy / dt
        pos_speed = math.hypot(vx, vy)
    gnss_ok = gnss_speed_mps >= deadband
    pos_ok = pos_speed >= deadband

    if not gnss_ok and not pos_ok:
        state.vx = 0.0
        state.vy = 0.0
        state.speed = _snap(0.0, deadband)
        return state.speed

    if gnss_ok:
        # 中控实时速度跟车体中心地速：有 VTG/RMC 时以它为幅值，稳定
        mag = gnss_speed_mps
    else:
        mag = pos_speed

    if abs(state.vx) > 1e-9 or abs(state.vy) > 1e-9:
        dot = vx * state.vx + vy * state.vy
        sign = dot if abs(dot) >= 1e-15 else (vx + vy)
    else:
        sign = vx + vy
    signed = math.copysign(mag, sign) if abs(sign) > 1e-15 else mag
    state.vx, state.vy = vx, vy

    state.speed = alpha * signed + (1.0 - alpha) * state.speed
    state.speed = _snap(state.speed, deadband)
    return state.speed


def relative_planar_speed(
    dx: float,
    dy: float,
    dt: float,
    *,
    min_disp_m: float = 0.02,
    deadband: float = 0.05,
) -> float:
    """
    小车相对速度：一段时间内相对零点的平面位移 / 时间。

    与仪表盘 ΔX/ΔY 同一套数。位移小于 min_disp_m（默认 2 cm）视为没动，速度为 0。
    大车走/转而相对位置不变时为 0。符号沿变化更大的那一轴。
    """
    if dt <= 0:
        return 0.0
    dist = math.hypot(dx, dy)
    if dist < min_disp_m:
        return 0.0
    speed = dist / dt
    if speed < deadband:
        return 0.0
    sign_src = dy if abs(dy) >= abs(dx) else dx
    if abs(sign_src) < 1e-12:
        return speed
    return math.copysign(speed, sign_src)


def update_trolley_beam_speed(
    state: MotionSpeedState,
    dx_body: float,
    dy_body: float,
    dt: float,
    *,
    deadband: float = DEADBAND_MPS,
    alpha: float = EMA_ALPHA,
) -> float:
    """
    小车速度：相对大车零点的车体平面位移变化（Δx、Δy）。

    零点固定时 Δ(中心−零点)=Δbody。相对零点不动则速度为 0，与大车地速无关。
    不用 RMC/VTG；不含 Z（高程噪声）。
    """
    return update_motion_speed(
        state, dx_body, dy_body, dt, 0.0, None,
        deadband=deadband, alpha=alpha,
    )
