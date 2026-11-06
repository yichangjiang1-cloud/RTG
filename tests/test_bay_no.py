"""贝位号解析单元测试"""

import sys
import unittest
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.solver.bay_residual import BayPosition, resolve_bay_no_next


class TestBayNoNext(unittest.TestCase):
    def _positions(self):
        return [
            BayPosition(8, 80.0),
            BayPosition(9, 86.0),
            BayPosition(10, 92.0),
        ]

    def test_at_bay(self):
        self.assertEqual(resolve_bay_no_next(80.0, self._positions()), 8)
        self.assertEqual(resolve_bay_no_next(86.0, self._positions()), 9)

    def test_between_bays_uses_next(self):
        self.assertEqual(resolve_bay_no_next(83.0, self._positions()), 9)
        self.assertEqual(resolve_bay_no_next(90.0, self._positions()), 10)

    def test_before_first_bay(self):
        self.assertEqual(resolve_bay_no_next(75.0, self._positions()), 8)

    def test_after_last_bay(self):
        self.assertEqual(resolve_bay_no_next(100.0, self._positions()), 10)

    def test_empty_returns_zero(self):
        self.assertEqual(resolve_bay_no_next(50.0, []), 0)

    def test_side_offset_stays_on_same_segment(self):
        """横着偏开几米，仍按所在线段算下一贝。"""
        self.assertEqual(resolve_bay_no_next(83.0, self._positions(), lpoint_y=4.0), 9)

    def test_curve_uses_both_axes(self):
        """北向走完再向东拐。只比 X 会把拐弯后的点算回上一贝。"""
        curve = [
            BayPosition(1, 0.0, 0.0),
            BayPosition(2, 50.0, 0.0),
            BayPosition(3, 50.0, 50.0),
        ]
        self.assertEqual(resolve_bay_no_next(25.0, curve, 0.0), 2)
        self.assertEqual(resolve_bay_no_next(50.0, curve, 0.0), 2)
        self.assertEqual(resolve_bay_no_next(50.0, curve, 25.0), 3)
        self.assertEqual(resolve_bay_no_next(50.0, curve, 60.0), 3)
        self.assertEqual(resolve_bay_no_next(-5.0, curve, 0.0), 1)


if __name__ == "__main__":
    unittest.main()
