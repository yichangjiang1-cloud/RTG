import { useMemo } from 'react';
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from 'recharts';
import {
  Navigation, Activity, Signal, Layers, Gauge,
  ArrowLeftRight, MoveHorizontal, WifiOff, Loader2,
} from 'lucide-react';
import { StatCard, StatusBadge, StatusDot, Compass } from './common';
import type { RealtimeData } from '../types';

/** 速度显示死区，与中控 motion_speed.DEADBAND_MPS 一致 */
const SPEED_DISPLAY_DEADBAND = 0.03;

function formatSpeedMps(v: number, digits = 3): string {
  const s = Math.abs(v) < SPEED_DISPLAY_DEADBAND ? 0 : v;
  return `${s.toFixed(digits)} m/s`;
}

function formatSignedSpeedMps(v: number, digits = 3): string {
  const s = Math.abs(v) < SPEED_DISPLAY_DEADBAND ? 0 : v;
  return `${s >= 0 ? '+' : ''}${s.toFixed(digits)} m/s`;
}

function formatCoordM(v: number, valid = true, digits = 4): string {
  if (valid === false) return '无数据';
  return `${v.toFixed(digits)} m`;
}

function isCenterValid(valid: boolean | undefined): boolean {
  return valid !== false;
}

interface DashboardProps {
  data: RealtimeData;
  history: RealtimeData[];
  isMock: boolean;
  connStatus: 'connecting' | 'connected' | 'disconnected';
}

export default function Dashboard({ data, history, isMock, connStatus }: DashboardProps) {
  const elecCenter = data.gantry.elec_lpoint ?? data.gantry.receiver_left ?? data.gantry.elec_center;
  const dieselCenter = data.gantry.diesel_lpoint ?? data.gantry.receiver_right ?? data.gantry.diesel_center;

  const chartData = useMemo(
    () =>
      history.slice(-60).map((d, i) => ({
        t: i,
        x: parseFloat(d.local_coord.x.toFixed(4)),
        y: parseFloat(d.local_coord.y.toFixed(4)),
        speed: parseFloat((Math.abs(d.speed) < SPEED_DISPLAY_DEADBAND ? 0 : d.speed).toFixed(3)),
      })),
    [history],
  );

  const statusLabel = data.status === 'fix' ? '固定解' : data.status === 'float' ? '浮动解' : '失锁';

  // 非模拟模式且未真实连接时，显示遮罩
  const showOverlay = !isMock && connStatus !== 'connected';

  return (
    <div className="animate-fade-in space-y-5 relative">
      {showOverlay && (
        <div className="absolute inset-0 z-20 flex flex-col items-center justify-center gap-3
                        bg-slate-950/80 backdrop-blur-sm rounded-2xl">
          {connStatus === 'connecting'
            ? <Loader2 className="w-10 h-10 text-amber-400 animate-spin" />
            : <WifiOff className="w-10 h-10 text-slate-500" />}
          <p className="text-sm font-bold text-slate-300">
            {connStatus === 'connecting' ? '正在连接中控机…' : '未连接 — 请先选择车辆或开启模拟模式'}
          </p>
          <p className="text-[11px] text-slate-500">数据已暂停，等待 WebSocket 连接建立后自动恢复</p>
        </div>
      )}
      {/* Top stat row */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <div className="card p-5 sm:col-span-2 lg:col-span-1">
          <p className="section-title mb-1">大车 L-Point（堆场坐标）</p>
          <p className="text-[10px] text-slate-500 mb-3">解算输出 · LYCS · 原点为堆场参考点</p>
          <div className="space-y-1">
            <div className="flex justify-between items-baseline">
              <span className="text-[10px] text-slate-500">X</span>
              <span className="font-mono text-emerald-400 text-sm">{data.local_coord.x.toFixed(4)} m</span>
            </div>
            <div className="flex justify-between items-baseline">
              <span className="text-[10px] text-slate-500">Y</span>
              <span className="font-mono text-emerald-400 text-sm">{data.local_coord.y.toFixed(4)} m</span>
            </div>
            <div className="flex justify-between items-baseline">
              <span className="text-[10px] text-slate-500">Z</span>
              <span className="font-mono text-emerald-400 text-sm">{data.local_coord.z.toFixed(4)} m</span>
            </div>
          </div>
        </div>

        <div className="card p-5">
          <p className="section-title mb-1">大车 L-Point（大地坐标）</p>
          <p className="text-[10px] text-slate-500 mb-3">解算输出 · WGS84 · 由堆场坐标反算</p>
          <div className="space-y-1">
            <div className="flex justify-between items-baseline">
              <span className="text-[10px] text-slate-500">LAT</span>
              <span className="font-mono text-blue-400 text-sm">{data.lpoint_geo.lat.toFixed(8)}°</span>
            </div>
            <div className="flex justify-between items-baseline">
              <span className="text-[10px] text-slate-500">LON</span>
              <span className="font-mono text-blue-400 text-sm">{data.lpoint_geo.lon.toFixed(8)}°</span>
            </div>
            <div className="flex justify-between items-baseline">
              <span className="text-[10px] text-slate-500">ALT</span>
              <span className="font-mono text-blue-400 text-sm">{data.lpoint_geo.alt.toFixed(3)} m</span>
            </div>
          </div>
        </div>

        <div className="card p-5">
          <p className="section-title mb-3">实时姿态</p>
          <div className="space-y-1">
            <div className="flex justify-between items-baseline">
              <span className="text-[10px] text-slate-500">Heading</span>
              <span className="font-mono text-amber-400 text-sm">{data.heading.toFixed(2)}°</span>
            </div>
            <div className="flex justify-between items-baseline">
              <span className="text-[10px] text-slate-500">Roll</span>
              <span className="font-mono text-amber-400 text-sm">{data.roll.toFixed(3)}°</span>
            </div>
            <div className="flex justify-between items-baseline">
              <span className="text-[10px] text-slate-500">Pitch</span>
              <span className="font-mono text-amber-400 text-sm">{data.pitch.toFixed(3)}°</span>
            </div>
          </div>
        </div>

        <div className="card p-5 flex flex-col gap-3">
          <div className="flex items-center justify-between">
            <p className="section-title">GNSS 状态</p>
            <StatusBadge status={data.status} />
          </div>
          <StatCard
            label=""
            value={formatSpeedMps(data.speed)}
            sub="实时速度"
            accent="blue"
            className="!p-0 !bg-transparent !border-0 !shadow-none"
          />
          <div className="mt-auto text-[10px] text-slate-500">
            {new Date(data.timestamp).toLocaleTimeString('zh-CN')}
          </div>
        </div>
      </div>

      {/* Main content row */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        {/* Chart area */}
        <div className="lg:col-span-2 card p-5">
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-2">
              <Activity className="w-4 h-4 text-blue-400" />
              <span className="text-sm font-semibold">实时位置轨迹 (X/Y)</span>
            </div>
            <span className="text-[10px] text-slate-500">最近 60 帧</span>
          </div>
          <ResponsiveContainer width="100%" height={220}>
            <AreaChart data={chartData} margin={{ top: 4, right: 4, left: -20, bottom: 0 }}>
              <defs>
                <linearGradient id="gx" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%"  stopColor="#3b82f6" stopOpacity={0.3} />
                  <stop offset="95%" stopColor="#3b82f6" stopOpacity={0} />
                </linearGradient>
                <linearGradient id="gy" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%"  stopColor="#10b981" stopOpacity={0.3} />
                  <stop offset="95%" stopColor="#10b981" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
              <XAxis dataKey="t" tick={false} axisLine={false} />
              <YAxis tick={{ fontSize: 10, fill: '#64748b' }} axisLine={false} tickLine={false} />
              <Tooltip
                contentStyle={{ background: '#1e293b', border: '1px solid #334155', borderRadius: 8, fontSize: 11 }}
                labelStyle={{ color: '#64748b' }}
              />
              <Area type="monotone" dataKey="x" stroke="#3b82f6" strokeWidth={1.5} fill="url(#gx)" dot={false} name="X (m)" />
              <Area type="monotone" dataKey="y" stroke="#10b981" strokeWidth={1.5} fill="url(#gy)" dot={false} name="Y (m)" />
            </AreaChart>
          </ResponsiveContainer>
        </div>

        {/* Right column */}
        <div className="space-y-4">
          {/* Compass */}
          <div className="card p-5">
            <div className="flex items-center gap-2 mb-4">
              <Navigation className="w-4 h-4 text-amber-400" />
              <span className="text-sm font-semibold">航向罗盘</span>
            </div>
            <div className="flex justify-center">
              <Compass heading={data.heading} size={130} />
            </div>
          </div>

          {/* Receiver status */}
          <div className="card p-5">
            <div className="flex items-center gap-2 mb-3">
              <Signal className="w-4 h-4 text-blue-400" />
              <span className="text-sm font-semibold">接收机状态</span>
            </div>
            <div className="space-y-2">
              {data.receivers.length === 0 ? (
                <p className="text-xs text-slate-500 py-2">暂无接收机状态</p>
              ) : (
                data.receivers.map((r) => (
                <div key={r.id} className="flex items-center justify-between p-2.5 bg-slate-900/50 rounded-lg">
                  <div className="flex items-center gap-2">
                    <StatusDot status={r.valid === false ? 'no_signal' : r.status} />
                    <span className={`text-xs ${r.valid === false ? 'text-slate-500' : 'text-slate-300'}`}>
                      {r.label}
                    </span>
                  </div>
                  <div className="flex items-center gap-3 text-[10px] text-slate-500">
                    {r.valid === false ? (
                      <span className="text-slate-500">无数据</span>
                    ) : (
                      <>
                        <span>{r.satellites} sats</span>
                        <span>HDOP {r.hdop}</span>
                        <span className={
                          r.status === 'fix' ? 'text-emerald-400' :
                          r.status === 'float' ? 'text-amber-400' : 'text-red-400'
                        }>{statusLabel}</span>
                      </>
                    )}
                  </div>
                </div>
              )))}
            </div>
          </div>
        </div>
      </div>

      {/* ── 大车 & 小车定位 ── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">

        {/* 大车定位 */}
        <div className="card p-5 space-y-4">
          <div className="flex items-center gap-2">
            <ArrowLeftRight className="w-4 h-4 text-blue-400" />
            <span className="text-sm font-semibold">大车定位</span>
          </div>

          {/* 双中心输出 */}
          <div className="grid grid-cols-2 gap-3">
            <div className="bg-slate-900/50 rounded-lg p-3">
              <p className="section-title mb-2">电气房侧作业点 (A1)</p>
              <p className="text-[9px] text-slate-600 mb-1">接收机1 主天线 · gantry_a1</p>
              <div className="space-y-1">
                <div className="flex justify-between items-baseline">
                  <span className="text-[10px] text-slate-500">X</span>
                  <span className={`font-mono text-sm ${isCenterValid(elecCenter.valid) ? 'text-blue-400' : 'text-slate-500'}`}>
                    {formatCoordM(elecCenter.x, elecCenter.valid)}
                  </span>
                </div>
                <div className="flex justify-between items-baseline">
                  <span className="text-[10px] text-slate-500">Y</span>
                  <span className={`font-mono text-sm ${isCenterValid(elecCenter.valid) ? 'text-blue-400' : 'text-slate-500'}`}>
                    {formatCoordM(elecCenter.y, elecCenter.valid)}
                  </span>
                </div>
              </div>
            </div>
            <div className="bg-slate-900/50 rounded-lg p-3">
              <p className="section-title mb-2">柴油机侧作业点 (A2)</p>
              <p className="text-[9px] text-slate-600 mb-1">接收机1 副天线 · gantry_a1 双 GGA</p>
              <div className="space-y-1">
                <div className="flex justify-between items-baseline">
                  <span className="text-[10px] text-slate-500">X</span>
                  <span className={`font-mono text-sm ${isCenterValid(dieselCenter.valid) ? 'text-purple-400' : 'text-slate-500'}`}>
                    {formatCoordM(dieselCenter.x, dieselCenter.valid)}
                  </span>
                </div>
                <div className="flex justify-between items-baseline">
                  <span className="text-[10px] text-slate-500">Y</span>
                  <span className={`font-mono text-sm ${isCenterValid(dieselCenter.valid) ? 'text-purple-400' : 'text-slate-500'}`}>
                    {formatCoordM(dieselCenter.y, dieselCenter.valid)}
                  </span>
                </div>
              </div>
            </div>
          </div>

          {/* 四门腿偏移 */}
          <div>
            <p className="section-title mb-2">门腿偏移量（相对跑道中心线）</p>
            <div className="grid grid-cols-2 gap-2">
              {(
                [
                  ['FL 前左', data.gantry.leg_offsets.fl],
                  ['FR 前右', data.gantry.leg_offsets.fr],
                  ['RL 后左', data.gantry.leg_offsets.rl],
                  ['RR 后右', data.gantry.leg_offsets.rr],
                ] as [string, number][]
              ).map(([label, val]) => (
                <div key={label} className="flex justify-between items-center px-3 py-1.5 bg-slate-900/40 rounded-lg">
                  <span className="text-[10px] text-slate-500">{label}</span>
                  <span className={`font-mono text-xs font-bold ${Math.abs(val) < 0.05 ? 'text-emerald-400' : 'text-amber-400'}`}>
                    {val >= 0 ? '+' : ''}{val.toFixed(4)} m
                  </span>
                </div>
              ))}
            </div>
          </div>

          {/* 大车速度 */}
          <div className="flex items-center justify-between px-3 py-2 bg-slate-900/40 rounded-lg">
            <span className="section-title">大车速度</span>
            <span className={`font-mono text-sm font-bold ${
              data.gantry.speed > 0.01 ? 'text-blue-400' :
              data.gantry.speed < -0.01 ? 'text-amber-400' : 'text-slate-400'
            }`}>
              {formatSignedSpeedMps(data.gantry.speed)}
            </span>
          </div>
        </div>

        {/* 小车定位 */}
        <div className="card p-5 space-y-4 relative">
          {data.trolley.valid === false && (
            <div className="absolute inset-0 z-10 flex items-center justify-center bg-slate-950/70 rounded-2xl">
              <p className="text-sm text-slate-400">小车无实时 GNSS 数据 (9003)</p>
            </div>
          )}
          <div className="flex items-center gap-2">
            <MoveHorizontal className="w-4 h-4 text-emerald-400" />
            <span className="text-sm font-semibold">小车定位</span>
          </div>

          {/* 相对起始点位置 + 速度 */}
          <div className="grid grid-cols-3 gap-2 text-center">
            {(['x', 'y', 'z'] as const).map(axis => (
              <div key={axis} className="bg-slate-900/50 rounded-lg p-2">
                <p className="section-title mb-1">Δ{axis.toUpperCase()}</p>
                <span className="font-mono text-emerald-400 text-sm">
                  {data.trolley.position[axis].toFixed(3)} m
                </span>
              </div>
            ))}
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="flex flex-col items-center justify-center py-3 bg-slate-900/40 rounded-lg gap-1">
              <span className="section-title">实际行程</span>
              <span className="font-mono text-lg font-bold text-emerald-400">{data.trolley.travel.toFixed(3)} m</span>
            </div>
            <div className="flex flex-col items-center justify-center py-3 bg-slate-900/40 rounded-lg gap-1">
              <span className="section-title">小车速度</span>
              <span className={`font-mono text-lg font-bold ${
                data.trolley.speed > 0.01 ? 'text-emerald-400' :
                data.trolley.speed < -0.01 ? 'text-amber-400' : 'text-slate-400'
              }`}>
                {formatSignedSpeedMps(data.trolley.speed)}
              </span>
            </div>
          </div>
        </div>
      </div>

      {/* RTG visual */}
      <div className="card p-5">
        <div className="flex items-center gap-2 mb-4">
          <Layers className="w-4 h-4 text-blue-400" />
          <span className="text-sm font-semibold">RTG 门架示意图</span>
          <Gauge className="w-4 h-4 text-slate-500 ml-auto" />
          <span className="text-[10px] text-slate-500">实时更新</span>
        </div>
        <div className="relative h-52 bg-slate-900/60 rounded-xl border border-slate-700/40 bg-grid overflow-hidden">
          {/* Simplified RTG SVG — trolley position driven by real trolley.travel data */}
          {(() => {
            // Map travel (0–20 m) to SVG x within the top beam (160–640 px)
            const trolleyX = 160 + Math.min(1, Math.max(0, data.trolley.travel / 20)) * 480;
            return (
          <svg
            className="absolute inset-0 w-full h-full"
            viewBox="0 0 800 200"
            preserveAspectRatio="xMidYMid meet"
          >
            {/* Ground line */}
            <line x1="50" y1="170" x2="750" y2="170" stroke="#334155" strokeWidth="2" />

            {/* Rails */}
            <line x1="150" y1="165" x2="150" y2="175" stroke="#475569" strokeWidth="3" />
            <line x1="650" y1="165" x2="650" y2="175" stroke="#475569" strokeWidth="3" />

            {/* Legs */}
            <line x1="150" y1="170" x2="160" y2="40"  stroke="#3b82f6" strokeWidth="3" strokeLinecap="round" />
            <line x1="650" y1="170" x2="640" y2="40"  stroke="#3b82f6" strokeWidth="3" strokeLinecap="round" />

            {/* Top beam */}
            <line x1="160" y1="40" x2="640" y2="40" stroke="#3b82f6" strokeWidth="4" strokeLinecap="round" />

            {/* Trolley (position driven by trolley.travel) */}
            <rect
              x={trolleyX - 25}
              y="30"
              width="50"
              height="20"
              rx="3"
              fill="#1e40af"
              stroke="#60a5fa"
              strokeWidth="1.5"
            />

            {/* Gantry antennas */}
            <circle cx="155" cy="38" r="5" fill="#10b981" />
            <circle cx="645" cy="38" r="5" fill="#10b981" />

            {/* L-Point */}
            <circle cx="150" cy="170" r="6" fill="#f59e0b" />
            <text x="158" y="185" fill="#f59e0b" fontSize="10">L-Point</text>

            {/* Trolley travel label */}
            <text x={trolleyX} y="22" fill="#60a5fa" fontSize="9" textAnchor="middle">
              {`${data.trolley.travel.toFixed(1)} m`}
            </text>

            {/* Data labels */}
            <text x="390" y="16" fill="#64748b" fontSize="9" textAnchor="middle">
              {`X: ${data.local_coord.x.toFixed(2)} m  |  HDG: ${data.heading.toFixed(1)}°  |  大车速度: ${formatSignedSpeedMps(data.gantry.speed, 2)}`}
            </text>

            {/* Leg offset indicators */}
            {[
              { cx: 150, label: `FL ${data.gantry.leg_offsets.fl >= 0 ? '+' : ''}${data.gantry.leg_offsets.fl.toFixed(3)}` },
              { cx: 650, label: `FR ${data.gantry.leg_offsets.fr >= 0 ? '+' : ''}${data.gantry.leg_offsets.fr.toFixed(3)}` },
            ].map(({ cx, label }) => (
              <text key={label} x={cx} y="195" fill="#94a3b8" fontSize="8" textAnchor="middle">{label}</text>
            ))}
          </svg>
            );
          })()}
        </div>
      </div>
    </div>
  );
}
