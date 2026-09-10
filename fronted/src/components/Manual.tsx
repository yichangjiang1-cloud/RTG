import { HardDrive, Cpu, BookOpen, ArrowRight } from 'lucide-react';

export default function Manual() {
  return (
    <div className="animate-fade-in space-y-6">
      <div className="card p-8 space-y-8">
        <div className="border-b border-slate-700/60 pb-6">
          <h1 className="text-2xl font-bold text-blue-400 mb-2">RTG 系统作业逻辑概览</h1>
          <p className="text-slate-400 text-sm">
            本系统采用「基站 + RTG 接收端」架构，为 RTG 大车/小车提供实时高精度定位数据，支撑自动化调度、大车纠偏及安全防护。
          </p>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
          {/* Data flow */}
          <div>
            <h3 className="flex items-center gap-2 text-base font-bold text-white mb-4">
              <HardDrive className="w-5 h-5 text-blue-400" />
              数据流与存储策略
            </h3>
            <div className="space-y-4">
              {[
                {
                  n: 1,
                  title: '本地优先存储',
                  desc: 'A 车测绘点位实时写入本地 SQLite。车辆下次进入场地时若服务器离线，系统将自动加载最后一份本地底图，保证业务连续性。',
                },
                {
                  n: 2,
                  title: '云端同步备份',
                  desc: '在测绘界面点击"同步"，底图上传至中心服务器。新 B 车入场时通过内网自动同步该底图，无需重复建图。',
                },
                {
                  n: 3,
                  title: '校准参数持久化',
                  desc: 'B 车在前 3 点算出的转换矩阵 M 永久保存在本地 config.yaml 中，直至下次重新标定。',
                },
              ].map(({ n, title, desc }) => (
                <div key={n} className="flex gap-3">
                  <div className="w-6 h-6 bg-blue-600 rounded-full flex items-center justify-center text-[11px] font-bold text-white shrink-0 mt-0.5">
                    {n}
                  </div>
                  <div>
                    <p className="text-sm font-semibold text-white mb-1">{title}</p>
                    <p className="text-xs text-slate-400 leading-relaxed">{desc}</p>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Core loop */}
          <div>
            <h3 className="flex items-center gap-2 text-base font-bold text-white mb-4">
              <Cpu className="w-5 h-5 text-blue-400" />
              中控机主循环 (10Hz)
            </h3>
            <div className="bg-slate-900/60 rounded-xl border border-slate-700/60 p-5 font-mono text-xs leading-loose">
              {[
                ['text-slate-500', '// 系统主循环 @10Hz'],
                ['text-slate-300', '1. 读取 6 根天线 NMEA + IMU 数据'],
                ['text-slate-300', '2. 姿态解算 (Heading, Roll, Pitch)'],
                ['text-yellow-400', '3. 倾斜补偿: P_ground = f(P_ant, H, Roll, Pitch)'],
                ['text-yellow-400', '4. 矩阵转换: P_final = M × P_ground'],
                ['text-yellow-400', '5. 地形微调: 基于 A 车底图残差修正 Z'],
                ['text-blue-400',   '6. 向 PLC 发送最终坐标报文 (TCP)'],
              ].map(([cls, line], i) => (
                <div key={i} className={`${cls}`}>{line}</div>
              ))}
            </div>
          </div>
        </div>

        {/* Three phases */}
        <div className="border-t border-slate-700/60 pt-6">
          <h3 className="flex items-center gap-2 text-base font-bold text-white mb-5">
            <BookOpen className="w-5 h-5 text-blue-400" />
            工程实施三阶段
          </h3>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {[
              {
                phase: '第一阶段',
                title: 'RTK 手持打点',
                subtitle: '物理底座',
                color: 'blue',
                items: [
                  'Web Mapping 页录入各贝位 RTK 真值',
                  'POST /api/survey/confirm (RTK_HANDHELD, mapping)',
                  '或 CSV 批量导入 batch_import',
                  '写入服务端 survey_points 表',
                ],
              },
              {
                phase: '第二阶段',
                title: 'A 车跑车建图',
                subtitle: '动态残差修正',
                color: 'emerald',
                items: [
                  'VehicleConfig 录入各车 L_arm、跨距等静态参数',
                  '首车 Calibration 全场逐贝采点',
                  '对比 RTK 与车载解算，写入 vehicle_bay_transforms',
                  'delta = measured − ref（含平面与高程）',
                ],
              },
              {
                phase: '第三阶段',
                title: 'B 车校准',
                subtitle: '参数继承',
                color: 'amber',
                items: [
                  '进场后在任意前 3 个点位执行',
                  '系统对比 A 车底图坐标',
                  'SVD 求解转换矩阵 M',
                  '直接调用 A 车 yard_map.json',
                ],
              },
            ].map(({ phase, title, subtitle, color, items }) => {
              const border: Record<string, string> = {
                blue: 'border-blue-500/30',
                emerald: 'border-emerald-500/30',
                amber: 'border-amber-500/30',
              };
              const text: Record<string, string> = {
                blue: 'text-blue-400',
                emerald: 'text-emerald-400',
                amber: 'text-amber-400',
              };
              const bg: Record<string, string> = {
                blue: 'bg-blue-900/10',
                emerald: 'bg-emerald-900/10',
                amber: 'bg-amber-900/10',
              };
              return (
                <div key={phase} className={`rounded-xl border p-5 ${border[color]} ${bg[color]}`}>
                  <div className={`text-[10px] font-bold uppercase ${text[color]} mb-1`}>{phase}</div>
                  <h4 className="font-bold text-white text-sm">{title}</h4>
                  <p className="text-xs text-slate-500 mb-3">{subtitle}</p>
                  <ul className="space-y-1.5">
                    {items.map((item) => (
                      <li key={item} className="flex items-start gap-2 text-xs text-slate-400">
                        <ArrowRight className={`w-3 h-3 ${text[color]} shrink-0 mt-0.5`} />
                        {item}
                      </li>
                    ))}
                  </ul>
                </div>
              );
            })}
          </div>
        </div>

        {/* API reference */}
        <div className="border-t border-slate-700/60 pt-6">
          <h3 className="text-base font-bold text-white mb-4">前后端接口一览</h3>
          <div className="overflow-x-auto">
            <table className="w-full text-sm text-left">
              <thead className="border-b border-slate-700/60">
                <tr>
                  <th className="section-title pb-3 pr-6">接口路径</th>
                  <th className="section-title pb-3 pr-6">方法</th>
                  <th className="section-title pb-3">说明</th>
                </tr>
              </thead>
              <tbody>
                {[
                  ['/api/survey/confirm',    'POST',       'RTK 建图（mapping）或车辆校准采点（calibration）'],
                  ['/api/survey/batch_import','POST',      'CSV 批量导入 RTK 真值'],
                  ['/api/survey/finalize',   'POST',       '标记校准完成（001 全场 / 002 求 M）'],
                  ['/api/yard/map',          'GET',        '堆场 RTK + 校准聚合查询'],
                  ['/api/vehicle/config',    'POST / GET', '车型静态参数存取'],
                  ['ws://host/ws/realtime',  'WebSocket',  '中控机实时推送坐标（10Hz+）'],
                ].map(([path, method, desc]) => (
                  <tr key={path} className="border-b border-slate-700/30 hover:bg-slate-700/20 transition-colors">
                    <td className="py-3 pr-6 font-mono text-xs text-blue-400">{path}</td>
                    <td className="py-3 pr-6 text-xs text-amber-400 font-bold">{method}</td>
                    <td className="py-3 text-xs text-slate-400">{desc}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        {/* WebSocket payload */}
        <div className="border-t border-slate-700/60 pt-6">
          <h3 className="text-base font-bold text-white mb-4">WebSocket 实时数据格式</h3>
          <div className="bg-slate-900/70 rounded-xl border border-slate-700/60 p-5 font-mono text-xs text-slate-400 leading-relaxed overflow-x-auto">
            <pre>{`{
  "timestamp": "2026-05-09T10:30:45.123Z",
  "gps_coord": { "lat": 22.123456, "lon": 114.234567, "alt": 12.345 },
  "local_coord": { "x": 123.45, "y": 234.56, "z": 1.23 },
  "heading": 45.6,
  "roll": 0.25,
  "pitch": 0.12,
  "speed": 0.35,
  "status": "fix | float | no_signal",
  "receivers": [
    { "id": "recv1", "label": "...", "status": "fix", "satellites": 14, "hdop": 0.8 }
  ],
  "gantry": {
    "elec_center":   { "x": 123.4512, "y": 5.1234 },
    "diesel_center": { "x": 123.4512, "y": -5.1234 },
    "leg_offsets": { "fl": 0.0082, "fr": -0.0115, "rl": 0.0097, "rr": -0.0091 },
    "speed": 0.35
  },
  "trolley": {
    "valid": true,
    "position": { "x": 0.012, "y": 8.500, "z": 0.005 },
    "travel": 8.500,
    "speed":  0.25
  }
}`}</pre>
          </div>
        </div>
      </div>
    </div>
  );
}
