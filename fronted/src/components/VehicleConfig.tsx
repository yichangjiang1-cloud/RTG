import { useState, useEffect, useCallback, useMemo, type Dispatch, type SetStateAction, type ReactNode } from 'react';
import { Settings2, Plus, Save, FolderOpen, Car, Radio, RefreshCw, X, Loader2, AlertTriangle, Copy, Sparkles, WifiOff, ArrowLeftRight, MoveHorizontal } from 'lucide-react';
import { SectionHeader, showToast } from './common';
import { useAppContext } from '../context/AppContext';
import type { ConnStatus, PendingDevice } from '../context/AppContext';
import type { VehicleProfile, RealtimeData, LArmCalibrationRecord, GpsCoord, GantryAttitudeConfig, GantryAntennaBody, ReceiverLayout, GantryAntennaPoint, TrolleyOriginConfig, NtripConfig } from '../types';
import { clsx } from 'clsx';
import { logger } from '../utils/logger';
import { httpBase } from '../utils/backendUrl';

// ── 新设备初始化弹窗 ──────────────────────────────────────
function InitDeviceModal({ device, backendBase, onClose, onDone }: {
  device: PendingDevice;
  backendBase: string;
  onClose: () => void;
  onDone: (newVehicleId: string) => void;
}) {
  const { initDevice, vehicles } = useAppContext();
  const [newId, setNewId] = useState('RTG-');
  const [label, setLabel] = useState('');
  const [h, setH] = useState('22.5');
  const [wSpan, setWSpan] = useState('23.47');
  const [saving, setSaving] = useState(false);
  const [clonedArms, setClonedArms] = useState<{
    l_arm_elec: [number, number, number];
    l_arm_diesel: [number, number, number];
  } | null>(null);

  const [cloneSourceId, setCloneSourceId] = useState<string | null>(null);
  const [loadingClone, setLoadingClone] = useState(false);

  async function handleCloneSelect(sourceId: string) {
    if (!sourceId) {
      setCloneSourceId(null);
      setClonedArms(null);
      return;
    }
    setCloneSourceId(sourceId);
    setLoadingClone(true);
    try {
      const res = await fetch(`${backendBase}/api/vehicle/config/${sourceId}`, { signal: AbortSignal.timeout(5000) });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const resp = await res.json() as { code: number; data: { l_arm?: number[]; l_arm_elec?: number[]; l_arm_diesel?: number[]; height?: number; w_span?: number } | null };
      if (resp.code === 200 && resp.data) {
        const d = resp.data;
        const legacy = d.l_arm ?? [0, 0, 0];
        const elec = (d.l_arm_elec ?? legacy).slice(0, 3) as [number, number, number];
        const diesel = (d.l_arm_diesel ?? legacy).slice(0, 3) as [number, number, number];
        setClonedArms({ l_arm_elec: elec, l_arm_diesel: diesel });
        if (d.height != null) setH(String(d.height));
        if (d.w_span != null) setWSpan(String(d.w_span));
        showToast(`已从 ${sourceId} 复制 H/W 与杆臂（杆臂请上线后用智能标定复核）`);
      } else {
        showToast('该车型暂无参数配置', 'error');
        setCloneSourceId(null);
        setClonedArms(null);
      }
    } catch {
      showToast('获取源车型参数失败', 'error');
      setCloneSourceId(null);
      setClonedArms(null);
    } finally {
      setLoadingClone(false);
    }
  }

  async function handleSubmit() {
    if (!newId.trim() || !label.trim()) { showToast('请填写正式 ID 和名称'); return; }
    const zero: [number, number, number] = [0, 0, 0];
    const elec = clonedArms?.l_arm_elec ?? zero;
    const diesel = clonedArms?.l_arm_diesel ?? zero;
    setSaving(true);
    try {
      await initDevice({
        temp_id: device.temp_id,
        new_vehicle_id: newId.trim(),
        label: label.trim(),
        l_arm: elec,
        l_arm_elec: elec,
        l_arm_diesel: diesel,
        h: parseFloat(h),
        w_span: parseFloat(wSpan),
      });

      if (cloneSourceId) {
        try {
          await fetch(`${backendBase}/api/vehicle/config/clone`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ source_vehicle_id: cloneSourceId, new_vehicle_id: newId.trim() }),
          });
        } catch { /* clone 失败不阻塞初始化流程 */ }
      }

      showToast(`${newId} 初始化成功；请在中控运行后使用「智能杆臂标定」完成杆臂配置`);
      onDone(newId.trim());
    } catch {
      showToast('初始化失败，请检查后端连接', 'error');
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4">
      <div className="bg-slate-900 border border-slate-600/60 rounded-2xl p-7 w-full max-w-md space-y-5 shadow-2xl shadow-black/50">
        <div className="flex items-center justify-between">
          <h3 className="text-lg font-bold text-white">初始化新设备</h3>
          <button onClick={onClose} className="btn-icon p-2 text-slate-500 hover:text-white"><X className="w-5 h-5" /></button>
        </div>

        <div className="p-3 bg-slate-800/60 rounded-lg text-xs space-y-1 font-mono">
          <div className="flex justify-between"><span className="text-slate-500">临时 ID</span><span className="text-amber-400">{device.temp_id}</span></div>
          <div className="flex justify-between"><span className="text-slate-500">SN</span><span className="text-slate-300">{device.sn || '-'}</span></div>
        </div>

        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="section-title block mb-1">正式 ID</label>
              <input className="input-field font-mono" value={newId} onChange={e => setNewId(e.target.value)} placeholder="RTG-001" />
            </div>
            <div>
              <label className="section-title block mb-1">显示名称</label>
              <input className="input-field" value={label} onChange={e => setLabel(e.target.value)} placeholder="RTG 01号机" />
            </div>
          </div>

          {vehicles.length > 0 && (
            <div>
              <label className="section-title block mb-1">
                <Copy className="w-3 h-3 inline mr-1 -mt-0.5" />
                从已有车型复制参数（可选）
              </label>
              <div className="flex items-center gap-2">
                <select
                  className="input-field font-mono text-sm flex-1"
                  value={cloneSourceId ?? ''}
                  onChange={e => handleCloneSelect(e.target.value)}
                  disabled={loadingClone}
                >
                  <option value="">— 不复制 —</option>
                  {vehicles.map(v => (
                    <option key={v.vehicle_id} value={v.vehicle_id}>
                      {v.label} ({v.vehicle_id})
                    </option>
                  ))}
                </select>
                {loadingClone && <Loader2 className="w-4 h-4 text-blue-400 animate-spin shrink-0" />}
              </div>
              {cloneSourceId && !loadingClone && (
                <p className="text-[10px] text-blue-400 mt-1">
                  已复制 H/W；杆臂上线后请用智能标定重新计算
                </p>
              )}
            </div>
          )}

          <p className="text-[10px] text-slate-500 leading-relaxed">
            杆臂 L_arm 不在此录入，初始化后于「智能杆臂标定」中一键计算。
          </p>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="section-title block mb-1">吊具基准高度 H (m)</label>
              <input type="number" step="0.1" className="input-field font-mono" value={h} onChange={e => setH(e.target.value)} />
            </div>
            <div>
              <label className="section-title block mb-1">跨距 W_span (m)</label>
              <input type="number" step="0.01" className="input-field font-mono" value={wSpan} onChange={e => setWSpan(e.target.value)} />
            </div>
          </div>
        </div>

        <div className="flex gap-3 pt-2">
          <button onClick={onClose} className="flex-1 btn-outline py-3">取消</button>
          <button onClick={handleSubmit} disabled={saving} className="flex-1 btn-primary py-3 flex items-center justify-center gap-2">
            {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
            {saving ? '初始化中…' : '确认初始化'}
          </button>
        </div>
      </div>
    </div>
  );
}

const DEFAULT_RECEIVER_LAYOUT: ReceiverLayout = {
  left: 'gantry_a1',
  right: 'gantry_a2',
  trolley: 'trolley',
  left_transport: 'tcp',
  left_host: '192.168.0.101',
  left_port: 30001,
  right_transport: 'tcp',
  right_host: '192.168.0.102',
  right_port: 30001,
  trolley_transport: 'udp',
  trolley_host: '0.0.0.0',
  trolley_port: 9003,
  gga_order: ['elec', 'diesel'],
};

const DEFAULT_NTRIP: NtripConfig = {
  enabled: false,
  caster_host: '',
  caster_port: 2101,
  mountpoint: '',
  username: '',
  password: '',
  gga_interval_s: 10,
};

function defaultGantryAttitude(span: number, baseline = 1): GantryAttitudeConfig {
  return {
    mode: 'hdt_tra_height_roll',
    baseline_m: baseline,
    roll_span_m: span,
    roll_alt0_m: 0,
    antenna_body: {
      a1: [0, 0, 0],
      a2: [baseline, 0, 0],
      b1: [0, -span, 0],
      b2: [baseline, -span, 0],
    },
  };
}

type ConfigTab = 'basic' | 'larm' | 'attitude' | 'trolley';
type CalibSide = 'elec' | 'diesel';

function ConfigPanelHeader({
  icon: Icon,
  title,
  trailing,
}: {
  icon: React.ComponentType<{ className?: string }>;
  title: string;
  trailing?: ReactNode;
}) {
  return (
    <div className="config-panel-header">
      <div className="w-9 h-9 rounded-xl bg-blue-500/15 border border-blue-500/25 flex items-center justify-center shrink-0">
        <Icon className="w-4 h-4 text-blue-400" />
      </div>
      <h3 className="config-panel-title">{title}</h3>
      {trailing}
    </div>
  );
}

function SwipeTabs<T extends string>({
  tabs,
  active,
  onChange,
}: {
  tabs: { id: T; label: string }[];
  active: T;
  onChange: (id: T) => void;
}) {
  return (
    <div className="config-tabs">
      {tabs.map((t) => (
        <button
          key={t.id}
          type="button"
          onClick={() => onChange(t.id)}
          className={active === t.id
            ? 'config-tab-active bg-gradient-to-b from-blue-600 to-blue-700 shadow-blue-900/50'
            : 'config-tab-inactive'}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}

function mergeVehicleConfig(
  local: VehicleProfile,
  data: {
    l_arm?: [number, number, number];
    l_arm_elec?: [number, number, number];
    l_arm_diesel?: [number, number, number];
    height?: number;
    w_span?: number;
    l_arm_calibration?: VehicleProfile['l_arm_calibration'];
    gantry_attitude?: GantryAttitudeConfig;
    receiver_layout?: ReceiverLayout;
    ntrip?: NtripConfig;
    trolley?: TrolleyOriginConfig;
  } | null,
): VehicleProfile {
  const legacy = data?.l_arm ?? local.l_arm ?? DEFAULT_ARM;
  const span = data?.w_span ?? local.span;
  return {
    ...local,
    height: data?.height ?? local.height,
    span,
    l_arm: legacy,
    l_arm_elec: data?.l_arm_elec ?? local.l_arm_elec ?? legacy,
    l_arm_diesel: data?.l_arm_diesel ?? local.l_arm_diesel ?? legacy,
    l_arm_calibration: data?.l_arm_calibration ?? local.l_arm_calibration,
    gantry_attitude: data?.gantry_attitude ?? local.gantry_attitude ?? defaultGantryAttitude(span),
    receiver_layout: data?.receiver_layout ?? local.receiver_layout ?? DEFAULT_RECEIVER_LAYOUT,
    ntrip: data?.ntrip ?? local.ntrip ?? DEFAULT_NTRIP,
    trolley: data?.trolley ?? local.trolley ?? { origin_mode: 'auto' },
  };
}

const DEFAULT_ARM: [number, number, number] = [0, 0, 0];

// ============================================================
// 智能杆臂标定
// ============================================================
const MAX_CALIB_SPEED = 0.08;

type AntennaEcefSource = { ecef_x?: number; ecef_y?: number; ecef_z?: number };

function pickAntennaEcef(src: AntennaEcefSource | undefined): { x: number; y: number; z: number } | null {
  if (!src) return null;
  const { ecef_x: x, ecef_y: y, ecef_z: z } = src;
  if (![x, y, z].every(v => typeof v === 'number' && Number.isFinite(v))) return null;
  if (x === 0 && y === 0 && z === 0) return null;
  return { x: x!, y: y!, z: z! };
}

/** 副天线 A2 是否可用于柴油机侧杆臂标定 */
function isAntenna2Ready(liveData: RealtimeData): boolean {
  const a2 = liveData.antenna2;
  if (!a2) return false;
  if (a2.valid === true) return true;
  const hasCoords = Math.abs(a2.lat) > 1e-6 && Math.abs(a2.lon) > 1e-6;
  if (!hasCoords) return false;
  const ref = liveData.ref_antenna;
  return Math.abs(a2.lat - ref.lat) > 1e-7 || Math.abs(a2.lon - ref.lon) > 1e-7;
}

function formatArm(arm: [number, number, number]) {
  return `[${arm.map(v => v.toFixed(3)).join(', ')}]`;
}

function ReadOnlyLArmBlock({
  title,
  arm,
  uncalibrated,
}: {
  title: string;
  arm: [number, number, number];
  uncalibrated?: boolean;
}) {
  const isZero = arm.every(v => Math.abs(v) < 1e-6);
  return (
    <div className="p-3 bg-slate-900/60 rounded-lg border border-slate-700/50">
      <p className="text-[10px] text-slate-500 mb-1">{title}</p>
      <p className={clsx(
        'font-mono text-sm',
        uncalibrated || isZero ? 'text-amber-400/80' : 'text-blue-200',
      )}>
        {uncalibrated || isZero ? '未标定' : formatArm(arm)}
      </p>
      <div className="grid grid-cols-3 gap-2 mt-2 text-[10px] font-mono text-slate-500">
        {(['X', 'Y', 'Z'] as const).map((axis, i) => (
          <div key={axis}>
            <span className="text-slate-600">{axis}</span>
            <p className="text-slate-400">{arm[i].toFixed(3)}</p>
          </div>
        ))}
      </div>
    </div>
  );
}

function LArmSmartCalibration({
  liveData,
  connStatus,
  isMock,
  useMock,
  baseUrl,
  cfg,
  setCfg,
}: {
  liveData: RealtimeData;
  connStatus: ConnStatus;
  isMock: boolean;
  useMock: boolean;
  baseUrl: string;
  cfg: VehicleProfile | null;
  setCfg: Dispatch<SetStateAction<VehicleProfile | null>>;
}) {
  const [lpElec, setLpElec] = useState({ lat: '', lon: '', alt: '' });
  const [lpDiesel, setLpDiesel] = useState({ lat: '', lon: '', alt: '' });
  const [computing, setComputing] = useState<CalibSide | null>(null);
  const [calibSide, setCalibSide] = useState<CalibSide>('elec');

  const stationary = liveData.speed <= MAX_CALIB_SPEED;
  const gnssOk = liveData.status === 'fix';
  const wsOk = connStatus === 'connected' && !useMock && !isMock;
  const a2Ok = isAntenna2Ready(liveData);
  const canCalib = wsOk && stationary && gnssOk && !!cfg;

  const liveHint = useMemo(() => {
    if (!cfg) return '请先在上方选择要配置的车辆';
    if (useMock || isMock) return '请关闭模拟模式，并在工作配置中选择车辆以建立 WebSocket';
    if (connStatus !== 'connected') return 'WebSocket 未连接：请在工作配置中选择车辆';
    if (!gnssOk) return 'GNSS 非固定解，请等待 RTK FIX';
    if (!stationary) return `车辆未静止（速度 ${liveData.speed.toFixed(3)} m/s），请停车后标定`;
    if (!a2Ok) {
      return '柴油侧天线坐标无效：请确认右接收机（EVK）主 GGA 已接入，遥测 antenna2 有效';
    }
    return '条件满足：可输入 RTK 作业点坐标并计算杆臂';
  }, [cfg, useMock, isMock, connStatus, gnssOk, stationary, liveData.speed, a2Ok]);

  async function computeSide(side: CalibSide) {
    if (!cfg) return;
    const lpStr = side === 'elec' ? lpElec : lpDiesel;
    const lat = parseFloat(lpStr.lat);
    const lon = parseFloat(lpStr.lon);
    const alt = parseFloat(lpStr.alt);
    if (![lat, lon, alt].every(Number.isFinite)) {
      showToast('请填写完整的作业点经纬高', 'error');
      return;
    }
    if (!canCalib) {
      showToast(liveHint, 'error');
      return;
    }
    const antenna: GpsCoord = side === 'elec'
      ? { lat: liveData.ref_antenna.lat, lon: liveData.ref_antenna.lon, alt: liveData.ref_antenna.alt }
      : {
          lat: liveData.antenna2?.lat ?? 0,
          lon: liveData.antenna2?.lon ?? 0,
          alt: liveData.antenna2?.alt ?? 0,
        };
    const antennaSrc = side === 'elec' ? liveData.ref_antenna : liveData.antenna2;
    const antennaEcef = pickAntennaEcef(antennaSrc);
    if (side === 'diesel' && !a2Ok) {
      showToast('柴油侧天线无效：请确认右接收机主 GGA 已接入', 'error');
      return;
    }
    const attitude = { roll: liveData.roll, pitch: liveData.pitch, heading: liveData.heading };
    const lpoint = { lat, lon, alt };

    setComputing(side);
    try {
      const res = await fetch(`${baseUrl}/api/vehicle/compute_l_arm`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          side,
          lpoint,
          antenna,
          attitude,
          ...(antennaEcef ? { antenna_ecef: antennaEcef } : {}),
        }),
        signal: AbortSignal.timeout(10000),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json = await res.json() as { code: number; data?: { l_arm: [number, number, number] } };
      if (json.code !== 200 || !json.data?.l_arm) throw new Error('反算失败');

      const arm = json.data.l_arm;
      const layout = cfg.receiver_layout ?? DEFAULT_RECEIVER_LAYOUT;
      const ggaIndex = layout.gga_order.indexOf(side);
      const record: LArmCalibrationRecord = {
        measured_at: new Date().toISOString(),
        lpoint,
        antenna,
        attitude,
        l_arm: arm,
        receiver_id: liveData.ref_antenna?.receiver_id ?? layout.left,
        gga_index: ggaIndex >= 0 ? ggaIndex : (side === 'elec' ? 0 : 1),
        layout_snapshot: {
          left: layout.left,
          right: layout.right,
          gga_order: layout.gga_order,
        },
      };

      setCfg(prev => {
        if (!prev) return null;
        const cal = { ...(prev.l_arm_calibration ?? {}) };
        if (side === 'elec') {
          cal.elec = record;
          return { ...prev, l_arm_elec: arm, l_arm: arm, l_arm_calibration: cal };
        }
        cal.diesel = record;
        return { ...prev, l_arm_diesel: arm, l_arm_calibration: cal };
      });
      showToast(`${side === 'elec' ? '电气房侧' : '柴油机侧'}杆臂已计算：[${arm.map(v => v.toFixed(3)).join(', ')}]`);
    } catch {
      showToast('杆臂计算失败，请检查后端与输入', 'error');
    } finally {
      setComputing(null);
    }
  }

  function renderLpointInputs(_side: CalibSide, val: typeof lpElec, setVal: typeof setLpElec) {
    return (
      <div className="grid grid-cols-3 gap-2">
        {(['lat', 'lon', 'alt'] as const).map(k => (
          <div key={k}>
            <span className="text-[10px] text-slate-500 uppercase">{k}</span>
            <input
              className="input-field font-mono text-xs py-1.5"
              value={val[k]}
              onChange={e => setVal(v => ({ ...v, [k]: e.target.value }))}
              placeholder={k === 'lat' ? '22.xxx' : k === 'lon' ? '114.xxx' : '12.xxx'}
            />
          </div>
        ))}
      </div>
    );
  }

  return (
    <div className="config-panel">
      <ConfigPanelHeader
        icon={Sparkles}
        title="智能杆臂标定"
        trailing={
          <span className={clsx(
            'text-xs font-bold px-3 py-1 rounded-full border',
            canCalib ? 'text-emerald-300 border-emerald-500/50 bg-emerald-900/40' : 'text-amber-300 border-amber-500/50 bg-amber-900/40',
          )}>
            {canCalib ? '可标定' : '等待条件'}
          </span>
        }
      />

      <p className="text-sm text-slate-400 leading-relaxed">
        车辆<strong className="text-slate-300">静止</strong>且中控<strong className="text-slate-300">实时运行</strong>时，
        输入 RTK 测量的<strong className="text-slate-300">作业点 WGS84</strong>；
        系统用当前天线坐标与姿态（heading/pitch/roll）反算车体系杆臂 <code className="text-blue-300">L_arm = Rᵀ·(P_天线−P_作业点)</code>。
      </p>

      <div className={clsx(canCalib ? 'config-hint' : 'config-hint-warn')}>
        {!wsOk ? <WifiOff className="w-4 h-4 shrink-0 mt-0.5" /> : <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />}
        <span>{liveHint}</span>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 text-xs font-mono">
        <div className="config-data-block space-y-1">
          <p className="text-slate-500 mb-1">实时 A1（电气房天线）</p>
          <p>lat {liveData.ref_antenna.lat.toFixed(8)}</p>
          <p>lon {liveData.ref_antenna.lon.toFixed(8)}</p>
          <p>alt {liveData.ref_antenna.alt.toFixed(3)} m</p>
        </div>
        <div className="config-data-block space-y-1">
          <p className="text-slate-500 mb-1">实时 A2（柴油机天线）{a2Ok ? '' : ' — 无效'}</p>
          <p>lat {(liveData.antenna2?.lat ?? 0).toFixed(8)}</p>
          <p>lon {(liveData.antenna2?.lon ?? 0).toFixed(8)}</p>
          <p>alt {(liveData.antenna2?.alt ?? 0).toFixed(3)} m</p>
        </div>
      </div>

      <div className="config-data-block grid grid-cols-3 gap-3 text-xs font-mono text-slate-400">
        <div>heading {liveData.heading.toFixed(2)}°</div>
        <div>pitch {liveData.pitch.toFixed(2)}°</div>
        <div>roll {liveData.roll.toFixed(2)}°</div>
        <div>speed {liveData.speed.toFixed(3)} m/s</div>
        <div>GNSS {liveData.status}</div>
        <div>WS {connStatus}</div>
      </div>

      <SwipeTabs
        tabs={[
          { id: 'elec' as const, label: '电气房侧 (A1)' },
          { id: 'diesel' as const, label: '柴油机侧 (A2)' },
        ]}
        active={calibSide}
        onChange={setCalibSide}
      />

      <div className="space-y-3">
        {calibSide === 'elec' ? (
          <>
            <label className="section-title">电气房侧作业点（RTK 测量）</label>
            {renderLpointInputs('elec', lpElec, setLpElec)}
            <button
              type="button"
              disabled={!canCalib || computing !== null}
              onClick={() => computeSide('elec')}
              className="w-full btn-primary py-3 flex items-center justify-center gap-2"
            >
              {computing === 'elec' ? <Loader2 className="w-4 h-4 animate-spin" /> : <Sparkles className="w-4 h-4" />}
              计算 l_arm_elec
            </button>
            {cfg && (
              <ReadOnlyLArmBlock
                title="当前 l_arm_elec"
                arm={cfg.l_arm_elec}
                uncalibrated={!cfg.l_arm_calibration?.elec}
              />
            )}
          </>
        ) : (
          <>
            <label className="section-title">柴油机侧作业点（RTK 测量）</label>
            {renderLpointInputs('diesel', lpDiesel, setLpDiesel)}
            <button
              type="button"
              disabled={!canCalib || !a2Ok || computing !== null}
              onClick={() => computeSide('diesel')}
              className="w-full btn-primary py-3 flex items-center justify-center gap-2"
            >
              {computing === 'diesel' ? <Loader2 className="w-4 h-4 animate-spin" /> : <Sparkles className="w-4 h-4" />}
              计算 l_arm_diesel
            </button>
            {cfg && (
              <ReadOnlyLArmBlock
                title="当前 l_arm_diesel"
                arm={cfg.l_arm_diesel}
                uncalibrated={!cfg.l_arm_calibration?.diesel}
              />
            )}
          </>
        )}
      </div>

      {cfg?.l_arm_calibration && (
        <div className="text-[10px] text-slate-500 space-y-0.5">
          {cfg.l_arm_calibration.elec && (
            <p>
              电气房溯源：{cfg.l_arm_calibration.elec.measured_at.slice(0, 19)}
              {cfg.l_arm_calibration.elec.receiver_id &&
                ` · 来源 ${cfg.l_arm_calibration.elec.receiver_id} GGA[${cfg.l_arm_calibration.elec.gga_index ?? 0}]`}
            </p>
          )}
          {cfg.l_arm_calibration.diesel && (
            <p>
              柴油机溯源：{cfg.l_arm_calibration.diesel.measured_at.slice(0, 19)}
              {cfg.l_arm_calibration.diesel.receiver_id &&
                ` · 来源 ${cfg.l_arm_calibration.diesel.receiver_id} GGA[${cfg.l_arm_calibration.diesel.gga_index ?? 1}]`}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

function areGantryAntennasReady(ga?: Record<string, GantryAntennaPoint>): boolean {
  if (!ga) return false;
  for (const k of ['a1', 'a2', 'b1', 'b2'] as const) {
    const p = ga[k];
    if (!p || p.valid === false) return false;
    const hasEcef = [p.ecef_x, p.ecef_y, p.ecef_z].every(v => typeof v === 'number' && Number.isFinite(v))
      && !(p.ecef_x === 0 && p.ecef_y === 0 && p.ecef_z === 0);
    const hasLla = Math.abs(p.lat) > 1e-6 && Math.abs(p.lon) > 1e-6;
    if (!hasEcef && !hasLla) return false;
  }
  return true;
}

function bodyListsToTuples(body: Record<string, number[]>): GantryAntennaBody {
  const row = (k: keyof GantryAntennaBody) => (body[k] ?? [0, 0, 0]).slice(0, 3) as [number, number, number];
  return { a1: row('a1'), a2: row('a2'), b1: row('b1'), b2: row('b2') };
}

function ReceiverLayoutPanel({
  layout,
  ntrip,
  onChange,
  onNtripChange,
}: {
  layout: ReceiverLayout;
  ntrip: NtripConfig;
  onChange: (layout: ReceiverLayout) => void;
  onNtripChange: (ntrip: NtripConfig) => void;
}) {
  const swapSides = () => {
    onChange({
      ...layout,
      left: layout.right,
      right: layout.left,
      left_host: layout.right_host,
      right_host: layout.left_host,
      left_port: layout.right_port,
      right_port: layout.left_port,
      left_transport: layout.right_transport,
      right_transport: layout.left_transport,
    });
    showToast('已交换左右 EVK 映射');
  };

  const patch = (partial: Partial<ReceiverLayout>) => onChange({ ...layout, ...partial });

  return (
    <div className="lg:col-span-2 space-y-4">
      <div className="config-panel">
        <div className="flex items-center justify-between pb-4 border-b border-slate-700/60">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl bg-blue-500/15 border border-blue-500/25 flex items-center justify-center shrink-0">
              <Radio className="w-4 h-4 text-blue-400" />
            </div>
            <div>
              <h3 className="text-sm font-bold text-slate-200">GNSS EVK 连接（大车）</h3>
              <p className="text-xs text-slate-500 mt-0.5">
                EVK 为 TCP 服务端；中控主动连接。左右各一台，主天线 GGA → 该侧作业点；定向天线前后摆。
              </p>
            </div>
          </div>
          <button type="button" onClick={swapSides} className="btn-outline flex items-center gap-2 shrink-0">
            <ArrowLeftRight className="w-4 h-4" />
            交换左右
          </button>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {[
            {
              title: '电气侧（左）',
              host: layout.left_host ?? '',
              port: layout.left_port,
              onHost: (v: string) => patch({ left_host: v }),
              onPort: (v: number) => patch({ left_port: v }),
            },
            {
              title: '动力侧（右）',
              host: layout.right_host ?? '',
              port: layout.right_port,
              onHost: (v: string) => patch({ right_host: v }),
              onPort: (v: number) => patch({ right_port: v }),
            },
          ].map((side) => (
            <div key={side.title} className="rounded-xl border border-slate-700/60 p-4 space-y-3">
              <p className="text-xs font-bold text-slate-300">{side.title} · TCP</p>
              <label className="block text-[11px] text-slate-500">
                EVK IP
                <input
                  className="mt-1 w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-200 font-mono"
                  value={side.host}
                  onChange={(e) => side.onHost(e.target.value)}
                  placeholder="192.168.0.101"
                />
              </label>
              <label className="block text-[11px] text-slate-500">
                端口
                <input
                  type="number"
                  className="mt-1 w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-200 font-mono"
                  value={side.port}
                  onChange={(e) => side.onPort(parseInt(e.target.value, 10) || 0)}
                />
              </label>
            </div>
          ))}
        </div>

        <div className="mt-4 rounded-xl border border-slate-700/60 p-4 space-y-2">
          <p className="text-xs font-bold text-slate-300">小车（UDP 监听，双 GGA 不变）</p>
          <p className="text-[11px] text-slate-500 font-mono">
            bind {layout.trolley_host ?? '0.0.0.0'}:{layout.trolley_port} · role={layout.trolley}
          </p>
        </div>
      </div>

      <div className="config-panel space-y-3">
        <div className="flex items-center gap-3 pb-3 border-b border-slate-700/60">
          <h3 className="text-sm font-bold text-slate-200">NTRIP 差分（全局一份 → 两台 EVK）</h3>
          <label className="ml-auto flex items-center gap-2 text-xs text-slate-400">
            <input
              type="checkbox"
              checked={!!ntrip.enabled}
              onChange={(e) => onNtripChange({ ...ntrip, enabled: e.target.checked })}
            />
            启用
          </label>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {(
            [
              ['caster_host', 'Caster 地址', ntrip.caster_host],
              ['mountpoint', '挂载点', ntrip.mountpoint],
              ['username', '用户名', ntrip.username],
              ['password', '密码', ntrip.password],
            ] as const
          ).map(([key, label, val]) => (
            <label key={key} className="block text-[11px] text-slate-500">
              {label}
              <input
                type={key === 'password' ? 'password' : 'text'}
                className="mt-1 w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-200 font-mono"
                value={val}
                disabled={!ntrip.enabled}
                onChange={(e) => onNtripChange({ ...ntrip, [key]: e.target.value })}
              />
            </label>
          ))}
          <label className="block text-[11px] text-slate-500">
            端口
            <input
              type="number"
              className="mt-1 w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-200 font-mono"
              value={ntrip.caster_port}
              disabled={!ntrip.enabled}
              onChange={(e) =>
                onNtripChange({ ...ntrip, caster_port: parseInt(e.target.value, 10) || 2101 })
              }
            />
          </label>
        </div>
        <p className="text-[10px] text-slate-500">
          中控拉 RTCM 后经与 NMEA 相同的 TCP 口写入两台 EVK；保存并下发后热重连。
        </p>
      </div>

      <div className="config-data-block text-xs text-slate-500 leading-relaxed space-y-1">
        <p>默认姿态模式为两主点（hdt_tra_height_roll）。四天线 GGA 相关 UI 仍保留在姿态分页，供后续机型切换 dual_receiver / four_antenna。</p>
      </div>
    </div>
  );
}

function TrolleyOriginPanel({
  cfg,
  setCfg,
  liveData,
  connStatus,
  useMock,
  isMock,
  baseUrl,
}: {
  cfg: VehicleProfile;
  setCfg: Dispatch<SetStateAction<VehicleProfile | null>>;
  liveData: RealtimeData;
  connStatus: ConnStatus;
  useMock: boolean;
  isMock: boolean;
  baseUrl: string;
}) {
  const trolley = cfg.trolley ?? { origin_mode: 'auto' as const };
  const [ox, setOx] = useState(String(trolley.origin_body?.[0] ?? 0));
  const [oy, setOy] = useState(String(trolley.origin_body?.[1] ?? 0));
  const [oz, setOz] = useState(String(trolley.origin_body?.[2] ?? 0));
  const [busy, setBusy] = useState<string | null>(null);

  const wsOk = connStatus === 'connected' && !useMock && !isMock;
  const tlyOk = liveData.trolley.valid !== false && liveData.trolley.body_position;

  async function callOrigin(action: 'capture' | 'manual' | 'reset', originBody?: [number, number, number]) {
    setBusy(action);
    try {
      const res = await fetch(`${baseUrl}/api/vehicle/trolley_origin`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          vehicle_id: cfg.vehicle_id,
          action,
          ...(originBody ? { origin_body: originBody } : {}),
        }),
        signal: AbortSignal.timeout(10000),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json = await res.json() as { code: number; data?: { trolley?: TrolleyOriginConfig } };
      if (json.code !== 200) throw new Error('指令失败');
      if (json.data?.trolley) {
        setCfg(p => p ? { ...p, trolley: json.data!.trolley } : null);
      } else if (action === 'reset') {
        setCfg(p => p ? { ...p, trolley: { origin_mode: 'auto' } } : null);
      }
      showToast(
        action === 'capture' ? '已下发：下一帧有效解将捕获为零点'
          : action === 'reset' ? '已重置为 auto 模式'
          : '手动零点已保存并下发',
      );
    } catch {
      showToast('小车零点指令失败', 'error');
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="config-panel">
      <ConfigPanelHeader icon={MoveHorizontal} title="小车零点（大车车体系）" />

      <p className="text-sm text-slate-400 leading-relaxed">
        零点锚定在大车 L-Point 车体系上，<strong className="text-slate-300">随大车移动/转向</strong>；
        行程 = 相对零点的 Y 分量（沿横梁）。双 GGA 在线且中控运行时可用「捕获当前位置」。
      </p>

      <div className="config-data-block text-xs font-mono space-y-1 text-slate-400">
        <p>模式 {liveData.trolley.origin_mode ?? trolley.origin_mode}</p>
        {liveData.trolley.body_position && (
          <p>实时车体系 body: {liveData.trolley.body_position.x.toFixed(3)}, {liveData.trolley.body_position.y.toFixed(3)}, {liveData.trolley.body_position.z.toFixed(3)}</p>
        )}
        {liveData.trolley.origin_body && (
          <p>零点 origin: {liveData.trolley.origin_body.x.toFixed(3)}, {liveData.trolley.origin_body.y.toFixed(3)}, {liveData.trolley.origin_body.z.toFixed(3)}</p>
        )}
        <p>相对 Δ: {liveData.trolley.position.x.toFixed(3)}, {liveData.trolley.position.y.toFixed(3)} m · 行程 {liveData.trolley.travel.toFixed(3)} m</p>
      </div>

      <div className="flex flex-wrap gap-3">
        <button
          type="button"
          disabled={!wsOk || !tlyOk || busy !== null}
          onClick={() => callOrigin('capture')}
          className="btn-primary flex items-center gap-2"
        >
          {busy === 'capture' ? <Loader2 className="w-4 h-4 animate-spin" /> : <MoveHorizontal className="w-4 h-4" />}
          {busy === 'capture' ? '下发中…' : '捕获当前位置为零点'}
        </button>
        <button
          type="button"
          disabled={busy !== null}
          onClick={() => callOrigin('reset')}
          className="btn-outline"
        >
          重置为 auto
        </button>
      </div>

      <div className="space-y-2">
        <p className="section-title">手动零点 origin_body（大车车体系，米）</p>
        <div className="grid grid-cols-3 gap-2">
          {(['x', 'y', 'z'] as const).map((axis, i) => (
            <div key={axis}>
              <span className="text-[10px] text-slate-500 uppercase">{axis}</span>
              <input
                className="input-field font-mono text-xs py-1.5"
                value={i === 0 ? ox : i === 1 ? oy : oz}
                onChange={e => (i === 0 ? setOx : i === 1 ? setOy : setOz)(e.target.value)}
              />
            </div>
          ))}
        </div>
        <button
          type="button"
          disabled={busy !== null}
          onClick={() => {
            const body: [number, number, number] = [parseFloat(ox), parseFloat(oy), parseFloat(oz)];
            if (!body.every(Number.isFinite)) {
              showToast('请填写有效坐标', 'error');
              return;
            }
            setCfg(p => p ? { ...p, trolley: { origin_mode: 'manual', origin_body: body } } : null);
            callOrigin('manual', body);
          }}
          className="w-full btn-outline py-3"
        >
          保存手动零点并下发
        </button>
      </div>
    </div>
  );
}

function GantryAttitudePanel({
  cfg,
  setCfg,
  liveData,
  connStatus,
  useMock,
  isMock,
  baseUrl,
}: {
  cfg: VehicleProfile;
  setCfg: Dispatch<SetStateAction<VehicleProfile | null>>;
  liveData: RealtimeData;
  connStatus: ConnStatus;
  useMock: boolean;
  isMock: boolean;
  baseUrl: string;
}) {
  const att = cfg.gantry_attitude ?? defaultGantryAttitude(cfg.span);
  const [capturing, setCapturing] = useState(false);

  const fourReady = areGantryAntennasReady(liveData.gantry_antennas);
  const wsOk = connStatus === 'connected' && !useMock && !isMock;
  const stationary = liveData.speed <= MAX_CALIB_SPEED;
  const gnssOk = liveData.status === 'fix';
  const canCapture = wsOk && fourReady && stationary && gnssOk;

  const captureHint = useMemo(() => {
    if (useMock || isMock) return '请关闭模拟模式并选择车辆以建立 WebSocket';
    if (connStatus !== 'connected') return 'WebSocket 未连接';
    if (!fourReady) return '四天线未齐：需左/右接收机均在线且各输出双 GGA（a1/a2/b1/b2）';
    if (!gnssOk) return 'GNSS 非固定解，请等待 RTK FIX';
    if (!stationary) return `车辆未静止（${liveData.speed.toFixed(3)} m/s），请停车后采集`;
    return '条件满足：可一键从实时四天线坐标标定安装坐标';
  }, [useMock, isMock, connStatus, fourReady, gnssOk, stationary, liveData.speed]);

  const updateAtt = (patch: Partial<GantryAttitudeConfig>) => {
    setCfg((p) => p ? {
      ...p,
      gantry_attitude: { ...att, ...patch, antenna_body: { ...att.antenna_body, ...(patch.antenna_body ?? {}) } },
    } : null);
  };

  const updateBody = (key: keyof GantryAntennaBody, idx: number, val: string) => {
    const n = parseFloat(val);
    if (!Number.isFinite(n)) return;
    const next = { ...att.antenna_body };
    const row = [...next[key]] as [number, number, number];
    row[idx] = n;
    next[key] = row;
    updateAtt({ antenna_body: next });
  };

  const applyDefaults = () => {
    updateAtt(defaultGantryAttitude(cfg.span, att.baseline_m));
    showToast('已按 W_span / baseline 生成参考安装坐标（建议上线后用一键采集覆盖）');
  };

  async function captureFromLive() {
    if (!canCapture || !liveData.gantry_antennas) {
      showToast(captureHint, 'error');
      return;
    }
    setCapturing(true);
    try {
      const res = await fetch(`${baseUrl}/api/vehicle/compute_antenna_body`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ antennas: liveData.gantry_antennas }),
        signal: AbortSignal.timeout(15000),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({})) as { detail?: string };
        throw new Error(err.detail ?? `HTTP ${res.status}`);
      }
      const json = await res.json() as {
        code: number;
        data?: { antenna_body: Record<string, number[]>; baseline_m: number; span_m: number };
      };
      if (json.code !== 200 || !json.data?.antenna_body) throw new Error('标定失败');

      const { antenna_body, baseline_m, span_m } = json.data;
      const body = bodyListsToTuples(antenna_body);
      const measuredAt = new Date().toISOString();

      setCfg((p) => {
        if (!p) return null;
        const prev = p.gantry_attitude ?? defaultGantryAttitude(p.span);
        return {
          ...p,
          span: span_m > 1 ? span_m : p.span,
          gantry_attitude: {
            ...prev,
            baseline_m,
            antenna_body: body,
            body_calibration: {
              measured_at: measuredAt,
              baseline_m,
              span_m,
              source: 'live',
            },
          },
        };
      });
      showToast(`四天线安装坐标已标定：baseline ${baseline_m.toFixed(2)} m，跨距 ${span_m.toFixed(2)} m`);
    } catch (e) {
      showToast(e instanceof Error ? e.message : '四天线标定失败', 'error');
    } finally {
      setCapturing(false);
    }
  }

  return (
    <div className="config-panel">
      <ConfigPanelHeader icon={Radio} title="四天线姿态解算" />

      <p className="text-sm text-slate-400 leading-relaxed">
        双接收机同时在线时：A/B 各自双 GGA 求航向/俯仰并取平均；<strong className="text-slate-300">roll</strong> 由四天线刚体定姿解算。
        安装坐标应通过<strong className="text-slate-300">一键采集</strong>从现场四天线 ECEF 标定，而非长期使用默认值。车体系：X 前、Y 左、Z 上。
      </p>

      <div className={clsx(canCapture ? 'config-hint' : 'config-hint-warn')}>
        {!wsOk ? <WifiOff className="w-4 h-4 shrink-0 mt-0.5" /> : <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />}
        <span>{captureHint}</span>
      </div>

      <button
        type="button"
        disabled={!canCapture || capturing}
        onClick={captureFromLive}
        className="w-full btn-primary py-3 flex items-center justify-center gap-2"
      >
        {capturing ? <Loader2 className="w-4 h-4 animate-spin" /> : <Sparkles className="w-4 h-4" />}
        一键采集四天线安装坐标
      </button>

      {att.body_calibration && (
        <p className="text-[10px] text-slate-500">
          最近标定：{att.body_calibration.measured_at.slice(0, 19)} · baseline {att.body_calibration.baseline_m.toFixed(2)} m · span {att.body_calibration.span_m.toFixed(2)} m · {att.body_calibration.source === 'live' ? '实时采集' : '手动'}
        </p>
      )}

      {fourReady && liveData.gantry_antennas && (
        <div className="overflow-x-auto">
          <p className="section-title mb-2">实时四天线预览</p>
          <table className="w-full text-[10px] font-mono">
            <thead>
              <tr className="text-slate-500 border-b border-slate-700">
                <th className="text-left py-1 pr-2">点</th>
                <th className="py-1 px-1">lat</th>
                <th className="py-1 px-1">lon</th>
                <th className="py-1 px-1">alt</th>
              </tr>
            </thead>
            <tbody>
              {(['a1', 'a2', 'b1', 'b2'] as const).map((k) => {
                const p = liveData.gantry_antennas![k];
                return (
                  <tr key={k} className="border-b border-slate-800/40 text-slate-400">
                    <td className="py-1 pr-2 uppercase text-blue-400/80">{k}</td>
                    <td className="py-1 px-1">{p.lat.toFixed(8)}</td>
                    <td className="py-1 px-1">{p.lon.toFixed(8)}</td>
                    <td className="py-1 px-1">{p.alt.toFixed(3)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div>
          <label className="section-title block mb-1.5">解算模式</label>
          <select
            className="input-field text-sm"
            value={att.mode}
            onChange={(e) => updateAtt({ mode: e.target.value as GantryAttitudeConfig['mode'] })}
          >
            <option value="hdt_tra_height_roll">两主点 UB482（HDT/TRA + 高差横滚）</option>
            <option value="dual_receiver">双接收机四 GGA（备用）</option>
            <option value="four_antenna">四天线刚体定姿（备用）</option>
          </select>
        </div>
        <div>
          <label className="section-title block mb-1.5">前后定向基线长 (m)</label>
          <input
            type="number"
            step="0.01"
            className="input-field font-mono"
            value={att.baseline_m}
            onChange={(e) => updateAtt({ baseline_m: parseFloat(e.target.value) || 0 })}
          />
        </div>
        <div>
          <label className="section-title block mb-1.5">左右主点间距 roll_span (m)</label>
          <input
            type="number"
            step="0.01"
            className="input-field font-mono"
            value={att.roll_span_m ?? cfg.span}
            onChange={(e) => updateAtt({ roll_span_m: parseFloat(e.target.value) || 0 })}
          />
        </div>
        <div>
          <label className="section-title block mb-1.5">水平时高差零点 roll_alt0 (m)</label>
          <input
            type="number"
            step="0.001"
            className="input-field font-mono"
            value={att.roll_alt0_m ?? 0}
            onChange={(e) => updateAtt({ roll_alt0_m: parseFloat(e.target.value) || 0 })}
          />
        </div>
      </div>

      <div className="flex items-center justify-between">
        <p className="section-title">天线安装坐标（车体系，米 · 四 GGA 备用）</p>
        <button type="button" onClick={applyDefaults} className="btn-outline text-xs py-2 px-3">
          按 W_span 生成参考值
        </button>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-xs font-mono">
          <thead>
            <tr className="text-slate-500 border-b border-slate-700">
              <th className="text-left py-2 pr-2">天线</th>
              <th className="py-2 px-1">X</th>
              <th className="py-2 px-1">Y</th>
              <th className="py-2 px-1">Z</th>
            </tr>
          </thead>
          <tbody>
            {(['a1', 'a2', 'b1', 'b2'] as const).map((key) => (
              <tr key={key} className="border-b border-slate-800/60">
                <td className="py-2 pr-2 text-slate-400 uppercase">{key}</td>
                {([0, 1, 2] as const).map((i) => (
                  <td key={i} className="py-1 px-1">
                    <input
                      type="number"
                      step="0.001"
                      className="input-field py-1 text-xs w-full min-w-[4.5rem]"
                      value={att.antenna_body[key][i]}
                      onChange={(e) => updateBody(key, i, e.target.value)}
                    />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="config-data-block grid grid-cols-3 gap-2 text-xs font-mono text-slate-400">
        <div>实时 heading {liveData.heading.toFixed(2)}°</div>
        <div>pitch {liveData.pitch.toFixed(2)}°</div>
        <div>roll {liveData.roll.toFixed(2)}°</div>
      </div>
      <p className="text-[10px] text-slate-500">保存后通过 MQTT 热更新至中控 fusion 节点。</p>
    </div>
  );
}

// ============================================================
// 主组件
// ============================================================
export default function VehicleConfig({
  liveData,
  connStatus,
  isMock,
}: {
  liveData: RealtimeData;
  connStatus: ConnStatus;
  isMock: boolean;
}) {
  const { vehicles, pendingDevices, updateVehicle, selectedVehicleId, backendConfig, useMock, fetchVehicles } = useAppContext();

  const [editId, setEditId] = useState<string | null>(
    () => selectedVehicleId ?? vehicles[0]?.vehicle_id ?? null,
  );
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(false);
  const [initTarget, setInitTarget] = useState<PendingDevice | null>(null);
  const [configTab, setConfigTab] = useState<ConfigTab>('basic');

  const baseVehicle = vehicles.find(v => v.vehicle_id === editId) ?? null;
  const [cfg, setCfg] = useState<VehicleProfile | null>(baseVehicle);

  const baseUrl = httpBase(backendConfig);

  // 进入页面时拉取车辆列表（与工作配置共用数据源）
  useEffect(() => {
    if (!useMock) {
      fetchVehicles().catch(() => {});
    }
  }, [useMock, fetchVehicles]);

  // 与工作配置所选车辆同步
  useEffect(() => {
    if (selectedVehicleId) {
      setEditId(selectedVehicleId);
    }
  }, [selectedVehicleId]);

  // 列表加载后：若尚未选中，默认第一台
  useEffect(() => {
    if (!editId && vehicles.length > 0) {
      setEditId(selectedVehicleId ?? vehicles[0].vehicle_id);
    }
  }, [vehicles, editId, selectedVehicleId]);

  useEffect(() => {
    const local = vehicles.find(v => v.vehicle_id === editId) ?? null;
    if (!editId) {
      setCfg(null);
      return;
    }
    if (!local) {
      setCfg(null);
      return;
    }
    if (useMock) {
      logger.debug('VehicleConfig', '模拟模式，使用本地车辆数据', { editId });
      setCfg(mergeVehicleConfig(local, null));
      return;
    }
    const url = `${baseUrl}/api/vehicle/config/${editId}`;
    logger.info('VehicleConfig', `GET /api/vehicle/config/${editId}`, { url });
    setLoading(true);
    fetch(url)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((resp: { code: number; data: {
        vehicle_id?: string;
        l_arm?: [number, number, number];
        l_arm_elec?: [number, number, number];
        l_arm_diesel?: [number, number, number];
        height?: number;
        w_span?: number;
        l_arm_calibration?: VehicleProfile['l_arm_calibration'];
        gantry_attitude?: GantryAttitudeConfig;
        receiver_layout?: ReceiverLayout;
        trolley?: TrolleyOriginConfig;
      } | null }) => {
        if (resp.code === 200 && local) {
          if (resp.data) {
            logger.info('VehicleConfig', '参数加载成功', resp.data);
          } else {
            logger.warn('VehicleConfig', '后端尚无配置记录，使用车辆列表默认值');
          }
          setCfg(mergeVehicleConfig(local, resp.data));
        } else {
          setCfg(mergeVehicleConfig(local, null));
        }
      })
      .catch((e) => {
        logger.error('VehicleConfig', '参数加载失败，fallback 到本地数据', e);
        setCfg(mergeVehicleConfig(local, null));
      })
      .finally(() => setLoading(false));
  }, [editId, baseUrl, useMock, vehicles]);

  const handleSelectVehicle = (id: string) => {
    setEditId(id);
  };

  const [refreshing, setRefreshing] = useState(false);
  const handleRefresh = useCallback(async () => {
    if (useMock) return;
    setRefreshing(true);
    try {
      await fetchVehicles();
      showToast('车辆列表已刷新');
    } catch {
      showToast('刷新失败', 'error');
    } finally {
      setRefreshing(false);
    }
  }, [useMock, fetchVehicles]);

  const handleInitDone = useCallback((newVehicleId: string) => {
    setInitTarget(null);
    setEditId(newVehicleId);
  }, []);

  const handleSave = async () => {
    if (!cfg) return;
    setSaving(true);
    updateVehicle(cfg);
    logger.info('VehicleConfig', 'POST /api/vehicle/config', { vehicle_id: cfg.vehicle_id });
    try {
      const payload = {
        vehicle_id: cfg.vehicle_id,
        l_arm: cfg.l_arm_elec,
        l_arm_elec: cfg.l_arm_elec,
        l_arm_diesel: cfg.l_arm_diesel,
        height: cfg.height,
        w_span: cfg.span,
        l_arm_calibration: cfg.l_arm_calibration,
        gantry_attitude: cfg.gantry_attitude,
        receiver_layout: cfg.receiver_layout ?? DEFAULT_RECEIVER_LAYOUT,
        ntrip: cfg.ntrip ?? DEFAULT_NTRIP,
        trolley: cfg.trolley,
      };
      const res = await fetch(`${baseUrl}/api/vehicle/config`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (res.ok) {
        logger.info('VehicleConfig', `参数保存成功 vehicle_id=${cfg.vehicle_id}`);
        showToast(`${cfg.label} 参数已保存至后端数据库`);
      } else {
        logger.warn('VehicleConfig', `保存返回 HTTP ${res.status}`);
        showToast('保存失败，请检查后端连接', 'error');
      }
    } catch (e) {
      logger.error('VehicleConfig', '保存请求失败', e);
      showToast('离线模式：参数已保存至本地（模拟）', 'info');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="animate-fade-in space-y-6">
      {/* 初始化弹窗 */}
      {initTarget && (
        <InitDeviceModal
          device={initTarget}
          backendBase={baseUrl}
          onClose={() => setInitTarget(null)}
          onDone={handleInitDone}
        />
      )}

      <SectionHeader
        title="车辆管理"
        subtitle="管理设备初始化与物理几何参数（杆臂值、跨距、高度），用于 L-Point 刚体变换。新设备上线后在此完成初始化和参数配置。"
      />

      {/* ── 待初始化设备 ── */}
      {!useMock && pendingDevices.length > 0 && (
        <div className="card p-6 border border-amber-500/40 bg-gradient-to-br from-amber-950/40 to-slate-900/60 space-y-4">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-amber-500/20 border border-amber-500/40 flex items-center justify-center">
              <AlertTriangle className="w-5 h-5 text-amber-400" />
            </div>
            <div>
              <h3 className="text-base font-bold text-amber-200">待初始化设备</h3>
              <p className="text-xs text-amber-400/70">{pendingDevices.length} 台待分配正式 ID</p>
            </div>
          </div>
          <p className="text-sm text-amber-100/60 leading-relaxed">
            以下设备已上线但尚未分配正式 ID。点击「初始化」填写参数后，后端将向设备下发 CMD_INIT_IDENTITY，中控机会自动切换 ID 并同步配置，无需重启。
          </p>
          <div className="space-y-3">
            {pendingDevices.map(dev => (
              <div key={dev.temp_id} className="flex items-center gap-4 p-4 bg-slate-900/70 rounded-2xl border border-amber-500/25">
                <div className="w-11 h-11 rounded-xl bg-amber-500/15 border border-amber-500/35 flex items-center justify-center shrink-0">
                  <Car className="w-5 h-5 text-amber-400" />
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-base font-bold text-amber-100 font-mono">{dev.temp_id}</p>
                  <p className="text-xs text-slate-500 font-mono mt-0.5">SN: {dev.sn || '-'}</p>
                </div>
                <button
                  onClick={() => setInitTarget(dev)}
                  className="btn-amber flex items-center gap-2 shrink-0 py-3 px-5"
                >
                  <Plus className="w-4 h-4" /> 初始化
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── 车辆选择器 ── */}
      <div className="card p-5">
        <div className="flex items-center gap-3 mb-4">
          <div className="w-9 h-9 rounded-xl bg-blue-500/15 border border-blue-500/30 flex items-center justify-center">
            <Car className="w-4 h-4 text-blue-400" />
          </div>
          <div className="flex-1 min-w-0">
            <span className="text-sm font-bold text-slate-200">选择要编辑的车辆</span>
            <p className="text-xs text-slate-500 mt-0.5">{vehicles.length} 台已注册</p>
          </div>
          <button
            onClick={handleRefresh}
            disabled={useMock || refreshing}
            title={useMock ? '模拟模式下不可刷新' : '从后端重新拉取车辆列表'}
            className="btn-icon"
          >
            <RefreshCw className={clsx('w-4 h-4', refreshing && 'animate-spin')} />
          </button>
        </div>
        <div className="flex flex-wrap gap-2.5">
          {vehicles.map((v) => (
            <button
              key={v.vehicle_id}
              onClick={() => handleSelectVehicle(v.vehicle_id)}
              className={editId === v.vehicle_id ? 'chip-btn-active' : 'chip-btn-inactive'}
            >
              {v.label}
              <span className="ml-2 text-xs font-mono opacity-70">{v.vehicle_id}</span>
            </button>
          ))}
          {vehicles.length === 0 && !useMock && (
            <p className="text-sm text-slate-500 py-2">
              暂无已注册车辆，请先初始化上方的待分配设备，或在
              <button type="button" className="text-blue-400 hover:underline mx-1" onClick={() => { window.location.hash = 'settings'; }}>
                工作配置
              </button>
              刷新列表
            </p>
          )}
          {editId && (
            <p className="text-[10px] text-slate-500 mt-2">
              当前编辑：<span className="text-blue-400 font-mono">{editId}</span>
              {selectedVehicleId === editId && ' · 与工作配置所选车辆一致'}
            </p>
          )}
        </div>
      </div>

      {cfg && !loading && (
        <SwipeTabs
          tabs={[
            { id: 'basic' as const, label: '基本参数' },
            { id: 'larm' as const, label: '智能杆臂标定' },
            { id: 'attitude' as const, label: '四天线姿态' },
            { id: 'trolley' as const, label: '小车零点' },
          ]}
          active={configTab}
          onChange={setConfigTab}
        />
      )}

      {loading ? (
        <div className="card p-10 flex items-center justify-center text-slate-400 gap-3">
          <FolderOpen className="w-5 h-5 animate-pulse" />
          <span>正在从后端加载参数...</span>
        </div>
      ) : cfg ? (
        <>
          {configTab === 'larm' && (
            <LArmSmartCalibration
              liveData={liveData}
              connStatus={connStatus}
              isMock={isMock}
              useMock={useMock}
              baseUrl={baseUrl}
              cfg={cfg}
              setCfg={setCfg}
            />
          )}

          {configTab === 'attitude' && (
            <GantryAttitudePanel
              cfg={cfg}
              setCfg={setCfg}
              liveData={liveData}
              connStatus={connStatus}
              useMock={useMock}
              isMock={isMock}
              baseUrl={baseUrl}
            />
          )}

          {configTab === 'trolley' && (
            <TrolleyOriginPanel
              cfg={cfg}
              setCfg={setCfg}
              liveData={liveData}
              connStatus={connStatus}
              useMock={useMock}
              isMock={isMock}
              baseUrl={baseUrl}
            />
          )}

          {configTab === 'basic' && (
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
            {/* 基本参数 */}
            <div className="config-panel">
              <ConfigPanelHeader icon={Settings2} title="基本属性" />

              <div className="space-y-4">
                <div>
                  <label className="section-title block mb-1.5">车辆编号</label>
                  <input
                    className="input-field font-mono opacity-60 cursor-not-allowed"
                    value={cfg.vehicle_id}
                    readOnly
                    title="车辆编号不可修改"
                  />
                </div>
                <div>
                  <label className="section-title block mb-1.5">显示名称</label>
                  <input
                    className="input-field"
                    value={cfg.label}
                    onChange={(e) => setCfg((p) => p ? { ...p, label: e.target.value } : null)}
                    placeholder="如：RTG 01号机"
                  />
                </div>
                <div>
                  <label className="section-title block mb-1.5">标称高度 H (m)</label>
                  <input
                    type="number"
                    step="0.001"
                    className="input-field font-mono text-lg"
                    value={cfg.height}
                    onChange={(e) => setCfg((p) => p ? { ...p, height: parseFloat(e.target.value) || 0 } : null)}
                  />
                </div>
                <div>
                  <label className="section-title block mb-1.5">门腿跨距 W_span (m)</label>
                  <p className="text-[10px] text-slate-500 mb-1">参考尺寸，不再用于推算另一侧作业点</p>
                  <input
                    type="number"
                    step="0.001"
                    className="input-field font-mono text-lg"
                    value={cfg.span}
                    onChange={(e) => setCfg((p) => p ? { ...p, span: parseFloat(e.target.value) || 0 } : null)}
                  />
                </div>
                <p className="text-[10px] text-slate-500 pt-1">
                  杆臂在「智能杆臂标定」页配置；四天线姿态在对应分页。保存时将一并写入后端。
                </p>
              </div>

              <div className="config-formula-box">
                <p className="section-title mb-2">L-Point 解算公式</p>
                <p className="text-xs font-mono text-blue-300 leading-relaxed">
                  P_final = P_gps − R · L_arm
                  <br />
                  R = Rz(ψ) · Ry(θ) · Rx(φ)
                </p>
              </div>
            </div>

            <ReceiverLayoutPanel
              layout={cfg.receiver_layout ?? DEFAULT_RECEIVER_LAYOUT}
              ntrip={cfg.ntrip ?? DEFAULT_NTRIP}
              onChange={(layout) => setCfg((p) => p ? { ...p, receiver_layout: layout } : null)}
              onNtripChange={(ntrip) => setCfg((p) => p ? { ...p, ntrip } : null)}
            />
          </div>
          )}

          {/* 操作按钮 */}
          <div className="action-bar">
            <button className="btn-outline flex items-center gap-2 py-3">
              <FolderOpen className="w-4 h-4" />
              导入配置
            </button>
            <button
              onClick={handleSave}
              disabled={saving}
              className="btn-primary flex items-center gap-2 py-3 px-8"
            >
              <Save className="w-4 h-4" />
              {saving ? '保存中...' : '保存并下发至中控机'}
            </button>
          </div>
        </>
      ) : (
        <div className="card p-12 text-center text-slate-500 space-y-3">
          <Car className="w-12 h-12 mx-auto opacity-30" />
          <p className="text-sm">请先在上方选择一台车辆进行参数编辑</p>
          {selectedVehicleId && !editId && (
            <p className="text-xs text-amber-400">工作配置已选 {selectedVehicleId}，但车辆列表中未找到，请点击刷新</p>
          )}
          {!selectedVehicleId && vehicles.length > 0 && (
            <p className="text-xs text-slate-400">也可在「工作配置」中选择车辆，将自动同步到本页</p>
          )}
        </div>
      )}
    </div>
  );
}
