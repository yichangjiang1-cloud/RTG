/**
 * RTG 头尾采点面板。
 * 点"开始采集"后，前端在约 10 秒内收集遥测里的两侧轮胎中点，取平均再提交。
 * 这 10 秒叫采集窗口：窗口越长，平均越稳，但大车要一直停着。
 */
import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { CheckCircle, Loader, MapPin, Pencil, RefreshCw } from 'lucide-react';
import { clsx } from 'clsx';
import { showToast } from './common';
import BayPointAdjust from './BayPointAdjust';
import type { RealtimeData, SurveyPoint, YardInfo } from '../types';
import { logger } from '../utils/logger';

const WINDOW_MS = 10_000;        // 采集窗口默认 10 秒
const MAX_SPEED_M_S = 0.05;      // 认为静止的速度上限
const MAX_STD_M = 0.05;          // 窗口内位置波动提示阈值（米）

type Role = 'head' | 'tail' | 'fix';

interface StatusSummary {
  ready?: boolean;
  bay_count?: number;
  bay_spacing_m?: number | null;
  head_tail_distance_m?: number | null;
  y_axis_bearing_deg?: number | null;
  ends?: {
    elec?: { head?: boolean; tail?: boolean };
    diesel?: { head?: boolean; tail?: boolean };
  };
  counts?: Record<string, number>;
  warnings?: string[];
  shift_limit_m?: number;
}

interface Sample {
  elec: { lat: number; lon: number; alt: number };
  diesel: { lat: number; lon: number; alt: number };
}

interface RtgSurveyPanelProps {
  yard: YardInfo;
  liveData: RealtimeData;
  vehicleId: string | null;
  backendBase: string;
  points: SurveyPoint[];
  onChanged: () => void;
}

function meanStd(samples: Sample[], side: 'elec' | 'diesel') {
  const n = samples.length;
  if (n === 0) return null;
  const lat = samples.reduce((s, x) => s + x[side].lat, 0) / n;
  const lon = samples.reduce((s, x) => s + x[side].lon, 0) / n;
  const alt = samples.reduce((s, x) => s + x[side].alt, 0) / n;
  // 用粗略米制估算平面标准差
  const mLat = 111_320;
  const mLon = 111_320 * Math.cos((lat * Math.PI) / 180);
  let acc = 0;
  for (const s of samples) {
    const dx = (s[side].lon - lon) * mLon;
    const dy = (s[side].lat - lat) * mLat;
    acc += dx * dx + dy * dy;
  }
  return { lat, lon, alt, std_m: Math.sqrt(acc / n) };
}

function sourceLabel(source?: string) {
  if (source === 'rtg_end') return '头尾';
  if (source === 'rtg_fix') return '重采';
  if (source === 'auto') return '自动';
  if (source === 'rtk') return 'RTK';
  return source || '—';
}

function sourceClass(source?: string) {
  if (source === 'rtg_end') return 'bg-emerald-600/30 text-emerald-200';
  if (source === 'rtg_fix') return 'bg-amber-600/30 text-amber-200';
  if (source === 'auto') return 'bg-slate-700/60 text-slate-300';
  return 'bg-blue-600/30 text-blue-200';
}

function fmtLycs(point?: SurveyPoint): string {
  if (point?.lycs_x == null || point?.lycs_y == null) return '—';
  if (!Number.isFinite(point.lycs_x) || !Number.isFinite(point.lycs_y)) return '—';
  return `X ${point.lycs_x.toFixed(3)}  Y ${point.lycs_y.toFixed(3)}`;
}

type AdjustLogRecord = {
  saved_at?: string;
  bay_no?: number;
  is_end_bay?: boolean;
  mode?: string;
  request_dx_m?: number;
  request_dy_m?: number;
  delta_lycs_m?: Record<string, { dx_m?: number | null; dy_m?: number | null }>;
  sides?: string[];
};

function fmtDeltaCm(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return '—';
  const cm = v * 100;
  return `${cm >= 0 ? '+' : ''}${cm.toFixed(1)} cm`;
}

export default function RtgSurveyPanel({
  yard, liveData, vehicleId, backendBase, points, onChanged,
}: RtgSurveyPanelProps) {
  const [status, setStatus] = useState<StatusSummary | null>(null);
  const [busy, setBusy] = useState(false);
  const [capturing, setCapturing] = useState(false);
  const [progress, setProgress] = useState(0);
  const [preview, setPreview] = useState<{
    role: Role;
    bayNo: number;
    elec: { lat: number; lon: number; alt: number; std_m: number };
    diesel: { lat: number; lon: number; alt: number; std_m: number };
    frame_count: number;
  } | null>(null);
  const [fixBay, setFixBay] = useState('2');
  const [editBay, setEditBay] = useState<number | null>(null);
  const [adjustLogs, setAdjustLogs] = useState<AdjustLogRecord[]>([]);
  const samplesRef = useRef<Sample[]>([]);
  const timerRef = useRef<number | null>(null);
  const startRef = useRef(0);
  const pendingRole = useRef<{ role: Role; bayNo: number } | null>(null);

  const yardId = yard.yard_id;
  const bayCount = yard.total_bays || status?.bay_count || 0;
  const midMin = 2;
  const midMax = Math.max(bayCount - 1, midMin);

  // 头尾采点完成后，重采默认从贝位 2 起；总数变化时把越界值收回范围内
  useEffect(() => {
    if (bayCount < 3) return;
    setFixBay(prev => {
      const n = parseInt(prev, 10);
      if (!Number.isFinite(n) || n < midMin || n > midMax) return String(midMin);
      return prev;
    });
  }, [bayCount, midMin, midMax]);

  const loadAdjustLogs = useCallback(async () => {
    try {
      const res = await fetch(
        `${backendBase}/api/survey/adjust-log?yard_id=${encodeURIComponent(yardId)}&end_only=true&limit=20`,
        { signal: AbortSignal.timeout(5000) },
      );
      const body = await res.json().catch(() => null);
      if (body?.code === 200 && Array.isArray(body?.data?.records)) {
        setAdjustLogs(body.data.records);
      }
    } catch (e) {
      logger.warn('RtgSurvey', '加载修正记录失败', e);
    }
  }, [backendBase, yardId]);

  useEffect(() => {
    loadAdjustLogs().catch(() => {});
  }, [loadAdjustLogs, points]);

  const loadStatus = useCallback(async () => {
    try {
      const res = await fetch(
        `${backendBase}/api/survey/rtg/status?yard_id=${encodeURIComponent(yardId)}`,
        { signal: AbortSignal.timeout(5000) },
      );
      const json = await res.json();
      if (json.code === 200) setStatus(json.data);
    } catch (e) {
      logger.warn('RtgSurvey', 'status 失败', e);
    }
  }, [backendBase, yardId]);

  useEffect(() => { loadStatus(); }, [loadStatus]);

  const center = liveData.gantry?.center_geo;
  const elecOk = Boolean(center?.elec?.valid);
  const dieselOk = Boolean(center?.diesel?.valid);
  const speedOk = Math.abs(liveData.speed ?? 0) <= MAX_SPEED_M_S;
  const inYard = Boolean(liveData.yard_id) && liveData.yard_id === yardId;
  const readyChecks = [
    { ok: Boolean(vehicleId), text: vehicleId ? `车辆 ${vehicleId}` : '请先选车' },
    { ok: elecOk && dieselOk, text: elecOk && dieselOk ? '两侧轮胎中点有效' : '两侧轮胎中点未就绪' },
    { ok: speedOk, text: speedOk ? `车速 ${(liveData.speed ?? 0).toFixed(3)} m/s` : `请停车（当前 ${(liveData.speed ?? 0).toFixed(3)} m/s）` },
    { ok: inYard, text: inYard ? `在场内 ${yardId}` : '大车不在本场地' },
  ];
  const canStart = readyChecks.every(c => c.ok) && !capturing && !busy;

  // 采集窗口内持续收样
  useEffect(() => {
    if (!capturing || !center?.elec?.valid || !center?.diesel?.valid) return;
    if (!speedOk) return;
    samplesRef.current.push({
      elec: { lat: center.elec.lat, lon: center.elec.lon, alt: center.elec.alt },
      diesel: { lat: center.diesel.lat, lon: center.diesel.lon, alt: center.diesel.alt },
    });
  }, [capturing, center, liveData.timestamp, speedOk]);

  const finishWindow = useCallback(() => {
    if (timerRef.current != null) {
      window.clearInterval(timerRef.current);
      timerRef.current = null;
    }
    setCapturing(false);
    setProgress(100);
    const samples = samplesRef.current;
    const roleInfo = pendingRole.current;
    pendingRole.current = null;
    if (!roleInfo) return;
    if (samples.length < 5) {
      showToast('采集窗口内有效帧太少，请重采', 'error');
      return;
    }
    const elec = meanStd(samples, 'elec');
    const diesel = meanStd(samples, 'diesel');
    if (!elec || !diesel) {
      showToast('无法计算平均值', 'error');
      return;
    }
    setPreview({
      role: roleInfo.role,
      bayNo: roleInfo.bayNo,
      elec,
      diesel,
      frame_count: samples.length,
    });
  }, []);

  const startCapture = (role: Role, bayNo: number) => {
    if (!canStart) {
      showToast(readyChecks.find(c => !c.ok)?.text || '条件未满足', 'error');
      return;
    }
    samplesRef.current = [];
    pendingRole.current = { role, bayNo };
    setPreview(null);
    setCapturing(true);
    setProgress(0);
    startRef.current = Date.now();
    timerRef.current = window.setInterval(() => {
      const elapsed = Date.now() - startRef.current;
      setProgress(Math.min(100, (elapsed / WINDOW_MS) * 100));
      if (elapsed >= WINDOW_MS) finishWindow();
    }, 100);
  };

  useEffect(() => () => {
    if (timerRef.current != null) window.clearInterval(timerRef.current);
  }, []);

  const submit = async (force = false) => {
    if (!preview || !vehicleId) return;
    setBusy(true);
    try {
      const res = await fetch(`${backendBase}/api/survey/rtg/capture`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          yard_id: yardId,
          vehicle_id: vehicleId,
          role: preview.role,
          bay_no: preview.role === 'fix' ? preview.bayNo : undefined,
          elec: { lat: preview.elec.lat, lon: preview.elec.lon, alt: preview.elec.alt },
          diesel: { lat: preview.diesel.lat, lon: preview.diesel.lon, alt: preview.diesel.alt },
          frame_count: preview.frame_count,
          std_m: Math.max(preview.elec.std_m, preview.diesel.std_m),
          force,
        }),
        signal: AbortSignal.timeout(15000),
      });
      const json = await res.json().catch(() => null);
      if (res.status === 409) {
        const ok = window.confirm(`${json?.detail || '需要确认'}\n\n强制保存吗？`);
        if (ok) {
          setBusy(false);
          await submit(true);
          return;
        }
        return;
      }
      if (!res.ok || json?.code !== 200) {
        throw new Error(json?.detail || json?.message || `HTTP ${res.status}`);
      }
      const warns = (json.data?.warnings || []) as string[];
      if (warns.length) showToast(warns[0], 'info');
      showToast(json.message || '已保存');
      setPreview(null);
      await loadStatus();
      onChanged();
    } catch (e) {
      showToast(e instanceof Error ? e.message : '保存失败', 'error');
    } finally {
      setBusy(false);
    }
  };

  const restore = async (bay: number) => {
    if (!window.confirm(`恢复贝位 ${bay} 为按头尾自动生成的位置？`)) return;
    setBusy(true);
    try {
      const res = await fetch(`${backendBase}/api/survey/rtg/restore_auto`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ yard_id: yardId, bay_no: bay }),
      });
      const json = await res.json().catch(() => null);
      if (!res.ok || json?.code !== 200) {
        throw new Error(json?.detail || json?.message || '恢复失败');
      }
      showToast(json.message || '已恢复');
      await loadStatus();
      onChanged();
    } catch (e) {
      showToast(e instanceof Error ? e.message : '恢复失败', 'error');
    } finally {
      setBusy(false);
    }
  };

  const rows = useMemo(() => {
    const map = new Map<number, { bay: number; elec?: SurveyPoint; diesel?: SurveyPoint }>();
    for (const p of points) {
      const bay = p.bay_no;
      if (bay == null) continue;
      const row = map.get(bay) ?? { bay };
      if (p.bay_side === 'diesel') row.diesel = p;
      else row.elec = p;
      map.set(bay, row);
    }
    return [...map.values()].sort((a, b) => a.bay - b.bay);
  }, [points]);

  // 重采辅助：当前位置相对已存贝位的平面差
  const fixHint = useMemo(() => {
    const n = parseInt(fixBay, 10);
    if (!Number.isFinite(n) || !center?.elec?.valid) return null;
    const saved = points.find(p => p.bay_no === n && p.bay_side !== 'diesel');
    if (!saved) return null;
    const mLat = 111_320;
    const mLon = 111_320 * Math.cos((center.elec.lat * Math.PI) / 180);
    const east = (center.elec.lon - saved.lon) * mLon;
    const north = (center.elec.lat - saved.lat) * mLat;
    return { bay: n, dist_m: Math.hypot(east, north), east, north };
  }, [fixBay, center, points]);

  const ends = status?.ends;
  const headDone = Boolean(ends?.elec?.head && ends?.diesel?.head);
  const tailDone = Boolean(ends?.elec?.tail && ends?.diesel?.tail);

  return (
    <div className="space-y-4">
      <div className="p-3 bg-cyan-900/10 border border-cyan-500/20 rounded-lg space-y-1">
        <p className="text-[10px] text-cyan-300 font-bold">RTG 头尾采点</p>
        <p className="text-[10px] text-cyan-200/70">
          先采 1 号（头）和 {bayCount || 'N'} 号（尾）。采头贝位时，堆场原点自动设为电气侧轮胎中点；
          头→尾作为 +Y，中间贝位按间距等分自动生成。偏差大的贝位再单独重采，只改该贝位，邻居不动。
        </p>
        <p className="text-[10px] text-cyan-200/50">
          采集窗口 {WINDOW_MS / 1000} 秒：这段时间里前端持续收大车两侧轮胎中点，取平均作为坐标。
          车要一直停着，波动太大请重采。
        </p>
      </div>

      <div className="card p-4 space-y-2">
        <div className="flex items-center justify-between">
          <span className="section-title">就绪检查</span>
          <button type="button" onClick={loadStatus} className="text-slate-500 hover:text-white">
            <RefreshCw className="w-3.5 h-3.5" />
          </button>
        </div>
        {readyChecks.map(c => (
          <div key={c.text} className={clsx('text-xs', c.ok ? 'text-emerald-400' : 'text-amber-400')}>
            {c.ok ? '✓' : '·'} {c.text}
          </div>
        ))}
        {!center && (
          <p className="text-[10px] text-amber-300">
            中控尚未上报两侧中点经纬度。请确认中控已升级到支持 center_geo 的版本。
          </p>
        )}
      </div>

      <div className="card p-4 space-y-3">
        <div className="grid grid-cols-2 gap-2">
          <button
            type="button"
            disabled={!canStart}
            onClick={() => startCapture('head', 1)}
            className="py-3 rounded-xl font-bold text-sm bg-emerald-600 hover:bg-emerald-500 disabled:opacity-40 text-white"
          >
            {headDone ? '重采头贝位 1' : '采集头贝位 1'}
          </button>
          <button
            type="button"
            disabled={!canStart || bayCount < 2}
            onClick={() => startCapture('tail', bayCount)}
            className="py-3 rounded-xl font-bold text-sm bg-emerald-600 hover:bg-emerald-500 disabled:opacity-40 text-white"
          >
            {tailDone ? `重采尾贝位 ${bayCount}` : `采集尾贝位 ${bayCount || '?'}`}
          </button>
        </div>

        {capturing && (
          <div>
            <div className="h-2 bg-slate-800 rounded-full overflow-hidden">
              <div className="h-full bg-cyan-500 transition-all" style={{ width: `${progress}%` }} />
            </div>
            <p className="text-[10px] text-slate-400 mt-1">
              采集中… 已收 {samplesRef.current.length} 帧，请保持停车
            </p>
          </div>
        )}

        {preview && (
          <div className="p-3 bg-slate-900/70 border border-slate-700 rounded-lg space-y-2">
            <p className="text-xs text-slate-200 font-bold">
              预览 · {preview.role === 'head' ? '头' : preview.role === 'tail' ? '尾' : '重采'}贝位 {preview.bayNo}
              （{preview.frame_count} 帧）
            </p>
            {(['elec', 'diesel'] as const).map(side => {
              const s = preview[side];
              const noisy = s.std_m > MAX_STD_M;
              return (
                <div key={side} className="text-[10px] font-mono text-slate-400">
                  {side === 'elec' ? '电气' : '动力'} {s.lat.toFixed(7)}, {s.lon.toFixed(7)}, {s.alt.toFixed(3)} m
                  <span className={noisy ? ' text-amber-400' : ' text-slate-500'}>
                    {' '}σ={ (s.std_m * 100).toFixed(1) } cm
                  </span>
                </div>
              );
            })}
            <div className="flex gap-2">
              <button
                type="button"
                disabled={busy}
                onClick={() => submit(false)}
                className="flex-1 py-2 rounded-lg bg-blue-600 hover:bg-blue-500 text-white text-sm font-bold disabled:opacity-50 flex items-center justify-center gap-1"
              >
                {busy ? <Loader className="w-4 h-4 animate-spin" /> : <CheckCircle className="w-4 h-4" />}
                确认提交
              </button>
              <button type="button" onClick={() => setPreview(null)} className="px-3 py-2 rounded-lg border border-slate-600 text-slate-400 text-sm">
                取消
              </button>
            </div>
          </div>
        )}

        {status?.ready && (
          <div className="text-[10px] text-slate-400 space-y-0.5">
            <div>头尾距离 {(status.head_tail_distance_m ?? 0).toFixed(2)} m</div>
            <div>贝位间距 {(status.bay_spacing_m ?? 0).toFixed(3)} m（头尾距离 ÷ {Math.max(bayCount - 1, 1)}）</div>
            <div>+Y 方位角 {(status.y_axis_bearing_deg ?? 0).toFixed(1)}°</div>
            <div>
              点数：头尾 {status.counts?.rtg_end ?? 0}，自动 {status.counts?.auto ?? 0}，重采 {status.counts?.rtg_fix ?? 0}
            </div>
            {(status.warnings || []).map(w => (
              <div key={w} className="text-amber-400">⚠ {w}</div>
            ))}
          </div>
        )}
      </div>

      <div className="card p-4 space-y-3">
        <div className="section-title">修正贝位（重采 / 手填 / XY 平移）</div>
        <p className="text-[10px] text-slate-500">
          大车重采只用于中间贝位 {midMin}～{bayCount >= 3 ? midMax : 'N−1'}（只改该贝，邻居不动）。
          头/尾用「修正」手填或 XY 平移时：会重钉 +Y，并按新头尾等分重算「自动」中间贝（已「重采」的中间贝不会被覆盖）。
        </p>
        <div className="flex gap-2 items-center">
          <input
            type="number"
            min={midMin}
            max={midMax}
            step={1}
            className="input-field font-mono w-24"
            value={fixBay}
            onChange={e => {
              const v = e.target.value;
              if (v === '' || /^\d+$/.test(v)) setFixBay(v);
            }}
            disabled={!status?.ready}
          />
          <button
            type="button"
            disabled={!canStart || !status?.ready}
            onClick={() => {
              const n = parseInt(fixBay, 10);
              if (!Number.isFinite(n) || n < midMin || n > midMax) {
                showToast(`请输入 ${midMin}～${midMax}`, 'error');
                return;
              }
              startCapture('fix', n);
            }}
            className="flex-1 py-2 rounded-lg bg-amber-600 hover:bg-amber-500 disabled:opacity-40 text-white text-sm font-bold flex items-center justify-center gap-1"
          >
            <MapPin className="w-4 h-4" />
            开始重采
          </button>
        </div>
        {fixHint && (
          <p className="text-[10px] text-slate-400">
            当前电气侧中点相对贝位 {fixHint.bay} 已存位置约 {fixHint.dist_m.toFixed(2)} m
            （东 {fixHint.east.toFixed(2)}，北 {fixHint.north.toFixed(2)}）
          </p>
        )}
      </div>

      <div className="card p-0 overflow-hidden">
        <div className="px-4 py-2 border-b border-slate-800 section-title">贝位列表</div>
        <div className="max-h-80 overflow-y-auto">
          <table className="w-full text-left">
            <thead className="text-[10px] text-slate-500 sticky top-0 bg-slate-900">
              <tr>
                <th className="px-3 py-2">贝位</th>
                <th className="px-3 py-2">来源</th>
                <th className="px-3 py-2">电气侧 WGS84 / LYCS</th>
                <th className="px-3 py-2">动力侧 WGS84 / LYCS</th>
                <th className="px-3 py-2" />
              </tr>
            </thead>
            <tbody>
              {rows.map(row => {
                const src = row.elec?.source || row.diesel?.source;
                const editing = editBay === row.bay;
                return (
                  <Fragment key={row.bay}>
                    <tr className="border-t border-slate-800/80 text-xs">
                      <td className="px-3 py-2 font-mono text-slate-200">{row.bay}</td>
                      <td className="px-3 py-2">
                        <span className={clsx('px-1.5 py-0.5 rounded text-[10px]', sourceClass(src))}>
                          {sourceLabel(src)}
                        </span>
                      </td>
                      <td className="px-3 py-2 font-mono text-[10px] text-slate-400">
                        {row.elec ? (
                          <div className="leading-tight space-y-0.5">
                            <div>{row.elec.lat.toFixed(7)}, {row.elec.lon.toFixed(7)}</div>
                            <div className="text-sky-300/90">LYCS {fmtLycs(row.elec)} m</div>
                          </div>
                        ) : '—'}
                      </td>
                      <td className="px-3 py-2 font-mono text-[10px] text-slate-400">
                        {row.diesel ? (
                          <div className="leading-tight space-y-0.5">
                            <div>{row.diesel.lat.toFixed(7)}, {row.diesel.lon.toFixed(7)}</div>
                            <div className="text-sky-300/90">LYCS {fmtLycs(row.diesel)} m</div>
                          </div>
                        ) : '—'}
                      </td>
                      <td className="px-3 py-2 text-right space-x-2 whitespace-nowrap">
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => setEditBay(editing ? null : row.bay)}
                          className="inline-flex items-center gap-0.5 text-[10px] text-sky-300 hover:text-sky-200"
                        >
                          <Pencil className="w-3 h-3" />
                          {editing ? '收起' : '修正'}
                        </button>
                        {src === 'rtg_fix' && (
                          <button
                            type="button"
                            disabled={busy}
                            onClick={() => restore(row.bay)}
                            className="text-[10px] text-amber-300 hover:text-amber-200"
                          >
                            恢复自动
                          </button>
                        )}
                      </td>
                    </tr>
                    {editing && (
                      <tr className="border-t border-slate-800/40">
                        <td colSpan={5} className="px-3 pb-3">
                          <BayPointAdjust
                            backendBase={backendBase}
                            yardId={yardId}
                            bayNo={row.bay}
                            bayCount={bayCount}
                            elec={row.elec}
                            diesel={row.diesel}
                            defaultSide="both"
                            allowBoth
                            onClose={() => setEditBay(null)}
                            onApplied={() => {
                              onChanged();
                              loadStatus().catch(() => {});
                              loadAdjustLogs().catch(() => {});
                            }}
                            onDone={() => {
                              setEditBay(null);
                              onChanged();
                              loadStatus().catch(() => {});
                              loadAdjustLogs().catch(() => {});
                            }}
                          />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
              {rows.length === 0 && (
                <tr>
                  <td colSpan={5} className="px-3 py-6 text-center text-slate-500 text-xs">
                    还没有贝位点，请先采头贝位和尾贝位
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <div className="card p-0 overflow-hidden">
        <div className="px-4 py-2 border-b border-slate-800 flex items-center justify-between gap-2">
          <span className="section-title">头尾贝修正记录</span>
          <button
            type="button"
            onClick={() => loadAdjustLogs().catch(() => {})}
            className="inline-flex items-center gap-1 text-[10px] text-slate-400 hover:text-slate-200"
          >
            <RefreshCw className="w-3 h-3" />
            刷新
          </button>
        </div>
        <div className="max-h-48 overflow-y-auto px-3 py-2 space-y-2">
          {adjustLogs.length === 0 && (
            <p className="text-[10px] text-slate-500 py-3 text-center">
              尚无头/尾修正记录（手填或 XY 平移后会落盘）
            </p>
          )}
          {adjustLogs.map((r, i) => {
            const sides = r.sides?.length ? r.sides : Object.keys(r.delta_lycs_m || {});
            const modeLabel = r.mode === 'nudge' ? '平移' : r.mode === 'set' ? '手填' : (r.mode || '—');
            return (
              <div
                key={`${r.saved_at || ''}-${r.bay_no}-${i}`}
                className="rounded-lg bg-slate-900/50 px-3 py-2 text-[10px] text-slate-400 space-y-0.5"
              >
                <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
                  <span className="font-mono text-slate-200">
                    {r.saved_at || '—'} · 贝位 {r.bay_no ?? '—'} · {modeLabel}
                  </span>
                  {r.mode === 'nudge' && (
                    <span className="font-mono text-slate-500">
                      请求 ΔX {fmtDeltaCm(r.request_dx_m)} ΔY {fmtDeltaCm(r.request_dy_m)}
                    </span>
                  )}
                </div>
                {sides.map((side) => {
                  const d = r.delta_lycs_m?.[side];
                  return (
                    <div key={side} className="font-mono text-sky-300/90">
                      {side === 'diesel' ? '动力' : '电气'}：
                      ΔX {fmtDeltaCm(d?.dx_m)}，ΔY {fmtDeltaCm(d?.dy_m)}
                    </div>
                  );
                })}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
