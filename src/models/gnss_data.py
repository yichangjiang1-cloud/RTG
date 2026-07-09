"""
数据模型：GNSS 原始数据
使用 dataclass 定义，便于类型检查和序列化。
"""

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional


class FixQuality(IntEnum):
    """GNSS 定位质量枚举（对应 NMEA GGA quality indicator）"""
    INVALID = 0
    GPS_FIX = 1
    DGPS_FIX = 2
    PPS_FIX = 3
    RTK_FIXED = 4       # RTK 固定解（最高精度）
    RTK_FLOAT = 5       # RTK 浮点解
    ESTIMATED = 6

    @classmethod
    def is_rtk(cls, value: int) -> bool:
        """NMEA GGA quality 4/5 均为可用 RTK（固定解精度高于浮点解，枚举值却更小）。"""
        try:
            fq = cls(int(value))
        except ValueError:
            return False
        return fq in (cls.RTK_FIXED, cls.RTK_FLOAT)


@dataclass
class GnssRaw:
    """
    单台接收机的原始 GNSS 输出数据，包含位置、速度、姿态角。

    来源：driver_node 解析 NMEA/专有协议后填充此结构，
    发布到 EventBus.TOPIC_GNSS_RAW 主题。
    """
    # 接收机标识
    receiver_id: str = ""          # 如 "gantry_a1"、"trolley"

    # 时间戳（UNIX 时间，秒）
    timestamp: float = 0.0

    # ---- WGS84 原始位置 ----
    latitude: float = 0.0           # 纬度（°，北纬为正）
    longitude: float = 0.0          # 经度（°，东经为正）
    altitude: float = 0.0           # 椭球高（m）

    # ---- 定位质量 ----
    fix_quality: FixQuality = FixQuality.INVALID
    num_satellites: int = 0
    hdop: float = 99.9              # 水平精度因子

    # ---- 速度（地面坐标系，m/s）----
    speed_mps: float = 0.0          # 地面速度（标量）
    course_deg: float = 0.0         # 地面航迹角（°，相对正北顺时针）

    # ---- 双天线姿态角（仅双天线接收机输出）----
    heading: Optional[float] = None   # 航向角 ψ（°）
    pitch: Optional[float] = None     # 俯仰角 θ（°）
    roll: Optional[float] = None      # 横滚角 φ（°）

    # ---- 局部坐标（ECEF，解析后填充）----
    ecef_x: float = 0.0
    ecef_y: float = 0.0
    ecef_z: float = 0.0

    # ---- 同机副天线（第二根 GGA，可选）----
    antenna2_latitude: Optional[float] = None
    antenna2_longitude: Optional[float] = None
    antenna2_altitude: Optional[float] = None
    antenna2_ecef_x: float = 0.0
    antenna2_ecef_y: float = 0.0
    antenna2_ecef_z: float = 0.0
    antenna2_fix_quality: FixQuality = FixQuality.INVALID
    antenna2_num_satellites: int = 0
    antenna2_hdop: float = 99.9

    @property
    def has_antenna2(self) -> bool:
        return self.antenna2_latitude is not None and self.antenna2_longitude is not None

    @property
    def is_valid(self) -> bool:
        """是否有可用定位（GGA fix_quality > 0，含单点/差分/RTK）。"""
        return self.fix_quality != FixQuality.INVALID

    @property
    def is_rtk(self) -> bool:
        """是否为 RTK 固定解或浮点解。"""
        return FixQuality.is_rtk(int(self.fix_quality))

    @property
    def is_rtk_fixed(self) -> bool:
        """是否为 RTK 固定解"""
        return self.fix_quality == FixQuality.RTK_FIXED

