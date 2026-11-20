import { useState, useEffect } from 'react';
import { Car, Map, CheckCircle2, AlertTriangle, Wifi, WifiOff, Loader2, Edit2, X, Save, Zap, RefreshCw, Settings2, Plus } from 'lucide-react';
import { SectionHeader, showToast } from './common';
import { useAppContext } from '../context/AppContext';
import { clsx } from 'clsx';
import { normalizeYardIdInput, validateYardId, PLC_YARD_ID_MAX_LEN } from '../utils/yardId';
import { httpBase } from '../utils/backendUrl';
import { logger } from '../utils/logger';
import type { YardInfo } from '../types';

function boundaryToText(boundary: { lat: number; lon: number }[] | undefined): string {
  return (boundary ?? []).map(p => `${p.lat},${p.lon}`).join('\n');
}

// ── 新建 / 编辑场地 ──────────────────────────────────
function YardFormModal({ backendBase, yard, onClose, onDone }: {
  backendBase: string;
  yard: YardInfo | null;
  onClose: () => void;
  onDone: () => void;
}) {
  const editing = yard !== null;
  const [yardId, setYardId] = useState(yard?.yard_id ?? '');
  const [name, setName] = useState(yard?.name ?? '');
  const [originLat, setOriginLat] = useState(yard ? String(yard.origin_lat) : '');
  const [originLon, setOriginLon] = useState(yard ? String(yard.origin_lon) : '');
  const [originAlt, setOriginAlt] = useState(yard ? String(yard.origin_alt ?? 0) : '0');
  const [bayCount, setBayCount] = useState(yard ? String(yard.total_bays) : '40');
  const [halfWidth, setHalfWidth] = useState(yard ? String(yard.bay_half_width_m ?? 0.3) : '0.30');
  const [surveyMode, setSurveyMode] = useState<'rtk' | 'rtg_ends'>(
    yard?.survey_mode === 'rtg_ends' ? 'rtg_ends' : 'rtk',
  );
  const [boundaryText, setBoundaryText] = useState(boundaryToText(yard?.boundary));
  const [saving, setSaving] = useState(false);

  async function handleSubmit() {
    const idErr = validateYardId(yardId);
    if (idErr) { showToast(idErr, 'error'); return; }
    if (!name.trim()) { showToast('请填写场地名称'); return; }
    const boundary = boundaryText.split(/\n+/).map(line => line.trim()).filter(Boolean).map(line => {
      const [lat, lon] = line.split(/[,，\s]+/).map(Number);
      return { lat, lon };
    });
    if (boundary.length < 3 || boundary.some(p => !Number.isFinite(p.lat) || !Number.isFinite(p.lon))) {
      showToast('区域至少 3 个顶点，每行：纬度,经度', 'error');
      return;
    }
    // RTG 头尾采点：原点在采头贝位时用电气侧轮胎中点写入；新建时先用区域第一个顶点顶上
    let originLatNum = parseFloat(originLat);
    let originLonNum = parseFloat(originLon);
    let originAltNum = parseFloat(originAlt) || 0;
    if (surveyMode === 'rtg_ends') {
      if (!Number.isFinite(originLatNum) || !Number.isFinite(originLonNum)) {
        originLatNum = boundary[0].lat;
        originLonNum = boundary[0].lon;
        originAltNum = 0;
      }
    } else if (!originLat.trim() || !originLon.trim() || !Number.isFinite(originLatNum) || !Number.isFinite(originLonNum)) {
      showToast('请填写原点经纬度', 'error');
      return;
    }
    const half = parseFloat(halfWidth);
    if (!Number.isFinite(half) || half <= 0) { showToast('贝位半宽必须大于 0', 'error'); return; }
    const count = parseInt(bayCount) || 0;
    if (surveyMode === 'rtg_ends' && count < 2) {
      showToast('RTG 采点需要先填写贝位总数，且至少为 2', 'error');
      return;
    }
    if (
      editing
      && surveyMode === 'rtg_ends'
      && (yard?.total_bays ?? 0) > 0
      && count !== (yard?.total_bays ?? 0)
    ) {
      const ok = window.confirm(
        `贝位总数将从 ${yard?.total_bays} 改为 ${count}。已有的自动贝位点会按新的头尾重新生成，重采过的中间贝位请人工核对。继续吗？`,
      );
      if (!ok) return;
    }
    setSaving(true);
    try {
      const path = editing ? '/api/yard/update' : '/api/yard/create';
      const res = await fetch(`${backendBase}${path}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          yard_id: yardId.trim(),
          name: name.trim(),
          origin_lat: originLatNum,
          origin_lon: originLonNum,
          origin_alt: originAltNum,
          bay_spacing: editing ? (yard?.bay_spacing ?? 0) : 0,
          bay_count: count,
          bay_half_width_m: half,
          boundary,
          survey_mode: surveyMode,
        }),
        signal: AbortSignal.timeout(10000),
      });
      const json = await res.json().catch(() => null);
      if (!res.ok) {
        throw new Error(json?.detail || json?.message || `HTTP ${res.status}`);
      }
      if (json?.code && json.code !== 200) {
        throw new Error(json.message || (editing ? '保存失败' : '创建失败'));
      }
      const warns = Array.isArray(json?.data?.warnings) ? json.data.warnings as string[] : [];
      if (warns.length) showToast(warns[0], 'info');
      showToast(editing ? `场地 ${name.trim()} 已保存` : `场地 ${name.trim()} 创建成功`);
      onDone();
    } catch (e) {
      const msg = e instanceof Error ? e.message : '保存失败，请检查后端连接';
      logger.error('Settings', editing ? '编辑场地失败' : '新建场地失败', msg);
      showToast(msg, 'error');
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4">
      <div className="bg-slate-900 border border-slate-700 rounded-2xl w-full max-w-md shadow-2xl max-h-[min(90vh,720px)] flex flex-col">
        <div className="flex items-center justify-between px-6 pt-5 pb-3 shrink-0 border-b border-slate-800">
          <h3 className="text-base font-bold text-white">{editing ? '编辑场地' : '新建场地'}</h3>
          <button onClick={onClose} className="text-slate-500 hover:text-white"><X className="w-5 h-5" /></button>
        </div>

        <div className="px-6 py-4 space-y-3 overflow-y-auto min-h-0 flex-1">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="section-title block mb-1">场号 (yard_id)</label>
              <input
                className="input-field font-mono disabled:opacity-60"
                value={yardId}
                maxLength={PLC_YARD_ID_MAX_LEN}
                disabled={editing}
                onChange={e => setYardId(normalizeYardIdInput(e.target.value))}
                placeholder="A01 / 25A"
              />
              <p className="text-[10px] text-slate-500 mt-1">
                最多 {PLC_YARD_ID_MAX_LEN} 字符 ASCII，下发 PLC 场号字段
              </p>
            </div>
            <div>
              <label className="section-title block mb-1">场地名称</label>
              <input className="input-field" value={name} onChange={e => setName(e.target.value)} placeholder="A区堆场" />
            </div>
          </div>

          {surveyMode === 'rtg_ends' ? (
            <div className="p-3 bg-cyan-900/15 border border-cyan-500/20 rounded-lg">
              <p className="text-[10px] text-cyan-200 font-bold mb-1">原点坐标</p>
              <p className="text-[10px] text-cyan-200/70 leading-relaxed">
                RTG 头尾采点时，原点在采集头贝位（1 号）时自动写入：取电气侧两只轮胎中心的中点。
                新建时不必手填；编辑里若已有原点，采头贝位后也会更新。
              </p>
              {editing && Number.isFinite(Number(originLat)) && Number.isFinite(Number(originLon)) && (
                <p className="text-[10px] font-mono text-slate-400 mt-2">
                  当前 {originLat}, {originLon}, {originAlt} m
                </p>
              )}
            </div>
          ) : (
          <div>
            <label className="section-title block mb-1">原点坐标 (WGS84)</label>
            <div className="grid grid-cols-3 gap-2">
              <div>
                <span className="text-[10px] text-slate-500">纬度 Lat</span>
                <input type="number" step="0.000001" className="input-field font-mono text-sm py-1.5" value={originLat} onChange={e => setOriginLat(e.target.value)} placeholder="30.123456" />
              </div>
              <div>
                <span className="text-[10px] text-slate-500">经度 Lon</span>
                <input type="number" step="0.000001" className="input-field font-mono text-sm py-1.5" value={originLon} onChange={e => setOriginLon(e.target.value)} placeholder="120.654321" />
              </div>
              <div>
                <span className="text-[10px] text-slate-500">高程 Alt (m)</span>
                <input type="number" step="0.01" className="input-field font-mono text-sm py-1.5" value={originAlt} onChange={e => setOriginAlt(e.target.value)} placeholder="5.0" />
              </div>
            </div>
          </div>
          )}

          <div>
            <label className="section-title block mb-1">区域顶点（纬度,经度，按顺序首尾相连）</label>
            <textarea
              className="input-field font-mono text-xs h-28"
              value={boundaryText}
              onChange={e => setBoundaryText(e.target.value)}
              placeholder={'30.53850,114.34910\n30.53850,114.34940\n30.53940,114.34940\n30.53940,114.34910'}
            />
          </div>

          <div>
            <label className="section-title block mb-1">贝位半宽 (m)</label>
            <input type="number" step="0.01" className="input-field font-mono w-32" value={halfWidth} onChange={e => setHalfWidth(e.target.value)} />
            <p className="text-[10px] text-slate-500 mt-1">沿贝位方向正负各延伸这段距离。相邻贝位间距须不小于两倍半宽。</p>
          </div>

          <div>
            <label className="section-title block mb-1">贝位总数</label>
            <input type="number" step="1" min={2} className="input-field font-mono w-32" value={bayCount} onChange={e => setBayCount(e.target.value)} placeholder="40" />
            <p className="text-[10px] text-slate-500 mt-1">
              {surveyMode === 'rtg_ends'
                ? '头贝位 1 号，尾贝位就是这个数。中间贝位按头尾间距等分自动生成。'
                : '手持 RTK 逐个采点时，这里只是预估；实际贝位以采点为准。'}
            </p>
          </div>

          <div>
            <label className="section-title block mb-1">采点方式</label>
            <div className="grid grid-cols-2 gap-2">
              {([
                ['rtk', '手持 RTK'],
                ['rtg_ends', 'RTG 头尾采点'],
              ] as const).map(([id, label]) => (
                <button
                  key={id}
                  type="button"
                  onClick={() => setSurveyMode(id)}
                  className={`py-2 rounded-lg text-sm font-bold border ${
                    surveyMode === id
                      ? 'bg-blue-600/30 border-blue-400 text-blue-100'
                      : 'bg-slate-900 border-slate-700 text-slate-400'
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
            <p className="text-[10px] text-slate-500 mt-1">
              RTG 头尾采点：大车只采 1 号和尾号，其余贝位自动生成；偏差大的贝位再单独重采。
              已有贝位点时不能直接切换方式，需要先清空。
            </p>
          </div>

          <div className="p-3 bg-slate-800/60 rounded-lg">
            <p className="text-[10px] text-slate-400 leading-relaxed">
              {surveyMode === 'rtg_ends'
                ? '原点在采头贝位时自动设为电气侧轮胎中点。建图状态和已打的贝位不会因这次保存被清掉。贝位间距由头尾距离 ÷ (总数−1) 算出。'
                : '原点坐标为堆场坐标系的参考原点。建图状态和已打的贝位不会因这次保存被清掉。贝位间距由打点数据决定。'}
            </p>
          </div>
        </div>

        <div className="flex gap-3 px-6 py-4 shrink-0 border-t border-slate-800">
          <button onClick={onClose} className="flex-1 btn-secondary">取消</button>
          <button onClick={handleSubmit} disabled={saving} className="flex-1 btn-primary flex items-center justify-center gap-2">
            {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : (editing ? <Save className="w-4 h-4" /> : <Plus className="w-4 h-4" />)}
            {saving ? '保存中…' : (editing ? '保存' : '确认创建')}
          </button>
        </div>
      </div>
    </div>
  );
}

export default function Settings() {
  const {
    vehicles, pendingDevices, yards,
    selectedVehicleId, setSelectedVehicleId,
    selectedYardId, setSelectedYardId,
    useMock,
    connStatus,
    backendConfig, setBackendConfig,
    fetchVehicles, fetchYards,
  } = useAppContext();

  const [refreshingVehicles, setRefreshingVehicles] = useState(false);
  const [refreshingYards, setRefreshingYards] = useState(false);

  async function handleRefreshVehicles() {
    if (useMock || refreshingVehicles) return;
    setRefreshingVehicles(true);
    try { await fetchVehicles(); showToast('车辆列表已刷新'); }
    catch { showToast('刷新失败，请检查后端连接', 'error'); }
    finally { setRefreshingVehicles(false); }
  }

  async function handleRefreshYards() {
    if (useMock || refreshingYards) return;
    setRefreshingYards(true);
    try { await fetchYards(); showToast('场地列表已刷新'); }
    catch { showToast('刷新失败，请检查后端连接', 'error'); }
    finally { setRefreshingYards(false); }
  }

  const selectedVehicle = vehicles.find(v => v.vehicle_id === selectedVehicleId) ?? null;
  const selectedYard    = yards.find(y => y.yard_id === selectedYardId) ?? null;

  const [calibrationStatus, setCalibrationStatus] = useState<'none' | 'calibrated' | null>(null);
  const [checkingRole, setCheckingRole] = useState(false);
  useEffect(() => {
    setCalibrationStatus(null);
    if (!selectedVehicleId || !selectedYardId || useMock) return;
    setCheckingRole(true);
    fetch(`${httpBase(backendConfig)}/api/yard/map?yard_id=${selectedYardId}&vehicle_id=${selectedVehicleId}`,
      { signal: AbortSignal.timeout(5000) })
      .then(r => r.ok ? r.json() : null)
      .then((resp: { code: number; data: { calibration_status?: string } } | null) => {
        const cs = resp?.data?.calibration_status;
        setCalibrationStatus(cs === 'completed' ? 'calibrated' : 'none');
      })
      .catch(() => setCalibrationStatus(null))
      .finally(() => setCheckingRole(false));
  }, [selectedVehicleId, selectedYardId, useMock, backendConfig]);

  const role: 'mapping' | 'calibration' | 'worker' | null =
    !selectedYard ? null :
    !selectedYard.mapped ? 'mapping' :
    calibrationStatus === 'calibrated' ? 'worker' :
    'calibration';

  const [yardForm, setYardForm] = useState<YardInfo | 'create' | null>(null);

  const [editingConn, setEditingConn] = useState(false);
  const [editHost, setEditHost] = useState(backendConfig.host);
  const [editPort, setEditPort] = useState(String(backendConfig.port));

  useEffect(() => {
    setEditHost(backendConfig.host);
    setEditPort(String(backendConfig.port));
    setEditingConn(false);
  }, [backendConfig]);

  function saveConnEdit() {
    const port = parseInt(editPort, 10);
    const host = editHost.trim();
    if (!host) {
      showToast('请填写主机地址（Docker 部署可填 auto）', 'error');
      return;
    }
    if (host.toLowerCase() !== 'auto' && (isNaN(port) || port < 1 || port > 65535)) {
      showToast('IP 或端口格式不正确', 'error');
      return;
    }
    setBackendConfig({ host, port: host.toLowerCase() === 'auto' ? 80 : port });
    setEditingConn(false);
    showToast('后端服务地址已保存，刷新后仍会保留');
  }

  const baseUrl = httpBase(backendConfig);

  return (
    <div className="animate-fade-in space-y-6">
      {yardForm && (
        <YardFormModal
          backendBase={baseUrl}
          yard={yardForm === 'create' ? null : yardForm}
          onClose={() => setYardForm(null)}
          onDone={() => { setYardForm(null); fetchYards().catch(() => {}); }}
        />
      )}

      <SectionHeader
        title="工作配置"
        subtitle="选择车辆与场地。流程：① RTK 建图真值 → ② 车体静态参数 → ③ 校准（全场/三点/免校准）→ ④ 作业。"
      />

      {/* ── 后端服务连接面板 ── */}
      <div className="card p-5 border border-slate-700/60 space-y-4">
        <div className="flex items-center gap-2">
          {connStatus === 'connected'
            ? <Wifi className="w-4 h-4 text-emerald-400" />
            : connStatus === 'connecting'
            ? <Loader2 className="w-4 h-4 text-amber-400 animate-spin" />
            : <WifiOff className="w-4 h-4 text-slate-500" />}
          <h3 className="text-sm font-bold uppercase tracking-wide text-slate-300">后端服务连接</h3>
          <span className={clsx(
            'ml-auto text-[10px] font-bold px-2 py-0.5 rounded-full border',
            connStatus === 'connected'
              ? 'text-emerald-400 bg-emerald-900/30 border-emerald-500/30'
              : connStatus === 'connecting'
              ? 'text-amber-400 bg-amber-900/30 border-amber-500/30'
              : 'text-slate-500 bg-slate-800 border-slate-700',
          )}>
            {connStatus === 'connected' ? '已连接' : connStatus === 'connecting' ? '连接中…' : '未连接'}
          </span>
        </div>

        <div className="space-y-3">
          <p className="text-[11px] text-slate-400">
            前端通过统一的后端服务接入，后端负责与各车辆中控机的 MQTT 通信。
            {selectedVehicleId && <span className="text-blue-400"> 当前订阅车辆：{selectedVehicleId}</span>}
          </p>
          <p className="text-[10px] text-slate-500">
            排查 WebSocket：浏览器 F12 控制台可看「← 帧 …」；每秒摘要默认开启。
            要打出每一帧（含 heartbeat）可执行{' '}
            <code className="text-slate-400">localStorage.setItem(&apos;rtg_ws_debug&apos;,&apos;1&apos;)</code>
            {' '}后刷新，或 URL 加 <code className="text-slate-400">?ws_debug=1</code>。
            服务端帧日志：<code className="text-slate-400">docker compose logs -f backend</code>
            （需 <code className="text-slate-400">WS_FRAME_LOG=true</code>）。
          </p>

          {editingConn ? (
            <div className="flex items-center gap-2 flex-wrap">
              <input
                className="bg-slate-900 border border-slate-600 rounded-lg px-3 py-1.5 text-sm text-slate-200 font-mono w-44 focus:outline-none focus:border-blue-500"
                value={editHost}
                onChange={e => setEditHost(e.target.value)}
                placeholder="localhost 或 auto"
              />
              <p className="text-[10px] text-slate-500 mt-1">Docker 同源部署填 auto（走 Nginx 反代）</p>
              <span className="text-slate-500">:</span>
              <input
                className="bg-slate-900 border border-slate-600 rounded-lg px-3 py-1.5 text-sm text-slate-200 font-mono w-20 focus:outline-none focus:border-blue-500"
                value={editPort}
                onChange={e => setEditPort(e.target.value)}
                placeholder="8000"
              />
              <button onClick={saveConnEdit} className="flex items-center gap-1 px-3 py-1.5 rounded-lg bg-blue-600 hover:bg-blue-500 text-white text-xs font-bold">
                <Save className="w-3.5 h-3.5" /> 保存
              </button>
              <button onClick={() => setEditingConn(false)} className="flex items-center gap-1 px-3 py-1.5 rounded-lg bg-slate-700 hover:bg-slate-600 text-slate-300 text-xs">
                <X className="w-3.5 h-3.5" /> 取消
              </button>
            </div>
          ) : (
            <div className="flex items-center gap-3">
              <span className="font-mono text-sm text-slate-300 bg-slate-900/60 px-3 py-1.5 rounded-lg border border-slate-700">
                {backendConfig.host}:{backendConfig.port}
              </span>
              <button
                onClick={() => { setEditHost(backendConfig.host); setEditPort(String(backendConfig.port)); setEditingConn(true); }}
                className="flex items-center gap-1 px-3 py-1.5 rounded-lg border border-slate-600 hover:border-slate-400 text-slate-400 hover:text-slate-200 text-xs"
              >
                <Edit2 className="w-3.5 h-3.5" /> 修改地址
              </button>
            </div>
          )}

          <p className="text-[10px] text-slate-500">
            {connStatus === 'connected'
              ? `已与后端服务建立 WebSocket（${backendConfig.host === 'auto' ? '同源 auto' : `${backendConfig.host}:${backendConfig.port}`}）。`
              : '修改地址后，重新选择车辆以触发连接。'}
          </p>
        </div>
      </div>

      {/* ── 当前选择状态 Banner ── */}
      <div className={clsx(
        'card p-5 border',
        selectedVehicle && selectedYard
          ? role === 'mapping'
            ? 'bg-blue-900/10 border-blue-500/30'
            : 'bg-amber-900/10 border-amber-500/30'
          : 'border-slate-700/60',
      )}>
        <h3 className="section-title mb-4">当前工作配置</h3>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {/* 车辆 */}
          <div className="flex items-center gap-3">
            <div className={clsx(
              'w-11 h-11 rounded-xl flex items-center justify-center shrink-0',
              selectedVehicle
                ? 'bg-blue-600/20 border border-blue-500/40'
                : 'bg-slate-800 border border-slate-700',
            )}>
              <Car className={clsx('w-5 h-5', selectedVehicle ? 'text-blue-400' : 'text-slate-600')} />
            </div>
            <div>
              <p className="section-title">当前车辆</p>
              <p className={clsx('text-sm font-bold mt-0.5', selectedVehicle ? 'text-white' : 'text-slate-500')}>
                {selectedVehicle ? selectedVehicle.label : '— 未选择 —'}
              </p>
              {selectedVehicle && (
                <p className="text-[10px] text-slate-500 font-mono mt-0.5">
                  {selectedVehicle.vehicle_id}
                </p>
              )}
            </div>
          </div>

          {/* 场地 */}
          <div className="flex items-center gap-3">
            <div className={clsx(
              'w-11 h-11 rounded-xl flex items-center justify-center shrink-0',
              selectedYard
                ? 'bg-emerald-600/20 border border-emerald-500/40'
                : 'bg-slate-800 border border-slate-700',
            )}>
              <Map className={clsx('w-5 h-5', selectedYard ? 'text-emerald-400' : 'text-slate-600')} />
            </div>
            <div>
              <p className="section-title">当前场地</p>
              <p className={clsx('text-sm font-bold mt-0.5', selectedYard ? 'text-white' : 'text-slate-500')}>
                {selectedYard ? selectedYard.name : '— 未选择 —'}
              </p>
              {selectedYard && (
                <p className={clsx('text-[10px] font-bold mt-0.5', selectedYard.mapped ? 'text-emerald-400' : 'text-amber-400')}>
                  {selectedYard.mapped ? '● 已建图' : '○ 未建图'} · {selectedYard.total_bays} 贝位
                </p>
              )}
            </div>
          </div>
        </div>

        {/* 角色提示 */}
        {selectedVehicle && selectedYard && (
          <div className={clsx(
            'mt-4 p-3 rounded-lg text-xs flex items-start gap-2 border',
            role === 'mapping'
              ? 'bg-blue-900/20 border-blue-500/20 text-blue-300'
              : role === 'worker'
              ? 'bg-emerald-900/20 border-emerald-500/20 text-emerald-300'
              : 'bg-amber-900/20 border-amber-500/20 text-amber-300',
          )}>
            {checkingRole ? (
              <><Loader2 className="w-4 h-4 shrink-0 animate-spin" /><span>正在查询校准状态…</span></>
            ) : role === 'mapping' ? (
              <>
                <Map className="w-4 h-4 shrink-0 mt-0.5" />
                <span>
                  <span className="font-bold">角色 A · 建图</span> — {selectedYard.name} 尚无底图，
                  {selectedVehicle.label} 将前往「场地建图」页面采集贝位坐标。
                </span>
              </>
            ) : role === 'worker' ? (
              <>
                <Zap className="w-4 h-4 shrink-0 mt-0.5" />
                <span>
                  <span className="font-bold">角色 C · 作业</span> — {selectedVehicle.label} 在 {selectedYard.name} 已完成校准，
                  直接进入高频定位作业模式，前往「仪表盘」查看实时数据。
                </span>
              </>
            ) : (
              <>
                <CheckCircle2 className="w-4 h-4 shrink-0 mt-0.5" />
                <span>
                  <span className="font-bold">角色 B · 校准</span> — {selectedYard.name} 已有底图，
                  {selectedVehicle.label} 将前往「车辆校准」页面完成 3 点标定，生成本车转换矩阵。
                </span>
              </>
            )}
          </div>
        )}

        {(!selectedVehicle || !selectedYard) && (
          <div className="mt-4 p-3 rounded-lg text-xs flex items-center gap-2 bg-slate-900/60 border border-slate-700/40 text-slate-400">
            <AlertTriangle className="w-4 h-4 shrink-0" />
            请在下方分别选择车辆和场地，以确定当前工作配置。
          </div>
        )}
      </div>

      {/* ── 两栏选择面板 ── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">

        {/* 左栏：车辆列表 */}
        <div className="card p-6 space-y-4">
          <div className="flex items-center gap-2 pb-3 border-b border-slate-700/60">
            <Car className="w-4 h-4 text-blue-400" />
            <h3 className="text-sm font-bold uppercase tracking-wide text-slate-300">车辆列表</h3>
            <span className="text-[10px] text-slate-500">
              {vehicles.length} 台已命名
              {pendingDevices.filter(d => d.status === 'online').length > 0
                ? ` · ${pendingDevices.filter(d => d.status === 'online').length} 台在线未命名`
                : ''}
            </span>
            <button
              onClick={handleRefreshVehicles}
              disabled={useMock || refreshingVehicles}
              title={useMock ? '模拟模式下不可刷新' : '从后端重新拉取车辆列表'}
              className="ml-auto p-1.5 rounded-lg text-slate-500 hover:text-blue-400 hover:bg-slate-800 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            >
              <RefreshCw className={clsx('w-3.5 h-3.5', refreshingVehicles && 'animate-spin')} />
            </button>
          </div>

          <div className="space-y-2">
            {[
              ...vehicles.map((v) => ({
                id: v.vehicle_id,
                label: v.label,
                status: v.status ?? 'offline',
                initialized: true,
              })),
              ...pendingDevices
                .filter((d) => d.status === 'online' && !vehicles.some((v) => v.vehicle_id === d.temp_id))
                .map((d) => ({
                  id: d.temp_id,
                  label: d.temp_id,
                  status: 'online' as const,
                  initialized: false,
                })),
            ].map((item) => {
              const isSelected = item.id === selectedVehicleId;
              const online = item.status === 'online';
              return (
                <button
                  key={item.id}
                  onClick={() => {
                    if (isSelected) {
                      setSelectedVehicleId(null);
                      return;
                    }
                    setSelectedVehicleId(item.id);
                    if (!item.initialized) {
                      showToast('该设备尚未命名，实时数据可看，正式作业请先到车辆管理初始化');
                    } else if (!online) {
                      showToast(`已选择 ${item.label}（当前离线，可能没有实时数据）`);
                    } else {
                      showToast(`已选择：${item.label}`);
                    }
                  }}
                  className={clsx(
                    'w-full text-left p-4 rounded-xl border transition-all duration-150',
                    isSelected
                      ? 'bg-blue-900/30 border-blue-500/60 shadow-lg shadow-blue-900/20'
                      : 'bg-slate-900/40 border-slate-700/60 hover:border-slate-500',
                  )}
                >
                  <div className="flex items-center gap-3">
                    <div className={clsx(
                      'w-9 h-9 rounded-lg flex items-center justify-center text-xs font-bold shrink-0',
                      isSelected ? 'bg-blue-600 text-white' : 'bg-slate-800 text-slate-400',
                    )}>
                      {item.id.slice(-3)}
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className={clsx('text-sm font-bold truncate', isSelected ? 'text-blue-200' : 'text-slate-200')}>
                        {item.label}
                      </p>
                      <p className="text-[10px] text-slate-500 font-mono mt-0.5">
                        {item.id}
                      </p>
                    </div>
                    <div className="flex items-center gap-2 shrink-0">
                      {!item.initialized && (
                        <span className="text-[10px] font-bold px-2 py-0.5 rounded-full border text-amber-400 bg-amber-900/30 border-amber-500/30">
                          待命名
                        </span>
                      )}
                      <span className={clsx(
                        'text-[10px] font-bold px-2 py-0.5 rounded-full border',
                        online
                          ? 'text-emerald-400 bg-emerald-900/30 border-emerald-500/30'
                          : 'text-slate-400 bg-slate-800/60 border-slate-600/40',
                      )}>
                        {online ? '在线' : '离线'}
                      </span>
                      {isSelected && <CheckCircle2 className="w-5 h-5 text-blue-400" />}
                    </div>
                  </div>
                </button>
              );
            })}
            {vehicles.length === 0 && pendingDevices.filter(d => d.status === 'online').length === 0 && (
              <p className="text-xs text-slate-500 py-6 text-center">暂无已命名或在线设备，请刷新或先初始化</p>
            )}
          </div>

          <button
            onClick={() => { window.location.hash = 'config'; }}
            className="w-full flex items-center justify-center gap-2 py-2.5 rounded-xl border border-dashed border-slate-600 hover:border-blue-500 text-slate-400 hover:text-blue-400 text-xs font-bold transition-colors"
          >
            <Settings2 className="w-3.5 h-3.5" />
            管理车辆 / 初始化新设备
          </button>
        </div>

        {/* 右栏：场地列表 */}
        <div className="card p-6 space-y-4">
          <div className="flex items-center gap-2 pb-3 border-b border-slate-700/60">
            <Map className="w-4 h-4 text-emerald-400" />
            <h3 className="text-sm font-bold uppercase tracking-wide text-slate-300">场地列表</h3>
            <span className="text-[10px] text-slate-500">
              {yards.filter(y => y.mapped).length}/{yards.length} 已建图
            </span>
            <button
              onClick={handleRefreshYards}
              disabled={useMock || refreshingYards}
              title={useMock ? '模拟模式下不可刷新' : '从后端重新拉取场地列表'}
              className="ml-auto p-1.5 rounded-lg text-slate-500 hover:text-emerald-400 hover:bg-slate-800 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            >
              <RefreshCw className={clsx('w-3.5 h-3.5', refreshingYards && 'animate-spin')} />
            </button>
          </div>

          <div className="space-y-2">
            {yards.map((y) => {
              const isSelected = y.yard_id === selectedYardId;
              return (
                <div
                  key={y.yard_id}
                  onClick={() => {
                    setSelectedYardId(isSelected ? null : y.yard_id);
                    if (!isSelected) showToast(`已选择：${y.name}`);
                  }}
                  className={clsx(
                    'w-full text-left p-4 rounded-xl border transition-all duration-150 cursor-pointer',
                    isSelected
                      ? 'bg-emerald-900/30 border-emerald-500/60 shadow-lg shadow-emerald-900/20'
                      : 'bg-slate-900/40 border-slate-700/60 hover:border-slate-500',
                  )}
                >
                  <div className="flex items-center gap-3">
                    <div className={clsx(
                      'w-9 h-9 rounded-lg flex items-center justify-center text-xs font-bold shrink-0',
                      isSelected ? 'bg-emerald-600 text-white' : 'bg-slate-800 text-slate-400',
                    )}>
                      {y.yard_id.slice(-2).toUpperCase()}
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className={clsx('text-sm font-bold truncate', isSelected ? 'text-emerald-200' : 'text-slate-200')}>
                        {y.name}
                      </p>
                      <p className="text-[10px] text-slate-500 font-mono mt-0.5">
                        {y.yard_id} · {y.total_bays} 贝位
                        {y.bay_half_width_m != null ? ` · 半宽 ${y.bay_half_width_m} m` : ''}
                      </p>
                    </div>
                    <div className="flex items-center gap-2 shrink-0">
                      <button
                        type="button"
                        disabled={useMock}
                        title={useMock ? '模拟模式下不可编辑' : '编辑场地'}
                        onClick={(e) => {
                          e.stopPropagation();
                          setYardForm(y);
                        }}
                        className="flex items-center gap-1 text-[10px] font-bold px-2 py-1 rounded-lg border border-slate-600 text-slate-300 hover:text-emerald-300 hover:border-emerald-500 disabled:opacity-30 disabled:cursor-not-allowed"
                      >
                        <Edit2 className="w-3 h-3" />
                        编辑
                      </button>
                      <span className={clsx(
                        'text-[10px] font-bold px-2 py-0.5 rounded-full border',
                        y.mapped
                          ? 'text-emerald-400 bg-emerald-900/30 border-emerald-500/30'
                          : 'text-amber-400 bg-amber-900/30 border-amber-500/30',
                      )}>
                        {y.mapped ? '已建图' : '未建图'}
                      </span>
                      {isSelected && <CheckCircle2 className="w-5 h-5 text-emerald-400" />}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>

          <button
            onClick={() => setYardForm('create')}
            disabled={useMock}
            className="w-full flex items-center justify-center gap-2 py-2.5 rounded-xl border border-dashed border-slate-600 hover:border-emerald-500 text-slate-400 hover:text-emerald-400 text-xs font-bold transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
          >
            <Plus className="w-3.5 h-3.5" />
            新建场地
          </button>

          <div className="p-3 bg-slate-900/60 border border-slate-700/40 rounded-lg">
            <p className="text-[10px] text-slate-500 leading-relaxed">
              <span className="text-slate-400 font-semibold">角色说明：</span>
              同一台车，在「未建图」场地为建图角色；在「已建图」场地为校准角色。
              角色由场地状态决定，与车辆编号无关。
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
