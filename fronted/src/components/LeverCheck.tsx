import { useCallback, useEffect, useMemo, useState } from 'react';
import { Bookmark, Eraser, RefreshCw, Save, Trash2 } from 'lucide-react';
import { useAppContext } from '../context/AppContext';
import { showToast } from './common';
import { gnssAtEnd, tireCenterLabel } from '../utils/gnssName';
import { httpBase } from '../utils/backendUrl';
import type { RealtimeData, ReceiverLayout, VehicleProfile } from '../types';
import {
  antennaMinusTireEnu,
  armDeltaCm,
  computeLArm,
  fmtArm,
  hypot2,
  hypot3,
  parseLla,
  projectTire,
  tireOffsetEnu,
  type Attitude,
  type Lla,
  type Vec3,
} from '../utils/geodesy';

type EndId = 'elec_down' | 'elec_up';
type LlaStr = { lat: string; lon: string; alt: string };

interface EndState {
  antenna: LlaStr;
  tire: LlaStr;
  antennaLocked: boolean;
  /** 天线坐标被记住/锁定/手改的时间 */
  antennaAt: string | null;
  /** 现场实时偏差手填：沿车体 X 方向，cm */
  liveBiasX: string;
}

/**
 * 一条记录 = 一个天线 + 一个轮胎（单端）。
 * 必填：antenna + tire_measured；其余有姿态/配置时才算，否则为 null。
 */
interface SavedRecord {
  id: string;
  saved_at: string;
  vehicle_id: string;
  end: EndId;
  gnss: string;
  attitude: Attitude;
  attitude_source: string;
  antenna: Lla;
  /** 记录天线坐标时的时间 */
  antenna_at: string | null;
  tire_measured: Lla;
  tire_predicted: Lla | null;
  l_arm_config: Vec3 | null;
  l_arm_inferred: Vec3 | null;
  dL_cm: { dx: number; dy: number; dz: number; abs: number } | null;
  offset_enu_cm: { e: number; n: number; u: number } | null;
  offset_horiz_cm: number | null;
  offset_3d_cm: number | null;
  ant_tire_enu_m: { e: number; n: number; u: number } | null;
  /** 手填：沿车体 X 方向偏差 cm；未填则为 null */
  live_bias_x_cm: number | null;
  /** @deprecated 旧版 ENU 手填，读旧记录时可能存在 */
  live_bias_cm?: { e: number; n: number; u: number } | null;
  /** 当时堆场；正投/实测轮胎到对应贝位线的横向偏差 cm（朝场内为负） */
  yard_id?: string | null;
  bay_offset_predicted_cm?: number | null;
  bay_offset_measured_cm?: number | null;
  /** 堆场 LYCS 坐标 [X,Y,Z] m：天线算出的轮胎 / 手填实测轮胎 */
  tire_predicted_lycs_m?: [number, number, number | null] | null;
  tire_measured_lycs_m?: [number, number, number | null] | null;
  /** 轮胎落在哪两个场地贝位之间（按堆场 Y 夹持） */
  bay_between_predicted?: BayBetween | null;
  bay_between_measured?: BayBetween | null;
  /** 仪表盘对比主显 + 解算 tire_attitude + 双机对齐 + 实时腿偏 */
  compare_primary?: 'per_side' | 'rigid' | null;
  tire_attitude?: string | null;
  gnss_pair_align?: RealtimeData['gnss_pair_align'] | null;
  leg_offset_per_side_m?: number | null;
  leg_offset_rigid_m?: number | null;
}

interface BayBetween {
  from_bay: number;
  to_bay: number;
  from_y_m?: number;
  to_y_m?: number;
  tire_y_m?: number;
  t?: number;
  label?: string;
}

const RECORD_FORMAT_EXAMPLE = `{
  "id": "1732180000000_elec_down",
  "saved_at": "2026-11-21T11:20:00.000Z",
  "vehicle_id": "RTG-001",
  "end": "elec_down",
  "gnss": "GNSS1",
  "attitude": { "heading": 90.12, "roll": 0.05, "pitch": -0.12 },
  "attitude_source": "snapshot",
  "antenna": { "lat": 30.12345678, "lon": 120.12345678, "alt": 25.100 },
  "antenna_at": "2026-11-21T11:15:30.000Z",
  "tire_measured": { "lat": 30.12345000, "lon": 120.12346000, "alt": 4.200 },
  "tire_predicted": { "lat": ..., "lon": ..., "alt": ... } | null,
  "l_arm_config": [0.6505, 1.2457, 20.9560] | null,
  "l_arm_inferred": [0.65, 1.24, 20.95] | null,
  "dL_cm": { "dx": 0.1, "dy": -0.2, "dz": 0.3, "abs": 0.4 } | null,  // 反算杆臂−标定杆臂(车体系cm)，不是到Y轴距离
  "offset_enu_cm": { "e": 1.2, "n": -0.5, "u": 0.3 } | null,
  "offset_horiz_cm": 1.3 | null,
  "offset_3d_cm": 1.4 | null,
  "ant_tire_enu_m": { "e": 0.65, "n": 1.25, "u": 20.95 } | null,
  "live_bias_x_cm": 2.0 | null,
  "yard_id": "A02",
  "bay_offset_predicted_cm": 1.2 | null,   // 天线算出的轮胎中心 → 贝位线
  "bay_offset_measured_cm": 8.0 | null,    // 手填实测轮胎中心 → 贝位线
  "tire_predicted_lycs_m": [-0.14, 15.38, -0.03] | null,
  "tire_measured_lycs_m": [-0.12, 15.37, -0.04] | null,
  "bay_between_predicted": { "from_bay": 5, "to_bay": 6, "label": "贝位 5–6 之间", "t": 0.42 } | null,
  "bay_between_measured": { "from_bay": 5, "to_bay": 6, "label": "贝位 5–6 之间", "t": 0.40 } | null,
  "compare_primary": "per_side" | "rigid",
  "tire_attitude": "per_side",
  "gnss_pair_align": { "dt_s": 0.02, "source": "utc", "dropped": false },
  "leg_offset_per_side_m": -0.046,
  "leg_offset_rigid_m": -0.052
}`;

const TIRE_COMPARE_KEY = 'rtg.dashboard.tire_compare_primary';

function loadComparePrimary(): 'per_side' | 'rigid' {
  try {
    const v = localStorage.getItem(TIRE_COMPARE_KEY);
    if (v === 'rigid' || v === 'per_side') return v;
  } catch { /* */ }
  return 'per_side';
}

function legOffsetForEnd(data: RealtimeData, end: EndId): { perSide: number | null; rigid: number | null } {
  const legs = data.gantry?.leg_offsets;
  const rigid = data.gantry?.leg_offsets_rigid;
  // fl=elec GNSS1 down, rl=elec GNSS2 up
  const key = end === 'elec_down' ? 'fl' : 'rl';
  return {
    perSide: legs?.[key] ?? null,
    rigid: rigid?.[key] ?? null,
  };
}

function fmtCmSigned(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return '—';
  return `${v >= 0 ? '+' : ''}${v.toFixed(1)} cm`;
}

function fmtLycs(v: [number, number, number | null] | null | undefined): string {
  if (!v) return '—';
  const z = v[2] == null || !Number.isFinite(v[2]) ? '—' : v[2].toFixed(4);
  return `X ${v[0].toFixed(4)}  Y ${v[1].toFixed(4)}  Z ${z} m`;
}

function fmtBayBetween(b: BayBetween | null | undefined): string {
  if (!b || b.from_bay == null || b.to_bay == null) return '—';
  const base = b.label || `贝位 ${b.from_bay}–${b.to_bay} 之间`;
  if (b.t != null && Number.isFinite(b.t)) {
    return `${base}（距 ${b.from_bay} 约 ${(b.t * 100).toFixed(0)}%）`;
  }
  return base;
}

const EMPTY: LlaStr = { lat: '', lon: '', alt: '' };
const STORAGE_KEY = (vid: string) => `rtg.lever_check.records.${vid}`;
const SNAP_KEY = (vid: string) => `rtg.lever_check.snap.${vid}`;

function fmtLocal(iso: string | null | undefined): string {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso.slice(0, 19).replace('T', ' ');
    const pad = (n: number) => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  } catch {
    return iso.slice(0, 19).replace('T', ' ');
  }
}

function parseBiasXCm(s: string): number | null {
  const v = parseFloat(s);
  return Number.isFinite(v) ? v : null;
}

function antennaKeyForEnd(
  layout: ReceiverLayout,
  role: 'elec' | 'diesel',
  end: 'upstream' | 'downstream',
): 'a1' | 'a2' | 'b1' | 'b2' {
  const elecLeft = (layout.elec_receiver ?? 'left') !== 'right';
  const onLeft = role === 'elec' ? elecLeft : !elecLeft;
  const primaryEnd = role === 'elec'
    ? (elecLeft ? layout.left_primary_end : layout.right_primary_end)
    : (elecLeft ? layout.right_primary_end : layout.left_primary_end);
  const primaryIsDown = (primaryEnd ?? 'down') !== 'up';
  const index = primaryIsDown === (end === 'downstream') ? 0 : 1;
  if (onLeft) return index === 0 ? 'a1' : 'a2';
  return index === 0 ? 'b1' : 'b2';
}

function liveAntenna(
  data: RealtimeData,
  layout: ReceiverLayout | undefined,
  end: 'upstream' | 'downstream',
): Lla | null {
  if (!layout) return null;
  const key = antennaKeyForEnd(layout, 'elec', end);
  const p = data.gantry_antennas?.[key];
  if (!p || p.valid === false) return null;
  if (Math.abs(p.lat) < 1e-6 && Math.abs(p.lon) < 1e-6) return null;
  return { lat: p.lat, lon: p.lon, alt: p.alt };
}

function toStr(p: Lla): LlaStr {
  return { lat: p.lat.toFixed(8), lon: p.lon.toFixed(8), alt: p.alt.toFixed(3) };
}

function liveAttitude(data: RealtimeData): Attitude {
  return { heading: data.heading, pitch: data.pitch, roll: data.roll };
}

interface Props {
  liveData: RealtimeData;
}

export default function LeverCheck({ liveData }: Props) {
  const { selectedVehicleId, vehicles, backendConfig, isMock } = useAppContext();
  const vehicle = vehicles.find((v) => v.vehicle_id === selectedVehicleId) ?? null;
  const layout = vehicle?.receiver_layout;
  const [cfgArms, setCfgArms] = useState<{
    down: Vec3;
    up: Vec3;
    downAt?: string;
    upAt?: string;
  } | null>(null);
  const [att, setAtt] = useState<Attitude>({ heading: 0, pitch: 0, roll: 0 });
  const [attSource, setAttSource] = useState<'live' | 'snapshot' | 'manual'>('live');
  const [snapAt, setSnapAt] = useState<string | null>(null);
  const [ends, setEnds] = useState<Record<EndId, EndState>>({
    elec_down: {
      antenna: { ...EMPTY }, tire: { ...EMPTY }, antennaLocked: false,
      antennaAt: null, liveBiasX: '',
    },
    elec_up: {
      antenna: { ...EMPTY }, tire: { ...EMPTY }, antennaLocked: false,
      antennaAt: null, liveBiasX: '',
    },
  });
  const [records, setRecords] = useState<SavedRecord[]>([]);
  const [filePath, setFilePath] = useState<string | null>(null);
  const [bayOffsets, setBayOffsets] = useState<Partial<Record<EndId, {
    predicted_cm: number | null;
    measured_cm: number | null;
    predicted_lycs: [number, number, number | null] | null;
    measured_lycs: [number, number, number | null] | null;
    predicted_between: BayBetween | null;
    measured_between: BayBetween | null;
  }>>>({});
  const [convIn, setConvIn] = useState<LlaStr>({ ...EMPTY });
  const [convYard, setConvYard] = useState('');
  const [convOut, setConvOut] = useState<[number, number, number] | null>(null);
  const [convErr, setConvErr] = useState<string | null>(null);
  const [showFormat, setShowFormat] = useState(false);
  const [expandedJsonId, setExpandedJsonId] = useState<string | null>(null);

  const baseUrl = httpBase(backendConfig);

  const cacheLocal = (vid: string, next: SavedRecord[]) => {
    try { localStorage.setItem(STORAGE_KEY(vid), JSON.stringify(next)); } catch { /* */ }
  };

  const loadRecords = useCallback(async (vid: string) => {
    let local: SavedRecord[] = [];
    try {
      const raw = localStorage.getItem(STORAGE_KEY(vid));
      local = raw ? (JSON.parse(raw) as SavedRecord[]) : [];
    } catch {
      local = [];
    }
    if (isMock) {
      setRecords(local);
      setFilePath(null);
      return;
    }
    try {
      const res = await fetch(`${baseUrl}/api/lever-check/${encodeURIComponent(vid)}`, {
        signal: AbortSignal.timeout(8000),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json = await res.json() as {
        code: number;
        data?: { records?: SavedRecord[]; path?: string };
      };
      if (json.code === 200 && json.data) {
        const remote = Array.isArray(json.data.records) ? json.data.records : [];
        setFilePath(json.data.path ?? null);
        if (remote.length > 0) {
          setRecords(remote);
          cacheLocal(vid, remote);
          return;
        }
        // 文件空但本机有旧记录：迁一次到文件
        if (local.length > 0) {
          const put = await fetch(`${baseUrl}/api/lever-check/${encodeURIComponent(vid)}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ vehicle_id: vid, records: local }),
            signal: AbortSignal.timeout(8000),
          });
          if (put.ok) {
            const pj = await put.json() as { code: number; data?: { path?: string } };
            if (pj.code === 200) setFilePath(pj.data?.path ?? null);
          }
        }
        setRecords(local);
        return;
      }
    } catch { /* fallback local */ }
    setRecords(local);
    setFilePath(null);
  }, [baseUrl, isMock]);

  useEffect(() => {
    if (liveData.yard_id && !convYard) setConvYard(liveData.yard_id);
  }, [liveData.yard_id, convYard]);

  useEffect(() => {
    if (isMock) {
      setConvOut(null);
      setConvErr(null);
      return;
    }
    const yardId = convYard.trim() || liveData.yard_id || '';
    const pt = parseLla(convIn.lat, convIn.lon, convIn.alt || '0');
    if (!yardId || !pt) {
      setConvOut(null);
      setConvErr(null);
      return;
    }
    let cancelled = false;
    const timer = window.setTimeout(() => {
      void (async () => {
        try {
          const res = await fetch(`${baseUrl}/api/lever-check/wgs84-to-lycs`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              yard_id: yardId,
              lat: pt.lat,
              lon: pt.lon,
              alt: pt.alt,
            }),
            signal: AbortSignal.timeout(8000),
          });
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          const json = await res.json() as {
            code: number;
            message?: string;
            data?: { lycs_m?: [number, number, number] };
          };
          if (cancelled) return;
          if (json.code !== 200 || !json.data?.lycs_m) {
            setConvOut(null);
            setConvErr(json.message || '转换失败');
            return;
          }
          setConvOut(json.data.lycs_m);
          setConvErr(null);
        } catch (e) {
          if (cancelled) return;
          setConvOut(null);
          setConvErr(e instanceof Error ? e.message : '转换失败');
        }
      })();
    }, 200);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [baseUrl, convIn, convYard, isMock, liveData.yard_id]);

  useEffect(() => {
    if (!selectedVehicleId) return;
    void loadRecords(selectedVehicleId);
    try {
      const snap = localStorage.getItem(SNAP_KEY(selectedVehicleId));
      if (snap) {
        const s = JSON.parse(snap) as {
          measured_at: string;
          attitude: Attitude;
          antennas: Partial<Record<EndId, Lla>>;
        };
        setSnapAt(s.measured_at);
        setAtt(s.attitude);
        setAttSource('snapshot');
        setEnds((prev) => ({
          elec_down: {
            ...prev.elec_down,
            antenna: s.antennas.elec_down ? toStr(s.antennas.elec_down) : prev.elec_down.antenna,
            antennaLocked: Boolean(s.antennas.elec_down),
            antennaAt: s.antennas.elec_down ? s.measured_at : prev.elec_down.antennaAt,
          },
          elec_up: {
            ...prev.elec_up,
            antenna: s.antennas.elec_up ? toStr(s.antennas.elec_up) : prev.elec_up.antenna,
            antennaLocked: Boolean(s.antennas.elec_up),
            antennaAt: s.antennas.elec_up ? s.measured_at : prev.elec_up.antennaAt,
          },
        }));
      }
    } catch { /* ignore */ }
  }, [selectedVehicleId, loadRecords]);

  useEffect(() => {
    if (attSource !== 'live') return;
    setAtt(liveAttitude(liveData));
  }, [liveData, attSource]);

  // 未锁定的天线框跟随实时，并刷新天线时刻
  useEffect(() => {
    setEnds((prev) => {
      let changed = false;
      const next = { ...prev };
      const now = new Date().toISOString();
      (['elec_down', 'elec_up'] as EndId[]).forEach((id) => {
        if (prev[id].antennaLocked) return;
        const end = id === 'elec_down' ? 'downstream' : 'upstream';
        const live = liveAntenna(liveData, layout, end);
        if (!live) return;
        const s = toStr(live);
        if (
          s.lat !== prev[id].antenna.lat
          || s.lon !== prev[id].antenna.lon
          || s.alt !== prev[id].antenna.alt
        ) {
          next[id] = { ...prev[id], antenna: s, antennaAt: now };
          changed = true;
        }
      });
      return changed ? next : prev;
    });
  }, [liveData, layout]);

  const loadArms = useCallback(async () => {
    if (!selectedVehicleId) return;
    const zero: Vec3 = [0, 0, 0];
    const fromLocal = (v: VehicleProfile | null) => {
      const cal = v?.l_arm_calibration;
      return {
        down: (v?.l_arm_elec_down ?? v?.l_arm_elec ?? zero).slice(0, 3) as Vec3,
        up: (v?.l_arm_elec_up ?? zero).slice(0, 3) as Vec3,
        downAt: cal?.elec_down?.measured_at,
        upAt: cal?.elec_up?.measured_at,
      };
    };
    if (isMock) {
      setCfgArms(fromLocal(vehicle));
      return;
    }
    try {
      const res = await fetch(`${baseUrl}/api/vehicle/config/${selectedVehicleId}`, {
        signal: AbortSignal.timeout(8000),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json = await res.json() as { code: number; data?: VehicleProfile | null };
      if (json.code === 200 && json.data) {
        setCfgArms(fromLocal(json.data));
        return;
      }
    } catch { /* fallback */ }
    setCfgArms(fromLocal(vehicle));
  }, [selectedVehicleId, baseUrl, isMock, vehicle]);

  useEffect(() => { void loadArms(); }, [loadArms]);

  const rememberLive = () => {
    if (!selectedVehicleId) {
      showToast('请先选择车辆', 'error');
      return;
    }
    const aDown = liveAntenna(liveData, layout, 'downstream');
    const aUp = liveAntenna(liveData, layout, 'upstream');
    if (!aDown && !aUp) {
      showToast('当前无有效电气侧天线', 'error');
      return;
    }
    const attitude = liveAttitude(liveData);
    const measured_at = new Date().toISOString();
    const antennas: Partial<Record<EndId, Lla>> = {};
    if (aDown) antennas.elec_down = aDown;
    if (aUp) antennas.elec_up = aUp;
    try {
      localStorage.setItem(SNAP_KEY(selectedVehicleId), JSON.stringify({ measured_at, attitude, antennas }));
    } catch { /* ignore */ }
    setSnapAt(measured_at);
    setAtt(attitude);
    setAttSource('snapshot');
    setEnds((prev) => ({
      elec_down: {
        ...prev.elec_down,
        antenna: aDown ? toStr(aDown) : prev.elec_down.antenna,
        antennaLocked: Boolean(aDown),
        antennaAt: aDown ? measured_at : prev.elec_down.antennaAt,
      },
      elec_up: {
        ...prev.elec_up,
        antenna: aUp ? toStr(aUp) : prev.elec_up.antenna,
        antennaLocked: Boolean(aUp),
        antennaAt: aUp ? measured_at : prev.elec_up.antennaAt,
      },
    }));
    showToast('已记住当前电气天线与姿态，可移车后填轮胎');
  };

  const clearSnap = () => {
    if (selectedVehicleId) {
      try { localStorage.removeItem(SNAP_KEY(selectedVehicleId)); } catch { /* */ }
    }
    setSnapAt(null);
    setAttSource('live');
    setEnds((prev) => ({
      elec_down: { ...prev.elec_down, antennaLocked: false },
      elec_up: { ...prev.elec_up, antennaLocked: false },
    }));
    showToast('已清除记住的天线，恢复跟随实时');
  };

  const updateAnt = (id: EndId, field: keyof LlaStr, value: string) => {
    const now = new Date().toISOString();
    setEnds((prev) => ({
      ...prev,
      [id]: {
        ...prev[id],
        antenna: { ...prev[id].antenna, [field]: value },
        antennaLocked: true,
        antennaAt: now,
      },
    }));
    if (attSource === 'live') setAttSource('manual');
  };

  const updateTire = (id: EndId, field: keyof LlaStr, value: string) => {
    setEnds((prev) => ({
      ...prev,
      [id]: { ...prev[id], tire: { ...prev[id].tire, [field]: value } },
    }));
  };

  const updateLiveBiasX = (id: EndId, value: string) => {
    setEnds((prev) => ({
      ...prev,
      [id]: { ...prev[id], liveBiasX: value },
    }));
  };

  const fillLiveAnt = (id: EndId) => {
    const end = id === 'elec_down' ? 'downstream' : 'upstream';
    const live = liveAntenna(liveData, layout, end);
    if (!live) {
      showToast('实时天线无效', 'error');
      return;
    }
    const now = new Date().toISOString();
    setEnds((prev) => ({
      ...prev,
      [id]: { ...prev[id], antenna: toStr(live), antennaLocked: true, antennaAt: now },
    }));
  };

  const analysis = useMemo(() => {
    const out: Partial<Record<EndId, {
      ant: Lla;
      tire: Lla | null;
      cfg: Vec3;
      inferred: Vec3 | null;
      predicted: Lla | null;
      dL: ReturnType<typeof armDeltaCm> | null;
      offset: ReturnType<typeof tireOffsetEnu> | null;
      antTire: ReturnType<typeof antennaMinusTireEnu> | null;
    }>> = {};
    (['elec_down', 'elec_up'] as EndId[]).forEach((id) => {
      const ant = parseLla(ends[id].antenna.lat, ends[id].antenna.lon, ends[id].antenna.alt);
      if (!ant) return;
      const tire = parseLla(ends[id].tire.lat, ends[id].tire.lon, ends[id].tire.alt);
      const cfg = id === 'elec_down'
        ? (cfgArms?.down ?? [0, 0, 0] as Vec3)
        : (cfgArms?.up ?? [0, 0, 0] as Vec3);
      const predicted = projectTire(ant, cfg, att);
      const inferred = tire ? computeLArm(ant, tire, att) : null;
      const dL = inferred ? armDeltaCm(inferred, cfg) : null;
      const offset = tire ? tireOffsetEnu(predicted, tire) : null;
      const antTire = tire ? antennaMinusTireEnu(ant, tire) : null;
      out[id] = { ant, tire, cfg, inferred, predicted, dL, offset, antTire };
    });
    return out;
  }, [ends, cfgArms, att]);

  // 正投/实测轮胎 → 对应贝位线横向偏差（与 leg_offsets 同号：朝场内为负）
  useEffect(() => {
    if (isMock) {
      setBayOffsets({});
      return;
    }
    const yardId = liveData.yard_id;
    if (!yardId) {
      setBayOffsets({});
      return;
    }
    let cancelled = false;
    const timer = window.setTimeout(() => {
      void (async () => {
        const next: typeof bayOffsets = {};
        await Promise.all((['elec_down', 'elec_up'] as EndId[]).map(async (id) => {
          const a = analysis[id];
          if (!a) return;
          const points: Record<string, { lat: number; lon: number; alt: number }> = {};
          if (a.predicted) points.predicted = a.predicted;
          if (a.tire) points.measured = a.tire;
          if (Object.keys(points).length === 0) return;
          try {
            const res = await fetch(`${baseUrl}/api/lever-check/bay-offset/batch`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ yard_id: yardId, side: 'elec', points }),
              signal: AbortSignal.timeout(8000),
            });
            if (!res.ok) return;
            type Off = {
              offset_cm?: number;
              tire_lycs_m?: [number, number, number | null] | null;
              bay_between?: BayBetween | null;
            } | null;
            const json = await res.json() as {
              code: number;
              data?: { offsets?: Record<string, Off> };
            };
            if (json.code !== 200 || !json.data?.offsets) return;
            const pred = json.data.offsets.predicted;
            const meas = json.data.offsets.measured;
            next[id] = {
              predicted_cm: pred?.offset_cm ?? null,
              measured_cm: meas?.offset_cm ?? null,
              predicted_lycs: pred?.tire_lycs_m ?? null,
              measured_lycs: meas?.tire_lycs_m ?? null,
              predicted_between: pred?.bay_between ?? null,
              measured_between: meas?.bay_between ?? null,
            };
          } catch { /* ignore */ }
        }));
        if (!cancelled) setBayOffsets(next);
      })();
    }, 250);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [analysis, baseUrl, isMock, liveData.yard_id]);

  const saveOne = async (id: EndId) => {
    if (!selectedVehicleId) {
      showToast('请先选择车辆', 'error');
      return;
    }
    const st = ends[id];
    const ant = parseLla(st.antenna.lat, st.antenna.lon, st.antenna.alt);
    const tire = parseLla(st.tire.lat, st.tire.lon, st.tire.alt);
    if (!ant || !tire) {
      showToast('至少填齐本端「一个天线 + 一个轮胎」坐标即可保存', 'error');
      return;
    }
    const a = analysis[id];
    const end = id === 'elec_down' ? 'downstream' : 'upstream';
    const bo = bayOffsets[id];
    const legs = legOffsetForEnd(liveData, id);
    const rec: SavedRecord = {
      id: `${Date.now()}_${id}`,
      saved_at: new Date().toISOString(),
      vehicle_id: selectedVehicleId,
      end: id,
      gnss: gnssAtEnd(layout, 'elec', end),
      attitude: { ...att },
      attitude_source: attSource,
      antenna: ant,
      antenna_at: st.antennaAt,
      tire_measured: tire,
      tire_predicted: a?.predicted ?? null,
      l_arm_config: a?.cfg ?? null,
      l_arm_inferred: a?.inferred ?? null,
      dL_cm: a?.dL ?? null,
      offset_enu_cm: a?.offset
        ? { e: a.offset.e * 100, n: a.offset.n * 100, u: a.offset.u * 100 }
        : null,
      offset_horiz_cm: a?.offset ? hypot2(a.offset.e, a.offset.n) * 100 : null,
      offset_3d_cm: a?.offset ? hypot3(a.offset.e, a.offset.n, a.offset.u) * 100 : null,
      ant_tire_enu_m: a?.antTire ?? null,
      live_bias_x_cm: parseBiasXCm(st.liveBiasX),
      yard_id: liveData.yard_id ?? null,
      bay_offset_predicted_cm: bo?.predicted_cm ?? null,
      bay_offset_measured_cm: bo?.measured_cm ?? null,
      tire_predicted_lycs_m: bo?.predicted_lycs ?? null,
      tire_measured_lycs_m: bo?.measured_lycs ?? null,
      bay_between_predicted: bo?.predicted_between ?? null,
      bay_between_measured: bo?.measured_between ?? null,
      compare_primary: loadComparePrimary(),
      tire_attitude: liveData.tire_attitude
        ?? liveData.gantry_attitude_compare?.tire_attitude
        ?? null,
      gnss_pair_align: liveData.gnss_pair_align ?? null,
      leg_offset_per_side_m: legs.perSide,
      leg_offset_rigid_m: legs.rigid,
    };

    if (!isMock) {
      try {
        const res = await fetch(`${baseUrl}/api/lever-check/save`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ vehicle_id: selectedVehicleId, record: rec }),
          signal: AbortSignal.timeout(8000),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const json = await res.json() as {
          code: number;
          message?: string;
          data?: { path?: string; record?: SavedRecord };
        };
        if (json.code !== 200) throw new Error(json.message || 'save failed');
        if (json.data?.path) setFilePath(json.data.path);
        const saved = json.data?.record ?? rec;
        const next = [saved, ...records].slice(0, 100);
        setRecords(next);
        cacheLocal(selectedVehicleId, next);
        showToast(`已写入文件 ${saved.gnss}`);
        return;
      } catch (e) {
        const next = [rec, ...records].slice(0, 100);
        setRecords(next);
        cacheLocal(selectedVehicleId, next);
        showToast(`文件写入失败，已暂存本机：${e instanceof Error ? e.message : e}`, 'error');
        return;
      }
    }
    const next = [rec, ...records].slice(0, 100);
    setRecords(next);
    cacheLocal(selectedVehicleId, next);
    showToast(`已保存 ${rec.gnss}（本机，无后端）`);
  };

  const clearRecords = async () => {
    if (!selectedVehicleId) return;
    try { localStorage.removeItem(STORAGE_KEY(selectedVehicleId)); } catch { /* */ }
    setRecords([]);
    if (!isMock) {
      try {
        await fetch(`${baseUrl}/api/lever-check/${encodeURIComponent(selectedVehicleId)}`, {
          method: 'DELETE',
          signal: AbortSignal.timeout(8000),
        });
      } catch { /* */ }
    }
    showToast('已清空本车保存记录');
  };

  const deleteOne = async (recordId: string) => {
    if (!selectedVehicleId) return;
    const next = records.filter((r) => r.id !== recordId);
    setRecords(next);
    cacheLocal(selectedVehicleId, next);
    if (expandedJsonId === recordId) setExpandedJsonId(null);
    if (!isMock) {
      try {
        const res = await fetch(
          `${baseUrl}/api/lever-check/${encodeURIComponent(selectedVehicleId)}/${encodeURIComponent(recordId)}`,
          { method: 'DELETE', signal: AbortSignal.timeout(8000) },
        );
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const json = await res.json() as { code: number; message?: string };
        if (json.code !== 200 && json.code !== 404) {
          throw new Error(json.message || 'delete failed');
        }
      } catch (e) {
        showToast(`文件删除失败，本机已去掉：${e instanceof Error ? e.message : e}`, 'error');
        return;
      }
    }
    showToast('已删除该条记录');
  };

  if (!selectedVehicleId) {
    return (
      <div className="card p-6 text-sm text-slate-400">请先在工作配置里选择车辆。</div>
    );
  }

  return (
    <div className="space-y-5">
      <div className="card p-5 space-y-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="text-sm font-semibold text-white">电气侧天线 ↔ 轮胎核对</h2>
            <p className="text-[11px] text-slate-500 mt-1 max-w-2xl leading-relaxed">
              每端独立：只填「一个天线 + 一个轮胎」就能保存，不必两端都做。
              可先「记住当前天线」再移车采轮胎；有姿态时会顺带算杆臂核对。
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <button type="button" className="btn-primary px-3 py-1.5 text-xs flex items-center gap-1.5" onClick={rememberLive}>
              <Bookmark className="w-3.5 h-3.5" /> 记住当前天线+姿态
            </button>
            <button type="button" className="btn-outline px-3 py-1.5 text-xs flex items-center gap-1.5" onClick={clearSnap}>
              <Eraser className="w-3.5 h-3.5" /> 清除记住
            </button>
            <button type="button" className="btn-outline px-3 py-1.5 text-xs flex items-center gap-1.5" onClick={() => void loadArms()}>
              <RefreshCw className="w-3.5 h-3.5" /> 刷新杆臂配置
            </button>
          </div>
        </div>

        {snapAt && (
          <p className="text-[11px] text-emerald-400/90">
            已记住 {fmtLocal(snapAt)} 的天线与姿态（可移车后只填轮胎）
          </p>
        )}

        <div className="grid grid-cols-1 sm:grid-cols-4 gap-3 pt-2 border-t border-slate-800">
          <div>
            <label className="text-[10px] text-slate-500">姿态来源</label>
            <select
              className="input-field mt-1 text-xs"
              value={attSource}
              onChange={(e) => setAttSource(e.target.value as typeof attSource)}
            >
              <option value="live">跟随实时</option>
              <option value="snapshot">用记住的</option>
              <option value="manual">手动改</option>
            </select>
          </div>
          {([
            ['heading', 'Heading °'],
            ['roll', 'Roll °'],
            ['pitch', 'Pitch °'],
          ] as const).map(([key, label]) => (
            <div key={key}>
              <label className="text-[10px] text-slate-500">{label}</label>
              <input
                className="input-field mt-1 font-mono text-xs"
                type="number"
                step="0.001"
                value={Number.isFinite(att[key]) ? att[key] : ''}
                disabled={attSource === 'live'}
                onChange={(e) => {
                  setAttSource('manual');
                  setAtt((prev) => ({ ...prev, [key]: parseFloat(e.target.value) || 0 }));
                }}
              />
            </div>
          ))}
        </div>
      </div>

      <div className="card p-4">
        <p className="section-title mb-1">电气侧标定杆臂（天线 → 轮胎中心，车体系）</p>
        <p className="text-[10px] text-slate-500 mb-3">
          来自车辆配置 l_arm_elec_down / l_arm_elec_up，正投轮胎时用这套
        </p>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {([
            {
              title: `${gnssAtEnd(layout, 'elec', 'downstream')} · elec_down`,
              arm: cfgArms?.down,
              at: cfgArms?.downAt,
            },
            {
              title: `${gnssAtEnd(layout, 'elec', 'upstream')} · elec_up`,
              arm: cfgArms?.up,
              at: cfgArms?.upAt,
            },
          ]).map((item) => (
            <div key={item.title} className="bg-slate-900/50 rounded-lg px-3 py-2.5 space-y-1">
              <p className="text-[10px] text-slate-500">{item.title}</p>
              {item.arm ? (
                <>
                  <p className="font-mono text-sm text-amber-300">{fmtArm(item.arm, 4)}</p>
                  <div className="grid grid-cols-3 gap-1 font-mono text-[11px] text-slate-300">
                    <span>X {item.arm[0].toFixed(4)} m</span>
                    <span>Y {item.arm[1].toFixed(4)} m</span>
                    <span>Z {item.arm[2].toFixed(4)} m</span>
                  </div>
                  {item.at && (
                    <p className="text-[10px] text-slate-600">
                      标定于 {fmtLocal(item.at)}
                    </p>
                  )}
                </>
              ) : (
                <p className="text-[11px] text-slate-500">未加载</p>
              )}
            </div>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {([
          { id: 'elec_down' as const, end: 'downstream' as const },
          { id: 'elec_up' as const, end: 'upstream' as const },
        ]).map(({ id, end }) => {
          const label = tireCenterLabel(layout, 'elec', end);
          const gnss = gnssAtEnd(layout, 'elec', end);
          const a = analysis[id];
          const st = ends[id];
          return (
            <div key={id} className="card p-4 space-y-3">
              <div className="flex items-center justify-between gap-2">
                <div>
                  <p className="text-sm font-semibold text-white">{label}</p>
                  <p className="text-[10px] text-slate-500">{gnss} · {id}{st.antennaLocked ? ' · 天线已锁定' : ' · 天线跟随实时'}</p>
                </div>
                <button type="button" className="btn-outline text-[10px] px-2 py-1" onClick={() => fillLiveAnt(id)}>
                  填入实时天线
                </button>
              </div>

              <FieldGroup title="天线 WGS84" values={st.antenna} onChange={(f, v) => updateAnt(id, f, v)} />
              <p className="text-[10px] text-slate-500 -mt-2">
                天线坐标时刻：
                <span className="font-mono text-slate-300 ml-1">{fmtLocal(st.antennaAt)}</span>
                {!st.antennaLocked && st.antennaAt ? (
                  <span className="text-slate-600 ml-1">（跟随实时，随坐标刷新）</span>
                ) : null}
              </p>
              <FieldGroup title="实测轮胎中心 WGS84" values={st.tire} onChange={(f, v) => updateTire(id, f, v)} />

              <div>
                <p className="text-[10px] text-slate-500 mb-1">现场实时偏差（手填）</p>
                <p className="text-[9px] text-slate-600 mb-1.5">只填沿车体 X 方向的偏差（cm），保存时写入 live_bias_x_cm</p>
                <div className="max-w-[10rem]">
                  <label className="text-[9px] text-slate-600">X cm</label>
                  <input
                    className="input-field mt-0.5 font-mono text-[11px] py-1.5"
                    type="number"
                    step="0.1"
                    value={st.liveBiasX}
                    onChange={(e) => updateLiveBiasX(id, e.target.value)}
                    placeholder="0.0"
                  />
                </div>
              </div>

              <div className="text-[11px] space-y-1.5 font-mono bg-slate-900/50 rounded-lg p-3">
                <p className="text-[9px] text-slate-500 font-sans">杆臂（车体系：X前 Y左 Z上，单位 m）</p>
                <Row label="标定杆臂（配置）" value={a ? `${fmtArm(a.cfg, 4)} m` : '—'} accent="amber" />
                <Row
                  label="实测轮胎反推杆臂"
                  value={a?.inferred ? `${fmtArm(a.inferred, 4)} m` : '—（填轮胎后）'}
                  accent="emerald"
                />
                <p className="text-[9px] text-slate-600 leading-relaxed font-sans">
                  反推杆臂 = 用「天线 + 手填实测轮胎 + 当时姿态」算出的 L，可直接和配置对比
                </p>
                <Row
                  label="杆臂差 dL（反推−标定）"
                  value={a?.dL
                    ? `|dL|=${a.dL.abs.toFixed(1)} cm（dx ${a.dL.dx.toFixed(1)}, dy ${a.dL.dy.toFixed(1)}, dz ${a.dL.dz.toFixed(1)}）`
                    : '—'}
                  accent={a?.dL ? (a.dL.abs <= 3 ? 'emerald' : 'amber') : undefined}
                />
                <p className="text-[9px] text-slate-600 leading-relaxed font-sans">
                  dL 单位 cm；不是轮胎到贝位线/Y 轴的距离
                </p>

                <p className="text-[9px] text-slate-500 font-sans pt-1">轮胎中心 WGS84</p>
                <Row
                  label="天线算出的轮胎（正投）"
                  value={a?.predicted
                    ? `${a.predicted.lat.toFixed(8)}, ${a.predicted.lon.toFixed(8)}, ${a.predicted.alt.toFixed(3)}`
                    : '—'}
                />
                <p className="text-[9px] text-slate-600 leading-relaxed font-sans">
                  正投 = 天线坐标 − R(姿态)·标定杆臂，即系统算出来的轮胎中心
                </p>
                <Row
                  label="正投相对实测 · 水平"
                  value={a?.offset ? `${(hypot2(a.offset.e, a.offset.n) * 100).toFixed(1)} cm` : '—'}
                  accent={a?.offset
                    ? (hypot2(a.offset.e, a.offset.n) * 100 <= 3 ? 'emerald' : 'amber')
                    : undefined}
                />
                <Row
                  label="正投相对实测 · ENU"
                  value={a?.offset
                    ? `E ${(a.offset.e * 100).toFixed(1)}  N ${(a.offset.n * 100).toFixed(1)}  U ${(a.offset.u * 100).toFixed(1)} cm`
                    : '—'}
                />
                <Row
                  label="天线−实测轮胎 ENU"
                  value={a?.antTire
                    ? `E ${a.antTire.e.toFixed(3)}  N ${a.antTire.n.toFixed(3)}  U ${a.antTire.u.toFixed(3)} m`
                    : '—'}
                />

                <p className="text-[9px] text-slate-500 font-sans pt-1">
                  堆场坐标 LYCS{liveData.yard_id ? `（${liveData.yard_id}）` : ''}
                </p>
                <Row
                  label="天线算出的轮胎 · 堆场"
                  value={fmtLycs(bayOffsets[id]?.predicted_lycs)}
                  accent="cyan"
                />
                <Row
                  label="手填实测轮胎 · 堆场"
                  value={a?.tire ? fmtLycs(bayOffsets[id]?.measured_lycs) : '—'}
                  accent="cyan"
                />
                <Row
                  label="天线算出的轮胎所在贝位"
                  value={fmtBayBetween(bayOffsets[id]?.predicted_between)}
                  accent="cyan"
                />
                <Row
                  label="手填实测轮胎所在贝位"
                  value={a?.tire ? fmtBayBetween(bayOffsets[id]?.measured_between) : '—'}
                  accent="cyan"
                />
                <p className="text-[9px] text-slate-600 leading-relaxed font-sans">
                  按堆场 Y 夹在相邻两个场地贝位点之间；百分比为从小号贝位往大号方向的位置
                </p>

                <p className="text-[9px] text-slate-500 font-sans pt-1">到对应贝位线的横向偏差</p>
                <Row
                  label="天线算出的轮胎 → 贝位线"
                  value={bayOffsets[id]?.predicted_cm != null
                    ? fmtCmSigned(bayOffsets[id]!.predicted_cm)
                    : (liveData.yard_id ? '计算中/—' : '无当前堆场')}
                  accent="cyan"
                />
                <Row
                  label="手填实测轮胎 → 贝位线"
                  value={bayOffsets[id]?.measured_cm != null
                    ? fmtCmSigned(bayOffsets[id]!.measured_cm)
                    : (a?.tire ? (liveData.yard_id ? '计算中/—' : '无当前堆场') : '—')}
                  accent="cyan"
                />
                <p className="text-[9px] text-slate-600 leading-relaxed font-sans">
                  符号与仪表盘腿偏相同：朝场内为负、朝场外为正
                  {(() => {
                    const live = id === 'elec_down'
                      ? liveData.gantry?.leg_offsets?.fl
                      : liveData.gantry?.leg_offsets?.rl;
                    return live != null
                      ? `；当前实时腿偏 ${fmtCmSigned(live * 100)}`
                      : '';
                  })()}
                </p>
              </div>

              <button
                type="button"
                className="btn-primary w-full py-2 text-xs flex items-center justify-center gap-1.5"
                onClick={() => void saveOne(id)}
              >
                <Save className="w-3.5 h-3.5" /> 保存本端（天线+轮胎即可）
              </button>
            </div>
          );
        })}
      </div>

      <div className="card p-5 space-y-3">
        <div>
          <p className="section-title">WGS84 → 堆场坐标（临时转换，不保存）</p>
          <p className="text-[10px] text-slate-600 mt-1">
            输入经纬高，实时输出当前堆场 LYCS（X/Y/Z，米）。堆场默认用实时 {liveData.yard_id || '—'}，可手改。
          </p>
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 items-end">
          <div>
            <label className="text-[9px] text-slate-600">堆场</label>
            <input
              className="input-field mt-0.5 font-mono text-[11px] py-1.5"
              value={convYard}
              onChange={(e) => setConvYard(e.target.value)}
              placeholder={liveData.yard_id || 'A02'}
            />
          </div>
          {([
            ['lat', 'Lat'],
            ['lon', 'Lon'],
            ['alt', 'Alt'],
          ] as const).map(([k, lab]) => (
            <div key={k}>
              <label className="text-[9px] text-slate-600">{lab}</label>
              <input
                className="input-field mt-0.5 font-mono text-[11px] py-1.5"
                value={convIn[k]}
                onChange={(e) => setConvIn((prev) => ({ ...prev, [k]: e.target.value }))}
                placeholder={k}
              />
            </div>
          ))}
        </div>
        <p className="text-[12px] font-mono text-sky-300 min-h-[1.25rem]">
          {convOut
            ? `堆场 LYCS：X ${convOut[0].toFixed(4)}  Y ${convOut[1].toFixed(4)}  Z ${convOut[2].toFixed(4)} m · ${convYard.trim() || liveData.yard_id || '—'}`
            : (convErr ? <span className="text-amber-400">{convErr}</span> : '填入 Lat/Lon（Alt 可空按 0）后自动换算')}
        </p>
      </div>

      <div className="card p-5 space-y-3">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <p className="section-title">已保存记录</p>
            <p className="text-[10px] text-slate-600 mt-1">
              落盘{' '}
              <span className="font-mono text-slate-400">
                {filePath ?? `logs/lever_check/${selectedVehicleId}.json`}
              </span>
              （后端）；本机另有缓存。一条 = 一端天线 + 轮胎
            </p>
          </div>
          <div className="flex gap-2">
            <button
              type="button"
              className="btn-outline text-[10px] px-2 py-1"
              onClick={() => setShowFormat((v) => !v)}
            >
              {showFormat ? '收起格式' : '看保存格式'}
            </button>
            <button type="button" className="btn-outline text-[10px] px-2 py-1 flex items-center gap-1" onClick={() => void clearRecords()}>
              <Trash2 className="w-3 h-3" /> 清空全部
            </button>
          </div>
        </div>

        {showFormat && (
          <pre className="text-[10px] font-mono text-slate-400 bg-slate-950/80 border border-slate-800 rounded-lg p-3 overflow-x-auto whitespace-pre-wrap leading-relaxed">
            {RECORD_FORMAT_EXAMPLE}
          </pre>
        )}

        {records.length === 0 ? (
          <p className="text-[11px] text-slate-500">暂无记录。任选一端填天线+轮胎后点保存即可。</p>
        ) : (
          <div className="space-y-2 max-h-96 overflow-y-auto pr-1">
            {records.map((r) => (
              <div key={r.id} className="text-[11px] font-mono bg-slate-900/40 rounded-lg p-3 space-y-0.5">
                <div className="flex items-start justify-between gap-2">
                  <p className="text-slate-300">
                    保存 {fmtLocal(r.saved_at)} · {r.gnss} · {r.end}
                  </p>
                  <div className="flex gap-1 shrink-0">
                    <button
                      type="button"
                      className="btn-outline text-[9px] px-1.5 py-0.5"
                      onClick={() => setExpandedJsonId((cur) => (cur === r.id ? null : r.id))}
                    >
                      {expandedJsonId === r.id ? '收起 JSON' : '原始 JSON'}
                    </button>
                    <button
                      type="button"
                      className="btn-outline text-[9px] px-1.5 py-0.5 flex items-center gap-0.5"
                      onClick={() => void deleteOne(r.id)}
                      title="删除本条"
                    >
                      <Trash2 className="w-3 h-3" /> 删除
                    </button>
                  </div>
                </div>
                <p className="text-slate-500">
                  天线时刻 {fmtLocal(r.antenna_at)}
                </p>
                <p className="text-slate-500">
                  H/R/P {r.attitude.heading.toFixed(2)}/{r.attitude.roll.toFixed(3)}/{r.attitude.pitch.toFixed(3)}
                  （{r.attitude_source}）
                </p>
                <p className="text-slate-500">
                  天线 WGS84 {r.antenna.lat.toFixed(8)}, {r.antenna.lon.toFixed(8)}, {r.antenna.alt.toFixed(3)}
                </p>
                <p className="text-slate-500">
                  天线算出的轮胎（正投）WGS84{' '}
                  {r.tire_predicted
                    ? `${r.tire_predicted.lat.toFixed(8)}, ${r.tire_predicted.lon.toFixed(8)}, ${r.tire_predicted.alt.toFixed(3)}`
                    : '—'}
                </p>
                <p className="text-slate-500">
                  手填实测轮胎 WGS84 {r.tire_measured.lat.toFixed(8)}, {r.tire_measured.lon.toFixed(8)}, {r.tire_measured.alt.toFixed(3)}
                </p>
                <p className="text-sky-300/90">
                  天线算出的轮胎 · 堆场 {fmtLycs(r.tire_predicted_lycs_m)}
                </p>
                <p className="text-sky-300/90">
                  手填实测轮胎 · 堆场 {fmtLycs(r.tire_measured_lycs_m)}
                </p>
                <p className="text-violet-300/90">
                  天线算出的轮胎所在贝位 {fmtBayBetween(r.bay_between_predicted)}
                </p>
                <p className="text-violet-300/90">
                  手填实测轮胎所在贝位 {fmtBayBetween(r.bay_between_measured)}
                </p>
                <p className="text-amber-300/90">
                  标定杆臂（配置） {r.l_arm_config ? fmtArm(r.l_arm_config, 4) : '—'} m
                </p>
                <p className="text-emerald-300/90">
                  实测轮胎反推杆臂 {r.l_arm_inferred ? fmtArm(r.l_arm_inferred, 4) : '—'} m
                  <span className="text-slate-500">（天线 + 手填轮胎 + 当时姿态）</span>
                </p>
                <p className="text-cyan-400/90">
                  杆臂差 dL{' '}
                  {r.dL_cm
                    ? `|dL|=${r.dL_cm.abs.toFixed(1)} cm（dx ${r.dL_cm.dx.toFixed(1)}, dy ${r.dL_cm.dy.toFixed(1)}, dz ${r.dL_cm.dz.toFixed(1)}）`
                    : '—'}
                  {' · '}正投相对实测水平 {r.offset_horiz_cm != null ? `${r.offset_horiz_cm.toFixed(1)} cm` : '—'}
                  {' · '}三维 {r.offset_3d_cm != null ? `${r.offset_3d_cm.toFixed(1)} cm` : '—'}
                </p>
                <p className="text-sky-300/90">
                  到贝位线：天线算出的轮胎 {fmtCmSigned(r.bay_offset_predicted_cm)}
                  {' · '}手填实测轮胎 {fmtCmSigned(r.bay_offset_measured_cm)}
                  {r.yard_id ? ` · 堆场 ${r.yard_id}` : ''}
                </p>
                <p className="text-amber-300/80">
                  手填现场 X 偏差（live_bias_x_cm）{' '}
                  {r.live_bias_x_cm != null
                    ? `${r.live_bias_x_cm.toFixed(1)} cm`
                    : r.live_bias_cm
                      ? `E ${r.live_bias_cm.e.toFixed(1)}  N ${r.live_bias_cm.n.toFixed(1)}  U ${r.live_bias_cm.u.toFixed(1)} cm（旧）`
                      : '—'}
                </p>
                {expandedJsonId === r.id && (
                  <pre className="mt-2 text-[10px] text-slate-400 bg-slate-950/70 rounded p-2 overflow-x-auto whitespace-pre-wrap">
                    {JSON.stringify(r, null, 2)}
                  </pre>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function FieldGroup({
  title,
  values,
  onChange,
}: {
  title: string;
  values: LlaStr;
  onChange: (field: keyof LlaStr, value: string) => void;
}) {
  return (
    <div>
      <p className="text-[10px] text-slate-500 mb-1">{title}</p>
      <div className="grid grid-cols-3 gap-2">
        {([
          ['lat', 'Lat'],
          ['lon', 'Lon'],
          ['alt', 'Alt'],
        ] as const).map(([k, lab]) => (
          <div key={k}>
            <label className="text-[9px] text-slate-600">{lab}</label>
            <input
              className="input-field mt-0.5 font-mono text-[11px] py-1.5"
              value={values[k]}
              onChange={(e) => onChange(k, e.target.value)}
              placeholder={k}
            />
          </div>
        ))}
      </div>
    </div>
  );
}

function Row({
  label,
  value,
  accent,
}: {
  label: string;
  value: string;
  accent?: 'emerald' | 'amber' | 'cyan';
}) {
  const color = accent === 'emerald'
    ? 'text-emerald-400'
    : accent === 'amber'
      ? 'text-amber-400'
      : accent === 'cyan'
        ? 'text-sky-300'
        : 'text-slate-300';
  return (
    <div className="flex justify-between gap-2 items-baseline">
      <span className="text-slate-500 shrink-0 text-[10px] font-sans">{label}</span>
      <span className={`text-right break-all ${color}`}>{value}</span>
    </div>
  );
}
