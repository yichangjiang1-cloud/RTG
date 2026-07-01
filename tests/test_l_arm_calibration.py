"""杆臂反算与正解往返一致性"""

import sys
import unittest
import numpy as np
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.solver.lpoint_solver import LPointSolver
from src.solver.l_arm_calibration import compute_l_arm_from_ecef


class TestLArmCalibration(unittest.TestCase):
    def test_roundtrip_wgs84(self):
        roll, pitch, heading = 1.0, -2.0, 45.0
        l_arm_true = np.array([1.2, 0.5, 3.0])

        solver = LPointSolver(l_arm_true.tolist())
        lpoint = np.array([100.0, 200.0, 50.0])
        antenna = solver.solve(lpoint, roll, pitch, heading)
        # solve: lpoint = antenna - R @ l_arm  =>  antenna = lpoint + R @ l_arm
        from src.solver.attitude import euler_to_rotation_matrix
        R = euler_to_rotation_matrix(roll, pitch, heading)
        antenna = lpoint + R @ l_arm_true

        recovered = compute_l_arm_from_ecef(antenna, lpoint, roll, pitch, heading)
        np.testing.assert_array_almost_equal(recovered, l_arm_true, decimal=6)

        check_lpoint = solver.solve(antenna, roll, pitch, heading)
        np.testing.assert_array_almost_equal(check_lpoint, lpoint, decimal=4)


    def test_nmea_roundtrip_sub_centimeter(self):
        """模拟器 NMEA 往返后杆臂反算误差应 < 1 cm。"""
        import sys
        from pathlib import Path
        tools = Path(__file__).parent.parent / "tools"
        sys.path.insert(0, str(tools))
        import sim_gantry_gnss as sg
        from src.driver.nmea_parser import parse_sentence
        from src.solver.coordinate_transform import wgs84_to_ecef
        from src.solver.l_arm_calibration import compute_l_arm_from_wgs84, compute_l_arm_from_ecef

        l_arm_true = [15.0, 5.0, 3.0]
        heading = 0.0
        a1_lat, a1_lon, a1_alt = 30.123456, 120.654321, 10.0

        solver = LPointSolver(l_arm_true)
        from src.solver.attitude import euler_to_rotation_matrix
        R = euler_to_rotation_matrix(0, 0, heading)
        ant_ecef = np.array(wgs84_to_ecef(a1_lat, a1_lon, a1_alt))
        lp_ecef = ant_ecef - R @ np.array(l_arm_true)
        from src.solver.coordinate_transform import ecef_to_wgs84
        lp_wgs = ecef_to_wgs84(lp_ecef[0], lp_ecef[1], lp_ecef[2])

        gga = sg.build_gga(a1_lat, a1_lon, a1_alt)
        parsed = parse_sentence(gga.strip())
        self.assertIsNotNone(parsed)
        arm_wgs = compute_l_arm_from_wgs84(
            parsed["lat"], parsed["lon"], parsed["alt"],
            lp_wgs[0], lp_wgs[1], lp_wgs[2],
            0, 0, heading,
        )
        ant2_ecef = np.array(wgs84_to_ecef(parsed["lat"], parsed["lon"], parsed["alt"]))
        arm_ecef = compute_l_arm_from_ecef(ant2_ecef, lp_ecef, 0, 0, heading)

        for arm in (arm_wgs, arm_ecef):
            err = np.linalg.norm(np.array(arm) - np.array(l_arm_true))
            self.assertLess(err, 0.01, f"杆臂误差 {err:.4f} m 超过 1 cm")
