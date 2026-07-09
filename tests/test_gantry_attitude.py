"""四天线 / 双接收机姿态解算单元测试"""

import math
import unittest

import numpy as np

from src.fusion.gantry_attitude import (
    GantryAttitudeConfig,
    compute_gantry_attitude,
    default_antenna_body,
    solve_four_antenna_attitude,
)
from src.models.gnss_data import GnssRaw, FixQuality
from src.solver.attitude import euler_to_rotation_matrix
from src.solver.coordinate_transform import wgs84_to_ecef, ecef_to_wgs84, enu_to_ecef


def _make_gnss(
    rid: str,
    lat: float,
    lon: float,
    alt: float,
    lat2: float,
    lon2: float,
    alt2: float,
) -> GnssRaw:
    g = GnssRaw(receiver_id=rid)
    g.latitude = lat
    g.longitude = lon
    g.altitude = alt
    g.fix_quality = FixQuality.RTK_FIXED
    g.ecef_x, g.ecef_y, g.ecef_z = wgs84_to_ecef(lat, lon, alt)
    g.antenna2_latitude = lat2
    g.antenna2_longitude = lon2
    g.antenna2_altitude = alt2
    g.antenna2_ecef_x, g.antenna2_ecef_y, g.antenna2_ecef_z = wgs84_to_ecef(lat2, lon2, alt2)
    g.antenna2_latitude = lat2
    g.antenna2_longitude = lon2
    g.antenna2_altitude = alt2
    return g


def _set_primary_from_ecef(g: GnssRaw, ex: float, ey: float, ez: float) -> None:
    g.ecef_x, g.ecef_y, g.ecef_z = ex, ey, ez
    g.latitude, g.longitude, g.altitude = ecef_to_wgs84(ex, ey, ez)


def _set_secondary_from_ecef(g: GnssRaw, ex: float, ey: float, ez: float) -> None:
    g.antenna2_ecef_x, g.antenna2_ecef_y, g.antenna2_ecef_z = ex, ey, ez
    g.antenna2_latitude, g.antenna2_longitude, g.antenna2_altitude = ecef_to_wgs84(ex, ey, ez)


class GantryAttitudeTest(unittest.TestCase):
    def test_single_receiver_no_roll_without_nmea(self):
        ga1 = _make_gnss("gantry_a1", 30.0, 120.0, 10.0, 30.00018, 120.0, 10.0)
        cfg = GantryAttitudeConfig()
        att = compute_gantry_attitude(ga1, None, True, False, cfg, 30.0, 120.0, 10.0)
        self.assertIsNotNone(att)
        self.assertEqual(att.source, "dual_gga")
        self.assertAlmostEqual(att.roll_deg, 0.0)

    def test_single_receiver_nmea_roll(self):
        ga1 = _make_gnss("gantry_a1", 30.0, 120.0, 10.0, 30.00018, 120.0, 10.0)
        ga1.roll = 2.5
        att = compute_gantry_attitude(ga1, None, True, False, GantryAttitudeConfig(), 30.0, 120.0, 10.0)
        self.assertAlmostEqual(att.roll_deg, 2.5)

    def test_short_baseline_falls_back_to_nmea(self):
        """基线 < 0.5 m 时不用双 GGA，采用 PASHR/HDT。"""
        ga1 = _make_gnss(
            "gantry_a1",
            30.53822969,
            114.34946534,
            71.947,
            30.53822969,
            114.34946534,
            71.948,
        )
        ga1.heading = 187.94
        ga1.pitch = 2.46
        ga1.roll = 0.65
        att = compute_gantry_attitude(
            ga1, None, True, False, GantryAttitudeConfig(), 30.53822969, 114.34946534, 71.947
        )
        self.assertIsNotNone(att)
        self.assertEqual(att.source, "nmea")
        self.assertAlmostEqual(att.heading_deg, 187.94, places=1)
        self.assertAlmostEqual(att.pitch_deg, 2.46, places=1)
        self.assertAlmostEqual(att.roll_deg, 0.65, places=1)

    def test_dual_receiver_averages_heading(self):
        span = 25.0
        baseline = 20.0
        body = default_antenna_body(span, baseline)
        ref_lat, ref_lon, ref_alt = 30.123456, 120.654321, 10.0
        hdg = 0.0
        r = euler_to_rotation_matrix(0.0, 0.0, hdg)
        ecef = {}
        for key, bp in body.items():
            e, n, u = (r @ np.array(bp)).tolist()
            ecef[key] = enu_to_ecef(e, n, u, ref_lat, ref_lon, ref_alt)

        ga1 = _make_gnss("gantry_a1", ref_lat, ref_lon, ref_alt, 0, 0, 0)
        _set_primary_from_ecef(ga1, *ecef["a1"])
        _set_secondary_from_ecef(ga1, *ecef["a2"])

        ga2 = _make_gnss("gantry_a2", ref_lat, ref_lon, ref_alt, 0, 0, 0)
        _set_primary_from_ecef(ga2, *ecef["b1"])
        _set_secondary_from_ecef(ga2, *ecef["b2"])

        cfg = GantryAttitudeConfig(mode="dual_receiver", baseline_m=baseline, antenna_body=body)
        att = compute_gantry_attitude(ga1, ga2, True, True, cfg, ref_lat, ref_lon, ref_alt)
        self.assertIsNotNone(att)
        self.assertIn("dual_receiver", att.source)
        self.assertAlmostEqual(att.pitch_deg, 0.0, delta=0.5)

    def test_four_antenna_mode_roll(self):
        span = 25.0
        baseline = 20.0
        body = default_antenna_body(span, baseline)
        ref_lat, ref_lon, ref_alt = 30.0, 120.0, 10.0
        roll = 3.0
        r = euler_to_rotation_matrix(roll, 0.0, 0.0)
        ecef_map = {}
        for key, bp in body.items():
            e, n, u = (r @ np.array(bp)).tolist()
            ecef_map[key] = enu_to_ecef(e, n, u, ref_lat, ref_lon, ref_alt)

        att = solve_four_antenna_attitude(ecef_map, body, ref_lat, ref_lon, ref_alt)
        self.assertIsNotNone(att)
        self.assertAlmostEqual(att.roll_deg, roll, delta=0.5)


if __name__ == "__main__":
    unittest.main()
