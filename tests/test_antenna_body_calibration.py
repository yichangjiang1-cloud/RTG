"""四天线 ECEF → 车体系安装坐标标定"""

import unittest

from src.fusion.antenna_body_calibration import compute_antenna_body_from_ecef
from src.solver.coordinate_transform import enu_to_ecef, wgs84_to_ecef


class AntennaBodyCalibrationTest(unittest.TestCase):
    def test_end_pair_layout_recovers_body(self):
        """上水电气在原点，动力房在东 5 m，下水端在南 20 m。X 指向上水，Y 指向电气房。"""
        lat, lon, alt = 30.0, 120.0, 10.0
        a1 = wgs84_to_ecef(lat, lon, alt)
        a2 = enu_to_ecef(5.0, 0.0, 0.0, lat, lon, alt)
        b1 = enu_to_ecef(0.0, -20.0, 0.0, lat, lon, alt)
        b2 = enu_to_ecef(5.0, -20.0, 0.0, lat, lon, alt)

        body, baseline_m, span_m = compute_antenna_body_from_ecef(a1, a2, b1, b2)

        self.assertAlmostEqual(baseline_m, 20.0, places=2)
        self.assertAlmostEqual(span_m, 5.0, places=2)
        self.assertAlmostEqual(body["a1"][0], 0.0, places=2)
        self.assertAlmostEqual(body["a1"][1], 0.0, places=2)
        self.assertAlmostEqual(body["a2"][0], 0.0, places=1)
        self.assertAlmostEqual(body["a2"][1], -5.0, places=1)
        self.assertAlmostEqual(body["b1"][0], -20.0, places=1)
        self.assertAlmostEqual(body["b1"][1], 0.0, places=1)
        self.assertAlmostEqual(body["b2"][0], -20.0, places=1)
        self.assertAlmostEqual(body["b2"][1], -5.0, places=1)


if __name__ == "__main__":
    unittest.main()
