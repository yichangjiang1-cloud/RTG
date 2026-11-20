import { Fragment, useState, useRef, useEffect, useCallback } from 'react';
import { CheckCircle, Loader, AlertTriangle, RefreshCw, Upload, Plus, Pencil, Truck } from 'lucide-react';
import { logger } from '../utils/logger';
import { SectionHeader, showToast } from './common';
import { useAppContext } from '../context/AppContext';
import type { SurveyPoint, RealtimeData, SurveyPointSource } from '../types';
import { clsx } from 'clsx';
import { httpBase } from '../utils/backendUrl';
import BayPointAdjust from './BayPointAdjust';
import RtgSurveyPanel from './RtgSurveyPanel';

type MappingMode = 'rtk_manual' | 'rtk_csv' | 'rtg';

function parseBayNo(value: string): number | null {
  const n = parseInt(value.replace(/\D/g, ''), 10);
  return Number.isFinite(n) && n > 0 ? n : null;
}

function bayNoToApiId(bayNo: string): string {
  const n = parseBayNo(bayNo);
  return n != null ? String(n) : bayNo;
}

type BaySide = 'elec' | 'diesel';

function sideName(side: BaySide): string {
  return side === 'diesel' ? '动力侧' : '电气侧';
}

const PAGE_SIZE = 10;

interface BayRow {
  bay: number;
  elec?: SurveyPoint;
  diesel?: SurveyPoint;
}

function rowsByBay(points: SurveyPoint[]): BayRow[] {
  const map = new Map<number, BayRow>();
  for (const point of points) {
    const bay = point.bay_no ?? parseBayNo(point.bay_id);
    if (bay == null) continue;
    const row = map.get(bay) ?? { bay };
    if (point.bay_side === 'diesel') row.diesel = point;
    else row.elec = point;
    map.set(bay, row);
  }
  return [...map.values()].sort((a, b) => a.bay - b.bay);
}

function PointCell({ point }: { point?: SurveyPoint }) {
  if (!point) return <span className="text-xs text-slate-600">未采集</span>;
  const sourceTag =
    point.source === 'auto' ? '自动'
      : point.source === 'rtg_end' ? '头尾'
        : point.source === 'rtg_fix' ? '重采'
          : point.source === 'rtk' ? 'RTK' : '';
  return (
    <div className="leading-tight">
      <div className="font-mono text-xs text-slate-200">{point.lat.toFixed(7)}, {point.lon.toFixed(7)}</div>
      <div className="mt-0.5 font-mono text-[10px] text-slate-500">
        {point.alt.toFixed(3)} m{sourceTag ? ` · ${sourceTag}` : ''}
      </div>
    </div>
  );
}

interface MappingProps {
  liveData: RealtimeData;
}

export default function Mapping({ liveData }: MappingProps) {
  const {
    yards, selectedYardId, selectedVehicleId, backendConfig, useMock, fetchYards, markYardMapped,
  } = useAppContext();
  const selectedYard = yards.find(y => y.yard_id === selectedYardId) ?? null;
  const isRtgYard = selectedYard?.survey_mode === 'rtg_ends';

  const [mode, setMode] = useState<MappingMode>(isRtgYard ? 'rtg' : 'rtk_manual');
  const [points, setPoints] = useState<SurveyPoint[]>([]);
  const [bayNo, setBayNo] = useState('1');
  const [baySide, setBaySide] = useState<BaySide>('elec');
  const [listSide, setListSide] = useState<'all' | BaySide>('all');
  const [bayQuery, setBayQuery] = useState('');
  const [page, setPage] = useState(0);
  const [editBay, setEditBay] = useState<number | null>(null);
  const yardId = selectedYard?.yard_id ?? '';
  const [clearing, setClearing] = useState(false);

  // RTK 手动录入表单
  const [manualLat, setManualLat] = useState('');
  const [manualLon, setManualLon] = useState('');
  const [manualAlt, setManualAlt] = useState('0');
  const [submitting, setSubmitting] = useState(false);

  // CSV 导入
  const [csvData, setCsvData] = useState<Array<{ bay_id: string; lat: number; lon: number; alt: number }>>([]);
  const [importing, setImporting] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [axisMode, setAxisMode] = useState<'fit' | 'two_point'>('fit');
  const [axisSource, setAxisSource] = useState<'bays' | 'manual'>('bays');
  const [axisFromBay, setAxisFromBay] = useState('');
  const [axisToBay, setAxisToBay] = useState('');
  const [axisFromLat, setAxisFromLat] = useState('');
  const [axisFromLon, setAxisFromLon] = useState('');
  const [axisToLat, setAxisToLat] = useState('');
  const [axisToLon, setAxisToLon] = useState('');
  const [savingAxis, setSavingAxis] = useState(false);

  const baseUrl = httpBase(backendConfig);

  useEffect(() => {
    const mode = selectedYard?.y_axis_mode === 'two_point' ? 'two_point' : 'fit';
    const spec = selectedYard?.y_axis_points;
    setAxisMode(mode);
    setAxisSource(spec?.source === 'manual' ? 'manual' : 'bays');
    setAxisFromBay(spec?.from_bay != null ? String(spec.from_bay) : '');
    setAxisToBay(spec?.to_bay != null ? String(spec.to_bay) : '');
    setAxisFromLat(spec?.from?.lat != null ? String(spec.from.lat) : '');
    setAxisFromLon(spec?.from?.lon != null ? String(spec.from.lon) : '');
    setAxisToLat(spec?.to?.lat != null ? String(spec.to.lat) : '');
    setAxisToLon(spec?.to?.lon != null ? String(spec.to.lon) : '');
  }, [selectedYard?.yard_id, selectedYard?.y_axis_mode, selectedYard?.y_axis_points]);

  useEffect(() => {
    setMode(isRtgYard ? 'rtg' : 'rtk_manual');
  }, [selectedYard?.yard_id, isRtgYard]);

  const saveAxis = async () => {
    if (!yardId) return;
    const body: Record<string, unknown> = { yard_id: yardId, mode: axisMode };
    if (axisMode === 'two_point') {
      body.source = axisSource;
      if (axisSource === 'bays') {
        const fromBay = parseBayNo(axisFromBay);
        const toBay = parseBayNo(axisToBay);
        if (!fromBay || !toBay) { showToast('请选择两个已采集的贝位'); return; }
        if (fromBay === toBay) { showToast('两个贝位不能相同'); return; }
        body.from_bay = fromBay;
        body.to_bay = toBay;
      } else {
        const nums = [axisFromLat, axisFromLon, axisToLat, axisToLon].map(Number);
        if (nums.some(n => !Number.isFinite(n))) { showToast('请填写两个点的纬度和经度'); return; }
        body.from_lat = nums[0];
        body.from_lon = nums[1];
        body.to_lat = nums[2];
        body.to_lon = nums[3];
      }
    }
    setSavingAxis(true);
    try {
      const res = await fetch(`${baseUrl}/api/yard/y_axis`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const json = await res.json().catch(() => ({})) as { detail?: string; message?: string };
      if (!res.ok) throw new Error(typeof json.detail === 'string' ? json.detail : '保存失败');
      await fetchYards();
      showToast(axisMode === 'fit' ? '已改为全部贝位拟合 +Y' : '已用两点确定 +Y');
    } catch (e) {
      const message = e instanceof Error ? e.message : '保存 Y 轴失败';
      logger.error('Mapping', '保存 Y 轴失败', message);
      showToast(message, 'error');
    } finally {
      setSavingAxis(false);
    }
  };

  // 加载历史打点记录
  const loadHistory = useCallback(() => {
    if (!yardId || useMock) return;
    const url = `${baseUrl}/api/survey/history?yard_id=${yardId}&point_type=mapping`;
    logger.info('Mapping', `GET /api/survey/history`, { yard_id: yardId });
    fetch(url, { signal: AbortSignal.timeout(5000) })
      .then(r => r.ok ? r.json() : null)
      .then((resp: { code: number; data: Array<{
        id: number; bay_id?: string; bay_no?: number; bay_side?: string; point_type?: string;
        lat: number; lon: number; alt: number;
        local_z?: number; created_at: string;
        source?: string; vehicle_id?: string | null; std_m?: number | null;
      }> } | null) => {
        if (resp?.code === 200 && Array.isArray(resp.data)) {
          const loaded: SurveyPoint[] = resp.data.map(p => ({
            id: String(p.id),
            bay_id: p.bay_id ?? (p.bay_no != null ? `Bay_${String(p.bay_no).padStart(2, '0')}` : ''),
            bay_no: p.bay_no ?? parseBayNo(p.bay_id ?? '') ?? undefined,
            bay_side: p.bay_side === 'diesel' ? 'diesel' : 'elec',
            yard_id: yardId,
            lat: p.lat,
            lon: p.lon,
            alt: p.alt,
            dz: p.local_z ?? 0,
            timestamp: p.created_at,
            synced: true,
            source: (p.source as SurveyPointSource | undefined) || 'rtk',
            vehicle_id: p.vehicle_id ?? null,
            std_m: p.std_m ?? null,
          }));
          loaded.sort((a, b) => {
            const sideOrder = (a.bay_side === 'diesel' ? 1 : 0) - (b.bay_side === 'diesel' ? 1 : 0);
            if (sideOrder !== 0) return sideOrder;
            return (a.bay_no ?? 0) - (b.bay_no ?? 0);
          });
          setPoints(loaded);
          logger.info('Mapping', `历史记录加载完成，共 ${loaded.length} 条`);
          if (loaded.length > 0) {
            markYardMapped(yardId);
            const last = loaded[loaded.length - 1];
            const lastNo = last.bay_no ?? parseBayNo(last.bay_id);
            if (lastNo != null) setBayNo(String(lastNo + 1));
          }
        }
      })
      .catch(e => logger.warn('Mapping', '历史记录加载失败', e));
  }, [yardId, useMock, baseUrl, markYardMapped]);

  useEffect(() => { loadHistory(); }, [loadHistory]);

  const advanceBayNo = () => {
    const n = parseBayNo(bayNo);
    if (n != null) setBayNo(String(n + 1));
  };

  // ── 模式 1: RTK 手动录入 ──
  const handleManualSubmit = async () => {
    if (!parseBayNo(bayNo)) { showToast('请输入有效的贝位编号（正整数）'); return; }
    if (!manualLat.trim() || !manualLon.trim()) { showToast('请填写纬度和经度'); return; }
    setSubmitting(true);
    const apiBayId = bayNoToApiId(bayNo);
    const lat = parseFloat(manualLat);
    const lon = parseFloat(manualLon);
    const alt = parseFloat(manualAlt) || 0;
    try {
      const res = await fetch(`${baseUrl}/api/survey/confirm`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          vehicle_id: 'RTK_HANDHELD', yard_id: yardId, bay_id: apiBayId,
          bay_side: baySide, mode: 'mapping', lat, lon, alt,
        }),
      });
      const body = await res.json().catch(() => ({})) as { detail?: string; data?: { warnings?: string[] } };
      if (res.ok) {
        markYardMapped(yardId);
        const warnings = body.data?.warnings?.filter(Boolean) ?? [];
        showToast(warnings.length
          ? `${sideName(baySide)}贝位 ${bayNo} 已保存。${warnings.join('；')}`
          : `${sideName(baySide)}贝位 ${bayNo} 打点成功`);
        loadHistory();
        fetchYards().catch(() => {});
        setManualLat('');
        setManualLon('');
        setManualAlt('0');
        advanceBayNo();
      } else {
        showToast(body.detail || `贝位 ${bayNo} 打点提交失败`, 'error');
      }
    } catch {
      showToast('提交失败，请检查后端连接', 'error');
    } finally {
      setSubmitting(false);
    }
  };

  // ── 模式 2: CSV 导入 ──
  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (ev) => {
      const text = ev.target?.result as string;
      const lines = text.trim().split('\n');
      const parsed: Array<{ bay_id: string; lat: number; lon: number; alt: number }> = [];
      for (let i = 0; i < lines.length; i++) {
        const line = lines[i].trim();
        if (!line || line.startsWith('#')) continue;
        const lower = line.toLowerCase();
        if (lower.startsWith('bay') || lower.startsWith('bay_no') || lower.startsWith('贝位')) continue;
        const parts = line.split(/[,\t;]+/).map(s => s.trim());
        if (parts.length < 3) continue;
        const latIdx = parts.length >= 4 ? 1 : 0;
        const bayRaw = parts.length >= 4 ? parts[0] : String(i + 1);
        const bayNum = parseBayNo(bayRaw);
        if (bayNum == null) continue;
        parsed.push({
          bay_id: String(bayNum),
          lat: parseFloat(parts[latIdx]),
          lon: parseFloat(parts[latIdx + 1]),
          alt: parts.length > latIdx + 2 ? parseFloat(parts[latIdx + 2]) || 0 : 0,
        });
      }
      setCsvData(parsed);
      showToast(`已解析 ${parsed.length} 条记录，请确认后提交`);
    };
    reader.readAsText(file);
    e.target.value = '';
  };

  const handleCsvImport = async () => {
    if (csvData.length === 0) { showToast('无数据可导入'); return; }
    setImporting(true);
    try {
      const res = await fetch(`${baseUrl}/api/survey/batch_import`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ yard_id: yardId, mode: 'mapping', bay_side: baySide, points: csvData }),
      });
      const body = await res.json().catch(() => ({})) as { detail?: string; data?: { warnings?: string[] } };
      if (res.ok) {
        markYardMapped(yardId);
        const warnings = body.data?.warnings?.filter(Boolean) ?? [];
        showToast(warnings.length
          ? `已导入 ${csvData.length} 个${sideName(baySide)}贝位。${warnings.join('；')}`
          : `成功导入 ${csvData.length} 个${sideName(baySide)}贝位坐标`);
        setCsvData([]);
        loadHistory();
        fetchYards().catch(() => {});
      } else {
        showToast(body.detail || '导入失败', 'error');
      }
    } catch {
      showToast('导入失败，请检查后端连接', 'error');
    } finally {
      setImporting(false);
    }
  };

  const handleClearRtk = async (side: BaySide | 'all') => {
    if (!yardId || useMock) return;
    const scope = side === 'all' ? '电气侧和动力侧全部' : sideName(side);
    const count = side === 'all' ? points.length : points.filter(p => (p.bay_side ?? 'elec') === side).length;
    const keep = side === 'all' ? '两侧都会删除。' : '另一侧保留。';
    if (!window.confirm(`确定清空「${selectedYard?.name}」的${scope} RTK 真值？\n将删除 ${count} 个点。${keep}清空电气侧后，依赖电气侧真值的车辆校准需要重新采集。`)) {
      return;
    }
    setClearing(true);
    try {
      const sideQuery = side === 'all' ? '' : `&bay_side=${side}`;
      const res = await fetch(`${baseUrl}/api/survey/rtk_points?yard_id=${encodeURIComponent(yardId)}${sideQuery}`, {
        method: 'DELETE',
      });
      const body = await res.json().catch(() => ({}));
      if (res.ok && body.code === 200) {
        if (side === 'all') {
          setPoints([]);
          setBayNo('1');
        } else {
          loadHistory();
        }
        await fetchYards();
        showToast(`已清空${scope}`);
      } else {
        showToast(body.detail ?? body.message ?? '清空失败', 'error');
      }
    } catch {
      showToast('清空失败，请检查后端连接', 'error');
    } finally {
      setClearing(false);
    }
  };

  const unsyncedCount = points.filter(p => !p.synced).length;
  const elecCount = points.filter(p => (p.bay_side ?? 'elec') === 'elec').length;
  const dieselCount = points.filter(p => p.bay_side === 'diesel').length;
  const bayRows = rowsByBay(points).filter((row) => {
    if (listSide === 'elec' && !row.elec) return false;
    if (listSide === 'diesel' && !row.diesel) return false;
    const query = bayQuery.trim();
    return !query || String(row.bay).includes(query);
  });
  const pageCount = Math.max(1, Math.ceil(bayRows.length / PAGE_SIZE));
  const safePage = Math.min(page, pageCount - 1);
  const pageRows = bayRows.slice(safePage * PAGE_SIZE, safePage * PAGE_SIZE + PAGE_SIZE);

  useEffect(() => { setPage(0); }, [listSide, bayQuery, yardId]);

  // ── 未选择场地 ──
  if (!selectedYard) {
    return (
      <div className="animate-fade-in flex items-center justify-center min-h-[60vh]">
        <div className="card p-12 max-w-md w-full text-center space-y-4">
          <AlertTriangle className="w-12 h-12 text-amber-400 mx-auto" />
          <h2 className="text-xl font-bold text-white">未选择作业场地</h2>
          <p className="text-slate-400 text-sm">请前往「工作配置」页面选择当前场地后，再进行场地建图。</p>
        </div>
      </div>
    );
  }

  const hasRtkData = points.length > 0 || selectedYard.mapped;

  return (
    <div className="animate-fade-in space-y-5">
      <SectionHeader
        title={`场地建图 · ${selectedYard.name}`}
        subtitle={`手持 RTK 采集贝位真值。电气侧 ${elecCount} 个，动力侧 ${dieselCount} 个。同一贝位号两侧各保留一个点。+Y 和当前贝位号按电气侧。`}
      >
        {unsyncedCount > 0 && (
          <span className="text-[11px] text-amber-400 bg-amber-900/30 border border-amber-500/30 px-3 py-1.5 rounded-lg">
            {unsyncedCount} 个贝位待同步
          </span>
        )}
      </SectionHeader>

      {hasRtkData && (
        <div className="card p-4 flex flex-col sm:flex-row sm:items-center justify-between gap-3 border border-emerald-500/30 bg-emerald-900/10">
          <div className="flex items-start gap-3">
            <CheckCircle className="w-5 h-5 text-emerald-400 shrink-0 mt-0.5" />
            <div>
              <p className="text-sm font-bold text-emerald-300">
                已有 RTK 真值（电气 {elecCount}，动力 {dieselCount}）
              </p>
              <p className="text-xs text-emerald-200/60 mt-0.5">
                同一侧、同一贝位号会覆盖。先导入电气侧，再导入动力侧，两侧互不覆盖。
              </p>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            {([
              ['elec', '清空电气侧'],
              ['diesel', '清空动力侧'],
              ['all', '清空全部'],
            ] as const).map(([id, label]) => (
              <button
                key={id}
                type="button"
                onClick={() => handleClearRtk(id)}
                disabled={clearing}
                className="btn-secondary flex items-center gap-2 shrink-0 text-amber-300 border-amber-500/40 hover:border-amber-400"
              >
                <RefreshCw className={clsx('w-4 h-4', clearing && 'animate-spin')} />
                {label}
              </button>
            ))}
          </div>
        </div>
      )}

      {!isRtgYard && (
      <div className="card p-4">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="min-w-0">
            <p className="text-sm font-bold text-white">Y 轴正方向</p>
            <p className="text-xs text-slate-500 mt-0.5">
              只用电气侧。+Y 沿贝位号增大，朝 +Y 看时右侧为 +X。
            </p>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <div className="inline-flex rounded-lg bg-slate-900 p-1 border border-slate-700">
              {([
                ['fit', '全部拟合'],
                ['two_point', '两点确定'],
              ] as const).map(([id, label]) => (
                <button
                  key={id}
                  type="button"
                  onClick={() => setAxisMode(id)}
                  className={clsx(
                    'px-3 py-1.5 rounded-md text-xs font-bold transition-colors',
                    axisMode === id ? 'bg-blue-600 text-white' : 'text-slate-400 hover:text-slate-200',
                  )}
                >
                  {label}
                </button>
              ))}
            </div>
            <button
              type="button"
              onClick={saveAxis}
              disabled={savingAxis}
              className="px-4 py-1.5 rounded-lg text-xs font-bold bg-blue-600 hover:bg-blue-500 text-white disabled:opacity-50"
            >
              {savingAxis ? '保存中…' : '保存'}
            </button>
          </div>
        </div>
        {axisMode === 'two_point' && (
          <div className="mt-3 pt-3 border-t border-slate-700/70 space-y-3">
            <div className="inline-flex rounded-lg bg-slate-900 p-1 border border-slate-700">
              {([
                ['bays', '电气侧贝位'],
                ['manual', '手填坐标'],
              ] as const).map(([id, label]) => (
                <button
                  key={id}
                  type="button"
                  onClick={() => setAxisSource(id)}
                  className={clsx(
                    'px-3 py-1 rounded-md text-xs font-bold',
                    axisSource === id ? 'bg-slate-600 text-white' : 'text-slate-400',
                  )}
                >
                  {label}
                </button>
              ))}
            </div>
            {axisSource === 'bays' ? (
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 max-w-xl">
                {([
                  ['起点', axisFromBay, setAxisFromBay],
                  ['+Y 终点', axisToBay, setAxisToBay],
                ] as const).map(([label, value, setValue]) => (
                  <label key={label} className="block">
                    <span className="text-[10px] text-slate-500">{label}</span>
                    <select
                      className="input-field font-mono text-sm py-1.5"
                      value={value}
                      onChange={e => setValue(e.target.value)}
                    >
                      <option value="">选择贝位</option>
                      {points
                        .filter(p => (p.bay_side ?? 'elec') === 'elec')
                        .map(p => p.bay_no ?? parseBayNo(p.bay_id))
                        .filter((n): n is number => n != null)
                        .filter((n, i, arr) => arr.indexOf(n) === i)
                        .sort((a, b) => a - b)
                        .map(n => (
                          <option key={n} value={n}>贝位 {n}</option>
                        ))}
                    </select>
                  </label>
                ))}
              </div>
            ) : (
              <div className="grid grid-cols-2 gap-3 max-w-xl">
                <label className="block">
                  <span className="text-[10px] text-slate-500">起点纬度</span>
                  <input className="input-field font-mono text-sm py-1.5" value={axisFromLat} onChange={e => setAxisFromLat(e.target.value)} />
                </label>
                <label className="block">
                  <span className="text-[10px] text-slate-500">起点经度</span>
                  <input className="input-field font-mono text-sm py-1.5" value={axisFromLon} onChange={e => setAxisFromLon(e.target.value)} />
                </label>
                <label className="block">
                  <span className="text-[10px] text-slate-500">+Y 纬度</span>
                  <input className="input-field font-mono text-sm py-1.5" value={axisToLat} onChange={e => setAxisToLat(e.target.value)} />
                </label>
                <label className="block">
                  <span className="text-[10px] text-slate-500">+Y 经度</span>
                  <input className="input-field font-mono text-sm py-1.5" value={axisToLon} onChange={e => setAxisToLon(e.target.value)} />
                </label>
              </div>
            )}
          </div>
        )}
      </div>
      )}

      {/* ── 模式切换 ── */}
      <div className="flex flex-wrap gap-2">
        {(isRtgYard
          ? ([['rtg', 'RTG 头尾采点', Truck]] as [MappingMode, string, typeof Truck][])
          : ([
              ['rtk_manual', 'RTK 手动录入', Pencil],
              ['rtk_csv', 'CSV 批量导入', Upload],
            ] as [MappingMode, string, typeof Pencil][])
        ).map(([m, label, Icon]) => (
          <button
            key={m}
            onClick={() => setMode(m)}
            className={clsx(
              'flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm font-bold border transition-all',
              mode === m
                ? 'bg-blue-600 border-blue-500 text-white shadow-lg shadow-blue-900/30'
                : 'bg-slate-800 border-slate-700 text-slate-400 hover:border-slate-500',
            )}
          >
            <Icon className="w-4 h-4" />
            {label}
          </button>
        ))}
      </div>

      {mode === 'rtg' && selectedYard && (
        <RtgSurveyPanel
          yard={selectedYard}
          liveData={liveData}
          vehicleId={selectedVehicleId}
          backendBase={baseUrl}
          points={points}
          onChanged={() => {
            loadHistory();
            fetchYards().catch(() => {});
            markYardMapped(yardId);
          }}
        />
      )}

      {mode !== 'rtg' && (
      <div className="grid grid-cols-1 lg:grid-cols-4 gap-5">
        {/* ── 左侧控制面板 ── */}
        <div className="space-y-4">
          {/* 堆场信息 */}
          <div className="card p-5 space-y-4">
            <div>
              <label className="section-title block mb-1.5">堆场 ID</label>
              <input className="input-field font-mono" value={yardId} readOnly />
            </div>
            <div>
              <label className="section-title block mb-1.5">贝位侧</label>
              <div className="grid grid-cols-2 gap-2">
                {([
                  ['elec', '电气侧'],
                  ['diesel', '动力侧'],
                ] as const).map(([id, label]) => (
                  <button
                    key={id}
                    type="button"
                    onClick={() => setBaySide(id)}
                    className={clsx(
                      'py-2 rounded-lg text-sm font-bold border',
                      baySide === id
                        ? id === 'elec'
                          ? 'bg-emerald-600/30 border-emerald-400 text-emerald-100'
                          : 'bg-orange-600/30 border-orange-400 text-orange-100'
                        : 'bg-slate-900 border-slate-700 text-slate-400',
                    )}
                  >
                    {label}
                  </button>
                ))}
              </div>
              <p className="text-[10px] text-slate-500 mt-1">
                手动录入和 CSV 都记到当前这一侧。编号两侧一一对应，例如电气 5 和动力 5 是同一个贝位。
              </p>
            </div>
            <div>
              <label className="section-title block mb-1.5">贝位编号</label>
              <input
                type="number"
                min={1}
                step={1}
                inputMode="numeric"
                className="input-field font-mono text-2xl text-blue-400 py-3"
                value={bayNo}
                onChange={e => {
                  const v = e.target.value;
                  if (v === '' || /^\d+$/.test(v)) setBayNo(v);
                }}
                disabled={mode === 'rtk_csv'}
                placeholder="1"
              />
              <p className="text-[10px] text-slate-500 mt-1">仅填数字，如 1、2、40（与 Bay_01 等价）</p>
            </div>

            {/* ── RTK 手动录入模式 ── */}
            {mode === 'rtk_manual' && (
              <div className="space-y-3">
                <div className="p-3 bg-amber-900/10 border border-amber-500/20 rounded-lg">
                  <p className="text-[10px] text-amber-300 font-bold mb-1">RTK 手动录入模式</p>
                  <p className="text-[10px] text-amber-200/60">手持 RTK 设备到达贝位后，将读数填入下方表单提交。</p>
                </div>
                <div>
                  <span className="text-[10px] text-slate-500">纬度 Lat</span>
                  <input type="number" step="0.0000001" className="input-field font-mono text-sm py-1.5" value={manualLat} onChange={e => setManualLat(e.target.value)} placeholder="30.1234567" />
                </div>
                <div>
                  <span className="text-[10px] text-slate-500">经度 Lon</span>
                  <input type="number" step="0.0000001" className="input-field font-mono text-sm py-1.5" value={manualLon} onChange={e => setManualLon(e.target.value)} placeholder="120.6543210" />
                </div>
                <div>
                  <span className="text-[10px] text-slate-500">高程 Alt (m)</span>
                  <input type="number" step="0.01" className="input-field font-mono text-sm py-1.5" value={manualAlt} onChange={e => setManualAlt(e.target.value)} placeholder="5.0" />
                </div>
                <button
                  onClick={handleManualSubmit}
                  disabled={submitting}
                  className="w-full py-3.5 rounded-xl font-bold text-sm bg-amber-600 hover:bg-amber-500 text-white shadow-lg shadow-amber-900/30 flex items-center justify-center gap-2 disabled:opacity-50"
                >
                  {submitting ? <Loader className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
                  {submitting ? '提交中…' : `记录${sideName(baySide)}贝位 ${bayNo || '?'}`}
                </button>
              </div>
            )}

            {/* ── CSV 导入模式 ── */}
            {mode === 'rtk_csv' && (
              <div className="space-y-3">
                <div className="p-3 bg-violet-900/10 border border-violet-500/20 rounded-lg">
                  <p className="text-[10px] text-violet-300 font-bold mb-1">CSV 批量导入模式</p>
                  <p className="text-[10px] text-violet-200/60">
                    上传 CSV 文件，格式：每行一个贝位，字段用逗号分隔。
                  </p>
                  <p className="text-[10px] text-violet-200/60 mt-1">
                    文件格式不变。当前会导入到{sideName(baySide)}，不会改掉另一侧。
                  </p>
                  <p className="text-[10px] text-violet-200/40 font-mono mt-1">bay_no, lat, lon, alt</p>
                  <p className="text-[10px] text-violet-200/40 font-mono">1, 30.123456, 120.654321, 5.2</p>
                </div>
                <input ref={fileInputRef} type="file" accept=".csv,.txt" onChange={handleFileSelect} className="hidden" />
                <button
                  onClick={() => fileInputRef.current?.click()}
                  className="w-full py-3 rounded-xl font-bold text-sm border-2 border-dashed border-slate-600 hover:border-violet-500 text-slate-400 hover:text-violet-400 flex items-center justify-center gap-2 transition-colors"
                >
                  <Upload className="w-4 h-4" />
                  选择 CSV 文件
                </button>
                {csvData.length > 0 && (
                  <>
                    <div className="p-3 bg-slate-900/60 rounded-lg border border-slate-700/40 max-h-40 overflow-y-auto">
                      <p className="text-[10px] text-slate-400 mb-2">已解析 {csvData.length} 条：</p>
                      {csvData.map((p, i) => (
                        <p key={i} className="text-[10px] font-mono text-slate-500">
                          {p.bay_id}: {p.lat.toFixed(7)}, {p.lon.toFixed(7)}, {p.alt.toFixed(2)}
                        </p>
                      ))}
                    </div>
                    <button
                      onClick={handleCsvImport}
                      disabled={importing}
                      className="w-full py-3.5 rounded-xl font-bold text-sm bg-violet-600 hover:bg-violet-500 text-white shadow-lg shadow-violet-900/30 flex items-center justify-center gap-2 disabled:opacity-50"
                    >
                      {importing ? <Loader className="w-4 h-4 animate-spin" /> : <Upload className="w-4 h-4" />}
                      {importing ? '导入中…' : `导入到${sideName(baySide)} ${csvData.length} 条`}
                    </button>
                  </>
                )}
              </div>
            )}
          </div>

          {/* 统计 */}
          <div className="card p-4 grid grid-cols-2 gap-3">
            <div className="text-center">
              <div className="text-2xl font-bold text-blue-400">{points.length}</div>
              <div className="section-title mt-1">已打点</div>
            </div>
            <div className="text-center">
              <div className="text-2xl font-bold text-emerald-400">{points.filter(p => p.synced).length}</div>
              <div className="section-title mt-1">已同步</div>
            </div>
          </div>
        </div>

        {/* ── 右侧数据表格 ── */}
        <div className="lg:col-span-3 card overflow-hidden flex flex-col">
          <div className="flex flex-wrap items-center gap-2 px-4 py-3 border-b border-slate-700/60">
            <div className="inline-flex rounded-lg bg-slate-900 p-1 border border-slate-700">
              {([
                ['all', '全部'],
                ['elec', '电气侧'],
                ['diesel', '动力侧'],
              ] as const).map(([id, label]) => (
                <button
                  key={id}
                  type="button"
                  onClick={() => setListSide(id)}
                  className={clsx(
                    'px-3 py-1 rounded-md text-xs font-bold',
                    listSide === id ? 'bg-slate-600 text-white' : 'text-slate-400',
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
              {bayRows.length} 个贝位号
            </span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="bg-slate-800/80 border-b border-slate-700/60">
                <tr>
                  {['贝位', '电气侧', '动力侧', '操作'].map(h => (
                    <th key={h} className="px-4 py-2.5 section-title whitespace-nowrap">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {pageRows.map(row => {
                  const editing = editBay === row.bay;
                  return (
                    <Fragment key={row.bay}>
                      <tr className="border-b border-slate-700/40 hover:bg-slate-700/20">
                        <td className="px-4 py-2.5 font-mono font-bold text-white align-top">{row.bay}</td>
                        <td className="px-4 py-2.5 align-top"><PointCell point={row.elec} /></td>
                        <td className="px-4 py-2.5 align-top"><PointCell point={row.diesel} /></td>
                        <td className="px-4 py-2.5 align-top">
                          <button
                            type="button"
                            onClick={() => setEditBay(editing ? null : row.bay)}
                            className="inline-flex items-center gap-1 text-xs text-sky-300 hover:text-sky-200"
                          >
                            <Pencil className="w-3.5 h-3.5" />
                            {editing ? '收起' : '修正'}
                          </button>
                        </td>
                      </tr>
                      {editing && (
                        <tr className="border-b border-slate-700/40 bg-slate-900/40">
                          <td colSpan={4} className="px-4 pb-3">
                            <BayPointAdjust
                              backendBase={baseUrl}
                              yardId={yardId}
                              bayNo={row.bay}
                              elec={row.elec}
                              diesel={row.diesel}
                              defaultSide={baySide}
                              allowBoth
                              onClose={() => setEditBay(null)}
                              onApplied={() => {
                                loadHistory();
                                fetchYards().catch(() => {});
                              }}
                              onDone={() => {
                                setEditBay(null);
                                loadHistory();
                                fetchYards().catch(() => {});
                              }}
                            />
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
                {pageRows.length === 0 && (
                  <tr><td colSpan={4} className="px-4 py-10 text-center text-slate-500">没有符合条件的贝位</td></tr>
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
      )}
    </div>
  );
}
