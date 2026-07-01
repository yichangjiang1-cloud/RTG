"""
平面运动速度估计：按实时位移方向计算地速，融合 GNSS RMC 标量速度。
带死区与 EMA，避免静止时 ±0.000 跳动。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

DEADBAND_MPS = 0.03
EMA_ALPHA = 0.35
MIN_DISPLACEMENT_M = 1e-4


@dataclass
class MotionSpeedState:
    speed: float = 0.0
    vx: float = 0.0
    vy: float = 0.0


def _snap(value: float, deadband: float) -> float:
    return 0.0 if abs(value) < deadband else value


def hold_gnss_speed(
    state: MotionSpeedState,
    gnss_speed_mps: float,
    course_deg: float | None = None,
    *,
    deadband: float = DEADBAND_MPS,
    alpha: float = EMA_ALPHA,
) -> float:
    """GNSS RMC 地速保持（位移未变、仅重复发布融合状态时）。"""
    if gnss_speed_mps < deadband:
        return state.speed

    if course_deg is not None and (abs(state.vx) > 1e-9 or abs(state.vy) > 1e-9):
        cr = math.radians(course_deg)
        # 航迹角：北为 0、东为 90 → LYCS 速度分量近似
        cvx = math.sin(cr)
        cvy = math.cos(cr)
        dot = state.vx * cvx + state.vy * cvy
        signed = math.copysign(gnss_speed_mps, dot if abs(dot) >= 1e-15 else 1.0)
    elif abs(state.speed) >= deadband:
        signed = math.copysign(gnss_speed_mps, state.speed)
    else:
        signed = gnss_speed_mps

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
    *,
    deadband: float = DEADBAND_MPS,
    alpha: float = EMA_ALPHA,
) -> float:
    """
    根据 LYCS 平面位移 (dx, dy) 与可选 GNSS 地速更新平滑速度。

    仅在位移超过 MIN_DISPLACEMENT_M 时调用；零位移重复帧不得衰减速度。
    """
    if dt <= 0:
        state.speed = _snap(state.speed, deadband)
        return state.speed

    disp = math.hypot(dx, dy)
    if disp < MIN_DISPLACEMENT_M:
        return hold_gnss_speed(
            state, gnss_speed_mps, course_deg, deadband=deadband, alpha=alpha
        )

    vx, vy = dx / dt, dy / dt
    pos_speed = math.hypot(vx, vy)
    gnss_ok = gnss_speed_mps >= deadband
    pos_ok = pos_speed >= deadband

    if not gnss_ok and not pos_ok:
        state.vx = 0.0
        state.vy = 0.0
        state.speed = _snap(0.0, deadband)
        return state.speed

    if gnss_ok and pos_ok:
        mag = 0.65 * gnss_speed_mps + 0.35 * pos_speed
    elif gnss_ok:
        mag = gnss_speed_mps
    else:
        mag = pos_speed

    if abs(state.vx) > 1e-9 or abs(state.vy) > 1e-9:
        dot = vx * state.vx + vy * state.vy
        sign = dot if abs(dot) >= 1e-15 else (vx + vy)
    else:
        sign = vx + vy
    signed = math.copysign(mag, sign)
    state.vx, state.vy = vx, vy

    state.speed = alpha * signed + (1.0 - alpha) * state.speed
    state.speed = _snap(state.speed, deadband)
    return state.speed
