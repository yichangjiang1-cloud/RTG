"""四天线 / 双接收机姿态解算单元测试"""

import math
import unittest

import numpy as np

from src.fusion.gantry_attitude import (
    GantryAttitudeConfig,
    compute_gantry_attitude,
    compute_gantry_attitude_compare,
    compute_nmea_height_roll_attitude,
    default_antenna_body,
    solve_four_antenna_attitude,
)
from src.models.gnss_data import GnssRaw, FixQuality
from src.solver.attitude import body_enu_to_nmea_euler, euler_to_rotation_matrix
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
    def test_four_antenna_falls_back_to_dual_gga(self):
        """四天线未齐时回退到单机双 GGA；无四天线刚体则横滚为 0。"""
        ga1 = _make_gnss("gantry_a1", 30.0, 120.0, 10.0, 30.00018, 120.0, 10.0)
        att = compute_gantry_attitude(
            ga1, None, True, False, GantryAttitudeConfig(mode="four_antenna"), 30.0, 120.0, 10.0
        )
        self.assertIsNotNone(att)
        self.assertEqual(att.source, "dual_gga_a1")
        self.assertAlmostEqual(att.roll_deg, 0.0)

        att_dual = compute_gantry_attitude(
            ga1, None, True, False,
            GantryAttitudeConfig(mode="dual_receiver"),
            30.0, 120.0, 10.0,
        )
        self.assertIsNotNone(att_dual)
        self.assertEqual(att_dual.source, "dual_gga_a1")
        self.assertAlmostEqual(att_dual.roll_deg, 0.0)

    def test_dual_gga_ignores_receiver_nmea_roll(self):
        """双 GGA 主通道不再沿用接收机 NMEA 横滚字段。"""
        ga1 = _make_gnss("gantry_a1", 30.0, 120.0, 10.0, 30.00018, 120.0, 10.0)
        ga1.heading = 10.0
        ga1.roll = 2.5
        att = compute_gantry_attitude(
            ga1, None, True, False,
            GantryAttitudeConfig(mode="dual_receiver"),
            30.0, 120.0, 10.0,
        )
        self.assertIsNotNone(att)
        self.assertAlmostEqual(att.roll_deg, 0.0)

    def test_short_baseline_yields_no_attitude(self):
        """基线过短时双 GGA 无效；两主点/NMEA 主通道已移除，结果为 None。"""
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
        self.assertIsNone(att)

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

    def test_nmea_height_roll_primary(self):
        ga1 = _make_gnss("gantry_a1", 30.0, 120.0, 10.0, 30.0, 120.0, 10.0)
        ga1.heading = 90.0
        ga1.pitch = 1.0
        # 右机在左机东侧约 25 m，略低 → 有横滚
        ga2 = _make_gnss("gantry_a2", 30.0, 120.00026, 9.5, 30.0, 120.00026, 9.5)
        cfg = GantryAttitudeConfig(roll_span_m=0.0)  # 不依赖手填跨距
        att = compute_nmea_height_roll_attitude(ga1, ga2, True, True, cfg)
        self.assertIsNotNone(att)
        self.assertAlmostEqual(att.heading_deg, 90.0)
        self.assertAlmostEqual(att.pitch_deg, 1.0)
        self.assertIn("nmea", att.source)
        self.assertIn("roll_gga", att.source)
        self.assertNotAlmostEqual(att.roll_deg, 0.0, places=2)

    def test_compare_both_methods(self):
        span = 25.0
        baseline = 20.0
        body = default_antenna_body(span, baseline)
        ref_lat, ref_lon, ref_alt = 30.0, 120.0, 10.0
        roll = 2.0
        hdg = 45.0
        r = euler_to_rotation_matrix(roll, 0.0, hdg)
        ecef = {}
        for key, bp in body.items():
            e, n, u = (r @ np.array(bp)).tolist()
            ecef[key] = enu_to_ecef(e, n, u, ref_lat, ref_lon, ref_alt)

        ga1 = _make_gnss("gantry_a1", ref_lat, ref_lon, ref_alt, 0, 0, 0)
        _set_primary_from_ecef(ga1, *ecef["a1"])
        _set_secondary_from_ecef(ga1, *ecef["a2"])
        ga1.heading = hdg
        ga1.pitch = 0.0

        ga2 = _make_gnss("gantry_a2", ref_lat, ref_lon, ref_alt, 0, 0, 0)
        _set_primary_from_ecef(ga2, *ecef["b1"])
        _set_secondary_from_ecef(ga2, *ecef["b2"])

        cfg = GantryAttitudeConfig(
            mode="four_antenna",
            baseline_m=baseline,
            roll_span_m=span,
            antenna_body=body,
        )
        cmp = compute_gantry_attitude_compare(
            ga1, ga2, True, True, cfg, ref_lat, ref_lon, ref_alt
        )
        self.assertTrue(cmp.four_ready)
        self.assertIsNotNone(cmp.primary)
        self.assertIsNotNone(cmp.four_antenna)
        self.assertEqual(cmp.primary_method, "four_antenna")
        self.assertEqual(cmp.primary.source, "four_antenna")
        self.assertIsNotNone(cmp.delta)
        self.assertAlmostEqual(cmp.four_antenna.roll_deg, roll, delta=0.5)
        self.assertAlmostEqual(cmp.four_antenna.heading_deg, hdg, delta=0.5)

        cfg_dual = GantryAttitudeConfig(
            mode="dual_receiver",
            baseline_m=baseline,
            roll_span_m=span,
            antenna_body=body,
        )
        cmp2 = compute_gantry_attitude_compare(
            ga1, ga2, True, True, cfg_dual, ref_lat, ref_lon, ref_alt
        )
        self.assertEqual(cmp2.primary_method, "dual_receiver")
        self.assertIsNotNone(cmp2.dual_receiver)

    def test_four_antenna_nmea_north_is_zero(self):
        """车头朝北时四天线航向应为 0°（NMEA），不是欧拉 90°/270°。"""
        span = 5.0
        baseline = 20.0
        body = default_antenna_body(span, baseline)
        ref_lat, ref_lon, ref_alt = 30.5384792173, 114.349230567, 9.8771
        ecef = {}
        for key, bp in body.items():
            east = -float(bp[1])   # Y 左 → 西
            north = float(bp[0])   # X 前 → 北
            up = float(bp[2])
            ecef[key] = enu_to_ecef(east, north, up, ref_lat, ref_lon, ref_alt)

        att = solve_four_antenna_attitude(ecef, body, ref_lat, ref_lon, ref_alt)
        self.assertIsNotNone(att)
        self.assertAlmostEqual(att.heading_deg, 0.0, delta=0.5)
        self.assertAlmostEqual(att.pitch_deg, 0.0, delta=0.5)
        self.assertAlmostEqual(att.roll_deg, 0.0, delta=0.5)

    def test_nmea_euler_unwraps_near_180_roll(self):
        """Kabsch 给出 -169° 横滚时应折到 (-90,90]。"""
        R = np.array([
            [0.0, 1.0, 0.0],
            [1.0, 0.0, 0.0],
            [0.0, 0.0, -1.0],
        ], dtype=float)
        # R[2,2]<0 → atan2 给出 ±180 附近
        roll, _pitch, _hdg = body_enu_to_nmea_euler(R)
        self.assertLessEqual(abs(roll), 90.0)

    def test_four_antenna_body_enu_matches_pashr(self):
        """按 NMEA 车体→ENU 摆天线，横滚应为 PASHR 的 -0.15，不能是 -169。"""
        from src.solver.attitude import body_to_enu_rotation

        span, baseline = 5.0, 20.0
        body = default_antenna_body(span, baseline)
        ref_lat, ref_lon, ref_alt = 30.0, 120.0, 10.0
        roll, pitch, hdg = -0.15, 0.56, 119.33
        r = body_to_enu_rotation(roll, pitch, hdg)
        ecef_map = {}
        for key, bp in body.items():
            e, n, u = (r @ np.array(bp)).tolist()
            ecef_map[key] = enu_to_ecef(e, n, u, ref_lat, ref_lon, ref_alt)
        att = solve_four_antenna_attitude(ecef_map, body, ref_lat, ref_lon, ref_alt)
        self.assertIsNotNone(att)
        self.assertAlmostEqual(att.heading_deg, hdg, delta=0.5)
        self.assertAlmostEqual(att.pitch_deg, pitch, delta=0.3)
        self.assertAlmostEqual(att.roll_deg, roll, delta=0.3)
        self.assertLess(abs(att.roll_deg), 90.0)

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
