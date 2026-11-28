"""
大车速度：作业点地速 = 走行 + ω×r。
中控展示的是走行（沿航向平移），必须用航向变化把转动切向扣掉。

幅值优先用作业点差分（扣 ω×r 后）；RMC/VTG 仅在位移不足时作备用，
避免 GNSS 天线地速量化 + 慢更新把仪表盘拖慢、拖糊。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from src.solver.gantry_frame import heading_axes_lycs

DEADBAND_MPS = 0.03
# 输入已提到 ~10 Hz（RMC/VTG 为主源），α 大一点响应快；0.75 ≈ 0.1 s 时间常数
EMA_ALPHA = 0.75
MIN_DISPLACEMENT_M = 1e-4
MIN_COURSE_ALIGN = 0.3
# 超过此航向变化率视为在转；GNSS 地速又接近 0 时按原地转处理
MIN_YAW_RATE_DPS = 8.0
PAIR_DT = 0.05
# 双机在线时两个采纳样本的最小间隔。原 0.18 会把速度压到 5 Hz 并制造 0.4 s 滞后，
# 现在幅值以 RMC/VTG 为主，位置差分只兜底，0.08 s 足够避开"一新一旧"的配对帧。
FLUSH_DT = 0.08
# 停止迟滞：连续 N 帧低于 ENTER 才归零，超过 EXIT 才重新报速度
STOP_ENTER_MPS = 0.02
STOP_EXIT_MPS = 0.04
STOP_CONFIRM_FRAMES = 2
# 位移足够时，GNSS 与差分幅值差过大则仍信差分（抑制天线地速偏置）
GNSS_POS_REJECT_RATIO = 2.5


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
    # 停止迟滞状态：stopped=True 时速度强制 0，直到幅值超过 STOP_EXIT_MPS
    stopped: bool = False
    still_frames: int = 0


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


def _apply_stop_hysteresis(state: MotionSpeedState) -> float:
    """
    停止迟滞：低于 STOP_ENTER 连续 STOP_CONFIRM_FRAMES 帧才归零；
    归零后要超过 STOP_EXIT 才重新报速度。避免停车时在 0/0.05 之间来回跳。
    """
    spd = state.speed
    if state.stopped:
        if abs(spd) > STOP_EXIT_MPS:
            state.stopped = False
            state.still_frames = 0
            return spd
        state.speed = 0.0
        return 0.0
    if abs(spd) < STOP_ENTER_MPS:
        state.still_frames += 1
        if state.still_frames >= STOP_CONFIRM_FRAMES:
            state.stopped = True
            state.speed = 0.0
            return 0.0
        return spd
    state.still_frames = 0
    return spd


def _signed_gnss_speed(
    state: MotionSpeedState,
    gnss_speed_mps: float,
    course_deg: float | None,
    deadband: float,
    r2d=None,
    gnss_signed_mps: float | None = None,
) -> float:
    """
    RMC/VTG 只提供标量地速。优先用调用方算好的带符号合成值
    （两侧按航向投影后平均，转向切向已抵消）；没有时才用位移/航迹定号。
    """
    if gnss_signed_mps is not None and abs(gnss_signed_mps) >= deadband:
        return math.copysign(gnss_speed_mps, gnss_signed_mps)
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
    gnss_signed_mps: float | None = None,
) -> float:
    """GNSS RMC 地速保持（位移未变、仅重复发布融合状态时）。"""
    if gnss_speed_mps < deadband:
        state.still_frames += 1
        if state.stopped or state.still_frames >= STOP_CONFIRM_FRAMES:
            state.stopped = True
            state.speed = 0.0
            return 0.0
        return state.speed

    signed = _signed_gnss_speed(
        state, gnss_speed_mps, course_deg, deadband, r2d, gnss_signed_mps
    )
    state.speed = alpha * signed + (1.0 - alpha) * state.speed
    state.speed = _snap(state.speed, deadband)
    return _apply_stop_hysteresis(state)


def _pick_speed_magnitude(
    pos_speed: float,
    gnss_speed_mps: float,
    *,
    deadband: float,
    gnss_available: bool = True,
) -> float | None:
    """
    选幅值：**RMC/VTG 为主**，位置差分只在 RMC 完全没数据时兜底。

    机内多普勒是各接收机独立算的，不受"另一台还没到"的滞后影响；
    而位置差分在双机错拍时会被 L-Point 前后抽动污染（实测停着能到 0.5 m/s）。
    只要 RMC 有数据就完全不看位置差分——包括"RMC 说确实停了"这种情况。
    """
    if gnss_available:
        return gnss_speed_mps if gnss_speed_mps >= deadband else None
    if pos_speed >= 0.15:
        return pos_speed
    return None


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
    gnss_signed_mps: float | None = None,
    gnss_available: bool = True,
) -> float:
    """
    大车速度 = 沿航向的走行速度。

    幅值以 RMC/VTG 为主（两侧带符号合成，见 _pick_speed_magnitude），
    位置差分兜底，并且已经扣掉绕转心的 ω×r 切向。
    """
    if dt <= 0:
        state.speed = _snap(state.speed, deadband)
        return _apply_stop_hysteresis(state)

    disp = math.hypot(dx, dy)
    omega = 0.0
    if heading_deg is not None and heading_prev_deg is not None:
        omega = yaw_rate_rad(heading_prev_deg, heading_deg, dt)

    if disp < MIN_DISPLACEMENT_M:
        if abs(math.degrees(omega)) >= MIN_YAW_RATE_DPS and gnss_speed_mps < deadband:
            state.vx = 0.0
            state.vy = 0.0
            state.speed = _snap(0.0, deadband)
            return _apply_stop_hysteresis(state)
        return hold_gnss_speed(
            state, gnss_speed_mps, course_deg, deadband=deadband, alpha=alpha, r2d=r2d,
            gnss_signed_mps=gnss_signed_mps,
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

    mag = _pick_speed_magnitude(
        pos_speed, gnss_speed_mps, deadband=deadband, gnss_available=gnss_available
    )
    if mag is None:
        # 没有任何可信幅值：半衰收掉，别一下跳 0（车真停时约 0.3 s 归零），
        # 也别保留旧值（那是"停了还显示速度"的来源）。
        state.vx = 0.0
        state.vy = 0.0
        state.speed = _snap(state.speed * 0.5, deadband)
        return _apply_stop_hysteresis(state)

    # 符号：RMC 合成值有数就用它；否则沿用位移方向
    if gnss_signed_mps is not None and abs(gnss_signed_mps) >= deadband:
        sign = gnss_signed_mps
    elif abs(state.vx) > 1e-9 or abs(state.vy) > 1e-9:
        dot = vx * state.vx + vy * state.vy
        sign = dot if abs(dot) >= 1e-15 else (vx + vy)
    else:
        sign = vx + vy
    signed = math.copysign(mag, sign) if abs(sign) > 1e-15 else mag
    state.vx, state.vy = vx, vy

    state.speed = alpha * signed + (1.0 - alpha) * state.speed
    state.speed = _snap(state.speed, deadband)
    return _apply_stop_hysteresis(state)


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
