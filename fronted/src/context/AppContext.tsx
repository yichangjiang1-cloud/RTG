import { createContext, useContext, useState, useCallback, useEffect } from 'react';
import type { ReactNode } from 'react';
import type { VehicleProfile, YardInfo, BackendConfig } from '../types';
import { logger } from '../utils/logger';

// ============================================================
// Context 类型定义
// ============================================================
export type ConnStatus = 'connecting' | 'connected' | 'disconnected';

/** 尚未初始化的设备（is_initialized=false） */
export interface PendingDevice {
  temp_id: string;
  sn: string;
  last_seen?: string;
}

interface AppState {
  /** 所有已注册车辆 */
  vehicles: VehicleProfile[];
  /** 尚未初始化的设备列表（is_initialized=false） */
  pendingDevices: PendingDevice[];
  /** 所有场地 */
  yards: YardInfo[];
  /** 当前选中车辆 ID（null = 未选择） */
  selectedVehicleId: string | null;
  /** 当前选中场地 ID（null = 未选择） */
  selectedYardId: string | null;
  /** 是否使用模拟数据（true = 模拟，false = 连接真实中控机） */
  useMock: boolean;
  setUseMock: (v: boolean) => void;
  /**
   * 后端服务地址（全局唯一）
   * 前端通过 ws://host:port/ws/realtime?vehicle_id=xxx 订阅实时数据
   * 后端负责 MQTT Broker <-> 中控机 的数据中转
   */
  backendConfig: BackendConfig;
  setBackendConfig: (cfg: BackendConfig) => void;
  /** WebSocket 连接状态（由 App.tsx 同步写入） */
  connStatus: ConnStatus;
  setConnStatus: (s: ConnStatus) => void;
  /** 当前是否处于模拟数据模式（由 App.tsx 同步写入） */
  isMock: boolean;
  setIsMock: (v: boolean) => void;

  setSelectedVehicleId: (id: string | null) => void;
  setSelectedYardId: (id: string | null) => void;
  addVehicle: (v: VehicleProfile) => void;
  updateVehicle: (v: VehicleProfile) => void;
  deleteVehicle: (vehicleId: string) => void;
  /** 将场地标记为"已建图"（建图完成后调用） */
  markYardMapped: (yardId: string) => void;
  /** 从后端 API 刷新车辆列表（非模拟模式下调用） */
  fetchVehicles: () => Promise<void>;
  /** 从后端 API 刷新场地列表（非模拟模式下调用） */
  fetchYards: () => Promise<void>;
  /**
   * 为待初始化设备分配正式 ID，调用 POST /api/vehicle/init
   */
  initDevice: (params: {
    temp_id: string;
    new_vehicle_id: string;
    label: string;
    l_arm: [number, number, number];
    l_arm_elec: [number, number, number];
    l_arm_diesel: [number, number, number];
    h: number;
    w_span: number;
  }) => Promise<void>;
  /**
   * 切换模拟模式：
   * - 开启模拟：还原 DEFAULT 数据
   * - 关闭模拟：清空列表和选中状态，再从后端拉取
   */
  switchMockMode: (enable: boolean) => void;
}

const AppContext = createContext<AppState | null>(null);

// ============================================================
// 默认数据（演示用，实际从后端加载）
// ============================================================
const DEFAULT_RECEIVER_LAYOUT = {
  left: 'gantry_a1',
  right: 'gantry_a2',
  trolley: 'trolley',
  left_port: 9001,
  right_port: 9002,
  trolley_port: 9003,
  gga_order: ['elec', 'diesel'] as ['elec', 'diesel'],
};

const DEFAULT_VEHICLES: VehicleProfile[] = [
  {
    vehicle_id: 'RTG-001',
    label: 'RTG 01号机',
    height: 30.0,
    span: 23.475,
    l_arm: [0.12, 0.08, -29.8],
    l_arm_elec: [0.12, 0.08, -29.8],
    l_arm_diesel: [0.12, 0.08, -29.8],
    receiver_layout: { ...DEFAULT_RECEIVER_LAYOUT },
  },
  {
    vehicle_id: 'RTG-002',
    label: 'RTG 02号机',
    height: 30.0,
    span: 23.475,
    l_arm: [0.12, 0.08, -29.8],
    l_arm_elec: [0.12, 0.08, -29.8],
    l_arm_diesel: [0.12, 0.08, -29.8],
    receiver_layout: { ...DEFAULT_RECEIVER_LAYOUT },
  },
  {
    vehicle_id: 'RTG-003',
    label: 'RTG 03号机',
    height: 30.5,
    span: 23.475,
    l_arm: [0.12, 0.08, -30.3],
    l_arm_elec: [0.12, 0.08, -30.3],
    l_arm_diesel: [0.12, 0.08, -30.3],
    receiver_layout: { ...DEFAULT_RECEIVER_LAYOUT },
  },
];

const DEFAULT_YARDS: YardInfo[] = [
  {
    yard_id: 'yard_01',
    name: '堆场 A区',
    origin_lat: 22.1234567,
    origin_lon: 114.2345678,
    heading: 0,
    total_bays: 10,
    mapped: true,   // 已完成建图
  },
  {
    yard_id: 'yard_02',
    name: '堆场 B区',
    origin_lat: 22.1244567,
    origin_lon: 114.2355678,
    heading: 90,
    total_bays: 8,
    mapped: false,  // 尚未建图
  },
  {
    yard_id: 'yard_03',
    name: '堆场 C区',
    origin_lat: 22.1254567,
    origin_lon: 114.2365678,
    heading: 0,
    total_bays: 12,
    mapped: false,
  },
];

// ============================================================
// Provider
// ============================================================
export function AppProvider({ children }: { children: ReactNode }) {
  const [vehicles, setVehicles] = useState<VehicleProfile[]>([]);
  const [pendingDevices, setPendingDevices] = useState<PendingDevice[]>([]);
  const [yards, setYards] = useState<YardInfo[]>([]);
  const [selectedVehicleId, setSelectedVehicleId] = useState<string | null>(null);
  const [selectedYardId, setSelectedYardId] = useState<string | null>(null);
  const [useMock, setUseMock] = useState<boolean>(false);
  const [backendConfig, setBackendConfig] = useState<BackendConfig>({ host: 'localhost', port: 8000 });
  const [connStatus, setConnStatus] = useState<ConnStatus>('disconnected');
  const [isMock, setIsMock] = useState<boolean>(false);

  const addVehicle = (v: VehicleProfile) =>
    setVehicles(prev => [...prev, v]);

  const updateVehicle = (v: VehicleProfile) =>
    setVehicles(prev => prev.map(x => x.vehicle_id === v.vehicle_id ? v : x));

  const deleteVehicle = (vehicleId: string) => {
    setVehicles(prev => prev.filter(x => x.vehicle_id !== vehicleId));
    if (selectedVehicleId === vehicleId) setSelectedVehicleId(null);
  };

  const markYardMapped = useCallback((yardId: string) => {
    setYards(prev => {
      const target = prev.find(y => y.yard_id === yardId);
      if (!target || target.mapped) return prev;
      return prev.map(y => (y.yard_id === yardId ? { ...y, mapped: true } : y));
    });
  }, []);

  /** 从后端 GET /api/yard/all_origins 拉取场地列表 */
  const fetchYards = useCallback(async () => {
    const url = `http://${backendConfig.host}:${backendConfig.port}/api/yard/all_origins`;
    logger.info('AppContext', 'GET /api/yard/all_origins', { url });
    try {
      const resp = await fetch(url, { signal: AbortSignal.timeout(5000) });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      // 后端返回 ApiResponse 包装格式 { code, message, data: [...] }
      const json = await resp.json() as { code: number; data: Array<{
        yard_id: string;
        name: string;
        origin: [number, number];
        heading?: number;
        total_bays?: number;
        mapped?: boolean;
      }> };
      if (json.code === 200 && Array.isArray(json.data) && json.data.length > 0) {
        const yards: YardInfo[] = json.data.map(y => ({
          yard_id: y.yard_id,
          name: y.name,
          origin_lat: y.origin[1],
          origin_lon: y.origin[0],
          heading: y.heading ?? 0,
          total_bays: y.total_bays ?? 0,
          mapped: y.mapped ?? false,
        }));
        logger.info('AppContext', `fetchYards 成功，共 ${yards.length} 个场地`);
        setYards(yards);
      } else {
        logger.warn('AppContext', 'fetchYards 返回空数组，保留当前列表');
      }
    } catch (e) {
      logger.error('AppContext', 'fetchYards 失败', e);
    }
  }, [backendConfig]);

  /** 从后端 GET /api/vehicle/list 拉取在线车辆列表 */
  const fetchVehicles = useCallback(async () => {
    const url = `http://${backendConfig.host}:${backendConfig.port}/api/vehicle/list`;
    logger.info('AppContext', 'GET /api/vehicle/list', { url });
    try {
      const resp = await fetch(url, { signal: AbortSignal.timeout(5000) });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const json = await resp.json() as { code: number; data: Array<{
        vehicle_id: string;
        sn: string;
        status: string;
        is_initialized: boolean;
        label?: string;
        height?: number;
        span?: number;
        last_seen?: string;
      }> };
      if (json.code === 200 && Array.isArray(json.data)) {
        const zeroArm = [0, 0, 0] as [number, number, number];
        const profiles: VehicleProfile[] = json.data
          .filter(d => d.is_initialized)
          .map(d => ({
            vehicle_id: d.vehicle_id,
            label: d.label ?? d.vehicle_id,
            height: d.height ?? 30.0,
            span: d.span ?? 23.475,
            l_arm: zeroArm,
            l_arm_elec: zeroArm,
            l_arm_diesel: zeroArm,
            receiver_layout: { ...DEFAULT_RECEIVER_LAYOUT },
          }));
        logger.info('AppContext', `fetchVehicles 成功，已初始化 ${profiles.length} 台，待初始化 ${json.data.length - profiles.length} 台`);
        setVehicles(profiles);
        const pending: PendingDevice[] = json.data
          .filter(d => !d.is_initialized)
          .map(d => ({ temp_id: d.vehicle_id, sn: d.sn ?? '', last_seen: d.last_seen }));
        setPendingDevices(pending);
      } else {
        logger.warn('AppContext', 'fetchVehicles 返回非预期格式', json);
      }
    } catch (e) {
      logger.error('AppContext', 'fetchVehicles 失败', e);
    }
  }, [backendConfig]);

  const switchMockMode = useCallback((enable: boolean) => {
    logger.info('AppContext', `切换模拟模式 → ${enable ? '开启' : '关闭'}`);
    setUseMock(enable);
    if (enable) {
      // 开启模拟：还原默认演示数据
      setVehicles(DEFAULT_VEHICLES);
      setYards(DEFAULT_YARDS);
      setSelectedVehicleId(null);
      setSelectedYardId(null);
      logger.debug('AppContext', '已还原 DEFAULT_VEHICLES / DEFAULT_YARDS');
    } else {
      // 关闭模拟：先清空，再从后端拉取（复用 fetchVehicles / fetchYards）
      setVehicles([]);
      setYards([]);
      setPendingDevices([]);
      setSelectedVehicleId(null);
      setSelectedYardId(null);
      logger.info('AppContext', '列表已清空，开始从后端拉取…');
      // fetchVehicles 内部同时处理 pendingDevices
      fetchVehicles().catch(() => {});
      fetchYards().catch(() => {});
    }
  }, [fetchVehicles, fetchYards]);

  // 非 Mock 模式下，启动时自动从后端拉取车辆和场地列表
  useEffect(() => {
    if (!useMock) {
      logger.info('AppContext', '非模拟模式启动，从后端拉取数据…');
      fetchVehicles().catch(() => {});
      fetchYards().catch(() => {});
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /** 为待初始化设备分配正式 ID，调用 POST /api/vehicle/init */
  const initDevice = useCallback(async (params: {
    temp_id: string;
    new_vehicle_id: string;
    label: string;
    l_arm: [number, number, number];
    l_arm_elec: [number, number, number];
    l_arm_diesel: [number, number, number];
    h: number;
    w_span: number;
  }) => {
    const resp = await fetch(
      `http://${backendConfig.host}:${backendConfig.port}/api/vehicle/init`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          temp_id: params.temp_id,
          new_vehicle_id: params.new_vehicle_id,
          label: params.label,
          params: {
            vehicle_id: params.new_vehicle_id,
            l_arm: params.l_arm,
            l_arm_elec: params.l_arm_elec,
            l_arm_diesel: params.l_arm_diesel,
            height: params.h,
            w_span: params.w_span,
          },
        }),
        signal: AbortSignal.timeout(10000),
      },
    );
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    // 从待初始化列表移除
    setPendingDevices(prev => prev.filter(d => d.temp_id !== params.temp_id));
    // 将新车辆加入已注册列表
    addVehicle({
      vehicle_id: params.new_vehicle_id,
      label: params.label,
      height: params.h,
      span: params.w_span,
      l_arm: params.l_arm,
      l_arm_elec: params.l_arm_elec,
      l_arm_diesel: params.l_arm_diesel,
      receiver_layout: { ...DEFAULT_RECEIVER_LAYOUT },
    });
  }, [backendConfig, addVehicle]);

  return (
    <AppContext.Provider value={{
      vehicles, pendingDevices, yards,
      selectedVehicleId, setSelectedVehicleId,
      selectedYardId, setSelectedYardId,
      useMock, setUseMock,
      backendConfig, setBackendConfig,
      connStatus, setConnStatus,
      isMock, setIsMock,
      addVehicle, updateVehicle, deleteVehicle,
      markYardMapped,
      fetchVehicles,
      fetchYards,
      initDevice,
      switchMockMode,
    }}>
      {children}
    </AppContext.Provider>
  );
}

// ============================================================
// Hook
// ============================================================
export function useAppContext() {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error('useAppContext must be used within AppProvider');
  return ctx;
}

/** 便捷 hook：获取当前选中的车辆和场地对象 */
export function useCurrentSession() {
  const { vehicles, yards, selectedVehicleId, selectedYardId } = useAppContext();
  const vehicle = vehicles.find(v => v.vehicle_id === selectedVehicleId) ?? null;
  const yard    = yards.find(y => y.yard_id === selectedYardId) ?? null;
  return { vehicle, yard };
}
