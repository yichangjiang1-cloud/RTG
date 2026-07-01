// ===== WebSocket 推送数据类型 =====
export type GnssStatus = 'fix' | 'float' | 'no_signal';

export interface GpsCoord {
  lat: number;
  lon: number;
  alt: number;
}

/** ECEF 坐标（米），杆臂标定优先使用 */
export interface EcefCoord {
  x: number;
  y: number;
  z: number;
}

/** 解算输入：接收机主天线 WGS84 */
export interface RefAntennaCoord extends GpsCoord {
  receiver_id?: string;
  ecef_x?: number;
  ecef_y?: number;
  ecef_z?: number;
}

/** 副天线 A2 WGS84（接收机1 柴油机侧 GGA） */
export interface Antenna2Coord extends GpsCoord {
  valid?: boolean;
  ecef_x?: number;
  ecef_y?: number;
  ecef_z?: number;
}

export interface LocalCoord {
  x: number;
  y: number;
  z: number;
}

/** 带有效标志的堆场坐标点 */
export interface GantryCenter {
  x: number;
  y: number;
  valid?: boolean;
}

/** 大车定位输出（6.1 节） */
export interface GantryData {
  /** 电气房侧作业点 L-Point（接收机1 天线 A1 + l_arm_elec） */
  elec_lpoint?: GantryCenter;
  /** 柴油机侧作业点 L-Point（接收机1 天线 A2 + l_arm_diesel） */
  diesel_lpoint?: GantryCenter;
  /** @deprecated 同 elec_lpoint */
  receiver_left: GantryCenter;
  /** @deprecated 同 diesel_lpoint */
  receiver_right: GantryCenter;
  /** @deprecated 同 elec_lpoint */
  elec_center: GantryCenter;
  /** @deprecated 同 diesel_lpoint */
  diesel_center: GantryCenter;
  leg_offsets: { fl: number; fr: number; rl: number; rr: number };
  speed: number;
}

/** 小车定位输出（6.2 节） */
export interface TrolleyData {
  valid?: boolean;
  ant_a: LocalCoord;
  ant_b: LocalCoord;
  travel: number;
  speed: number;
}

export interface RealtimeData {
  timestamp: string;
  /** 解算输出：大车 L-Point WGS84 */
  lpoint_geo: GpsCoord;
  /** 解算输出：大车 L-Point 堆场坐标 (LYCS) */
  local_coord: LocalCoord;
  /** 解算输入：参考接收机主天线 WGS84 */
  ref_antenna: RefAntennaCoord;
  /** 接收机1 副天线 A2 WGS84（智能杆臂标定柴油机侧） */
  antenna2?: Antenna2Coord;
  /** @deprecated 同 ref_antenna，兼容旧遥测 */
  gps_coord?: GpsCoord;
  heading: number;
  roll: number;
  pitch: number;
  speed: number;
  status: GnssStatus;
  receivers: ReceiverStatus[];
  /** 大车定位数据（双中心 + 四门腿偏移 + 速度） */
  gantry: GantryData;
  /** 小车定位数据（两端天线坐标 + 行程 + 速度） */
  trolley: TrolleyData;
}

export interface ReceiverStatus {
  id: string;
  label: string;
  position: string;
  status: GnssStatus;
  satellites: number;
  hdop: number;
  valid?: boolean;
}

// ===== 车辆参数类型 =====
/** 天线标识：用于区分 6 根天线各自对应的物理角色（大车左前/右前/左后/右后、小车A端/B端） */
export interface AntennaLabel {
  id: string;
  label: string;
}

/** 车辆物理几何参数（与场地角色无关，角色由场地是否建图决定） */
export interface VehicleProfile {
  vehicle_id: string;
  label: string;       // 用户可读名称，如 "RTG 01号机"
  height: number;
  span: number;        // 门腿跨距 W_span (m)，参考尺寸
  /** @deprecated 兼容单杆臂；请使用 l_arm_elec / l_arm_diesel */
  l_arm: [number, number, number];
  /** 电气房侧作业点杆臂（天线 A1 → L-Point） */
  l_arm_elec: [number, number, number];
  /** 柴油机侧作业点杆臂（天线 A2 → L-Point） */
  l_arm_diesel: [number, number, number];
  /** 最近一次智能标定记录（可选，便于追溯） */
  l_arm_calibration?: {
    elec?: LArmCalibrationRecord;
    diesel?: LArmCalibrationRecord;
  };
  /** 天线标签列表，标识每根天线对应的物理角色 */
  antennas: AntennaLabel[];
}

/** 杆臂智能标定记录 */
export interface LArmCalibrationRecord {
  measured_at: string;
  lpoint: GpsCoord;
  antenna: GpsCoord;
  attitude: { roll: number; pitch: number; heading: number };
  l_arm: [number, number, number];
}

/**
 * 后端服务连接配置（全局唯一）
 * 架构：前端 <-> 后端(WebSocket) <-> MQTT Broker <-> 中控机
 * 前端通过 ws://host:port/ws/realtime?vehicle_id=xxx 订阅指定车辆的实时数据
 */
export interface BackendConfig {
  host: string;   // 后端服务 IP，如 "192.168.1.200"
  port: number;   // 后端 WS 端口，如 8080
}

// ===== 测绘数据类型 =====
export interface SurveyPoint {
  id: string;
  bay_id: string;
  bay_no?: number;
  yard_id: string;
  lat: number;
  lon: number;
  alt: number;
  dz: number;
  timestamp: string;
  synced: boolean;
}

export interface YardInfo {
  yard_id: string;
  name: string;
  origin_lat: number;
  origin_lon: number;
  heading: number;
  total_bays: number;
  mapped: boolean;      // 是否已完成场地底图测绘
}

// ===== 校准数据类型 =====
export interface CalibrationStep {
  step: 1 | 2 | 3;
  bay_id: string;
  status: 'waiting' | 'in_progress' | 'done';
  measured?: LocalCoord;
  reference?: LocalCoord;
  delta?: LocalCoord;
}

// ===== API 响应类型 =====
export interface ApiResponse<T> {
  success: boolean;
  data?: T;
  message?: string;
}

export type TabId = 'dashboard' | 'settings' | 'config' | 'mapping' | 'calibration' | 'manual';
