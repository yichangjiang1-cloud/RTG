"""四天线 ECEF → 车体系安装坐标标定"""

import unittest

from src.fusion.antenna_body_calibration import compute_antenna_body_from_ecef
from src.solver.coordinate_transform import enu_to_ecef, wgs84_to_ecef


class AntennaBodyCalibrationTest(unittest.TestCase):
    def test_square_layout_recovers_body(self):
        """理想矩形：baseline=20m, span=23m"""
        lat, lon, alt = 30.0, 120.0, 10.0
        baseline = 20.0
        span = 23.0
        a1 = wgs84_to_ecef(lat, lon, alt)
        a2 = enu_to_ecef(baseline, 0.0, 0.0, lat, lon, alt)
        b1 = enu_to_ecef(0.0, -span, 0.0, lat, lon, alt)
        b2 = enu_to_ecef(baseline, -span, 0.0, lat, lon, alt)

        body, baseline_m, span_m = compute_antenna_body_from_ecef(a1, a2, b1, b2)

        self.assertAlmostEqual(baseline_m, baseline, places=2)
        self.assertAlmostEqual(span_m, span, places=2)
        self.assertAlmostEqual(body["a1"][0], 0.0, places=2)
        self.assertAlmostEqual(body["a1"][1], 0.0, places=2)
        self.assertAlmostEqual(body["a2"][0], baseline, places=1)
        self.assertAlmostEqual(body["b1"][1], -span, places=1)
        self.assertAlmostEqual(body["b2"][0], baseline, places=1)
        self.assertAlmostEqual(body["b2"][1], -span, places=1)


if __name__ == "__main__":
    unittest.main()
