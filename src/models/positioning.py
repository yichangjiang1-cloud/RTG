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

    坐标基准：堆场局部坐标系（LYCS）——相对堆场原点的常规局部平面坐标
    （当前实现：X=北、Y=东、Z=上；不强制对齐贝位）。
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

    双 GGA 直接定位置，不求姿态句。
    坐标基准：堆场 LYCS；相对起始点 (rel_x, rel_y)，Y 轴沿横梁行程方向。
    速度符号：沿行程增大方向为正。
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

    # ---- 当前位置（大车车体系，相对 L-Point）----
    body_x: float = 0.0
    body_y: float = 0.0
    body_z: float = 0.0

    # ---- 相对起始零点（大车车体系，米）----
    rel_x: float = 0.0
    rel_y: float = 0.0
    rel_z: float = 0.0

    # ---- 已配置零点（大车车体系，供前端展示）----
    origin_body_x: float = 0.0
    origin_body_y: float = 0.0
    origin_body_z: float = 0.0
    origin_mode: str = "auto"  # auto | manual

    # ---- 距轨道起始点的实际行程（米，= rel_y）----
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
    yard_id: str = ""               # 当前所在堆场 ID（= PLC 场号，≤8 字节 ASCII）

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

    # 四天线 WGS84/ECEF（a1/a2 左列，b1/b2 右列；GGA[0]=电气，GGA[1]=柴油）
    gantry_antennas: dict = field(default_factory=dict)

    # PLC V1.0.0 扩展字段
    # 已废弃：场号与 yard_id 相同，保留字段仅为兼容旧遥测
    yard_display_code: str = ""
    yard_origin_lat: float = 0.0      # 堆场原点（六天线 XYZ 基准站，与 yard_config 一致）
    yard_origin_lon: float = 0.0
    yard_origin_alt: float = 0.0
    bay_spacing_m: float = 6.0
    current_bay_no: int = 0           # PLC 字段 10，由 RTK 贝位表「下一贝」规则得出
    fix_quality_left: int = 0         # 左列接收机主天线
    fix_quality_left_ant2: int = 0    # 左列副天线
    fix_quality_right: int = 0        # 右列主天线
    fix_quality_right_ant2: int = 0
    fix_quality_trolley: int = 0
    fix_quality_trolley_ant2: int = 0
    trolley_antenna_primary: dict = field(default_factory=dict)
    trolley_antenna_secondary: dict = field(default_factory=dict)

