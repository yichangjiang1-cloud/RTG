"""
解算模块：姿态旋转矩阵构建
实现 ZYX 欧拉角 → 旋转矩阵 R = Rz(ψ)·Ry(θ)·Rx(φ)
以及旋转矩阵 → 欧拉角的逆运算。
"""

import math
import numpy as np


def euler_to_rotation_matrix(
    roll_deg: float,
    pitch_deg: float,
    heading_deg: float,
) -> np.ndarray:
    """
    由欧拉角构建旋转矩阵（ZYX 顺序，即先绕 Z 旋转航向，再绕 Y 旋转俯仰，最后绕 X 旋转横滚）。

    R = Rz(ψ) · Ry(θ) · Rx(φ)

    各角定义（与文档一致）：
      roll_deg    φ：绕前后行驶轴（X 轴）旋转，反映左右倾斜
      pitch_deg   θ：绕横向轴（Y 轴）旋转，反映前后倾斜
      heading_deg ψ：绕垂直轴（Z 轴）旋转，定义行驶朝向

    :param roll_deg:    横滚角（°）
    :param pitch_deg:   俯仰角（°）
    :param heading_deg: 航向角（°）
    :return: 3×3 旋转矩阵 ndarray
    """
    phi   = math.radians(roll_deg)
    theta = math.radians(pitch_deg)
    psi   = math.radians(heading_deg)

    # 绕 X 轴旋转（横滚）
    Rx = np.array([
        [1,             0,              0],
        [0,  math.cos(phi), -math.sin(phi)],
        [0,  math.sin(phi),  math.cos(phi)],
    ])

    # 绕 Y 轴旋转（俯仰）
    Ry = np.array([
        [ math.cos(theta), 0, math.sin(theta)],
        [              0,  1,             0],
        [-math.sin(theta), 0, math.cos(theta)],
    ])

    # 绕 Z 轴旋转（航向）
    Rz = np.array([
        [math.cos(psi), -math.sin(psi), 0],
        [math.sin(psi),  math.cos(psi), 0],
        [0,              0,             1],
    ])

    # ZYX 组合：先航向，再俯仰，最后横滚
    R = Rz @ Ry @ Rx
    return R


def body_to_enu_rotation(
    roll_deg: float,
    pitch_deg: float,
    heading_deg: float,
) -> np.ndarray:
    """
    车体 (X 前, Y 左, Z 上) → ENU。航向 0=北、顺时针（NMEA）。
    朝北时：+X→北、+Y→西。
    """
    phi = math.radians(roll_deg)
    theta = math.radians(pitch_deg)
    psi = math.radians(heading_deg)
    cx, sx = math.cos(phi), math.sin(phi)
    cy, sy = math.cos(theta), math.sin(theta)
    cz, sz = math.cos(psi), math.sin(psi)
    Rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    Ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    # 航向 0：X→北 (0,1,0)，Y→西 (-1,0,0)
    Rz = np.array([[sz, -cz, 0], [cz, sz, 0], [0, 0, 1]])
    return Rz @ Ry @ Rx


def body_enu_to_nmea_euler(R: np.ndarray) -> tuple:
    """
    从车体→ENU 旋转矩阵提取 NMEA 欧拉角（与 body_to_enu_rotation 互逆）。

    heading：北 0、顺时针。roll/pitch 由 R 第三行取出，与 Rz 无关。
    |roll|>90° 时折到 (-90, 90]：刚体 Y 与安装定义相反时 Kabsch 会给出
    接近 ±180° 的横滚（页面上常见 -169），对近水平车体取等价小角。
    """
    heading = math.degrees(math.atan2(float(R[0, 0]), float(R[1, 0]))) % 360.0
    if heading >= 359.95:
        heading = 0.0
    z0 = float(np.clip(-R[2, 0], -1.0, 1.0))
    pitch = math.degrees(math.asin(z0))
    roll = math.degrees(math.atan2(float(R[2, 1]), float(R[2, 2])))
    if roll > 90.0:
        roll -= 180.0
    elif roll < -90.0:
        roll += 180.0
    return roll, pitch, heading


def rotation_matrix_to_euler(R: np.ndarray) -> tuple:
    """
    从旋转矩阵（ZYX 顺序）提取欧拉角。

    :param R: 3×3 旋转矩阵
    :return: (roll_deg, pitch_deg, heading_deg) 单位：度
    """
    # 处理 gimbal lock
    if abs(R[2, 0]) >= 1.0 - 1e-6:
        heading = 0.0
        if R[2, 0] < 0:        # pitch = +90°
            pitch = 90.0
            roll = math.degrees(math.atan2(R[0, 1], R[0, 2]))
        else:                   # pitch = -90°
            pitch = -90.0
            roll = math.degrees(math.atan2(-R[0, 1], -R[0, 2]))
    else:
        pitch   = math.degrees(math.asin(-R[2, 0]))
        roll    = math.degrees(math.atan2(R[2, 1], R[2, 2]))
        heading = math.degrees(math.atan2(R[1, 0], R[0, 0]))
        heading = heading % 360.0
        if heading >= 359.95:
            heading = 0.0

    return roll, pitch, heading

