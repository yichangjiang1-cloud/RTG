"""堆场多边形、拟合轴和贝位带。"""
import unittest

from src.solver.yard_geometry import (
    diesel_bays,
    electrical_bays,
    en_meters,
    fit_y_axis,
    is_electrical_bay,
    lane_deviation_m,
    lane_deviations_mm,
    parallel_reference_x,
    point_in_polygon,
    polygon_centroid,
    resolve_bay_band,
    resolve_y_axis,
    spacing_violation,
    to_yard_xy,
    two_point_y_axis,
)
from src.solver.yard_manager import YardManager


def _square():
    # 原点附近约 100m 见方，东、北各正
    return [
        (30.0, 114.0),
        (30.0, 114.001),
        (30.001, 114.001),
        (30.001, 114.0),
    ]


class TestYardGeometry(unittest.TestCase):
    def test_polygon_contains_center(self):
        poly = [(0, 0), (10, 0), (10, 10), (0, 10)]
        self.assertTrue(point_in_polygon(5, 5, poly))
        self.assertFalse(point_in_polygon(15, 5, poly))

    def test_fit_y_points_toward_increasing_bay(self):
        # 贝位号增大时向东
        points = [(1, 0.0, 0.0), (2, 50.0, 0.0), (3, 100.0, 0.0)]
        ye, yn = fit_y_axis(points)
        self.assertAlmostEqual(ye, 1.0, places=5)
        self.assertAlmostEqual(yn, 0.0, places=5)
        # 朝东看，右侧是南，+X 为负北
        x, y = to_yard_xy(0.0, -10.0, ye, yn)
        self.assertAlmostEqual(y, 0.0, places=5)
        self.assertGreater(x, 0.0)

    def test_two_points_set_plus_y(self):
        ye, yn = two_point_y_axis(0.0, 0.0, 0.0, 10.0)
        self.assertAlmostEqual(ye, 0.0, places=5)
        self.assertAlmostEqual(yn, 1.0, places=5)
        points = [(1, 0.0, 0.0), (3, 30.0, 0.0), (2, 0.0, 40.0)]
        fitted = resolve_y_axis(points, "fit", None, 30.0, 114.0)
        self.assertAlmostEqual(fitted[0], 1.0, places=4)
        chosen = resolve_y_axis(
            points, "two_point", {"source": "bays", "from_bay": 1, "to_bay": 2}, 30.0, 114.0,
        )
        self.assertAlmostEqual(chosen[0], 0.0, places=5)
        self.assertAlmostEqual(chosen[1], 1.0, places=5)
        self.assertIsNone(resolve_y_axis(
            points, "two_point", {"source": "bays", "from_bay": 1, "to_bay": 9}, 30.0, 114.0,
        ))

    def test_bay_band_and_spacing(self):
        bays = [(1, 0.0), (2, 50.0)]
        self.assertEqual(resolve_bay_band(0.2, bays, 0.3), 1)
        self.assertEqual(resolve_bay_band(10.0, bays, 0.3), 0)
        self.assertIsNone(spacing_violation(bays, 0.3))
        self.assertIsNotNone(spacing_violation([(1, 0.0), (2, 0.5)], 0.3))

    def test_four_tire_center_picks_yard(self):
        boundary = [{"lat": lat, "lon": lon} for lat, lon in _square()]
        yards = [{
            "yard_id": "A01",
            "name": "A",
            "origin_lat": 30.0,
            "origin_lon": 114.0,
            "origin_alt": 0,
            "boundary": boundary,
        }]
        mgr = YardManager("missing.json", yards)
        self.assertEqual(mgr.detect_yard_from_point(30.0005, 114.0005), "A01")
        self.assertIsNone(mgr.detect_yard_from_point(30.01, 114.01))
        self.assertIsNone(mgr.detect_yard_from_point(None, None))

    def test_lane_deviation_toward_centroid_is_negative(self):
        square = [(1.0, 0.0), (1.0, 10.0), (-1.0, 10.0), (-1.0, 0.0)]
        cx, _cy = polygon_centroid(square)
        self.assertAlmostEqual(cx, 0.0, places=6)
        shifted = [(p[0] + 5.0, p[1]) for p in square]
        cx, _cy = polygon_centroid(shifted)
        self.assertGreater(cx, 0.0)
        self.assertAlmostEqual(lane_deviation_m(0.2, cx), -0.2, places=6)
        self.assertAlmostEqual(lane_deviation_m(-0.2, cx), 0.2, places=6)

    def test_diesel_deviation_is_relative_to_its_own_line(self):
        # 场地中心在电气线 X=0 与动力线 X=20 之间。朝中心为负。
        cx = 10.0
        self.assertAlmostEqual(lane_deviation_m(0.0, cx, 0.0), 0.0, places=6)
        self.assertAlmostEqual(lane_deviation_m(0.2, cx, 0.0), -0.2, places=6)
        self.assertAlmostEqual(lane_deviation_m(20.0, cx, 20.0), 0.0, places=6)
        self.assertAlmostEqual(lane_deviation_m(19.8, cx, 20.0), -0.2, places=6)
        self.assertAlmostEqual(lane_deviation_m(20.2, cx, 20.0), 0.2, places=6)

    def test_parallel_reference_is_mean_x_and_needs_two_points(self):
        self.assertIsNone(parallel_reference_x([20.0]))
        self.assertIsNone(parallel_reference_x([]))
        self.assertAlmostEqual(parallel_reference_x([19.0, 21.0, 20.0]), 20.0, places=6)

    def test_each_side_uses_its_own_bay_line(self):
        tires = [(1.7, True), (1.5, True), (20.2, True), (20.0, False)]
        mm = lane_deviations_mm(tires, 10.0, 1.5, 20.0)
        self.assertEqual(mm, [-200, 0, 200, 0])

    def test_missing_diesel_line_uses_electrical_line(self):
        tires = [(1.5, True), (1.7, True), (1.5, True), (1.9, True)]
        self.assertEqual(lane_deviations_mm(tires, 10.0, 1.5, None), [0, -200, 0, -400])

    def test_missing_electrical_line_uses_the_origin_axis(self):
        tires = [(0.2, True), (0.0, True), (20.0, True), (20.2, True)]
        self.assertEqual(
            lane_deviations_mm(tires, 10.0, None, None),
            [-200, 0, -20000, -20200],
        )

    def test_diesel_bays_are_separate_from_the_axis(self):
        mixed = [
            {"bay_side": "elec"},
            {"bay_side": "diesel"},
            {"bay_side": "diesel"},
        ]
        self.assertEqual(len(electrical_bays(mixed)), 1)
        self.assertEqual(len(diesel_bays(mixed)), 2)

    def test_en_scale(self):
        from src.solver.coordinate_transform import ecef_to_enu, wgs84_to_ecef

        east, north = en_meters(30.0001, 114.0, 30.0, 114.0)
        x, y, z = wgs84_to_ecef(30.0001, 114.0, 0.0)
        e, n, _u = ecef_to_enu(x, y, z, 30.0, 114.0, 0.0)
        self.assertAlmostEqual(east, e, places=4)
        self.assertAlmostEqual(north, n, places=4)
        self.assertAlmostEqual(east, 0.0, places=3)
        self.assertGreater(north, 11.0)

    def test_diesel_bays_do_not_tilt_the_electrical_axis(self):
        self.assertTrue(is_electrical_bay({"bay_no": 1}))
        self.assertTrue(is_electrical_bay({"bay_side": "elec"}))
        self.assertFalse(is_electrical_bay({"bay_side": "diesel"}))
        mixed = [
            {"bay_no": 1, "bay_side": "elec", "east": 0.0},
            {"bay_no": 2, "bay_side": "elec", "east": 50.0},
            {"bay_no": 1, "bay_side": "diesel", "east": 20.0},
            {"bay_no": 2, "bay_side": "diesel", "east": 70.0},
        ]
        elec = electrical_bays(mixed)
        points = [(p["bay_no"], p["east"], 0.0) for p in elec]
        ye, yn = fit_y_axis(points)
        self.assertAlmostEqual(ye, 1.0, places=5)
        self.assertAlmostEqual(yn, 0.0, places=5)
        self.assertEqual([p["bay_side"] for p in elec], ["elec", "elec"])

    def test_solver_uses_only_diesel_points_for_the_parallel_line(self):
        from src.solver.solver_node import SolverNode

        class _Cal:
            survey_points = [
                {"bay_side": "elec", "lat": 1.0, "lon": 1.0, "alt": 0.0},
                {"bay_side": "elec", "lat": 3.0, "lon": 1.0, "alt": 0.0},
                {"bay_side": "diesel", "lat": 0.0, "lon": 0.0, "alt": 0.0},
                {"bay_side": "diesel", "lat": 0.0, "lon": 1.0, "alt": 0.0},
                {"bay_side": "diesel", "lat": 2.0, "lon": 1.0, "alt": 0.0},
            ]

        class _Ctx:
            def get(self, _yard_id):
                return _Cal()

        class _Transform:
            def wgs84_to_lycs(self, lat, lon, alt):
                return (20.0 + lat, lon, alt)

        node = SolverNode.__new__(SolverNode)
        node._calibration_ctx = _Ctx()
        self.assertAlmostEqual(node._side_reference_x(_Transform(), "B01", "elec"), 22.0, places=6)
        self.assertAlmostEqual(node._side_reference_x(_Transform(), "B01", "diesel"), 21.0, places=6)
        _Cal.survey_points = [
            {"bay_side": "elec", "lat": 1.0, "lon": 1.0, "alt": 0.0},
            {"bay_side": "diesel", "lat": 1.0, "lon": 1.0, "alt": 0.0},
        ]
        self.assertIsNone(node._side_reference_x(_Transform(), "B01", "elec"))
        self.assertIsNone(node._side_reference_x(_Transform(), "B01", "diesel"))


if __name__ == "__main__":
    unittest.main()
