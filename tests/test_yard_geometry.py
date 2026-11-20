"""堆场多边形、拟合轴和贝位带。"""
import unittest

from src.solver.yard_geometry import (
    diesel_bays,
    electrical_bays,
    en_meters,
    fit_y_axis,
    from_yard_xy,
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
    yard_delta_to_en,
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

    def test_yard_xy_inverse_and_delta(self):
        axis = (0.0, 1.0)  # +Y 北，+X 东
        x, y = to_yard_xy(10.0, 20.0, *axis)
        e, n = from_yard_xy(x, y, *axis)
        self.assertAlmostEqual(e, 10.0, places=9)
        self.assertAlmostEqual(n, 20.0, places=9)
        de, dn = yard_delta_to_en(1.0, 2.0, axis)
        self.assertAlmostEqual(de, 1.0, places=9)
        self.assertAlmostEqual(dn, 2.0, places=9)
        # 未拟合：X=北 Y=东
        de0, dn0 = yard_delta_to_en(3.0, 4.0, None)
        self.assertAlmostEqual(de0, 4.0, places=9)
        self.assertAlmostEqual(dn0, 3.0, places=9)

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

    def test_prefer_yard_only_on_overlap(self):
        # 大半场 A02 包住小半场 A01；点在重叠区
        small = [{"lat": lat, "lon": lon} for lat, lon in _square()]
        big = [
            {"lat": 29.999, "lon": 113.999},
            {"lat": 29.999, "lon": 114.002},
            {"lat": 30.002, "lon": 114.002},
            {"lat": 30.002, "lon": 113.999},
        ]
        yards = [
            {
                "yard_id": "A01",
                "name": "small",
                "origin_lat": 30.0,
                "origin_lon": 114.0,
                "origin_alt": 0,
                "boundary": small,
            },
            {
                "yard_id": "A02",
                "name": "big",
                "origin_lat": 30.0,
                "origin_lon": 114.0,
                "origin_alt": 0,
                "boundary": big,
            },
        ]
        mgr = YardManager("missing.json", yards)
        # 无偏好：重叠取面积最小 → A01
        self.assertEqual(mgr.detect_yard_from_point(30.0005, 114.0005), "A01")
        self.assertFalse(mgr.last_detect_used_prefer())
        # 偏好大场：重叠消歧 → A02
        self.assertEqual(mgr.set_forced_yard("A02"), "A02")
        self.assertEqual(mgr.detect_yard_from_point(30.0005, 114.0005), "A02")
        self.assertTrue(mgr.last_detect_used_prefer())
        # 场外：选中无效
        self.assertIsNone(mgr.detect_yard_from_point(30.01, 114.01))
        self.assertFalse(mgr.last_detect_used_prefer())
        # 只在大场内（小场外）：单场，忽略偏好
        self.assertEqual(mgr.detect_yard_from_point(30.0015, 114.0015), "A02")
        self.assertFalse(mgr.last_detect_used_prefer())

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

    def test_diesel_line_follows_its_own_points_and_can_tilt(self):
        from src.solver.yard_geometry import (
            deviation_adjust_along_line,
            fit_bay_line,
            line_deviation_m,
        )

        parallel = [(1, 20.0, 0.0), (2, 20.0, 10.0)]
        origin, direction = fit_bay_line(parallel)
        self.assertAlmostEqual(origin[0], 20.0, places=6)
        self.assertAlmostEqual(direction[0], 0.0, places=6)
        self.assertAlmostEqual(abs(direction[1]), 1.0, places=6)
        self.assertAlmostEqual(
            line_deviation_m(19.8, 4.0, origin, direction, (10.0, 4.0)),
            -0.2,
            places=6,
        )

        tilted = [(1, 20.0, 0.0), (2, 22.0, 10.0)]
        origin, direction = fit_bay_line(tilted)
        self.assertGreater(abs(direction[0]), 0.1)
        self.assertAlmostEqual(line_deviation_m(21.0, 5.0, origin, direction, (0.0, 5.0)), 0.0, places=6)
        self.assertLess(line_deviation_m(20.0, 5.0, origin, direction, (0.0, 5.0)), 0.0)
        shift = deviation_adjust_along_line(-0.05, origin, direction, (0.0, 5.0))
        moved = line_deviation_m(
            21.0 + shift[0], 5.0 + shift[1], origin, direction, (0.0, 5.0),
        )
        self.assertAlmostEqual(moved, -0.05, places=6)
        self.assertIsNone(fit_bay_line([(1, 20.0, 0.0)]))

    def test_solver_uses_only_diesel_points_for_the_parallel_line(self):
        from src.solver.solver_node import SolverNode

        class _Cal:
            survey_points = [
                {"bay_no": 1, "bay_side": "elec", "lat": 1.0, "lon": 1.0, "alt": 0.0},
                {"bay_no": 2, "bay_side": "elec", "lat": 3.0, "lon": 1.0, "alt": 0.0},
                {"bay_no": 1, "bay_side": "diesel", "lat": 0.0, "lon": 0.0, "alt": 0.0},
                {"bay_no": 1, "bay_side": "diesel", "lat": 0.0, "lon": 1.0, "alt": 0.0},
                {"bay_no": 2, "bay_side": "diesel", "lat": 2.0, "lon": 1.0, "alt": 0.0},
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
        origin, direction = node._diesel_bay_line(_Transform(), "B01")
        self.assertAlmostEqual(origin[0], 21.0, places=6)
        self.assertGreater(abs(direction[0]), 0.5)
        _Cal.survey_points = [
            {"bay_no": 1, "bay_side": "elec", "lat": 1.0, "lon": 1.0, "alt": 0.0},
            {"bay_no": 1, "bay_side": "diesel", "lat": 1.0, "lon": 1.0, "alt": 0.0},
        ]
        self.assertIsNone(node._side_reference_x(_Transform(), "B01", "elec"))
        self.assertIsNone(node._diesel_bay_line(_Transform(), "B01"))

    def test_local_bay_segment_is_used_instead_of_one_straight_line(self):
        from src.solver.yard_geometry import bracket_bay_line, line_deviation_m

        # 前一段竖直在 X=0，后一段斜到 X=2。一个总拟合线过不了这两个折点。
        points = [(0.0, 0.0), (0.0, 10.0), (2.0, 20.0)]
        centroid = (10.0, 10.0)
        front = bracket_bay_line(5.0, points)
        rear = bracket_bay_line(15.0, points)
        self.assertIsNotNone(front)
        self.assertIsNotNone(rear)
        self.assertAlmostEqual(
            line_deviation_m(0.3, 5.0, front[0], front[1], centroid), -0.3, places=6,
        )
        self.assertAlmostEqual(
            line_deviation_m(1.0, 15.0, rear[0], rear[1], centroid), 0.0, places=5,
        )
        before = bracket_bay_line(-5.0, points)
        after = bracket_bay_line(30.0, points)
        self.assertEqual(before[0], front[0])
        self.assertEqual(after[0], rear[0])
        self.assertIsNone(bracket_bay_line(5.0, [(0.0, 0.0)]))

    def test_one_lane_stop_ramps_from_the_bay_line_ends(self):
        from src.solver.yard_geometry import applied_lane_shifts

        # 贝位线 Y 从 0 到 100，只在 40 m 记 60 cm。起点和终点是 0。
        one = [(40.0, (0.60, 0.0, 0.0, 0.0))]
        self.assertAlmostEqual(applied_lane_shifts(0.0, one, 0.0, 100.0)[0], 0.0)
        self.assertAlmostEqual(applied_lane_shifts(20.0, one, 0.0, 100.0)[0], 0.30)
        self.assertAlmostEqual(applied_lane_shifts(40.0, one, 0.0, 100.0)[0], 0.60)
        self.assertAlmostEqual(applied_lane_shifts(70.0, one, 0.0, 100.0)[0], 0.30)
        self.assertAlmostEqual(applied_lane_shifts(100.0, one, 0.0, 100.0)[0], 0.0)
        self.assertAlmostEqual(applied_lane_shifts(120.0, one, 0.0, 100.0)[0], 0.0)
        self.assertEqual(applied_lane_shifts(40.0, one, None, None), (0.0, 0.0, 0.0, 0.0))
        # 纠偏点记在贝位线起点之前：过了这一点不再保持，和起点之间仍是斜线。
        outside = [(-10.0, (0.60, 0.0, 0.0, 0.0))]
        self.assertAlmostEqual(applied_lane_shifts(-20.0, outside, 0.0, 100.0)[0], 0.0)
        self.assertAlmostEqual(applied_lane_shifts(-10.0, outside, 0.0, 100.0)[0], 0.60)
        self.assertAlmostEqual(applied_lane_shifts(-5.0, outside, 0.0, 100.0)[0], 0.30)
        self.assertAlmostEqual(applied_lane_shifts(50.0, outside, 0.0, 100.0)[0], 0.0)

    def test_lane_stop_only_moves_the_neighboring_bays(self):
        from src.solver.yard_geometry import applied_lane_shifts

        bays = [0.0, 10.0, 20.0, 30.0]
        # 记在 10 m 和 20 m 之间：只动这一段，相邻贝位是 0，其它段是 0。
        one = [(15.0, (0.60, 0.0, 0.0, 0.0))]
        self.assertAlmostEqual(applied_lane_shifts(15.0, one, bays)[0], 0.60)
        self.assertAlmostEqual(applied_lane_shifts(12.5, one, bays)[0], 0.30)
        self.assertAlmostEqual(applied_lane_shifts(17.5, one, bays)[0], 0.30)
        self.assertAlmostEqual(applied_lane_shifts(10.0, one, bays)[0], 0.0)
        self.assertAlmostEqual(applied_lane_shifts(20.0, one, bays)[0], 0.0)
        self.assertAlmostEqual(applied_lane_shifts(5.0, one, bays)[0], 0.0)
        self.assertAlmostEqual(applied_lane_shifts(25.0, one, bays)[0], 0.0)
        # 就记在 10 m 这个贝上：前后两段都连到它，再往外是 0。
        at_bay = [(10.0, (0.40, 0.0, 0.0, 0.0))]
        self.assertAlmostEqual(applied_lane_shifts(10.0, at_bay, bays)[0], 0.40)
        self.assertAlmostEqual(applied_lane_shifts(5.0, at_bay, bays)[0], 0.20)
        self.assertAlmostEqual(applied_lane_shifts(15.0, at_bay, bays)[0], 0.20)
        self.assertAlmostEqual(applied_lane_shifts(0.0, at_bay, bays)[0], 0.0)
        self.assertAlmostEqual(applied_lane_shifts(20.0, at_bay, bays)[0], 0.0)
        self.assertAlmostEqual(applied_lane_shifts(25.0, at_bay, bays)[0], 0.0)
        # 相邻两段各记一点：中间贝位仍是 0。
        two = [(5.0, (0.60, 0.0, 0.0, 0.0)), (15.0, (0.20, 0.0, 0.0, 0.0))]
        self.assertAlmostEqual(applied_lane_shifts(5.0, two, bays)[0], 0.60)
        self.assertAlmostEqual(applied_lane_shifts(7.5, two, bays)[0], 0.30)
        self.assertAlmostEqual(applied_lane_shifts(10.0, two, bays)[0], 0.0)
        self.assertAlmostEqual(applied_lane_shifts(15.0, two, bays)[0], 0.20)

    def test_lane_shift_holds_outside_the_ends_and_splits_when_a_middle_point_is_added(self):
        from src.solver.yard_geometry import interpolate_lane_shifts

        ends = [(0.0, (0.10, 0.0, 0.0, 0.0)), (10.0, (0.0, 0.0, 0.0, 0.0))]
        self.assertAlmostEqual(interpolate_lane_shifts(-5.0, ends)[0], 0.10)
        self.assertAlmostEqual(interpolate_lane_shifts(2.0, ends)[0], 0.08)
        self.assertAlmostEqual(interpolate_lane_shifts(20.0, ends)[0], 0.0)
        with_middle = ends + [(4.0, (-0.05, 0.0, 0.0, 0.0))]
        self.assertAlmostEqual(interpolate_lane_shifts(0.0, with_middle)[0], 0.10)
        self.assertAlmostEqual(interpolate_lane_shifts(2.0, with_middle)[0], 0.025)
        self.assertAlmostEqual(interpolate_lane_shifts(4.0, with_middle)[0], -0.05)
        self.assertAlmostEqual(interpolate_lane_shifts(10.0, with_middle)[0], 0.0)
        self.assertEqual(interpolate_lane_shifts(3.0, []), (0.0, 0.0, 0.0, 0.0))

    def test_additional_centimeters_accumulate_on_the_correction_already_in_effect(self):
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
        from app.utils.lane_correction import absolute_with_additional

        # 贝位在 0、50、100 m。40 m 落在 0–50 这一段，先记 60 cm，再填 5 cm 记成 65 cm。
        # 70 m 在下一段，这段还没有纠偏，再填 5 cm 就记成 5 cm。
        bays = [0.0, 50.0, 100.0]
        first = absolute_with_additional(40.0, [], (0.60, 0.60, 0.60, 0.60), bays)
        self.assertEqual(first, (0.60, 0.60, 0.60, 0.60))
        points = [(40.0, first)]
        second = absolute_with_additional(40.0, points, (0.05, 0.0, 0.0, 0.0), bays)
        self.assertAlmostEqual(second[0], 0.65)
        self.assertAlmostEqual(second[1], 0.60)
        other_segment = absolute_with_additional(70.0, points, (0.05, 0.0, 0.0, 0.0), bays)
        self.assertAlmostEqual(other_segment[0], 0.05)
        self.assertAlmostEqual(other_segment[1], 0.0)
        from app.utils.lane_correction import applied_lane_shifts as server_apply
        from src.solver.yard_geometry import applied_lane_shifts as solver_apply
        outside = [(-10.0, (0.60, 0.0, 0.0, 0.0))]
        for y in (-20.0, -10.0, -5.0, 50.0, 120.0):
            self.assertEqual(server_apply(y, outside, 0.0, 100.0), solver_apply(y, outside, 0.0, 100.0))
        bays = [0.0, 10.0, 20.0, 30.0]
        nearby = [(15.0, (0.60, 0.0, 0.0, 0.0)), (10.0, (0.40, 0.0, 0.0, 0.0))]
        for y in (0.0, 5.0, 10.0, 15.0, 20.0, 25.0):
            self.assertEqual(server_apply(y, nearby, bays), solver_apply(y, nearby, bays))

    def test_tire_to_survey_point_uses_the_same_sign_as_lane_deviation(self):
        from src.solver.yard_geometry import tire_survey_offset_m as solver_offset

        # 贝位线在 X=0，场心在 +X。轮胎比实测点偏向场内 0.2 m，横向应为 -0.2。
        side = [(0.0, 0.0), (0.0, 10.0)]
        centroid = (10.0, 5.0)
        lateral, along = solver_offset(0.2, 5.0, 0.0, 5.0, side, [], centroid, 0.0)
        self.assertAlmostEqual(lateral, -0.2)
        self.assertAlmostEqual(along, 0.0)
        _lateral, ahead = solver_offset(0.2, 6.0, 0.0, 5.0, side, [], centroid, 0.0)
        self.assertAlmostEqual(ahead, 1.0)
        fallback_lateral, fallback_along = solver_offset(0.2, 5.0, 0.0, 4.0, [], [], centroid, 0.0)
        self.assertAlmostEqual(fallback_lateral, -0.2)
        self.assertAlmostEqual(fallback_along, 1.0)

        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
        from app.utils.yard_geometry import tire_survey_offset_m as server_offset

        self.assertEqual(
            server_offset(0.2, 5.0, 0.0, 5.0, side, [], centroid, 0.0),
            solver_offset(0.2, 5.0, 0.0, 5.0, side, [], centroid, 0.0),
        )

    def test_signed_deviation_moves_toward_the_yard_interior(self):
        from src.solver.yard_geometry import deviation_adjust_to_dx

        # 场心在 +X，朝场内 5 cm 应把轮胎往 +X 移
        self.assertAlmostEqual(deviation_adjust_to_dx(-0.05, 10.0, 1.5), 0.05)
        # 场心在贝位线的 -X 一侧，朝场内 5 cm 应往 -X 移
        self.assertAlmostEqual(deviation_adjust_to_dx(-0.05, 10.0, 20.0), -0.05)
        self.assertAlmostEqual(deviation_adjust_to_dx(0.05, 10.0, 20.0), 0.05)


if __name__ == "__main__":
    unittest.main()
