"""大车车体系坐标变换"""

import math
import unittest

from src.solver.gantry_frame import heading_axes_lycs, lycs_to_gantry_body


class GantryFrameTest(unittest.TestCase):
    def test_origin_at_lpoint(self):
        bx, by, bz = lycs_to_gantry_body(10.0, 20.0, 5.0, 10.0, 20.0, 5.0, 0.0)
        self.assertAlmostEqual(bx, 0.0, places=6)
        self.assertAlmostEqual(by, 0.0, places=6)
        self.assertAlmostEqual(bz, 0.0, places=6)

    def test_translation_with_gantry(self):
        """大车平移 100m 时，同物理点相对 L-Point 不变。"""
        bx1, by1, _ = lycs_to_gantry_body(12.0, 25.0, 5.0, 10.0, 20.0, 5.0, 30.0)
        bx2, by2, _ = lycs_to_gantry_body(112.0, 125.0, 5.0, 110.0, 120.0, 5.0, 30.0)
        self.assertAlmostEqual(bx1, bx2, places=3)
        self.assertAlmostEqual(by1, by2, places=3)

    def test_fitted_axis_rigid_rotation_keeps_body_coords(self):
        """拟合贝位轴后，车体绕转心原地转，固连的小车与 L-Point 车体坐标不变。"""
        # +Y 沿车头（与 CarMode 静止场景的拟合结果一致）
        ye, yn = -0.747, -0.6648
        norm = math.hypot(ye, yn)
        ye, yn = ye / norm, yn / norm
        r2d = ((yn, -ye), (ye, yn))
        centre = (11.2769, 4.0219, 0.0)  # 车体转心（LYCS）
        lpoint_off_m = 0.74  # L-Point 相对转心的车体偏置（前向）
        trolley_body = (0.0, 5.0)  # 小车：前向 0、车右侧 5 m

        def pose(h_deg):
            (fx, fy), (rx, ry) = heading_axes_lycs(h_deg, r2d)
            cx, cy, cz = centre
            lx = cx + lpoint_off_m * fx
            ly = cy + lpoint_off_m * fy
            ax, ay = trolley_body
            tx = cx + ax * fx + ay * rx
            ty = cy + ax * fy + ay * ry
            return (tx, ty, cz), (lx, ly, cz)

        vals = []
        for dh in (0.0, 1.0, 3.0):
            h = 228.2316 + dh
            (tx, ty, tz), (lx, ly, lz) = pose(h)
            vals.append(lycs_to_gantry_body(tx, ty, tz, lx, ly, lz, h, r2d=r2d))
        for bx, by, _ in vals[1:]:
            self.assertAlmostEqual(bx, vals[0][0], places=6)
            self.assertAlmostEqual(by, vals[0][1], places=6)
        # 小车仍在车右侧 5 m 处（减去 L-Point 偏置后的前向分量）
        self.assertAlmostEqual(vals[0][1], 5.0, delta=0.05)

    def test_default_axis_matches_legacy_formula(self):
        """未拟合（默认 X=北、Y=东）时数值与旧公式一致。"""
        h = math.radians(37.0)
        dx, dy = 3.0, -2.0
        bx, by, _ = lycs_to_gantry_body(dx, dy, 0.0, 0.0, 0.0, 0.0, 37.0)
        self.assertAlmostEqual(bx, dx * math.cos(h) + dy * math.sin(h), places=9)
        self.assertAlmostEqual(by, -dx * math.sin(h) + dy * math.cos(h), places=9)


if __name__ == "__main__":
    unittest.main()
