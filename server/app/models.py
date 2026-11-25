from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, JSON, Index
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime, timezone, timedelta

Base = declarative_base()

# 中国标准时间 UTC+8
CST = timezone(timedelta(hours=8))

def get_cst_now():
    """获取当前中国标准时间"""
    return datetime.now(CST)

class Vehicle(Base):
    """车辆信息表"""
    __tablename__ = "vehicles"

    id = Column(Integer, primary_key=True, index=True)
    vehicle_id = Column(String(50), unique=True, index=True, nullable=False)
    temp_id = Column(String(50), nullable=True)   # 临时 ID（未初始化时）
    sn = Column(String(100), unique=True, nullable=True)  # 设备序列号（唯一标识）
    label = Column(String(100), nullable=True)     # 用户可读名称，如 "RTG 01号机"
    vehicle_type = Column(String(20), default="RTG")
    initialized = Column(Boolean, default=False)
    created_at = Column(DateTime, default=get_cst_now)
    updated_at = Column(DateTime, default=get_cst_now, onupdate=get_cst_now)

class Yard(Base):
    """堆场配置表"""
    __tablename__ = "yards"

    id = Column(Integer, primary_key=True, index=True)
    yard_id = Column(String(50), unique=True, index=True, nullable=False)
    name = Column(String(100), nullable=False)
    origin_lat = Column(Float, nullable=False)   # 原点纬度
    origin_lon = Column(Float, nullable=False)   # 原点经度
    origin_alt = Column(Float, nullable=True, default=0)  # 原点高度
    bay_spacing = Column(Float, nullable=True, default=6.0)  # 贝位间距（米）
    bay_count = Column(Integer, nullable=True, default=0)    # 贝位数
    bay_half_width_m = Column(Float, nullable=True, default=0.3)  # 贝位沿 Y 半宽（米）
    boundary = Column(JSON, nullable=True)  # 区域多边形 [{lat, lon}, ...]，首尾相连
    y_axis_mode = Column(String(20), nullable=True, default="fit")  # fit | two_point
    y_axis_points = Column(JSON, nullable=True)  # 两点：from→to 为 +Y
    mapped = Column(Boolean, default=False)       # 是否已有 RTK 场地真值（至少一个 mapping 点）
    # 采点方式：rtk 手持逐贝位；rtg_ends 只用大车采头尾贝位，其余贝位自动生成
    survey_mode = Column(String(16), nullable=True, default="rtk")
    created_at = Column(DateTime, default=get_cst_now)

class SurveyPoint(Base):
    """RTK 打点数据表"""
    __tablename__ = "survey_points"

    id = Column(Integer, primary_key=True, index=True)
    yard_id = Column(String(50), index=True, nullable=False)
    point_type = Column(String(20), nullable=False)  # 'origin', 'bay', 'magnet'
    bay_no = Column(Integer, nullable=True)
    bay_side = Column(String(10), nullable=False, default="elec")  # elec | diesel
    lat = Column(Float, nullable=False)
    lon = Column(Float, nullable=False)
    alt = Column(Float, nullable=False)
    local_x = Column(Float, nullable=True)
    local_y = Column(Float, nullable=True)
    local_z = Column(Float, nullable=True)
    frame_count = Column(Integer, default=10)
    # 来源：rtk 手持；rtg_end 大车采的头尾；rtg_fix 大车重采的中间贝位；auto 按头尾等分生成
    source = Column(String(16), nullable=False, default="rtk")
    vehicle_id = Column(String(50), nullable=True)   # 大车采集时记录是哪台车
    std_m = Column(Float, nullable=True)             # 采集窗口内位置标准差（米）
    created_at = Column(DateTime, default=get_cst_now)
    updated_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index('idx_yard_type', 'yard_id', 'point_type'),
        Index(
            'idx_survey_bay_side',
            'yard_id', 'point_type', 'bay_no', 'bay_side',
            unique=True,
        ),
    )

class VehicleConfig(Base):
    """车辆参数配置表"""
    __tablename__ = "vehicle_configs"

    id = Column(Integer, primary_key=True, index=True)
    vehicle_id = Column(String(50), unique=True, index=True, nullable=False)
    l_arm = Column(JSON, nullable=True)  # 兼容单杆臂
    l_arm_elec = Column(JSON, nullable=True)  # 电气下游，兼容旧字段
    l_arm_diesel = Column(JSON, nullable=True)  # 动力下游，兼容旧字段
    l_arm_elec_up = Column(JSON, nullable=True)
    l_arm_elec_down = Column(JSON, nullable=True)
    l_arm_diesel_up = Column(JSON, nullable=True)
    l_arm_diesel_down = Column(JSON, nullable=True)
    height = Column(Float, nullable=True)  # 天线高度
    w_span = Column(Float, nullable=True)  # 跨距（参考）
    antennas = Column(JSON, nullable=True)  # 天线标识 {"ant1": {"label": "大车左前"}, ...}，用于区分 6 根天线的物理角色
    l_arm_calibration = Column(JSON, nullable=True)  # 杆臂智能标定溯源 {elec?, diesel?}
    gantry_attitude = Column(JSON, nullable=True)  # 姿态 {mode, baseline_m, roll_span_m, antenna_body}
    heading_deadband = Column(JSON, nullable=True)  # 航向死区纠偏 {enabled, ref_deg, band_deg}
    receiver_layout = Column(JSON, nullable=True)  # 接收机布局 / EVK host:port / transport
    ntrip = Column(JSON, nullable=True)  # 全局 NTRIP {enabled, caster_host, mountpoint, ...}
    trolley = Column(JSON, nullable=True)  # 小车零点 {origin_mode, origin_body} 大车车体系
    created_at = Column(DateTime, default=get_cst_now)
    updated_at = Column(DateTime, default=get_cst_now, onupdate=get_cst_now)

class CalibrationData(Base):
    """校准结果表（每车每场地一条，含全局转换矩阵 M）"""
    __tablename__ = "calibration_data"

    id = Column(Integer, primary_key=True, index=True)
    vehicle_id = Column(String(50), index=True, nullable=False)
    yard_id = Column(String(50), index=True, nullable=False)
    m_matrix = Column(JSON, nullable=True)  # SVD 校准矩阵
    status = Column(String(20), default="pending")  # 'pending', 'completed', 'failed'
    source_vehicle_id = Column(String(50), nullable=True)  # 校准2：参考车（如 RTG-A）
    created_at = Column(DateTime, default=get_cst_now)
    updated_at = Column(DateTime, default=get_cst_now, onupdate=get_cst_now)


class VehicleBayTransform(Base):
    """逐贝位转换列表：每车在每个场地的各贝位 RTK 真值 vs 车载解算对比"""
    __tablename__ = "vehicle_bay_transforms"

    id = Column(Integer, primary_key=True, index=True)
    vehicle_id = Column(String(50), index=True, nullable=False)
    yard_id = Column(String(50), index=True, nullable=False)
    bay_no = Column(Integer, nullable=False)
    ref_lat = Column(Float, nullable=False)
    ref_lon = Column(Float, nullable=False)
    ref_alt = Column(Float, nullable=False)
    meas_lat = Column(Float, nullable=False)
    meas_lon = Column(Float, nullable=False)
    meas_alt = Column(Float, nullable=False)
    meas_local_x = Column(Float, nullable=True)
    meas_local_y = Column(Float, nullable=True)
    meas_local_z = Column(Float, nullable=True)
    delta_lat = Column(Float, nullable=True)
    delta_lon = Column(Float, nullable=True)
    delta_alt = Column(Float, nullable=True)
    created_at = Column(DateTime, default=get_cst_now)
    updated_at = Column(DateTime, default=get_cst_now, onupdate=get_cst_now)

    __table_args__ = (
        Index('idx_vehicle_yard_bay', 'vehicle_id', 'yard_id', 'bay_no', unique=True),
    )


class LaneCorrection(Base):
    """任意停留点的横向修正。负值朝场内，正值朝场外，单位米。沿贝位位置 y_m 分段过渡。"""
    __tablename__ = "lane_corrections"

    id = Column(Integer, primary_key=True, index=True)
    vehicle_id = Column(String(50), index=True, nullable=False)
    yard_id = Column(String(50), index=True, nullable=False)
    y_m = Column(Float, nullable=False)
    elec1_m = Column(Float, nullable=False, default=0.0)
    elec2_m = Column(Float, nullable=False, default=0.0)
    diesel1_m = Column(Float, nullable=False, default=0.0)
    diesel2_m = Column(Float, nullable=False, default=0.0)
    created_at = Column(DateTime, default=get_cst_now)
    updated_at = Column(DateTime, default=get_cst_now, onupdate=get_cst_now)

    __table_args__ = (
        Index('idx_lane_correction_vehicle_yard', 'vehicle_id', 'yard_id'),
    )
