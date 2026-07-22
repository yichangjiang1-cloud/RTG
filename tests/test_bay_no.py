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


if __name__ == "__main__":
    unittest.main()
