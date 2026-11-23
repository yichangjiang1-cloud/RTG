/**
 * 贝位点修正：手动写经纬度，或沿堆场 X/Y 平移。
 * 首尾贝建图、逐贝位建图共用。
 */
import { useEffect, useState } from 'react';
import { ArrowDown, ArrowLeft, ArrowRight, ArrowUp, Loader, Save } from 'lucide-react';
import { showToast } from './common';
import type { SurveyPoint } from '../types';

type Side = 'elec' | 'diesel' | 'both';

interface BayPointAdjustProps {
  backendBase: string;
  yardId: string;
  bayNo: number;
  /** 场地贝位总数；用于判断是否头/尾贝 */
  bayCount?: number;
  elec?: SurveyPoint | null;
  diesel?: SurveyPoint | null;
  defaultSide?: Side;
  /** RTG 场默认两侧一起改；RTK 场常选一侧 */
  allowBoth?: boolean;
  /** 保存手填坐标后：关闭并刷新 */
  onDone: () => void;
  /** 方向键平移后：只刷新，保持面板打开 */
  onApplied?: () => void;
  onClose?: () => void;
}

type AdjustResult = {
  ok: boolean;
  status: number;
  message: string;
  forceNeeded: boolean;
  data?: {
    warnings?: string[];
    generated?: { created?: number; updated?: number; removed?: number };
    origin_updated?: boolean;
    updates?: Record<string, { lat: number; lon: number; alt: number }>;
  };
};

function fmtCoord(n: number | undefined | null, digits = 8): string {
  if (n == null || !Number.isFinite(n)) return '';
  return n.toFixed(digits);
}

async function postAdjust(
  backendBase: string,
  body: Record<string, unknown>,
): Promise<AdjustResult> {
  const res = await fetch(`${backendBase}/api/survey/points/adjust`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const json = await res.json().catch(() => ({})) as {
    detail?: string;
    message?: string;
    data?: AdjustResult['data'];
  };
  const detail = typeof json.detail === 'string' ? json.detail : json.message || '';
  if (res.status === 409) {
    return { ok: false, status: 409, message: detail || '位移较大，需要确认', forceNeeded: true };
  }
  if (!res.ok) {
    return { ok: false, status: res.status, message: detail || `HTTP ${res.status}`, forceNeeded: false };
  }
  return {
    ok: true,
    status: 200,
    message: json.message || '已保存',
    forceNeeded: false,
    data: json.data,
  };
}

export default function BayPointAdjust({
  backendBase,
  yardId,
  bayNo,
  bayCount,
  elec,
  diesel,
  defaultSide = 'both',
  allowBoth = true,
  onDone,
  onApplied,
  onClose,
}: BayPointAdjustProps) {
  const isEndBay = bayNo === 1 || (bayCount != null && bayCount > 0 && bayNo === bayCount);
  const initialSide: Side = allowBoth
    ? defaultSide
    : (defaultSide === 'diesel' ? 'diesel' : 'elec');
  const [side, setSide] = useState<Side>(initialSide);
  const [step, setStep] = useState('0.05');
  const [busy, setBusy] = useState(false);

  const seed = side === 'diesel' ? diesel : elec;
  const [lat, setLat] = useState(seed ? fmtCoord(seed.lat) : '');
  const [lon, setLon] = useState(seed ? fmtCoord(seed.lon) : '');
  const [alt, setAlt] = useState(seed ? String(seed.alt ?? 0) : '0');
  const [elecLat, setElecLat] = useState(elec ? fmtCoord(elec.lat) : '');
  const [elecLon, setElecLon] = useState(elec ? fmtCoord(elec.lon) : '');
  const [elecAlt, setElecAlt] = useState(elec ? String(elec.alt ?? 0) : '0');
  const [dslLat, setDslLat] = useState(diesel ? fmtCoord(diesel.lat) : '');
  const [dslLon, setDslLon] = useState(diesel ? fmtCoord(diesel.lon) : '');
  const [dslAlt, setDslAlt] = useState(diesel ? String(diesel.alt ?? 0) : '0');

  // 列表刷新后同步表单（方向键平移不关面板）
  useEffect(() => {
    if (elec) {
      setElecLat(fmtCoord(elec.lat));
      setElecLon(fmtCoord(elec.lon));
      setElecAlt(String(elec.alt ?? 0));
    }
    if (diesel) {
      setDslLat(fmtCoord(diesel.lat));
      setDslLon(fmtCoord(diesel.lon));
      setDslAlt(String(diesel.alt ?? 0));
    }
    const p = side === 'diesel' ? diesel : elec;
    if (side !== 'both' && p) {
      setLat(fmtCoord(p.lat));
      setLon(fmtCoord(p.lon));
      setAlt(String(p.alt ?? 0));
    }
  }, [side, elec, diesel]);

  const applyLocalUpdates = (
    updates?: Record<string, { lat: number; lon: number; alt: number }>,
  ) => {
    if (!updates) return;
    const e = updates.elec;
    const d = updates.diesel;
    if (e) {
      setElecLat(fmtCoord(e.lat));
      setElecLon(fmtCoord(e.lon));
      setElecAlt(String(e.alt ?? 0));
      if (side === 'elec') {
        setLat(fmtCoord(e.lat));
        setLon(fmtCoord(e.lon));
        setAlt(String(e.alt ?? 0));
      }
    }
    if (d) {
      setDslLat(fmtCoord(d.lat));
      setDslLon(fmtCoord(d.lon));
      setDslAlt(String(d.alt ?? 0));
      if (side === 'diesel') {
        setLat(fmtCoord(d.lat));
        setLon(fmtCoord(d.lon));
        setAlt(String(d.alt ?? 0));
      }
    }
  };

  const run = async (body: Record<string, unknown>, opts: { close: boolean }) => {
    setBusy(true);
    try {
      let result = await postAdjust(backendBase, body);
      if (result.forceNeeded) {
        if (!window.confirm(`${result.message}\n\n确认强制保存？`)) return;
        result = await postAdjust(backendBase, { ...body, force: true });
      }
      if (!result.ok) {
        showToast(result.message, 'error');
        return;
      }
      const warnings = result.data?.warnings?.filter(Boolean) ?? [];
      showToast(result.message);
      if (warnings.length) showToast(warnings.join('；'));
      applyLocalUpdates(result.data?.updates);
      if (opts.close) onDone();
      else (onApplied ?? onDone)();
    } catch {
      showToast('修正失败，请检查后端连接', 'error');
    } finally {
      setBusy(false);
    }
  };

  const saveManual = async () => {
    if (side === 'both') {
      const ela = parseFloat(elecLat);
      const elo = parseFloat(elecLon);
      const ealt = parseFloat(elecAlt) || 0;
      const dla = parseFloat(dslLat);
      const dlo = parseFloat(dslLon);
      const dalt = parseFloat(dslAlt) || 0;
      if (![ela, elo, dla, dlo].every(Number.isFinite)) {
        showToast('请填写两侧有效经纬度', 'error');
        return;
      }
      await run({
        yard_id: yardId,
        bay_no: bayNo,
        bay_side: 'both',
        mode: 'set',
        elec: { lat: ela, lon: elo, alt: ealt },
        diesel: { lat: dla, lon: dlo, alt: dalt },
      }, { close: true });
      return;
    }
    const la = parseFloat(lat);
    const lo = parseFloat(lon);
    const al = parseFloat(alt) || 0;
    if (!Number.isFinite(la) || !Number.isFinite(lo)) {
      showToast('请填写有效经纬度', 'error');
      return;
    }
    await run({
      yard_id: yardId,
      bay_no: bayNo,
      bay_side: side,
      mode: 'set',
      lat: la,
      lon: lo,
      alt: al,
    }, { close: true });
  };

  const nudge = async (dx: number, dy: number) => {
    const hasTarget =
      side === 'both' ? Boolean(elec || diesel) : side === 'diesel' ? Boolean(diesel) : Boolean(elec);
    if (!hasTarget) {
      showToast('该侧还没有点，请先采点或手动写入', 'error');
      return;
    }
    let baySide: Side = side;
    if (side === 'both') {
      if (elec && diesel) baySide = 'both';
      else if (elec) baySide = 'elec';
      else baySide = 'diesel';
    }
    await run({
      yard_id: yardId,
      bay_no: bayNo,
      bay_side: baySide,
      mode: 'nudge',
      dx_m: dx,
      dy_m: dy,
    }, { close: false });
  };

  const stepM = Math.abs(parseFloat(step)) || 0.05;

  return (
    <div className="mt-2 p-3 rounded-lg border border-slate-700 bg-slate-900/80 space-y-3">
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs font-bold text-slate-200">修正贝位 {bayNo}</p>
        {onClose && (
          <button type="button" onClick={onClose} className="text-[10px] text-slate-500 hover:text-slate-300">
            收起
          </button>
        )}
      </div>

      <div className="inline-flex rounded-lg bg-slate-950 p-1 border border-slate-700">
        {(allowBoth
          ? ([['both', '两侧'], ['elec', '电气'], ['diesel', '动力']] as const)
          : ([['elec', '电气'], ['diesel', '动力']] as const)
        ).map(([id, label]) => (
          <button
            key={id}
            type="button"
            onClick={() => setSide(id)}
            className={
              side === id
                ? 'px-2.5 py-1 rounded-md text-[10px] font-bold bg-slate-600 text-white'
                : 'px-2.5 py-1 rounded-md text-[10px] text-slate-400'
            }
          >
            {label}
          </button>
        ))}
      </div>

      <div className="space-y-2">
        <p className="text-[10px] text-slate-500">
          手动写经纬度（保存后关闭）。
          {isEndBay
            ? (bayNo === 1
              ? '改头贝：会更新原点、重钉 +Y，并重算所有「自动」中间贝。'
              : '改尾贝：会重钉 +Y，并重算所有「自动」中间贝。')
            : '改中间贝：只改本贝位，不会动其它点。'}
        </p>
        {side === 'both' ? (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            <div className="space-y-1">
              <span className="text-[10px] text-emerald-400">电气侧</span>
              <input className="input-field font-mono text-xs py-1" value={elecLat} onChange={e => setElecLat(e.target.value)} placeholder="lat" />
              <input className="input-field font-mono text-xs py-1" value={elecLon} onChange={e => setElecLon(e.target.value)} placeholder="lon" />
              <input className="input-field font-mono text-xs py-1" value={elecAlt} onChange={e => setElecAlt(e.target.value)} placeholder="alt" />
            </div>
            <div className="space-y-1">
              <span className="text-[10px] text-orange-400">动力侧</span>
              <input className="input-field font-mono text-xs py-1" value={dslLat} onChange={e => setDslLat(e.target.value)} placeholder="lat" />
              <input className="input-field font-mono text-xs py-1" value={dslLon} onChange={e => setDslLon(e.target.value)} placeholder="lon" />
              <input className="input-field font-mono text-xs py-1" value={dslAlt} onChange={e => setDslAlt(e.target.value)} placeholder="alt" />
            </div>
          </div>
        ) : (
          <div className="grid grid-cols-3 gap-2">
            <input className="input-field font-mono text-xs py-1" value={lat} onChange={e => setLat(e.target.value)} placeholder="lat" />
            <input className="input-field font-mono text-xs py-1" value={lon} onChange={e => setLon(e.target.value)} placeholder="lon" />
            <input className="input-field font-mono text-xs py-1" value={alt} onChange={e => setAlt(e.target.value)} placeholder="alt" />
          </div>
        )}
        <button
          type="button"
          disabled={busy}
          onClick={saveManual}
          className="w-full py-2 rounded-lg bg-slate-700 hover:bg-slate-600 text-white text-xs font-bold flex items-center justify-center gap-1 disabled:opacity-40"
        >
          {busy ? <Loader className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
          保存坐标
        </button>
      </div>

      <div className="space-y-2 border-t border-slate-800 pt-2">
        <p className="text-[10px] text-slate-500">
          沿堆场坐标平移（+X 右，+Y 沿贝位）。点一下移一步。
          {isEndBay
            ? '改头/尾时中间「自动」点会一起重算。'
            : '中间贝只平移本点。'}
        </p>
        <label className="flex items-center gap-2 text-[10px] text-slate-400">
          <span className="shrink-0">每步距离</span>
          <input
            type="number"
            step="0.01"
            min="0.01"
            className="input-field font-mono w-28 text-sm py-1.5 tracking-wide"
            value={step}
            onChange={e => setStep(e.target.value)}
          />
          <span className="shrink-0 text-slate-300">m / 次</span>
        </label>
        <div className="flex flex-col items-center gap-1">
          <button
            type="button"
            disabled={busy}
            onClick={() => nudge(0, stepM)}
            className="p-2 rounded-lg bg-slate-800 border border-slate-600 text-slate-200 hover:border-blue-400 disabled:opacity-40"
            title={`+Y ${stepM} m`}
          >
            <ArrowUp className="w-4 h-4" />
          </button>
          <div className="flex items-center gap-1">
            <button
              type="button"
              disabled={busy}
              onClick={() => nudge(-stepM, 0)}
              className="p-2 rounded-lg bg-slate-800 border border-slate-600 text-slate-200 hover:border-blue-400 disabled:opacity-40"
              title={`-X ${stepM} m`}
            >
              <ArrowLeft className="w-4 h-4" />
            </button>
            <span className="min-w-[4.5rem] px-1 text-center font-mono text-[10px] text-slate-400">
              {stepM}m/次
            </span>
            <button
              type="button"
              disabled={busy}
              onClick={() => nudge(stepM, 0)}
              className="p-2 rounded-lg bg-slate-800 border border-slate-600 text-slate-200 hover:border-blue-400 disabled:opacity-40"
              title={`+X ${stepM} m`}
            >
              <ArrowRight className="w-4 h-4" />
            </button>
          </div>
          <button
            type="button"
            disabled={busy}
            onClick={() => nudge(0, -stepM)}
            className="p-2 rounded-lg bg-slate-800 border border-slate-600 text-slate-200 hover:border-blue-400 disabled:opacity-40"
            title={`-Y ${stepM} m`}
          >
            <ArrowDown className="w-4 h-4" />
          </button>
        </div>
      </div>
    </div>
  );
}
