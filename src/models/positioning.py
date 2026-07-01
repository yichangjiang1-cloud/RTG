"""
数据模型：定位解算结果
包含大车、小车的完整输出，最终由 bridge_node 下发给 PLC。
"""

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional


class DataSource(IntEnum):
    """结果数据来源"""
    GNSS_RTK_FIXED = 1   # GNSS RTK 固定解
    GNSS_RTK_FLOAT = 2   # GNSS RTK 浮点解
    IMU_COASTING = 3     # IMU 惯导补位（GNSS 丢星期间）
    INVALID = 0


@dataclass
class GantryResult:
    """
    大车定位解算结果。

    坐标基准：堆场局部坐标系（LYCS）
    偏移基准：以跑道中心线为零点，偏向堆场内为负，偏向堆场外为正。
    速度符号：大车值增大方向为正，减小为负。
    """
    timestamp: float = 0.0

    # ---- 大车双作业点 L-Point（堆场 LYCS，米）----
    # 接收机1 天线 A1(主) → 电气房侧作业点；字段名历史遗留 center_elec_*
    center_elec_side: float = 0.0
    center_elec_y: float = 0.0
    # 接收机1 天线 A2(副) → 柴油机侧作业点；字段名历史遗留 center_engine_*
    center_engine_side: float = 0.0
    center_engine_y: float = 0.0
    a1_valid: bool = False   # 电气房侧作业点有效（A1 + l_arm_elec）
    a2_valid: bool = False   # 柴油机侧作业点有效（A2 + l_arm_diesel）
    recv1_live: bool = False  # gantry_a1 / 9001 接收机在线
    recv2_live: bool = False  # gantry_a2 / 9002 接收机在线（监控用）

    # ---- 四个门腿相对跑道中心线偏移（米）----
    # 排列顺序：左前、左后、右前、右后
    leg_offsets: list = field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0])

    # ---- 速度（m/s）----
    speed: float = 0.0

    # ---- 姿态角（°）----
    heading: float = 0.0
    pitch: float = 0.0
    roll: float = 0.0

    # ---- L-Point 坐标（堆场坐标系，米）----
    lpoint_x: float = 0.0
    lpoint_y: float = 0.0
    lpoint_z: float = 0.0

    data_source: DataSource = DataSource.INVALID


@dataclass
class TrolleyResult:
    """
    小车定位解算结果。

    坐标基准：RTG 局部坐标系（笛卡尔右手系）
    X 轴：沿轨道数值增大方向为正
    Z 轴：竖直向上为正
    速度符号：沿轨道增大方向为正。
    """
    timestamp: float = 0.0

    # ---- 小车两端天线相对 RTG 坐标系位置（米）----
    antenna1_x: float = 0.0
    antenna1_y: float = 0.0
    antenna1_z: float = 0.0

    antenna2_x: float = 0.0
    antenna2_y: float = 0.0
    antenna2_z: float = 0.0

    # ---- 小车中心位置（米）----
    center_x: float = 0.0
    center_y: float = 0.0
    center_z: float = 0.0

    # ---- 距轨道起始点的实际行程（米）----
    travel_distance: float = 0.0

    # ---- 速度（m/s）----
    speed: float = 0.0

    is_valid: bool = False

    data_source: DataSource = DataSource.INVALID


@dataclass
class PositionResult:
    """
    系统输出的完整定位结果，solver_node 组装后发布到 EventBus。
    bridge_node ��收并按协议下发给 PLC。
    """
    timestamp: float = 0.0
    yard_id: str = ""               # 当前所在堆场 ID

    gantry: GantryResult = field(default_factory=GantryResult)
    trolley: TrolleyResult = field(default_factory=TrolleyResult)

    # 参考 GNSS 主天线 WGS84（解算输入，非 L-Point）
    latitude: float = 0.0
    longitude: float = 0.0
    altitude: float = 0.0
    ref_receiver_id: str = ""       # 如 gantry_a2、gantry_a1+gantry_a2

    # 主天线 ECEF（全精度，杆臂标定优先使用，避免 WGS84 显示/量化误差）
    ref_ecef_x: float = 0.0
    ref_ecef_y: float = 0.0
    ref_ecef_z: float = 0.0

    # 接收机1 副天线 A2 WGS84（杆臂标定 / 柴油机侧解算输入）
    antenna2_latitude: float = 0.0
    antenna2_longitude: float = 0.0
    antenna2_altitude: float = 0.0
    antenna2_ecef_x: float = 0.0
    antenna2_ecef_y: float = 0.0
    antenna2_ecef_z: float = 0.0
    antenna2_valid: bool = False

    # L-Point 解算输出 WGS84（由堆场 LYCS 反算）
    lpoint_latitude: float = 0.0
    lpoint_longitude: float = 0.0
    lpoint_altitude: float = 0.0

    # 系统健康状态
    gnss_fix_quality: int = 0       # 当前 GNSS 定位质量
    imu_coasting_active: bool = False
    imu_coasting_duration: float = 0.0   # 当前惯导补位已持续秒数

