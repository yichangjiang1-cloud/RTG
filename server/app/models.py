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
    heading_deg = Column(Float, default=0)        # 堆场朝向（度）
    bay_spacing = Column(Float, nullable=True, default=6.0)  # 贝位间距（米）
    bay_count = Column(Integer, nullable=True, default=0)    # 贝位数
    mapped = Column(Boolean, default=False)       # 是否已有 RTK 场地真值（至少一个 mapping 点）
    created_at = Column(DateTime, default=get_cst_now)

class SurveyPoint(Base):
    """RTK 打点数据表"""
    __tablename__ = "survey_points"

    id = Column(Integer, primary_key=True, index=True)
    yard_id = Column(String(50), index=True, nullable=False)
    point_type = Column(String(20), nullable=False)  # 'origin', 'bay', 'magnet'
    bay_no = Column(Integer, nullable=True)
    lat = Column(Float, nullable=False)
    lon = Column(Float, nullable=False)
    alt = Column(Float, nullable=False)
    local_x = Column(Float, nullable=True)
    local_y = Column(Float, nullable=True)
    local_z = Column(Float, nullable=True)
    frame_count = Column(Integer, default=10)
    created_at = Column(DateTime, default=get_cst_now)

    __table_args__ = (
        Index('idx_yard_type', 'yard_id', 'point_type'),
    )

class VehicleConfig(Base):
    """车辆参数配置表"""
    __tablename__ = "vehicle_configs"

    id = Column(Integer, primary_key=True, index=True)
    vehicle_id = Column(String(50), unique=True, index=True, nullable=False)
    l_arm = Column(JSON, nullable=True)  # 兼容单杆臂
    l_arm_elec = Column(JSON, nullable=True)  # 电气房侧 A1 杆臂
    l_arm_diesel = Column(JSON, nullable=True)  # 柴油机侧 A2 杆臂
    height = Column(Float, nullable=True)  # 天线高度
    w_span = Column(Float, nullable=True)  # 跨距（参考）
    antennas = Column(JSON, nullable=True)  # 天线标识 {"ant1": {"label": "大车左前"}, ...}，用于区分 6 根天线的物理角色
    l_arm_calibration = Column(JSON, nullable=True)  # 杆臂智能标定溯源 {elec?, diesel?}
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
