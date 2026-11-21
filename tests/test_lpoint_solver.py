"""
单元测试：L-Point 刚体变换解算器
测试 P_final = P_gps − R · L_arm 公式的正确性。
"""

import sys
import unittest
import numpy as np
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.solver.lpoint_solver import LPointSolver


class TestLPointSolver(unittest.TestCase):
    """L-Point 解算器单元测试。"""

    def test_zero_attitude(self):
        """测试：零姿态角时，L-Point = GPS - L_arm（无旋转）。"""
        L_arm = [0.1, 0.2, -8.0]  # [dx, dy, dz]
        solver = LPointSolver(L_arm)

        gps_pos = np.array([100.0, 200.0, 10.0])
        roll, pitch, heading = 0.0, 0.0, 0.0

        lpoint = solver.solve(gps_pos, roll, pitch, heading)

        # R = I（单位矩阵），所以 P_final = P_gps - L_arm
        expected = gps_pos - np.array(L_arm)
        np.testing.assert_array_almost_equal(lpoint, expected, decimal=6)

    def test_heading_90deg(self):
        """测试：航向角 90° 时的旋转变换。"""
        L_arm = [1.0, 0.0, 0.0]  # 沿 X
        solver = LPointSolver(L_arm)

        gps_pos = np.array([0.0, 0.0, 0.0])
        roll, pitch, heading = 0.0, 0.0, 90.0

        lpoint = solver.solve(gps_pos, roll, pitch, heading)

        # 航向角 90° → Rz(90°) 使 [1, 0, 0] 旋转为 [0, 1, 0]
        # P_final = [0, 0, 0] - [0, 1, 0] = [0, -1, 0]
        np.testing.assert_array_almost_equal(lpoint, [0.0, -1.0, 0.0], decimal=6)

    def test_roll_pitch_combined(self):
        """测试：横滚 + 俯仰组合姿态。"""
        L_arm = [0.0, 0.0, 5.0]  # 杆臂：天线在基准点上方 5 m（与 config 里 dz=+20.9 同一约定）
        solver = LPointSolver(L_arm)

        gps_pos = np.array([100.0, 200.0, 8.0])
        roll, pitch, heading = 5.0, -3.0, 45.0

        lpoint = solver.solve(gps_pos, roll, pitch, heading)

        # 验证：Z 分量应该小于 GPS 的 Z（因为杆臂向下，旋转后投影到地面）
        self.assertLess(lpoint[2], gps_pos[2])

    def test_height_correction(self):
        """测试：带高程残差补偿的解算。"""
        L_arm = [0.0, 0.0, -10.0]
        solver = LPointSolver(L_arm)

        gps_pos = np.array([0.0, 0.0, 10.0])
        delta_z = 0.5  # 天线偏高 0.5m

        lpoint_raw = solver.solve(gps_pos, 0.0, 0.0, 0.0)
        lpoint_corrected = solver.solve_with_height_correction(
            gps_pos, 0.0, 0.0, 0.0, delta_z
        )

        # 修正后 Z 应减去 delta_z
        self.assertAlmostEqual(lpoint_corrected[2], lpoint_raw[2] - delta_z, places=6)

    def test_update_l_arm(self):
        """测试：动态更新杆臂向量。"""
        solver = LPointSolver([1.0, 2.0, 3.0])
        gps_pos = np.array([0.0, 0.0, 0.0])

        # 初始杆臂
        lpoint1 = solver.solve(gps_pos, 0.0, 0.0, 0.0)
        self.assertAlmostEqual(lpoint1[0], -1.0, places=6)

        # 更新杆臂
        solver.update_l_arm([2.0, 3.0, 4.0])
        lpoint2 = solver.solve(gps_pos, 0.0, 0.0, 0.0)
        self.assertAlmostEqual(lpoint2[0], -2.0, places=6)


if __name__ == "__main__":
    unittest.main()

