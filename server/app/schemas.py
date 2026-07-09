from pydantic import BaseModel, ConfigDict
from typing import Optional, List, Dict, Any
from datetime import datetime

# ==================== Vehicle Schemas ====================
class VehicleBase(BaseModel):
    vehicle_id: str
    vehicle_type: str = "RTG"

class VehicleCreate(VehicleBase):
    temp_id: Optional[str] = None

class VehicleResponse(VehicleBase):
    id: int
    initialized: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

# ==================== Yard Schemas ====================
class YardBase(BaseModel):
    yard_id: str
    name: str
    origin_lat: float
    origin_lon: float
    origin_alt: float
    heading_deg: float = 0
    bay_spacing: float
    bay_count: int

class YardCreate(YardBase):
    pass

class YardResponse(YardBase):
    id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

# ==================== Survey Point Schemas ====================
class SurveyPointBase(BaseModel):
    yard_id: str
    point_type: str
    bay_no: Optional[int] = None
    lat: float
    lon: float
    alt: float
    frame_count: int = 10

class SurveyPointCreate(SurveyPointBase):
    pass

class SurveyPointResponse(SurveyPointBase):
    id: int
    local_x: Optional[float]
    local_y: Optional[float]
    local_z: Optional[float]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

# ==================== Vehicle Config Schemas ====================
class VehicleConfigBase(BaseModel):
    vehicle_id: str
    l_arm: Optional[List[float]] = None       # 兼容：单杆臂 [dx, dy, dz] (m)
    l_arm_elec: Optional[List[float]] = None    # 电气房侧 A1 → 作业点杆臂
    l_arm_diesel: Optional[List[float]] = None  # 柴油机侧 A2 → 作业点杆臂
    height: Optional[float] = None            # 标称高度 H (m)
    w_span: Optional[float] = None            # 门腿跨距 W_span (m)，参考/展示
    antennas: Optional[Dict[str, Any]] = None # 天线标识 {"ant1": {"label": "大车左前"}, ...}
    l_arm_calibration: Optional[Dict[str, Any]] = None  # 杆臂标定记录 {elec?, diesel?}
    gantry_attitude: Optional[Dict[str, Any]] = None  # 四天线姿态 {mode, baseline_m, antenna_body}
    receiver_layout: Optional[Dict[str, Any]] = None  # 接收机布局 {left, right, trolley, gga_order, ports}
    trolley: Optional[Dict[str, Any]] = None  # 小车零点 {origin_mode, origin_body}


class TrolleyOriginRequest(BaseModel):
    vehicle_id: str
    action: str  # capture | manual | reset
    origin_body: Optional[List[float]] = None  # manual 时必填，大车车体系 [x,y,z]

class VehicleConfigCreate(VehicleConfigBase):
    pass

class VehicleConfigResponse(VehicleConfigBase):
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

# ==================== Calibration Schemas ====================
class CalibrationDataBase(BaseModel):
    vehicle_id: str
    yard_id: str
    status: str = "pending"

class CalibrationDataCreate(CalibrationDataBase):
    m_matrix: Optional[Dict[str, Any]] = None

class CalibrationDataResponse(CalibrationDataBase):
    id: int
    m_matrix: Optional[Dict[str, Any]]
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

# ==================== 请求/响应包装 ====================
class ApiResponse(BaseModel):
    code: int = 200
    message: str = "success"
    data: Any = None

class ErrorResponse(BaseModel):
    code: int = 400
    message: str
    detail: Optional[str] = None

# ==================== 业务相关 Schemas ====================
class VehicleInitRequest(BaseModel):
    temp_id: str
    new_vehicle_id: str
    label: Optional[str] = None          # 用户可读名称，如 "RTG 01号机"
    params: Optional[VehicleConfigBase] = None

class VehicleConfigCloneRequest(BaseModel):
    source_vehicle_id: str
    new_vehicle_id: str


class Wgs84Point(BaseModel):
    lat: float
    lon: float
    alt: float


class EcefPoint(BaseModel):
  x: float
  y: float
  z: float


class AttitudeDeg(BaseModel):
    roll: float = 0.0
    pitch: float = 0.0
    heading: float = 0.0


class AntennaBodyComputeRequest(BaseModel):
    """四天线 ECEF/WGS84 → 车体系安装坐标标定"""
    antennas: Dict[str, Any]  # keys: a1, a2, b1, b2


class LArmComputeRequest(BaseModel):
    """
    杆臂反算：L_arm = Rᵀ · (P_antenna − P_lpoint)
    标定时车应静止，天线坐标与姿态来自实时 GNSS/双天线。
    antenna_ecef 优先（全精度 ECEF，避免 NMEA WGS84 量化误差）。
    """
    side: str  # 'elec' | 'diesel'
    lpoint: Wgs84Point
    antenna: Wgs84Point
    attitude: AttitudeDeg
    antenna_ecef: Optional[EcefPoint] = None

class SurveyConfirmRequest(BaseModel):
    vehicle_id: str
    yard_id: str
    bay_id: str
    mode: str = "mapping"  # 'mapping' (RTK 真值) or 'calibration' (车载对比)
    lat: float = 0.0
    lon: float = 0.0
    alt: float = 0.0
    local_x: Optional[float] = None
    local_y: Optional[float] = None
    local_z: Optional[float] = None

class SurveyBatchPoint(BaseModel):
    bay_id: str
    lat: float
    lon: float
    alt: float = 0.0

class SurveyBatchImportRequest(BaseModel):
    yard_id: str
    mode: str = "mapping"
    points: List[SurveyBatchPoint]

class SurveyFinalizeRequest(BaseModel):
    vehicle_id: str
    yard_id: str
    calibration_data: Optional[Dict[str, Any]] = None

# ==================== WebSocket 消息格式 ====================
class RealtimeData(BaseModel):
    timestamp: datetime
    gps_coord: Dict[str, float]  # {lat, lon, alt}
    local_coord: Dict[str, float]  # {x, y, z}
    heading: float
    roll: float
    pitch: float
    speed: float
    status: str  # "fix", "float", "no_signal"
    receivers: List[Dict[str, Any]] = []
    gantry: Optional[Dict[str, Any]] = None
    trolley: Optional[Dict[str, Any]] = None

class HealthCheckResponse(BaseModel):
    status: str  # "healthy", "degraded", "unhealthy"
    timestamp: datetime
    mqtt_connected: bool
    db_connected: bool
    version: str
