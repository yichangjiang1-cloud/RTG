"""
解算模块：L-Point 刚体变换
实现文档中的核心公式：P_final = P_gps − R · L_arm
将 GNSS 天线位置投影到地面作业基准点（L-Point）。
"""

import logging
import numpy as np
from typing import Tuple

from src.solver.attitude import euler_to_rotation_matrix

logger = logging.getLogger(__name__)


class LPointSolver:
    """
    L-Point 刚体变换解算器。

    原理：
      天线安装在车体上方，由于车体存在横滚（Roll）、俯仰（Pitch）、
      航向（Heading）姿态，天线相位中心相对于地面基准点（L-Point）
      发生三维空间位移。通过构建旋转矩阵 R 并补偿杆臂向量 L_arm，
      将天线坐标还原为地面 L-Point 坐标。

    公式：
      P_final = P_gps − R · L_arm

    其中：
      P_gps  : GNSS 天线位置（ECEF 或局部坐标系，米）
      L_arm  : 杆臂向量 [dx, dy, dz]（RTG 局部坐标系，米）
      R      : 旋转矩阵，R = Rz(ψ)·Ry(θ)·Rx(φ)
      P_final: L-Point 地面坐标（与 P_gps 同坐标系）
    """

    def __init__(self, l_arm: list):
        """
        :param l_arm: 杆臂向量 [dx, dy, dz]（米），从配置文件读取
        """
        self._l_arm = np.array(l_arm, dtype=float)
        logger.info(f"LPointSolver 初始化，杆臂向量: {l_arm}")

    def solve(
        self,
        gps_pos: np.ndarray,
        roll_deg: float,
        pitch_deg: float,
        heading_deg: float,
    ) -> np.ndarray:
        """
        执行 L-Point 变换。

        :param gps_pos:    天线坐标 [x, y, z]（ECEF 或 LYCS，米）
        :param roll_deg:   横滚角φ（°）
        :param pitch_deg:  俯仰角θ（°）
        :param heading_deg:航向角ψ（°）
        :return:           L-Point 坐标 [x, y, z]（与输入同坐标系，米）
        """
        R = euler_to_rotation_matrix(roll_deg, pitch_deg, heading_deg)
        p_final = np.array(gps_pos, dtype=float) - R @ self._l_arm
        return p_final

    def solve_with_height_correction(
        self,
        gps_pos: np.ndarray,
        roll_deg: float,
        pitch_deg: float,
        heading_deg: float,
        delta_z: float = 0.0,
    ) -> np.ndarray:
        """
        带 Z 轴高程残差补偿的 L-Point 变换。

        :param delta_z: Z 轴残差补偿（m），通常来自逐贝 delta 插值（vehicle_bay_transforms）
        :return:        修正后的 L-Point 坐标
        """
        p_final = self.solve(gps_pos, roll_deg, pitch_deg, heading_deg)
        p_final[2] -= delta_z   # 高程残差补偿
        return p_final

    def update_l_arm(self, l_arm: list) -> None:
        """动态更新杆臂向量（用于安装补偿调整）。"""
        self._l_arm = np.array(l_arm, dtype=float)
        logger.info(f"杆臂向量已更新: {l_arm}")

