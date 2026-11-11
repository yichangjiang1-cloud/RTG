"""大车速度：VTG/RMC 航迹与位移正交时不得翻号。"""

import math

from src.solver.motion_speed import MotionSpeedState, hold_gnss_speed, update_motion_speed


def test_hold_gnss_ignores_perpendicular_course():
    state = MotionSpeedState(speed=2.0, vx=2.0, vy=0.0)  # 沿堆场 +X
    # VTG 航迹 0°=北，与 +X 正交；旧逻辑会按 vy≈0 随机翻号
    hold_gnss_speed(state, 2.0, course_deg=0.0)
    assert state.speed > 1.5


def test_hold_gnss_keeps_aligned_course_sign():
    state = MotionSpeedState(speed=2.0, vx=0.0, vy=2.0)  # 向北
    hold_gnss_speed(state, 2.0, course_deg=0.0)
    assert state.speed > 1.5


def test_update_blend_stays_near_two():
    state = MotionSpeedState()
    for _ in range(20):
        update_motion_speed(state, 0.20, 0.0, 0.10, gnss_speed_mps=2.0, course_deg=0.0)
    assert 1.7 <= state.speed <= 2.3


def test_along_heading_ignores_rotation_sweep():
    """朝北时纯横向位移（原地转的切向）不计入大车速度。"""
    state = MotionSpeedState()
    for _ in range(15):
        update_motion_speed(
            state, 0.0, 0.52, 0.10, gnss_speed_mps=0.0, heading_deg=0.0,
        )
    assert abs(state.speed) < 0.05


def test_omega_removes_spin_chord():
    """作业点绕转心转：投到新航向为负；扣掉 ω×r 后为 0。"""
    from src.solver.motion_speed import lycs_along_heading, translational_along_m

    r = 10.0
    h0, h1 = 0.0, 12.0
    x0, y0 = -r, 0.0
    h1r = math.radians(h1)
    x1, y1 = -r * math.cos(h1r), -r * math.sin(h1r)
    dx, dy = x1 - x0, y1 - y0
    assert lycs_along_heading(dx, dy, h1) < -0.05
    assert abs(translational_along_m(dx, dy, h0, h1)) < 0.02
    # 逆时针同样为 0
    h1 = -12.0
    h1r = math.radians(h1)
    x1, y1 = -r * math.cos(h1r), -r * math.sin(h1r)
    assert abs(translational_along_m(x1 - x0, y1 - y0, h0, h1)) < 0.02


def test_along_heading_keeps_forward_when_turning():
    """边走边转：只保留沿航向 2 m/s，丢掉横向 ω×r。"""
    state = MotionSpeedState()
    for _ in range(20):
        update_motion_speed(
            state, 0.20, 0.52, 0.10, gnss_speed_mps=2.0, heading_deg=0.0,
        )
    assert 1.7 <= abs(state.speed) <= 2.3


def test_dual_publish_waits_for_pair():
    from src.solver.motion_speed import use_gantry_speed_sample

    assert use_gantry_speed_sample(2, 0.100, 0.100) is False  # 双机第一包
    assert use_gantry_speed_sample(2, 0.001, 0.100) is True   # 配对包
    assert use_gantry_speed_sample(2, 0.001, 0.001) is False  # 间隔太短
    assert use_gantry_speed_sample(1, 0.100, 0.100) is True   # 单机
    assert use_gantry_speed_sample(2, 0.100, 0.200) is True   # 超时补一帧


def test_paired_spin_frames_speed_stable():
    """用日志里配对帧（两侧到齐）的作业点，原地转速度应接近 0 且不跳。"""
    frames = [
        (-0.2779, 0.3958, 321.63),
        (-0.2861, 0.4021, 324.10),
        (-0.2935, 0.4072, 327.10),
        (-0.2996, 0.4111, 330.13),
        (-0.3051, 0.4143, 333.23),
        (-0.3095, 0.4166, 335.75),
        (-0.3135, 0.4185, 339.02),
        (-0.3200, 0.4212, 344.92),
        (-0.3225, 0.4222, 348.38),
        (-0.3245, 0.4228, 351.75),
        (-0.3264, 0.4234, 354.38),
        (-0.3278, 0.4238, 357.50),
        (-0.3291, 0.4242, 0.25),
        (-0.3302, 0.4245, 3.60),
        (-0.3312, 0.4248, 6.00),
        (-0.3320, 0.4250, 9.38),
        (-0.3328, 0.4252, 11.86),
        (-0.3334, 0.4254, 15.26),
        (-0.3339, 0.4255, 18.76),
        (-0.3344, 0.4257, 19.68),
    ]
    state = MotionSpeedState()
    speeds = []
    for i in range(1, len(frames)):
        x0, y0, h0 = frames[i - 1]
        x1, y1, h1 = frames[i]
        update_motion_speed(
            state, x1 - x0, y1 - y0, 0.10,
            gnss_speed_mps=0.0,
            heading_deg=h1,
            heading_prev_deg=h0,
        )
        speeds.append(state.speed)
    assert max(abs(s) for s in speeds) < 0.08
    assert max(speeds) - min(speeds) < 0.10


def test_drive_plus_turn_keeps_two():
    """边走边转：扣掉 ω×r 后走行仍为 2 m/s。"""
    state = MotionSpeedState()
    h = 0.0
    for _ in range(20):
        h1 = (h + 3.0) % 360.0
        # 沿航向 0.2 m + 后向点转 3° 的切向
        r = 10.0
        h0r, h1r = math.radians(h), math.radians(h1)
        x0, y0 = -r * math.cos(h0r), -r * math.sin(h0r)
        x1, y1 = -r * math.cos(h1r), -r * math.sin(h1r)
        dx = (x1 - x0) + 0.20 * math.cos(h1r)
        dy = (y1 - y0) + 0.20 * math.sin(h1r)
        update_motion_speed(
            state, dx, dy, 0.10,
            gnss_speed_mps=2.0,
            heading_deg=h1,
            heading_prev_deg=h,
        )
        h = h1
    assert 1.7 <= abs(state.speed) <= 2.3


def test_rear_lpoint_spin_speed_near_zero_both_ways():
    """后向作业点绕转心转：扣掉角速度后两侧都接近 0。"""
    r = 10.0
    dt = 0.10
    for sign, label in ((+1.0, "cw"), (-1.0, "ccw")):
        state = MotionSpeedState()
        h = 0.0
        x, y = -r, 0.0
        for _ in range(25):
            h1 = (h + sign * 3.0) % 360.0
            h1r = math.radians(h1)
            x1, y1 = -r * math.cos(h1r), -r * math.sin(h1r)
            update_motion_speed(
                state, x1 - x, y1 - y, dt,
                gnss_speed_mps=0.0,
                heading_deg=h1,
                heading_prev_deg=h,
            )
            h, x, y = h1, x1, y1
        assert abs(state.speed) < 0.05, label


def test_trolley_beam_speed_ignores_gnss_when_still():
    """相对零点不动：Δx=Δy=0，速度为 0。"""
    from src.solver.motion_speed import update_trolley_beam_speed

    state = MotionSpeedState()
    for _ in range(15):
        update_trolley_beam_speed(state, 0.0, 0.0, 0.10)
    assert abs(state.speed) < 0.03


def test_trolley_beam_speed_follows_rel_y():
    """相对零点只沿 Y 走 0.5 m/s。"""
    from src.solver.motion_speed import update_trolley_beam_speed

    state = MotionSpeedState()
    for _ in range(25):
        update_trolley_beam_speed(state, 0.0, 0.05, 0.10)
    assert 0.40 <= abs(state.speed) <= 0.60


def test_relative_speed_zero_when_position_unchanged():
    """相对位置不变（大车在走、小车没动）→ 相对速度为 0。"""
    from src.solver.motion_speed import relative_planar_speed

    assert relative_planar_speed(0.0, 0.0, 0.4) == 0.0
    assert relative_planar_speed(0.008, -0.006, 0.4) == 0.0  # 1 cm 抖动


def test_relative_speed_follows_delta_xy():
    """0.4 s 内 Δx=0.12、Δy=0.16 → 0.5 m/s，符号跟变化更大的 Y。"""
    from src.solver.motion_speed import relative_planar_speed

    v = relative_planar_speed(0.12, 0.16, 0.4)
    assert abs(v - 0.5) < 1e-9
    assert v > 0
    v_back = relative_planar_speed(0.12, -0.16, 0.4)
    assert v_back < 0


def test_trolley_relative_xy_from_origin():
    """零点(0,0)、中心从(1,1)走到(1.05,1.04)：带上 Δx。"""
    from src.solver.motion_speed import update_trolley_beam_speed

    state = MotionSpeedState()
    for _ in range(25):
        update_trolley_beam_speed(state, 0.05, 0.04, 0.10)
    # √(0.5²+0.4²)=0.64
    assert 0.50 <= abs(state.speed) <= 0.78
