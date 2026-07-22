"""
单元测试：坐标系变换模块
测试 WGS84 → ECEF → ENU → LYCS 的完整变换链。
"""

import sys
import unittest
import math
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.solver.coordinate_transform import (
    wgs84_to_ecef,
    ecef_to_wgs84,
    ecef_to_enu,
    YardTransform,
)


class TestCoordinateTransform(unittest.TestCase):
    """坐标变换单元测试。"""

    def test_wgs84_to_ecef_origin(self):
        """测试：赤道本初子午线点（0°N, 0°E）→ ECEF。"""
        X, Y, Z = wgs84_to_ecef(0.0, 0.0, 0.0)
        # 应该在 X 轴上，约 6378137 m
        self.assertAlmostEqual(X, 6378137.0, places=0)
        self.assertAlmostEqual(Y, 0.0, places=3)
        self.assertAlmostEqual(Z, 0.0, places=3)

    def test_wgs84_ecef_roundtrip(self):
        """测试：WGS84 → ECEF → WGS84 往返转换精度。"""
        lat_in, lon_in, alt_in = 22.5, 113.9, 50.0
        X, Y, Z = wgs84_to_ecef(lat_in, lon_in, alt_in)
        lat_out, lon_out, alt_out = ecef_to_wgs84(X, Y, Z)

        self.assertAlmostEqual(lat_in, lat_out, places=9)
        self.assertAlmostEqual(lon_in, lon_out, places=9)
        self.assertAlmostEqual(alt_in, alt_out, places=3)

    def test_ecef_to_enu_zero_offset(self):
        """测试：参考点自身 → ENU 应为 (0, 0, 0)。"""
        ref_lat, ref_lon, ref_alt = 22.5, 113.9, 100.0
        X, Y, Z = wgs84_to_ecef(ref_lat, ref_lon, ref_alt)
        E, N, U = ecef_to_enu(X, Y, Z, ref_lat, ref_lon, ref_alt)

        self.assertAlmostEqual(E, 0.0, places=6)
        self.assertAlmostEqual(N, 0.0, places=6)
        self.assertAlmostEqual(U, 0.0, places=6)

    def test_ecef_to_enu_known_offset(self):
        """测试：已知偏移量的 ENU 变换。"""
        # 参考点
        ref_lat, ref_lon, ref_alt = 22.5, 113.9, 100.0
        # 目标点：向东 100m，向北 200m，向上 50m（近似）
        # 在赤道附近，1° ≈ 111 km，粗略计算偏移
        target_lat = ref_lat + 200 / 111000.0
        target_lon = ref_lon + 100 / (111000.0 * math.cos(math.radians(ref_lat)))
        target_alt = ref_alt + 50.0

        X0, Y0, Z0 = wgs84_to_ecef(ref_lat, ref_lon, ref_alt)
        X1, Y1, Z1 = wgs84_to_ecef(target_lat, target_lon, target_alt)
        E, N, U = ecef_to_enu(X1, Y1, Z1, ref_lat, ref_lon, ref_alt)

        # 允许一定误差（由于地球曲率）
        self.assertAlmostEqual(E, 100.0, delta=1.0)
        self.assertAlmostEqual(N, 200.0, delta=1.0)
        self.assertAlmostEqual(U, 50.0, delta=0.1)

    def test_yard_transform_wgs84_to_lycs(self):
        """测试：YardTransform WGS84 → LYCS（相对原点，X=北 Y=东；heading 忽略）。"""
        origin_lat, origin_lon, origin_alt = 22.5, 113.9, 5.0
        # heading_deg 配置存在但不参与变换
        transform = YardTransform(origin_lat, origin_lon, origin_alt, heading_deg=90.0)

        lx, ly, lz = transform.wgs84_to_lycs(origin_lat, origin_lon, origin_alt)
        self.assertAlmostEqual(lx, 0.0, places=3)
        self.assertAlmostEqual(ly, 0.0, places=3)
        self.assertAlmostEqual(lz, 0.0, places=3)

        # 正东约 100m → Y≈100，X≈0
        target_lon = origin_lon + 100 / (111000.0 * math.cos(math.radians(origin_lat)))
        lx, ly, lz = transform.wgs84_to_lycs(origin_lat, target_lon, origin_alt)
        self.assertAlmostEqual(lx, 0.0, delta=1.0)
        self.assertAlmostEqual(ly, 100.0, delta=1.0)
        self.assertAlmostEqual(lz, 0.0, delta=0.1)

        # 正北约 100m → X≈100，Y≈0
        target_lat = origin_lat + 100 / 111000.0
        lx, ly, lz = transform.wgs84_to_lycs(target_lat, origin_lon, origin_alt)
        self.assertAlmostEqual(lx, 100.0, delta=1.0)
        self.assertAlmostEqual(ly, 0.0, delta=1.0)

    def test_yard_transform_roundtrip(self):
        """测试：LYCS → WGS84 → LYCS 往返。"""
        origin_lat, origin_lon, origin_alt = 22.5, 113.9, 10.0
        heading_deg = 87.5

        transform = YardTransform(origin_lat, origin_lon, origin_alt, heading_deg)

        # 堆场内任意点
        lx_in, ly_in, lz_in = 50.0, -20.0, 5.0
        lat, lon, alt = transform.lycs_to_wgs84(lx_in, ly_in, lz_in)
        lx_out, ly_out, lz_out = transform.wgs84_to_lycs(lat, lon, alt)

        self.assertAlmostEqual(lx_in, lx_out, places=3)
        self.assertAlmostEqual(ly_in, ly_out, places=3)
        self.assertAlmostEqual(lz_in, lz_out, places=3)


if __name__ == "__main__":
    unittest.main()

