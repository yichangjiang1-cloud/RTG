"""
解算模块：坐标系变换
实现 WGS84 → ECEF → ENU → 堆场局部坐标系（LYCS）的完整变换链。
"""

import math
import logging
import numpy as np
from typing import Tuple

logger = logging.getLogger(__name__)

# WGS84 椭球参数
_A = 6378137.0              # 长半轴（m）
_F = 1 / 298.257223563      # 扁率
_B = _A * (1 - _F)          # 短半轴（m）
_E2 = 1 - (_B / _A) ** 2   # 第一偏心率平方


# ==============================================================
# 1. WGS84 → ECEF
# ==============================================================

def wgs84_to_ecef(lat_deg: float, lon_deg: float, alt_m: float
                  ) -> Tuple[float, float, float]:
    """
    将 WGS84 大地坐标转换为地心地固坐标（ECEF）。

    :param lat_deg: 纬度（°），北纬为正
    :param lon_deg: 经度（°），东经为正
    :param alt_m:   椭球高（m）
    :return: (X, Y, Z) ECEF 坐标（m）
    """
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)

    # 卯酉圈曲率半径
    N = _A / math.sqrt(1 - _E2 * math.sin(lat) ** 2)

    X = (N + alt_m) * math.cos(lat) * math.cos(lon)
    Y = (N + alt_m) * math.cos(lat) * math.sin(lon)
    Z = (N * (1 - _E2) + alt_m) * math.sin(lat)
    return X, Y, Z


def ecef_to_wgs84(X: float, Y: float, Z: float
                  ) -> Tuple[float, float, float]:
    """
    ECEF → WGS84（Bowring 迭代法）。

    :return: (lat_deg, lon_deg, alt_m)
    """
    lon = math.atan2(Y, X)
    p = math.sqrt(X ** 2 + Y ** 2)

    # 迭代初值
    lat = math.atan2(Z, p * (1 - _E2))
    for _ in range(10):
        N = _A / math.sqrt(1 - _E2 * math.sin(lat) ** 2)
        lat_new = math.atan2(Z + _E2 * N * math.sin(lat), p)
        if abs(lat_new - lat) < 1e-12:
            lat = lat_new
            break
        lat = lat_new

    N = _A / math.sqrt(1 - _E2 * math.sin(lat) ** 2)
    alt = p / math.cos(lat) - N if abs(math.cos(lat)) > 1e-10 else abs(Z) / math.sin(lat) - N * (1 - _E2)

    return math.degrees(lat), math.degrees(lon), alt


# ==============================================================
# 2. ECEF → 局部 ENU（以参考点为原点）
# ==============================================================

def ecef_to_enu(
    X: float, Y: float, Z: float,
    ref_lat_deg: float, ref_lon_deg: float, ref_alt_m: float,
) -> Tuple[float, float, float]:
    """
    将 ECEF 坐标转换为以参考点为原点的局部 ENU 坐标。

    :param X, Y, Z:                  目标点 ECEF 坐标（m）
    :param ref_lat_deg/lon_deg/alt_m: 参考点 WGS84 坐标
    :return: (East, North, Up) 单位：米
    """
    ref_lat = math.radians(ref_lat_deg)
    ref_lon = math.radians(ref_lon_deg)

    # 参考点 ECEF
    X0, Y0, Z0 = wgs84_to_ecef(ref_lat_deg, ref_lon_deg, ref_alt_m)
    dX, dY, dZ = X - X0, Y - Y0, Z - Z0

    # 旋转矩阵（ECEF → ENU）
    R = np.array([
        [-math.sin(ref_lon),                  math.cos(ref_lon),               0],
        [-math.sin(ref_lat) * math.cos(ref_lon), -math.sin(ref_lat) * math.sin(ref_lon), math.cos(ref_lat)],
        [ math.cos(ref_lat) * math.cos(ref_lon),  math.cos(ref_lat) * math.sin(ref_lon), math.sin(ref_lat)],
    ])
    enu = R @ np.array([dX, dY, dZ])
    return float(enu[0]), float(enu[1]), float(enu[2])


def enu_to_ecef(
    east_m: float,
    north_m: float,
    up_m: float,
    ref_lat_deg: float,
    ref_lon_deg: float,
    ref_alt_m: float,
) -> Tuple[float, float, float]:
    """ENU（相对参考点）→ ECEF。"""
    ref_lat = math.radians(ref_lat_deg)
    ref_lon = math.radians(ref_lon_deg)
    X0, Y0, Z0 = wgs84_to_ecef(ref_lat_deg, ref_lon_deg, ref_alt_m)
    R = np.array([
        [-math.sin(ref_lon),                  math.cos(ref_lon),               0],
        [-math.sin(ref_lat) * math.cos(ref_lon), -math.sin(ref_lat) * math.sin(ref_lon), math.cos(ref_lat)],
        [ math.cos(ref_lat) * math.cos(ref_lon),  math.cos(ref_lat) * math.sin(ref_lon), math.sin(ref_lat)],
    ])
    d = R.T @ np.array([east_m, north_m, up_m])
    return float(X0 + d[0]), float(Y0 + d[1]), float(Z0 + d[2])


# ==============================================================
# 3. ENU → 堆场局部坐标系（LYCS）
# ==============================================================

class YardTransform:
    """
    ENU → 堆场局部坐标系（LYCS）变换器。

    以堆场原点为坐标系原点，堆场 X 轴（大车行驶方向）相对于
    正北方向顺时针旋转 heading_deg 角。

    LYCS 定义：
      X 轴：沿大车行驶方向（贝位增大方向）
      Y 轴：垂直于大车方向（横向，向右为正）
      Z 轴：正上方
    """

    def __init__(
        self,
        origin_lat: float,
        origin_lon: float,
        origin_alt: float,
        heading_deg: float,
    ):
        """
        :param origin_lat/lon/alt: 堆场原点 WGS84 坐标
        :param heading_deg:        堆场 X 轴相对正北的顺时针角度（°）
        """
        self.origin_lat = origin_lat
        self.origin_lon = origin_lon
        self.origin_alt = origin_alt
        self.heading_deg = heading_deg

        # 预计算 ENU→LYCS 的 2D 旋转矩阵
        # ENU 中：East=X_enu, North=Y_enu
        # LYCS X 轴与 ENU North 轴的夹角 = heading_deg（顺时针）
        theta = math.radians(heading_deg)
        self._R2d = np.array([
            [ math.sin(theta),  math.cos(theta)],   # LYCS_X = E·sin + N·cos
            [ math.cos(theta), -math.sin(theta)],   # LYCS_Y = E·cos - N·sin
        ])

    def wgs84_to_lycs(
        self, lat_deg: float, lon_deg: float, alt_m: float
    ) -> Tuple[float, float, float]:
        """
        WGS84 → LYCS（一步完成）。

        :return: (lx, ly, lz) 堆场坐标（m）
        """
        X, Y, Z = wgs84_to_ecef(lat_deg, lon_deg, alt_m)
        E, N, U = ecef_to_enu(X, Y, Z,
                               self.origin_lat, self.origin_lon, self.origin_alt)
        xy = self._R2d @ np.array([E, N])
        return float(xy[0]), float(xy[1]), float(U)

    def ecef_to_lycs(
        self, ecef_x: float, ecef_y: float, ecef_z: float
    ) -> Tuple[float, float, float]:
        """
        ECEF → LYCS。
        """
        E, N, U = ecef_to_enu(ecef_x, ecef_y, ecef_z,
                               self.origin_lat, self.origin_lon, self.origin_alt)
        xy = self._R2d @ np.array([E, N])
        return float(xy[0]), float(xy[1]), float(U)

    def lycs_to_wgs84(
        self, lx: float, ly: float, lz: float
    ) -> Tuple[float, float, float]:
        """
        LYCS → WGS84（逆变换）。
        """
        # LYCS → ENU
        xy_enu = self._R2d.T @ np.array([lx, ly])
        E, N, U = float(xy_enu[0]), float(xy_enu[1]), lz

        # ENU → ECEF
        ref_lat = math.radians(self.origin_lat)
        ref_lon = math.radians(self.origin_lon)
        X0, Y0, Z0 = wgs84_to_ecef(self.origin_lat, self.origin_lon, self.origin_alt)

        R_inv = np.array([
            [-math.sin(ref_lon), -math.sin(ref_lat) * math.cos(ref_lon),  math.cos(ref_lat) * math.cos(ref_lon)],
            [ math.cos(ref_lon), -math.sin(ref_lat) * math.sin(ref_lon),  math.cos(ref_lat) * math.sin(ref_lon)],
            [ 0,                  math.cos(ref_lat),                       math.sin(ref_lat)],
        ])
        dXYZ = R_inv @ np.array([E, N, U])
        X, Y, Z = X0 + dXYZ[0], Y0 + dXYZ[1], Z0 + dXYZ[2]
        return ecef_to_wgs84(X, Y, Z)

