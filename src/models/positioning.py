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

    坐标基准：堆场局部坐标系（LYCS）——相对堆场原点。
    X 为北，Y 为东，Z 向上。
    偏移基准：以跑道中心线为零点，偏向堆场内为负，偏向堆场外为正。
    速度符号：大车值增大方向为正，减小为负。
    """
    timestamp: float = 0.0

    # ---- 四个轮胎中心（堆场 X/Y/Z，米）。顺序：该侧 GGA、GGA2 ----
    tire_elec1: tuple = (0.0, 0.0, 0.0)
    tire_elec1_valid: bool = False
    tire_elec2: tuple = (0.0, 0.0, 0.0)
    tire_elec2_valid: bool = False
    tire_diesel1: tuple = (0.0, 0.0, 0.0)
    tire_diesel1_valid: bool = False
    tire_diesel2: tuple = (0.0, 0.0, 0.0)
    tire_diesel2_valid: bool = False
    # 主天线 GGA 是否在下游。用来把 GGA/GGA2 标成上游、下游轮胎。
    elec_primary_down: bool = True
    diesel_primary_down: bool = True
    # 车道偏离 mm：电气 GGA、电气 GGA2、动力 GGA、动力 GGA2。朝场内为负。
    lane_mm: list = field(default_factory=lambda: [0, 0, 0, 0])

    # 电气侧 / 动力侧中心 = 该侧两个轮胎的中点（堆场局部坐标）
    center_elec_side: float = 0.0
    center_elec_y: float = 0.0
    center_engine_side: float = 0.0
    center_engine_y: float = 0.0
    # 同一中点的 WGS84，给前端做 RTG 头尾采点用，不依赖 +Y
    center_elec_lat: float = 0.0
    center_elec_lon: float = 0.0
    center_elec_alt: float = 0.0
    center_engine_lat: float = 0.0
    center_engine_lon: float = 0.0
    center_engine_alt: float = 0.0
    a1_valid: bool = False   # 电气侧两个轮胎都有效
    a2_valid: bool = False   # 动力侧两个轮胎都有效
    recv1_live: bool = False  # gantry_a1 / 9001 接收机在线
    recv2_live: bool = False  # gantry_a2 / 9002 接收机在线（监控用）

    # ---- 四个门腿相对跑道中心线偏移（米）----
    # 排列顺序：左前、左后、右前、右后（主通道：分侧航向俯仰 + 共用横滚）
    leg_offsets: list = field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0])
    # 对比通道：四天线刚体整车 H/P/R 投轮胎（仅 UI，不参与下发）
    tire_elec1_rigid: tuple = (0.0, 0.0, 0.0)
    tire_elec1_rigid_valid: bool = False
    tire_elec2_rigid: tuple = (0.0, 0.0, 0.0)
    tire_elec2_rigid_valid: bool = False
    tire_diesel1_rigid: tuple = (0.0, 0.0, 0.0)
    tire_diesel1_rigid_valid: bool = False
    tire_diesel2_rigid: tuple = (0.0, 0.0, 0.0)
    tire_diesel2_rigid_valid: bool = False
    # 顺序同 leg_offsets：fl, rl, fr, rr
    leg_offsets_rigid: list = field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0])

    # ---- 速度（m/s）----
    speed: float = 0.0
    # 电气房 / 动力房轮胎中心沿堆场 +Y 的速度
    speed_y_elec: float = 0.0
    speed_y_diesel: float = 0.0
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

    优先双 GGA 取中点；仅主天线 GGA 时用单点作中心（不求姿态句）。
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
    # 小车中心沿堆场 +X 的速度
    speed_x: float = 0.0

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
    in_yard: bool = False

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
    yard_axis_east: float = 0.0       # 堆场 +Y 的东向分量；未拟合时为 0
    yard_axis_north: float = 0.0      # 堆场 +Y 的北向分量；未拟合时为 0
    bay_spacing_m: float = 6.0
    current_bay_no: int = 0           # PLC 字段 10。四轮胎中心的中心落在电气贝位 ±半宽内才有号
    fix_quality_left: int = 0         # 左列接收机主天线
    fix_quality_left_ant2: int = 0    # 左列副天线
    fix_quality_right: int = 0        # 右列主天线
    fix_quality_right_ant2: int = 0
    # 按电气/动力侧分组。None 时协议仍用左/右字段。
    fix_quality_elec: Optional[int] = None
    fix_quality_elec_ant2: Optional[int] = None
    fix_quality_diesel: Optional[int] = None
    fix_quality_diesel_ant2: Optional[int] = None
    fix_quality_trolley: int = 0
    fix_quality_trolley_ant2: int = 0
    satellites_left: int = 0
    satellites_right: int = 0
    satellites_trolley: int = 0
    hdop_left: float = 99.9
    hdop_right: float = 99.9
    hdop_trolley: float = 99.9
    trolley_antenna_primary: dict = field(default_factory=dict)
    trolley_antenna_secondary: dict = field(default_factory=dict)

    # 大车双路姿态对比：primary=NMEA+高差，four_antenna=四 GGA 刚体
    gantry_attitude_compare: dict = field(default_factory=dict)
    # 左右机历元对齐诊断：dt_s / source(utc|arrival) / dropped
    gnss_pair_align: dict = field(default_factory=dict)

