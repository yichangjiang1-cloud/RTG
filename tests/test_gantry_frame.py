"""大车车体系坐标变换"""

import math
import unittest

from src.solver.gantry_frame import lycs_to_gantry_body


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


if __name__ == "__main__":
    unittest.main()
