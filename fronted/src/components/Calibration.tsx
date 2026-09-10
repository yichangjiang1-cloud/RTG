import { useState, useEffect, useCallback } from 'react';
import { Zap, CheckCircle2, AlertTriangle, Map, RefreshCw, Plus, Loader } from 'lucide-react';
import { SectionHeader, showToast, InfoRow } from './common';
import { useAppContext } from '../context/AppContext';
import type { RealtimeData } from '../types';
import { clsx } from 'clsx';
import { logger } from '../utils/logger';
import { httpBase } from '../utils/backendUrl';

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

function parseBayNo(value: string): number | null {
  const n = parseInt(value.replace(/\D/g, ''), 10);
  return Number.isFinite(n) && n > 0 ? n : null;
}

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

  const baseUrl = httpBase(backendConfig);
  const yardId = selectedYard?.yard_id ?? '';

  const fetchRtkFromHistory = async (): Promise<RtkBay[]> => {
    const histRes = await fetch(
      `${baseUrl}/api/survey/history?yard_id=${encodeURIComponent(yardId)}&point_type=mapping`,
      { signal: AbortSignal.timeout(5000) },
    );
    const histJson = await histRes.json();
    if (histJson.code !== 200 || !Array.isArray(histJson.data)) return [];
    return histJson.data
      .filter((p: { bay_no?: number | null }) => p.bay_no != null)
      .map((p: { bay_no: number; lat: number; lon: number; alt: number }) => ({
        bay_no: p.bay_no,
        bay_id: String(p.bay_no),
        lat: p.lat,
        lon: p.lon,
        alt: p.alt,
      }));
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
        survey_points?: Array<{ bay_no: number | null; lat: number; lon: number; alt: number }>;
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

      let bays: RtkBay[] = (d.survey_points ?? [])
        .filter(p => p.bay_no != null)
        .map(p => ({
          bay_no: p.bay_no as number,
          bay_id: String(p.bay_no),
          lat: p.lat,
          lon: p.lon,
          alt: p.alt,
        }));

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
            <InfoRow label="GNSS" value={liveData.status === 'fix' ? 'RTK FIXED' : 'FLOAT'} mono={false} />
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

        <div className="lg:col-span-3 card overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="bg-slate-700/30 border-b border-slate-700/60">
                <tr>
                  {['贝位', 'RTK Lat', 'ΔLat', 'ΔLon', 'ΔAlt', '状态'].map(h => (
                    <th key={h} className="px-4 py-3 section-title whitespace-nowrap">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rtkBays.map(b => {
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
                      <td className="px-4 py-3 font-mono font-bold text-white">{b.bay_no}</td>
                      <td className="px-4 py-3 font-mono text-xs text-slate-400">{b.lat.toFixed(7)}</td>
                      <td className="px-4 py-3 font-mono text-xs">{d?.lat?.toFixed(7) ?? '—'}</td>
                      <td className="px-4 py-3 font-mono text-xs">{d?.lon?.toFixed(7) ?? '—'}</td>
                      <td className="px-4 py-3 font-mono text-xs">{d?.alt?.toFixed(3) ?? '—'}</td>
                      <td className="px-4 py-3">
                        {saved ? (
                          <span className="text-emerald-400 text-xs">已采</span>
                        ) : (
                          <span className="text-slate-500 text-xs">待采</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}

