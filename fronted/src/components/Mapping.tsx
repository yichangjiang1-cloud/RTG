import { useState, useRef, useEffect, useCallback } from 'react';
import { CheckCircle, Clock, Loader, AlertTriangle, RefreshCw, Upload, Plus, Pencil } from 'lucide-react';
import { logger } from '../utils/logger';
import { SectionHeader, showToast } from './common';
import { useAppContext } from '../context/AppContext';
import type { SurveyPoint, RealtimeData } from '../types';
import { clsx } from 'clsx';
import { httpBase } from '../utils/backendUrl';

type MappingMode = 'rtk_manual' | 'rtk_csv';

function parseBayNo(value: string): number | null {
  const n = parseInt(value.replace(/\D/g, ''), 10);
  return Number.isFinite(n) && n > 0 ? n : null;
}

function bayNoToApiId(bayNo: string): string {
  const n = parseBayNo(bayNo);
  return n != null ? String(n) : bayNo;
}

interface MappingProps {
  liveData: RealtimeData;
}

export default function Mapping({ liveData: _liveData }: MappingProps) {
  const { yards, selectedYardId, backendConfig, useMock, fetchYards, markYardMapped } = useAppContext();
  const selectedYard = yards.find(y => y.yard_id === selectedYardId) ?? null;

  const [mode, setMode] = useState<MappingMode>('rtk_manual');
  const [points, setPoints] = useState<SurveyPoint[]>([]);
  const [bayNo, setBayNo] = useState('1');
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

  const baseUrl = httpBase(backendConfig);

  // 加载历史打点记录
  const loadHistory = useCallback(() => {
    if (!yardId || useMock) return;
    const url = `${baseUrl}/api/survey/history?yard_id=${yardId}&point_type=mapping`;
    logger.info('Mapping', `GET /api/survey/history`, { yard_id: yardId });
    fetch(url, { signal: AbortSignal.timeout(5000) })
      .then(r => r.ok ? r.json() : null)
      .then((resp: { code: number; data: Array<{
        id: number; bay_id?: string; bay_no?: number; point_type?: string;
        lat: number; lon: number; alt: number;
        local_z?: number; created_at: string;
      }> } | null) => {
        if (resp?.code === 200 && Array.isArray(resp.data)) {
          const loaded: SurveyPoint[] = resp.data.map(p => ({
            id: String(p.id),
            bay_id: p.bay_id ?? (p.bay_no != null ? `Bay_${String(p.bay_no).padStart(2, '0')}` : ''),
            bay_no: p.bay_no ?? parseBayNo(p.bay_id ?? '') ?? undefined,
            yard_id: yardId,
            lat: p.lat,
            lon: p.lon,
            alt: p.alt,
            dz: p.local_z ?? 0,
            timestamp: p.created_at,
            synced: true,
          }));
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
        body: JSON.stringify({ vehicle_id: 'RTK_HANDHELD', yard_id: yardId, bay_id: apiBayId, mode: 'mapping', lat, lon, alt }),
      });
      if (res.ok) {
        markYardMapped(yardId);
        showToast(`贝位 ${bayNo} 打点成功`);
        loadHistory();
        fetchYards().catch(() => {});
        setManualLat('');
        setManualLon('');
        setManualAlt('0');
        advanceBayNo();
      } else {
        showToast(`贝位 ${bayNo} 打点提交失败`, 'error');
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
        body: JSON.stringify({ yard_id: yardId, mode: 'mapping', points: csvData }),
      });
      if (res.ok) {
        markYardMapped(yardId);
        showToast(`成功导入 ${csvData.length} 个贝位坐标`);
        setCsvData([]);
        loadHistory();
        fetchYards().catch(() => {});
      } else {
        showToast('导入失败', 'error');
      }
    } catch {
      showToast('导入失败，请检查后端连接', 'error');
    } finally {
      setImporting(false);
    }
  };

  const handleClearRtk = async () => {
    if (!yardId || useMock) return;
    if (!window.confirm(`确定清空「${selectedYard?.name}」的全部 RTK 真值并重新打点？\n已有 ${points.length} 个贝位将被删除，车辆校准数据需重新采集。`)) {
      return;
    }
    setClearing(true);
    try {
      const res = await fetch(`${baseUrl}/api/survey/rtk_points?yard_id=${encodeURIComponent(yardId)}`, {
        method: 'DELETE',
      });
      const body = await res.json().catch(() => ({}));
      if (res.ok && body.code === 200) {
        setPoints([]);
        setBayNo('1');
        await fetchYards();
        showToast('已清空 RTK 真值，可重新打点');
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
        subtitle={`手持 RTK 采集贝位真值（步骤①）。已录入 ${points.length} 个贝位${selectedYard.total_bays ? `，预估共 ${selectedYard.total_bays} 个` : ''}。可随时追加新贝位。`}
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
                已有 RTK 真值（{points.length} 个贝位）
              </p>
              <p className="text-xs text-emerald-200/60 mt-0.5">
                可继续录入新贝位或修改已有贝位（同编号会覆盖）。需全部重打时请点右侧按钮。
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={handleClearRtk}
            disabled={clearing}
            className="btn-secondary flex items-center gap-2 shrink-0 text-amber-300 border-amber-500/40 hover:border-amber-400"
          >
            <RefreshCw className={clsx('w-4 h-4', clearing && 'animate-spin')} />
            {clearing ? '清空中…' : '清空重新打点'}
          </button>
        </div>
      )}

      {/* ── 模式切换 ── */}
      <div className="flex flex-wrap gap-2">
        {([
          ['rtk_manual', 'RTK 手动录入', Pencil],
          ['rtk_csv', 'CSV 批量导入', Upload],
        ] as [MappingMode, string, typeof Pencil][]).map(([m, label, Icon]) => (
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
                  {submitting ? '提交中…' : `记录贝位 ${bayNo || '?'}`}
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
                      {importing ? '导入中…' : `确认导入 ${csvData.length} 条`}
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
        <div className="lg:col-span-3 card overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="bg-slate-700/30 border-b border-slate-700/60">
                <tr>
                  {['贝位编号', 'WGS84 坐标 (Lat / Lon)', '高度 (m)', 'Z 补偿 (m)', '采集时间', '状态'].map(h => (
                    <th key={h} className="px-4 py-3 section-title whitespace-nowrap">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {points.map(p => (
                  <tr key={p.id} className="border-b border-slate-700/40 hover:bg-slate-700/20 transition-colors">
                    <td className="px-4 py-3 font-mono font-bold text-white">{p.bay_no ?? parseBayNo(p.bay_id) ?? p.bay_id}</td>
                    <td className="px-4 py-3 font-mono text-slate-300 text-xs">{p.lat.toFixed(7)}, {p.lon.toFixed(7)}</td>
                    <td className="px-4 py-3 font-mono text-slate-400">{p.alt.toFixed(3)}</td>
                    <td className={`px-4 py-3 font-mono font-bold ${p.dz >= 0 ? 'text-blue-400' : 'text-amber-400'}`}>
                      {p.dz >= 0 ? '+' : ''}{p.dz.toFixed(4)}
                    </td>
                    <td className="px-4 py-3 text-slate-500 text-xs">
                      <div className="flex items-center gap-1">
                        <Clock className="w-3 h-3" />
                        {new Date(p.timestamp).toLocaleTimeString('zh-CN')}
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      {p.synced ? (
                        <span className="badge badge-fix"><CheckCircle className="w-3 h-3" />已同步</span>
                      ) : (
                        <span className="badge bg-amber-900/30 text-amber-400 border-amber-500/30">本地</span>
                      )}
                    </td>
                  </tr>
                ))}
                {points.length === 0 && (
                  <tr><td colSpan={6} className="px-4 py-12 text-center text-slate-500">暂无打点记录</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}
