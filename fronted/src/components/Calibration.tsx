import { useState, useEffect, useCallback } from 'react';
import { Zap, CheckCircle2, AlertTriangle, Map, RefreshCw, Plus, Loader } from 'lucide-react';
import { SectionHeader, showToast, InfoRow } from './common';
import { useAppContext } from '../context/AppContext';
import type { RealtimeData } from '../types';
import { clsx } from 'clsx';
import { logger } from '../utils/logger';
import { httpBase } from '../utils/backendUrl';
import { gnssAtEnd } from '../utils/gnssName';

interface CalibrationProps {
  liveData: RealtimeData;
}

type CalMode = 'full' | 'verify' | 'skip' | 'loading' | 'no_rtk';

interface RtkBay {
  bay_no: number;
  bay_id: string;
  lat: number;
  lon: number;
  alt: number;
}

interface TransformRow {
  bay_no: number;
  ref?: { lat: number; lon: number; alt: number };
  measured?: { lat: number; lon: number; alt: number; local_x?: number; local_y?: number; local_z?: number };
  delta?: { lat: number; lon: number; alt: number };
}

interface LanePoint {
  id: number;
  y_m: number;
  elec1_m: number;
  elec2_m: number;
  diesel1_m: number;
  diesel2_m: number;
}

const LANE_FIELDS = [
  { key: 'elec1', label: '电气 GNSS1', live: 'fl', role: 'elec', gnss: 'GNSS1' },
  { key: 'elec2', label: '电气 GNSS2', live: 'rl', role: 'elec', gnss: 'GNSS2' },
  { key: 'diesel1', label: '动力 GNSS1', live: 'fr', role: 'diesel', gnss: 'GNSS1' },
  { key: 'diesel2', label: '动力 GNSS2', live: 'rr', role: 'diesel', gnss: 'GNSS2' },
] as const;

function formatSignedCm(meters: number): string {
  const cm = meters * 100;
  return `${cm >= 0 ? '+' : ''}${cm.toFixed(1)} cm`;
}

function cmToMeters(text: string): number | null {
  const trimmed = text.trim();
  if (!trimmed) return 0;
  const cm = Number(trimmed);
  if (!Number.isFinite(cm) || Math.abs(cm) > 200) return null;
  return cm / 100;
}

function oneRowPerBay(
  points: Array<{ bay_no: number; lat: number; lon: number; alt: number; bay_side?: string | null }>,
): RtkBay[] {
  const byBay: Record<number, RtkBay> = {};
  for (const point of points) {
    if (point.bay_no == null) continue;
    const diesel = point.bay_side === 'diesel';
    const existing = byBay[point.bay_no];
    if (existing && (diesel || point.bay_side == null)) continue;
    byBay[point.bay_no] = {
      bay_no: point.bay_no,
      bay_id: String(point.bay_no),
      lat: point.lat,
      lon: point.lon,
      alt: point.alt,
    };
  }
  return Object.values(byBay).sort((a, b) => a.bay_no - b.bay_no);
}

function parseBayNo(value: string): number | null {
  const n = parseInt(value.replace(/\D/g, ''), 10);
  return Number.isFinite(n) && n > 0 ? n : null;
}

const PAGE_SIZE = 10;

export default function Calibration({ liveData }: CalibrationProps) {
  const { vehicles, yards, selectedVehicleId, selectedYardId, backendConfig } = useAppContext();
  const selectedVehicle = vehicles.find(v => v.vehicle_id === selectedVehicleId) ?? null;
  const selectedYard = yards.find(y => y.yard_id === selectedYardId) ?? null;

  const [calMode, setCalMode] = useState<CalMode>('loading');
  const [calibrationStatus, setCalibrationStatus] = useState<'none' | 'pending' | 'completed'>('none');
  const [refVehicleId, setRefVehicleId] = useState<string | null>(null);
  const [rtkBays, setRtkBays] = useState<RtkBay[]>([]);
  const [transformList, setTransformList] = useState<TransformRow[]>([]);
  const [bayNo, setBayNo] = useState('1');
  const [confirming, setConfirming] = useState(false);
  const [finalizing, setFinalizing] = useState(false);
  const [clearing, setClearing] = useState(false);
  const [lanePoints, setLanePoints] = useState<LanePoint[]>([]);
  const [laneCm, setLaneCm] = useState({ elec1: '', elec2: '', diesel1: '', diesel2: '' });
  const [surveyLat, setSurveyLat] = useState('');
  const [surveyLon, setSurveyLon] = useState('');
  const [surveyTire, setSurveyTire] = useState<(typeof LANE_FIELDS)[number]['key']>('elec1');
  const [surveyDist, setSurveyDist] = useState<Record<string, { lateral_m: number; along_m: number }>>({});
  const [surveyDistNote, setSurveyDistNote] = useState('');
  const [surveyBusy, setSurveyBusy] = useState(false);
  const [savingLane, setSavingLane] = useState(false);
  const [bayQuery, setBayQuery] = useState('');
  const [listStatus, setListStatus] = useState<'all' | 'saved' | 'pending'>('all');
  const [page, setPage] = useState(0);

  const baseUrl = httpBase(backendConfig);
  const yardId = selectedYard?.yard_id ?? '';

  const fetchRtkFromHistory = async (): Promise<RtkBay[]> => {
    const histRes = await fetch(
      `${baseUrl}/api/survey/history?yard_id=${encodeURIComponent(yardId)}&point_type=mapping`,
      { signal: AbortSignal.timeout(5000) },
    );
    const histJson = await histRes.json();
    if (histJson.code !== 200 || !Array.isArray(histJson.data)) return [];
    return oneRowPerBay(histJson.data);
  };

  const loadTransformList = useCallback(async (): Promise<TransformRow[]> => {
    if (!yardId || !selectedVehicleId) return [];
    const res = await fetch(
      `${baseUrl}/api/calibration/transform_list?vehicle_id=${encodeURIComponent(selectedVehicleId)}&yard_id=${encodeURIComponent(yardId)}`,
      { signal: AbortSignal.timeout(5000) },
    );
    const json = await res.json();
    if (json.code === 200) {
      const list = json.data.transform_list ?? [];
      setTransformList(list);
      return list;
    }
    return [];
  }, [baseUrl, selectedVehicleId, yardId]);

  const loadContext = useCallback(async () => {
    if (!yardId) return;
    setCalMode('loading');
    try {
      const mapQs = selectedVehicleId
        ? `yard_id=${encodeURIComponent(yardId)}&vehicle_id=${encodeURIComponent(selectedVehicleId)}`
        : `yard_id=${encodeURIComponent(yardId)}`;

      let d: {
        has_rtk_map?: boolean;
        rtk_point_count?: number;
        calibration_mode?: string;
        calibration_status?: string;
        survey_points?: Array<{ bay_no: number | null; lat: number; lon: number; alt: number; bay_side?: string | null }>;
        transform_list?: TransformRow[];
      } = {};

      try {
        const mapRes = await fetch(`${baseUrl}/api/yard/map?${mapQs}`, {
          signal: AbortSignal.timeout(8000),
        });
        const mapJson = await mapRes.json();
        if (mapJson.code === 200) {
          d = mapJson.data;
        }
      } catch (mapErr) {
        logger.warn('Calibration', 'yard/map 失败', mapErr);
      }

      let bays: RtkBay[] = oneRowPerBay(
        (d.survey_points ?? []).filter((p): p is { bay_no: number; lat: number; lon: number; alt: number; bay_side?: string | null } => p.bay_no != null),
      );

      if (bays.length === 0) {
        bays = await fetchRtkFromHistory();
      }

      const hasRtk =
        bays.length > 0 ||
        (d.rtk_point_count ?? 0) > 0 ||
        d.has_rtk_map === true;

      if (!hasRtk) {
        setRtkBays([]);
        setTransformList([]);
        setCalMode('no_rtk');
        return;
      }

      setRtkBays(bays);

      const status = (d.calibration_status ?? 'none') as 'none' | 'pending' | 'completed';
      setCalibrationStatus(status);
      setTransformList(d.transform_list ?? []);

      let mode: CalMode = 'full';
      let refId: string | null = null;
      if (selectedVehicleId) {
        const modeRes = await fetch(
          `${baseUrl}/api/calibration/mode?vehicle_id=${encodeURIComponent(selectedVehicleId)}&yard_id=${encodeURIComponent(yardId)}`,
          { signal: AbortSignal.timeout(5000) },
        );
        const modeJson = await modeRes.json();
        if (modeJson.code === 200) {
          mode = (modeJson.data.calibration_mode ?? 'full') as CalMode;
          refId = modeJson.data.reference_vehicle_id ?? null;
        } else {
          mode = (d.calibration_mode ?? 'full') as CalMode;
        }
        await loadTransformList();
      } else {
        mode = (d.calibration_mode ?? 'full') as CalMode;
      }

      setCalMode(mode);
      setRefVehicleId(refId);

      const savedNos = new Set((d.transform_list ?? []).map(t => t.bay_no));
      const nextBay = bays.find(b => !savedNos.has(b.bay_no)) ?? bays[bays.length - 1];
      if (nextBay) setBayNo(String(nextBay.bay_no));
    } catch (e) {
      logger.warn('Calibration', '加载失败', e);
      try {
        const bays = await fetchRtkFromHistory();
        if (bays.length > 0) {
          setRtkBays(bays);
          setCalMode('full');
          setCalibrationStatus('none');
          setTransformList([]);
          setBayNo(String(bays[0].bay_no));
          return;
        }
      } catch { /* ignore */ }
      setCalMode('no_rtk');
    }
  }, [baseUrl, selectedVehicleId, yardId, loadTransformList]);

  useEffect(() => { loadContext(); }, [loadContext]);

  const requiredCount = calMode === 'verify' ? Math.min(3, rtkBays.length) : rtkBays.length;
  const savedCount = transformList.length;
  const canFinalize = savedCount >= requiredCount && requiredCount > 0;

  const modeLabel =
    calMode === 'full' ? '校准1 · 全场逐贝对比' :
    calMode === 'verify' ? '校准2 · 三点核验（可继续补点）' :
    calMode === 'skip' ? '校准3 · 曾完成（可修改/追加）' : '';

  const handleConfirmBay = async () => {
    const n = parseBayNo(bayNo);
    if (!n) {
      showToast('请输入有效的贝位编号', 'error');
      return;
    }
    if (!rtkBays.some(b => b.bay_no === n)) {
      showToast(`贝位 ${n} 尚无 RTK 真值，请先在场地建图录入`, 'error');
      return;
    }
    if (confirming || !selectedVehicleId) return;
    setConfirming(true);

    try {
      const res = await fetch(`${baseUrl}/api/survey/confirm`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          vehicle_id: selectedVehicleId,
          yard_id: yardId,
          bay_id: String(n),
          mode: 'calibration',
          lat: (liveData.lpoint_geo ?? liveData.gps_coord!).lat,
          lon: (liveData.lpoint_geo ?? liveData.gps_coord!).lon,
          alt: (liveData.lpoint_geo ?? liveData.gps_coord!).alt,
          local_x: liveData.local_coord.x,
          local_y: liveData.local_coord.y,
          local_z: liveData.local_coord.z,
        }),
      });
      const body = await res.json();
      if (!res.ok) {
        showToast(body.detail ?? body.message ?? '采点失败', 'error');
        return;
      }
      showToast(`贝位 ${n} 已写入/更新转换记录`);
      const list = await loadTransformList();
      if (calibrationStatus === 'completed') {
        setCalibrationStatus('pending');
        setCalMode(calMode === 'verify' ? 'verify' : 'full');
      }
      const next = rtkBays.find(b => !list.some(t => t.bay_no === b.bay_no));
      if (next) setBayNo(String(next.bay_no));
    } catch (e) {
      logger.error('Calibration', 'confirm 失败', e);
      showToast('请求失败', 'error');
    } finally {
      setConfirming(false);
    }
  };

  const handleFinalize = async () => {
    if (!selectedVehicleId || finalizing) return;
    if (!canFinalize) {
      showToast(
        calMode === 'verify'
          ? `校准2 建议至少完成 ${requiredCount} 个贝位（当前 ${savedCount}）`
          : `请先完成至少 ${requiredCount} 个贝位采点`,
        'error',
      );
      return;
    }
    setFinalizing(true);
    try {
      const res = await fetch(`${baseUrl}/api/survey/finalize`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          vehicle_id: selectedVehicleId,
          yard_id: yardId,
          calibration_data: { mode: calMode, steps: savedCount },
        }),
      });
      const body = await res.json();
      if (res.ok && body.code === 200) {
        setCalibrationStatus('completed');
        setCalMode('skip');
        showToast('已标记校准完成，仍可继续修改任意贝位');
        loadContext();
      } else {
        showToast(body.detail ?? body.message ?? 'finalize 失败', 'error');
      }
    } catch {
      showToast('请求失败', 'error');
    } finally {
      setFinalizing(false);
    }
  };

  const handleClearCalibration = async () => {
    if (!selectedVehicleId || clearing) return;
    if (!window.confirm(
      `确定清空 ${selectedVehicle?.label} 在 ${selectedYard?.name} 的全部校准采点？\n共 ${savedCount} 条，清空后需重新采点。`,
    )) return;
    setClearing(true);
    try {
      const res = await fetch(
        `${baseUrl}/api/calibration/transform_list?vehicle_id=${encodeURIComponent(selectedVehicleId)}&yard_id=${encodeURIComponent(yardId)}`,
        { method: 'DELETE' },
      );
      const body = await res.json();
      if (res.ok && body.code === 200) {
        setTransformList([]);
        setCalibrationStatus('none');
        setCalMode(calMode === 'verify' ? 'verify' : 'full');
        setBayNo(rtkBays[0] ? String(rtkBays[0].bay_no) : '1');
        showToast('已清空校准记录，可重新采点');
        loadContext();
      } else {
        showToast(body.detail ?? body.message ?? '清空失败', 'error');
      }
    } catch {
      showToast('清空失败', 'error');
    } finally {
      setClearing(false);
    }
  };

  const loadLanePoints = useCallback(async () => {
    if (!yardId || !selectedVehicleId) {
      setLanePoints([]);
      return;
    }
    try {
      const res = await fetch(
        `${baseUrl}/api/survey/lane_corrections?vehicle_id=${encodeURIComponent(selectedVehicleId)}&yard_id=${encodeURIComponent(yardId)}`,
        { signal: AbortSignal.timeout(5000) },
      );
      const body = await res.json();
      if (body.code === 200 && Array.isArray(body.data)) {
        setLanePoints(body.data);
      }
    } catch (e) {
      logger.warn('Calibration', '读取停留修正失败', e);
    }
  }, [baseUrl, yardId, selectedVehicleId]);

  useEffect(() => { loadLanePoints(); }, [loadLanePoints]);

  const layout = selectedVehicle?.receiver_layout;

  const tireCenter = (key: (typeof LANE_FIELDS)[number]['key']) => {
    const field = LANE_FIELDS.find(item => item.key === key);
    if (!field) return null;
    const end = gnssAtEnd(layout, field.role, 'downstream') === field.gnss ? 'downstream' : 'upstream';
    const point = liveData.gantry?.tires?.[`${field.role}_${end}`];
    if (point?.valid !== true || !Number.isFinite(point.x) || !Number.isFinite(point.y)) return null;
    return { x: point.x, y: point.y };
  };

  const computeSurveyDistance = async () => {
    const lat = Number(surveyLat.trim());
    const lon = Number(surveyLon.trim());
    if (!yardId || !Number.isFinite(lat) || !Number.isFinite(lon) || Math.abs(lat) > 90 || Math.abs(lon) > 180) {
      setSurveyDist({});
      setSurveyDistNote('请先填写实测点的纬度和经度');
      return;
    }
    const center = tireCenter(surveyTire);
    if (!center) {
      setSurveyDist({});
      setSurveyDistNote('这只轮胎中心还没有坐标');
      return;
    }
    setSurveyBusy(true);
    setSurveyDistNote('');
    try {
      const res = await fetch(`${baseUrl}/api/survey/tire_point_distance`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          yard_id: yardId,
          lat,
          lon,
          tires: { [surveyTire]: center },
        }),
        signal: AbortSignal.timeout(5000),
      });
      const body = await res.json();
      if (body.code === 200 && body.data) {
        setSurveyDist(body.data);
        setSurveyDistNote('');
      } else {
        const detail = body.detail;
        setSurveyDist({});
        setSurveyDistNote(typeof detail === 'string' ? detail : (body.message ?? '算不出到实测点的距离'));
      }
    } catch {
      setSurveyDist({});
      setSurveyDistNote('算不出到实测点的距离');
    } finally {
      setSurveyBusy(false);
    }
  };

  const visibleBays = rtkBays.filter(bay => {
    const saved = transformList.some(row => row.bay_no === bay.bay_no);
    if (listStatus === 'saved' && !saved) return false;
    if (listStatus === 'pending' && saved) return false;
    const query = bayQuery.trim();
    return !query || String(bay.bay_no).includes(query);
  });
  const pageCount = Math.max(1, Math.ceil(visibleBays.length / PAGE_SIZE));
  const safePage = Math.min(page, pageCount - 1);
  const pageBays = visibleBays.slice(safePage * PAGE_SIZE, safePage * PAGE_SIZE + PAGE_SIZE);

  useEffect(() => { setPage(0); }, [bayQuery, listStatus, yardId]);

  const handleSaveLane = async () => {
    if (!selectedVehicleId || savingLane) return;
    if (liveData.status === 'no_signal') {
      showToast('当前没有定位，先停车等信号稳定', 'error');
      return;
    }
    const shifts: Record<string, number> = {};
    for (const field of LANE_FIELDS) {
      const meters = cmToMeters(laneCm[field.key]);
      if (meters === null) {
        showToast(`${field.label} 请填 ±200 cm 以内的数字`, 'error');
        return;
      }
      shifts[`${field.key}_m`] = meters;
    }
    setSavingLane(true);
    try {
      const res = await fetch(`${baseUrl}/api/survey/lane_corrections`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          vehicle_id: selectedVehicleId,
          yard_id: yardId,
          y_m: liveData.local_coord?.y ?? 0,
          ...shifts,
        }),
      });
      const body = await res.json();
      if (res.ok && body.code === 200) {
        showToast(body.message || '已记录到全场');
        setLaneCm({ elec1: '', elec2: '', diesel1: '', diesel2: '' });
        loadLanePoints();
      } else {
        showToast(body.detail ?? body.message ?? '记录失败', 'error');
      }
    } catch {
      showToast('记录失败', 'error');
    } finally {
      setSavingLane(false);
    }
  };

  const handleDeleteBay = async (bay: number) => {
    if (!selectedVehicleId) return;
    if (!window.confirm(`删除贝位 ${bay} 的采点？\n删掉后这一贝不再参与差值，稍后可以重新确认。`)) return;
    try {
      const res = await fetch(
        `${baseUrl}/api/calibration/transform_list/${bay}?vehicle_id=${encodeURIComponent(selectedVehicleId)}&yard_id=${encodeURIComponent(yardId)}`,
        { method: 'DELETE' },
      );
      const body = await res.json();
      if (res.ok && body.code === 200) {
        setTransformList(prev => prev.filter(row => row.bay_no !== bay));
        if (body.data?.calibration_status === 'pending') setCalibrationStatus('pending');
        showToast(body.message || `已删除贝位 ${bay}`);
      } else {
        showToast(body.detail ?? body.message ?? '删除失败', 'error');
      }
    } catch {
      showToast('删除失败', 'error');
    }
  };

  const handleDeleteLane = async (row: LanePoint) => {
    if (!selectedVehicleId) return;
    if (!window.confirm(`删除沿贝位 ${row.y_m.toFixed(1)} m 处的修正？`)) return;
    try {
      const res = await fetch(
        `${baseUrl}/api/survey/lane_corrections/${row.id}?vehicle_id=${encodeURIComponent(selectedVehicleId)}`,
        { method: 'DELETE' },
      );
      const body = await res.json();
      if (res.ok && body.code === 200) {
        showToast('已删除');
        loadLanePoints();
      } else {
        showToast(body.detail ?? body.message ?? '删除失败', 'error');
      }
    } catch {
      showToast('删除失败', 'error');
    }
  };

  if (!selectedYard) {
    return (
      <div className="animate-fade-in flex items-center justify-center min-h-[60vh]">
        <div className="card p-12 max-w-md w-full text-center space-y-4">
          <AlertTriangle className="w-12 h-12 text-amber-400 mx-auto" />
          <h2 className="text-xl font-bold text-white">未选择场地</h2>
          <p className="text-slate-400 text-sm">请前往「工作配置」选择场地。</p>
        </div>
      </div>
    );
  }

  if (calMode === 'loading') {
    return (
      <div className="animate-fade-in flex items-center justify-center min-h-[40vh] text-slate-400">
        加载校准数据…
      </div>
    );
  }

  if (calMode === 'no_rtk') {
    return (
      <div className="animate-fade-in flex items-center justify-center min-h-[60vh]">
        <div className="card p-12 max-w-md w-full text-center space-y-4">
          <Map className="w-12 h-12 text-blue-400 mx-auto" />
          <h2 className="text-xl font-bold text-white">尚无 RTK 场地真值</h2>
          <p className="text-slate-400 text-sm">
            请先在「场地建图」录入至少一个 RTK 贝位（当前场地 {yardId}）。
          </p>
          <button type="button" onClick={() => loadContext()} className="btn-secondary mx-auto">
            重新检测 RTK 数据
          </button>
        </div>
      </div>
    );
  }

  if (!selectedVehicleId) {
    return (
      <div className="animate-fade-in flex items-center justify-center min-h-[60vh]">
        <div className="card p-12 max-w-md w-full text-center space-y-4">
          <AlertTriangle className="w-12 h-12 text-amber-400 mx-auto" />
          <h2 className="text-xl font-bold text-white">未选择车辆</h2>
          <p className="text-slate-400 text-sm">
            已检测到 {rtkBays.length} 个 RTK 贝位。请前往「工作配置」选择要校准的车辆。
          </p>
        </div>
      </div>
    );
  }

  const hasSaved = savedCount > 0 || calibrationStatus === 'completed';

  return (
    <div className="animate-fade-in space-y-5">
      <SectionHeader
        title={`车辆校准 · ${selectedVehicle?.label}`}
        subtitle={`${modeLabel}。已采 ${savedCount} / ${rtkBays.length} 个 RTK 贝位。同编号再次确认将覆盖。${refVehicleId ? ` 参考车 ${refVehicleId}。` : ''}`}
      />

      {hasSaved && (
        <div className="card p-4 flex flex-col sm:flex-row sm:items-center justify-between gap-3 border border-emerald-500/30 bg-emerald-900/10">
          <div className="flex items-start gap-3">
            <CheckCircle2 className="w-5 h-5 text-emerald-400 shrink-0 mt-0.5" />
            <div>
              <p className="text-sm font-bold text-emerald-300">
                {calibrationStatus === 'completed'
                  ? `已完成校准（${savedCount} 个贝位）`
                  : `已有校准采点（${savedCount} 个贝位）`}
              </p>
              <p className="text-xs text-emerald-200/60 mt-0.5">
                可继续追加新贝位或覆盖已有贝位；全部重采请点右侧「清空重新校准」。
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={handleClearCalibration}
            disabled={clearing}
            className="btn-secondary flex items-center gap-2 shrink-0 text-amber-300 border-amber-500/40"
          >
            <RefreshCw className={clsx('w-4 h-4', clearing && 'animate-spin')} />
            {clearing ? '清空中…' : '清空重新校准'}
          </button>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-4 gap-5">
        <div className="space-y-4">
          <div className="card p-5 space-y-4">
            <div>
              <label className="section-title block mb-1.5">堆场</label>
              <input className="input-field font-mono" value={yardId} readOnly />
            </div>
            <div>
              <label className="section-title block mb-1.5">贝位编号</label>
              <input
                type="number"
                min={1}
                className="input-field font-mono text-2xl text-blue-400 py-3"
                value={bayNo}
                onChange={e => {
                  const v = e.target.value;
                  if (v === '' || /^\d+$/.test(v)) setBayNo(v);
                }}
              />
              <p className="text-[10px] text-slate-500 mt-1">
                从列表点选或输入；须已有 RTK 真值
              </p>
            </div>
            <div className="p-3 bg-blue-900/10 border border-blue-500/20 rounded-lg text-[10px] text-blue-200/70">
              {calMode === 'verify'
                ? `校准2：建议先完成前 ${requiredCount} 个贝位，也可补采其它贝位。`
                : '校准1：建议覆盖全部 RTK 贝位，也可随时单点覆盖。'}
            </div>
          </div>

          <div className="card p-5 space-y-3">
            <InfoRow label="L-Point LAT" value={(liveData.lpoint_geo ?? liveData.gps_coord ?? { lat: 0 }).lat.toFixed(7)} />
            <InfoRow label="L-Point LON" value={(liveData.lpoint_geo ?? liveData.gps_coord ?? { lon: 0 }).lon.toFixed(7)} />
            <InfoRow
              label="GNSS"
              value={
                liveData.status === 'fix' ? 'RTK FIXED'
                  : liveData.status === 'float' ? 'RTK FLOAT'
                  : liveData.status === 'dgps' ? 'DGPS'
                  : liveData.status === 'single' ? '单点 GPS'
                  : 'NO SIGNAL'
              }
              mono={false}
            />
          </div>

          <button
            onClick={handleConfirmBay}
            disabled={confirming || liveData.status === 'no_signal'}
            className="w-full py-3.5 rounded-xl font-bold text-sm bg-blue-600 hover:bg-blue-500 text-white disabled:opacity-50 flex items-center justify-center gap-2"
          >
            {confirming ? <Loader className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
            {confirming ? '提交中…' : `确认/覆盖 贝位 ${bayNo || '?'}`}
          </button>

          <button
            onClick={handleFinalize}
            disabled={finalizing || !canFinalize}
            className="w-full py-3 rounded-xl font-bold text-sm border border-emerald-500/50 text-emerald-300 hover:bg-emerald-900/20 disabled:opacity-40 flex items-center justify-center gap-2"
          >
            {finalizing ? <Loader className="w-4 h-4 animate-spin" /> : <Zap className="w-4 h-4" />}
            {finalizing ? '提交中…' : `标记校准完成（${savedCount}/${requiredCount}）`}
          </button>
        </div>

        <div className="lg:col-span-3 card overflow-hidden flex flex-col">
          <div className="flex flex-wrap items-center gap-2 px-4 py-3 border-b border-slate-700/60">
            <div className="inline-flex rounded-lg bg-slate-900 p-1 border border-slate-700">
              {([
                ['all', '全部'],
                ['saved', '已采'],
                ['pending', '待采'],
              ] as const).map(([id, label]) => (
                <button
                  key={id}
                  type="button"
                  onClick={() => setListStatus(id)}
                  className={clsx(
                    'px-3 py-1 rounded-md text-xs font-bold',
                    listStatus === id ? 'bg-slate-600 text-white' : 'text-slate-400',
                  )}
                >
                  {label}
                </button>
              ))}
            </div>
            <input
              className="input-field w-28 font-mono text-sm py-1.5"
              inputMode="numeric"
              placeholder="贝位号"
              value={bayQuery}
              onChange={e => setBayQuery(e.target.value.replace(/[^\d]/g, ''))}
            />
            <span className="text-xs text-slate-500 ml-auto">
              {visibleBays.length} 个贝位
            </span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="bg-slate-800/80 border-b border-slate-700/60">
                <tr>
                  {['贝位', 'RTK Lat', 'ΔLat', 'ΔLon', 'ΔAlt', '状态', ''].map(h => (
                    <th key={h} className="px-4 py-2.5 section-title whitespace-nowrap">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {pageBays.map(b => {
                  const saved = transformList.find(t => t.bay_no === b.bay_no);
                  const d = saved?.delta;
                  const isSelected = parseBayNo(bayNo) === b.bay_no;
                  return (
                    <tr
                      key={b.bay_no}
                      onClick={() => setBayNo(String(b.bay_no))}
                      className={clsx(
                        'border-b border-slate-700/40 cursor-pointer transition-colors',
                        isSelected ? 'bg-blue-900/25' : 'hover:bg-slate-700/20',
                      )}
                    >
                      <td className="px-4 py-2.5 font-mono font-bold text-white">{b.bay_no}</td>
                      <td className="px-4 py-2.5 font-mono text-xs text-slate-400">{b.lat.toFixed(7)}</td>
                      <td className="px-4 py-2.5 font-mono text-xs">{d?.lat?.toFixed(7) ?? '—'}</td>
                      <td className="px-4 py-2.5 font-mono text-xs">{d?.lon?.toFixed(7) ?? '—'}</td>
                      <td className="px-4 py-2.5 font-mono text-xs">{d?.alt?.toFixed(3) ?? '—'}</td>
                      <td className="px-4 py-2.5">
                        {saved ? (
                          <span className="text-emerald-400 text-xs">已采</span>
                        ) : (
                          <span className="text-slate-500 text-xs">待采</span>
                        )}
                      </td>
                      <td className="px-4 py-2.5">
                        {saved && (
                          <button
                            type="button"
                            onClick={e => {
                              e.stopPropagation();
                              handleDeleteBay(b.bay_no);
                            }}
                            className="text-xs text-slate-400 hover:text-red-300"
                          >
                            删除
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
                {pageBays.length === 0 && (
                  <tr><td colSpan={7} className="px-4 py-10 text-center text-slate-500">没有符合条件的贝位</td></tr>
                )}
              </tbody>
            </table>
          </div>
          <div className="flex items-center justify-between gap-3 px-4 py-2.5 border-t border-slate-700/60 text-xs text-slate-400">
            <span>第 {safePage + 1} / {pageCount} 页，每页 {PAGE_SIZE} 个</span>
            <div className="flex gap-2">
              <button
                type="button"
                disabled={safePage <= 0}
                onClick={() => setPage(safePage - 1)}
                className="px-3 py-1 rounded-md border border-slate-600 disabled:opacity-40"
              >
                上一页
              </button>
              <button
                type="button"
                disabled={safePage >= pageCount - 1}
                onClick={() => setPage(safePage + 1)}
                className="px-3 py-1 rounded-md border border-slate-600 disabled:opacity-40"
              >
                下一页
              </button>
            </div>
          </div>
        </div>
      </div>

      <div className="card p-5 space-y-4">
        <div>
          <h3 className="text-sm font-bold text-white">任意停留修正</h3>
          <p className="text-xs text-slate-400 mt-1">
            纠偏只落在记下的位置前后两个贝位之间。这两个贝位没有单独记下时，修正是 0，再往外的贝位保持 0。只打偏了一个点时，记这一点即可。中途再填的数，加在这个位置已经连出来的修正上。
            留空表示这个轮胎不再追加。负值朝场内（电气朝动力侧，动力朝电气侧），正值朝场外。表内是这一点的累计值。逐贝位采点不受影响。
          </p>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3 items-end">
          <label className="space-y-1">
            <span className="text-[11px] text-slate-400 block">对应轮胎</span>
            <select
              className="input-field"
              value={surveyTire}
              onChange={e => {
                setSurveyTire(e.target.value as (typeof LANE_FIELDS)[number]['key']);
                setSurveyDist({});
              }}
            >
              {LANE_FIELDS.map(field => (
                <option key={field.key} value={field.key}>{field.label}</option>
              ))}
            </select>
          </label>
          <label className="space-y-1">
            <span className="text-[11px] text-slate-400 block">实测点纬度</span>
            <input
              className="input-field font-mono"
              inputMode="decimal"
              placeholder="测量仪纬度"
              value={surveyLat}
              onChange={e => setSurveyLat(e.target.value)}
            />
          </label>
          <label className="space-y-1">
            <span className="text-[11px] text-slate-400 block">实测点经度</span>
            <input
              className="input-field font-mono"
              inputMode="decimal"
              placeholder="测量仪经度"
              value={surveyLon}
              onChange={e => setSurveyLon(e.target.value)}
            />
          </label>
          <button
            type="button"
            onClick={computeSurveyDistance}
            disabled={surveyBusy}
            className="btn-primary"
          >
            {surveyBusy ? '计算中…' : '计算到实测点'}
          </button>
        </div>
        <p className="text-[11px] text-slate-500">
          一次只算选中的那只轮胎。到实测点与偏差同一方向，场内为负。两个数一致时，用下面的厘米框改这只轮胎的报位。不一致时，到场地建图按当前贝位号重填该侧贝位点的经纬度。电气侧重填后，+Y 会按新坐标重算。
          {surveyDistNote ? ` ${surveyDistNote}` : ''}
        </p>
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          {LANE_FIELDS.map(field => {
            const liveM = liveData.gantry?.leg_offsets?.[field.live] ?? 0;
            const dist = field.key === surveyTire ? surveyDist[field.key] : undefined;
            const alongM = dist?.along_m;
            return (
              <label key={field.key} className="space-y-1">
                <span className="text-[11px] text-slate-400 block">{field.label}</span>
                <span className="text-[10px] text-slate-500 block">偏差 { (liveM * 100).toFixed(1) } cm</span>
                <span className="text-[10px] text-slate-300 block">
                  到实测点 { dist ? formatSignedCm(dist.lateral_m) : '—' }
                </span>
                {dist && alongM != null && Math.abs(alongM) >= 0.5 && (
                  <span className="text-[10px] text-amber-400/90 block">
                    实测点沿贝位偏了 {formatSignedCm(alongM)}，请采在这只轮胎旁边
                  </span>
                )}
                <input
                  className="input-field font-mono"
                  inputMode="decimal"
                  placeholder="再修正 cm"
                  value={laneCm[field.key]}
                  onChange={e => setLaneCm(prev => ({ ...prev, [field.key]: e.target.value }))}
                />
              </label>
            );
          })}
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <span className="text-xs text-slate-400 font-mono">
            当前位置沿贝位 { (liveData.local_coord?.y ?? 0).toFixed(2) } m
            {' · '}
            四轮胎中心贝位 { (liveData.current_bay_no ?? 0) > 0 ? liveData.current_bay_no : '不在贝位上' }
          </span>
          <button
            type="button"
            onClick={handleSaveLane}
            disabled={savingLane || liveData.status === 'no_signal'}
            className="btn-primary flex items-center gap-2"
          >
            {savingLane ? <Loader className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
            记录到全场
          </button>
        </div>
        {lanePoints.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-slate-500 text-left">
                  <th className="py-2 pr-3">沿贝位 m</th>
                  {LANE_FIELDS.map(field => (
                    <th key={field.key} className="py-2 pr-3">{field.label} cm</th>
                  ))}
                  <th className="py-2">操作</th>
                </tr>
              </thead>
              <tbody>
                {lanePoints.map(row => (
                  <tr key={row.id} className="border-t border-slate-700/40 text-slate-200 font-mono">
                    <td className="py-2 pr-3">{row.y_m.toFixed(1)}</td>
                    <td className="py-2 pr-3">{(row.elec1_m * 100).toFixed(1)}</td>
                    <td className="py-2 pr-3">{(row.elec2_m * 100).toFixed(1)}</td>
                    <td className="py-2 pr-3">{(row.diesel1_m * 100).toFixed(1)}</td>
                    <td className="py-2 pr-3">{(row.diesel2_m * 100).toFixed(1)}</td>
                    <td className="py-2">
                      <button
                        type="button"
                        onClick={() => handleDeleteLane(row)}
                        className="text-xs text-slate-400 hover:text-red-300"
                      >
                        删除
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

