import { useCallback, useEffect, useRef, useState } from 'react';
import { useAppContext } from '../context/AppContext';
import { httpBase } from '../utils/backendUrl';
import type { GantryAntennaPoint, RealtimeData } from '../types';
import GaodeYardMap from './GaodeYardMap';
import { logger } from '../utils/logger';

const A = 6378137;
const E2 = 0.00669437999014;

interface En {
  east: number;
  north: number;
}

interface GeoPoint extends En {
  lat: number;
  lon: number;
}

interface BayPoint extends GeoPoint {
  bay: number;
  side: 'elec' | 'diesel';
}

interface Scene {
  yardId: string;
  name: string;
  originLat: number;
  originLon: number;
  originAlt: number;
  boundary: GeoPoint[];
  bays: BayPoint[];
}

interface Mark extends En {
  label: string;
  color: string;
}

const GANTRY_KEYS: { key: string; label: string; color: string }[] = [
  { key: 'a1', label: '电气1', color: '#34d399' },
  { key: 'a2', label: '电气2', color: '#6ee7b7' },
  { key: 'b1', label: '动力1', color: '#fb923c' },
  { key: 'b2', label: '动力2', color: '#fdba74' },
];

function wgs84ToEcef(lat: number, lon: number, alt: number): [number, number, number] {
  const sl = Math.sin((lat * Math.PI) / 180);
  const cl = Math.cos((lat * Math.PI) / 180);
  const sn = Math.sin((lon * Math.PI) / 180);
  const cn = Math.cos((lon * Math.PI) / 180);
  const n = A / Math.sqrt(1 - E2 * sl * sl);
  return [(n + alt) * cl * cn, (n + alt) * cl * sn, (n * (1 - E2) + alt) * sl];
}

function toEn(lat: number, lon: number, alt: number, scene: Scene): En {
  const [x, y, z] = wgs84ToEcef(lat, lon, alt);
  const [x0, y0, z0] = wgs84ToEcef(scene.originLat, scene.originLon, scene.originAlt);
  const dx = x - x0;
  const dy = y - y0;
  const dz = z - z0;
  const sl = Math.sin((scene.originLat * Math.PI) / 180);
  const cl = Math.cos((scene.originLat * Math.PI) / 180);
  const sn = Math.sin((scene.originLon * Math.PI) / 180);
  const cn = Math.cos((scene.originLon * Math.PI) / 180);
  return {
    east: -sn * dx + cn * dy,
    north: -sl * cn * dx - sl * sn * dy + cl * dz,
  };
}

function usable(p?: GantryAntennaPoint | null): p is GantryAntennaPoint {
  if (!p) return false;
  if (p.valid === false) return false;
  return Number.isFinite(p.lat) && Number.isFinite(p.lon) && (Math.abs(p.lat) > 1e-6 || Math.abs(p.lon) > 1e-6);
}

function mean(points: En[]): En | null {
  if (!points.length) return null;
  return {
    east: points.reduce((s, p) => s + p.east, 0) / points.length,
    north: points.reduce((s, p) => s + p.north, 0) / points.length,
  };
}

export default function YardLive({ data }: { data: RealtimeData }) {
  const {
    yards, vehicles, selectedYardId, setSelectedYardId,
    selectedVehicleId, setSelectedVehicleId, backendConfig, fetchYards,
  } = useAppContext();
  const userPickedYard = useRef(false);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const viewRef = useRef({ east: 0, north: 0, ppm: 4, follow: true });
  const dragRef = useRef<{ x: number; y: number; east: number; north: number } | null>(null);
  const hudRef = useRef('');
  const [scene, setScene] = useState<Scene | null>(null);
  const [loadError, setLoadError] = useState('');
  const [follow, setFollow] = useState(true);
  const [hud, setHud] = useState('等待场地和天线');
  const [basemap, setBasemap] = useState<'canvas' | 'gaode'>(() => (
    localStorage.getItem('rtg.live.basemap') === 'gaode' ? 'gaode' : 'canvas'
  ));
  const [mapCmd, setMapCmd] = useState<{ id: number; type: 'in' | 'out' | 'fit' } | null>(null);

  useEffect(() => {
    viewRef.current.follow = follow;
  }, [follow]);

  useEffect(() => {
    localStorage.setItem('rtg.live.basemap', basemap);
  }, [basemap]);

  const releaseFollow = useCallback(() => {
    viewRef.current.follow = false;
    setFollow(false);
  }, []);

  useEffect(() => {
    if (yards.length > 0) return;
    const pull = () => { fetchYards().catch(() => {}); };
    pull();
    const timer = window.setInterval(pull, 3000);
    return () => window.clearInterval(timer);
  }, [yards.length, fetchYards]);

  useEffect(() => {
    if (userPickedYard.current || yards.length === 0) return;
    const fromCar = data.yard_id && yards.some((y) => y.yard_id === data.yard_id)
      ? data.yard_id
      : null;
    if (fromCar) setSelectedYardId(fromCar);
    else if (!selectedYardId) setSelectedYardId(yards[0].yard_id);
  }, [yards, selectedYardId, setSelectedYardId, data.yard_id]);

  useEffect(() => {
    if (!selectedVehicleId && vehicles.length > 0) setSelectedVehicleId(vehicles[0].vehicle_id);
  }, [vehicles, selectedVehicleId, setSelectedVehicleId]);

  useEffect(() => {
    if (!selectedYardId) {
      setScene(null);
      return;
    }
    let cancelled = false;
    const base = httpBase(backendConfig);
    (async () => {
      try {
        const [yardsResp, mapResp] = await Promise.all([
          fetch(`${base}/api/yard/all_origins`),
          fetch(`${base}/api/yard/map?yard_id=${encodeURIComponent(selectedYardId)}`),
        ]);
        const yardsJson = await yardsResp.json();
        const mapJson = await mapResp.json();
        const raw = (yardsJson.data || []).find((y: { yard_id: string }) => y.yard_id === selectedYardId);
        if (!raw) throw new Error('场地不在列表里');
        const originLon = Number(raw.origin?.[0]);
        const originLat = Number(raw.origin?.[1]);
        const originAlt = Number(raw.origin?.[2] ?? 0);
        const draft: Scene = {
          yardId: raw.yard_id,
          name: raw.name || raw.yard_id,
          originLat,
          originLon,
          originAlt,
          boundary: [],
          bays: [],
        };
        const boundary = Array.isArray(raw.boundary) ? raw.boundary : [];
        draft.boundary = boundary
          .map((p: { lat?: number; lon?: number }) => {
            const lat = Number(p.lat);
            const lon = Number(p.lon);
            if (!Number.isFinite(lat) || !Number.isFinite(lon)) return null;
            return { ...toEn(lat, lon, originAlt, draft), lat, lon };
          })
          .filter((p: GeoPoint | null): p is GeoPoint => p !== null);
        const points = mapJson.data?.survey_points || [];
        draft.bays = points
          .map((p: { bay_no?: number; bay_side?: string; lat?: number; lon?: number; alt?: number }) => {
            const lat = Number(p.lat);
            const lon = Number(p.lon);
            if (!Number.isFinite(lat) || !Number.isFinite(lon)) return null;
            const en = toEn(lat, lon, Number(p.alt ?? originAlt), draft);
            return {
              bay: Number(p.bay_no),
              side: p.bay_side === 'diesel' ? 'diesel' as const : 'elec' as const,
              lat,
              lon,
              ...en,
            };
          })
          .filter((p: BayPoint | null): p is BayPoint => p !== null && Number.isFinite(p.bay))
          .sort((a: BayPoint, b: BayPoint) => (
            a.side === b.side ? a.bay - b.bay : (a.side === 'elec' ? -1 : 1)
          ));
        if (!cancelled) {
          setScene(draft);
          setLoadError('');
          fitTo(viewRef.current, draft, []);
        }
      } catch (err) {
        if (!cancelled) {
          const message = err instanceof Error ? err.message : '场地加载失败';
          setLoadError(message);
          logger.error('YardLive', `场地加载失败 yard=${selectedYardId}`, message);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selectedYardId, backendConfig]);

  const antennaMarks = useCallback((): Mark[] => {
    if (!scene) return [];
    const ants = data.gantry_antennas || {};
    const marks: Mark[] = [];
    for (const item of GANTRY_KEYS) {
      const p = ants[item.key];
      if (!usable(p)) continue;
      const en = toEn(p.lat, p.lon, Number(p.alt ?? scene.originAlt), scene);
      marks.push({ ...en, label: item.label, color: item.color });
    }
    const trolley = data.trolley_antennas;
    for (const [point, label, color] of [
      [trolley?.primary, '小车1', '#60a5fa'],
      [trolley?.secondary, '小车2', '#93c5fd'],
    ] as const) {
      if (!usable(point)) continue;
      const en = toEn(point.lat, point.lon, Number(point.alt ?? scene.originAlt), scene);
      marks.push({ ...en, label, color });
    }
    return marks;
  }, [data, scene]);

  useEffect(() => {
    const marks = scene ? antennaMarks() : [];
    const gantry = marks.filter((m) => m.label.startsWith('电气') || m.label.startsWith('动力'));
    const center = mean(gantry);
    const elecBays = scene ? scene.bays.filter((b) => b.side !== 'diesel') : [];
    const bayLine = elecBays.length ? elecBays : (scene?.bays ?? []);
    let nearest = '';
    if (scene && center && bayLine.length) {
      let best = bayLine[0];
      let bestD = Infinity;
      for (const bay of bayLine) {
        const d = Math.hypot(bay.east - center.east, bay.north - center.north);
        if (d < bestD) {
          best = bay;
          bestD = d;
        }
      }
      nearest = `最近贝位 ${best.bay}，${bestD.toFixed(1)} m`;
    }
    const text = scene
      ? `${scene.name}  天线 ${gantry.length}/4  航向 ${Number.isFinite(data.heading) ? data.heading.toFixed(1) : '—'}°  ${nearest}`
      : '未选场地';
    if (hudRef.current !== text) {
      hudRef.current = text;
      setHud(text);
    }
  }, [antennaMarks, data.heading, scene]);

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    const wrap = wrapRef.current;
    if (!canvas || !wrap) return;
    const dpr = window.devicePixelRatio || 1;
    const width = wrap.clientWidth;
    const height = wrap.clientHeight || 520;
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    canvas.width = Math.floor(width * dpr);
    canvas.height = Math.floor(height * dpr);
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = '#0b1220';
    ctx.fillRect(0, 0, width, height);

    const marks = scene ? antennaMarks() : [];
    const gantry = marks.filter((m) => m.label.startsWith('电气') || m.label.startsWith('动力'));
    const center = mean(gantry);
    const view = viewRef.current;
    if (view.follow && center) {
      view.east = center.east;
      view.north = center.north;
    }

    const project = (p: En) => ({
      x: width / 2 + (p.east - view.east) * view.ppm,
      y: height / 2 - (p.north - view.north) * view.ppm,
    });

    if (scene && scene.boundary.length >= 3) {
      ctx.beginPath();
      scene.boundary.forEach((p, i) => {
        const s = project(p);
        if (i === 0) ctx.moveTo(s.x, s.y);
        else ctx.lineTo(s.x, s.y);
      });
      ctx.closePath();
      ctx.fillStyle = 'rgba(251, 191, 36, 0.12)';
      ctx.strokeStyle = '#fbbf24';
      ctx.lineWidth = 1.5;
      ctx.fill();
      ctx.stroke();
    }

    const elecBays = scene ? scene.bays.filter((b) => b.side !== 'diesel') : [];
    const dieselBays = scene ? scene.bays.filter((b) => b.side === 'diesel') : [];
    const strokeBays = (bays: BayPoint[], color: string) => {
      if (bays.length < 2) return;
      ctx.beginPath();
      bays.forEach((p, i) => {
        const s = project(p);
        if (i === 0) ctx.moveTo(s.x, s.y);
        else ctx.lineTo(s.x, s.y);
      });
      ctx.strokeStyle = color;
      ctx.lineWidth = 1.5;
      ctx.stroke();
    };
    if (scene) {
      strokeBays(elecBays, 'rgba(56, 189, 248, 0.85)');
      strokeBays(dieselBays, 'rgba(251, 146, 60, 0.9)');
    }

    if (scene) {
      for (const bay of scene.bays) {
        const s = project(bay);
        const diesel = bay.side === 'diesel';
        ctx.fillStyle = diesel ? '#fb923c' : '#38bdf8';
        ctx.beginPath();
        ctx.arc(s.x, s.y, 3, 0, Math.PI * 2);
        ctx.fill();
        const group = diesel ? dieselBays : elecBays;
        const showLabel = group.length <= 16 || bay.bay === 1 || bay.bay % 5 === 0;
        if (showLabel && view.ppm > 1.2) {
          ctx.fillStyle = diesel ? '#fdba74' : '#bae6fd';
          ctx.font = '11px sans-serif';
          ctx.fillText(String(bay.bay), s.x + 5, diesel ? s.y + 12 : s.y - 5);
        }
      }

      const origin = project({ east: 0, north: 0 });
      ctx.strokeStyle = '#f8fafc';
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.moveTo(origin.x - 8, origin.y);
      ctx.lineTo(origin.x + 8, origin.y);
      ctx.moveTo(origin.x, origin.y - 8);
      ctx.lineTo(origin.x, origin.y + 8);
      ctx.stroke();
      ctx.fillStyle = '#f8fafc';
      ctx.font = '12px sans-serif';
      ctx.fillText('原点', origin.x + 10, origin.y - 8);

      const axisBays = elecBays.length >= 2 ? elecBays : scene.bays;
      if (axisBays.length >= 2) {
        const a = axisBays[0];
        const b = axisBays[axisBays.length - 1];
        const de = b.east - a.east;
        const dn = b.north - a.north;
        const len = Math.hypot(de, dn) || 1;
        const tip = project({ east: (de / len) * 18, north: (dn / len) * 18 });
        const tail = project({ east: 0, north: 0 });
        ctx.strokeStyle = '#a78bfa';
        ctx.beginPath();
        ctx.moveTo(tail.x, tail.y);
        ctx.lineTo(tip.x, tip.y);
        ctx.stroke();
        ctx.fillStyle = '#c4b5fd';
        ctx.fillText('+Y 贝位增大', tip.x + 4, tip.y);
      }
    }

    if (gantry.length >= 3) {
      const order = ['电气1', '电气2', '动力2', '动力1'];
      const ring = order
        .map((label) => gantry.find((m) => m.label === label))
        .filter((m): m is Mark => Boolean(m));
      if (ring.length >= 3) {
        ctx.beginPath();
        ring.forEach((p, i) => {
          const s = project(p);
          if (i === 0) ctx.moveTo(s.x, s.y);
          else ctx.lineTo(s.x, s.y);
        });
        ctx.closePath();
        ctx.fillStyle = 'rgba(52, 211, 153, 0.18)';
        ctx.strokeStyle = '#e2e8f0';
        ctx.lineWidth = 1.5;
        ctx.fill();
        ctx.stroke();
      }
    }

    for (const mark of marks) {
      const s = project(mark);
      ctx.fillStyle = mark.color;
      ctx.beginPath();
      ctx.arc(s.x, s.y, 5, 0, Math.PI * 2);
      ctx.fill();
      ctx.font = '12px sans-serif';
      ctx.fillText(mark.label, s.x + 7, s.y + 4);
    }

    if (center && Number.isFinite(data.heading)) {
      const rad = (data.heading * Math.PI) / 180;
      const tip = project({
        east: center.east + Math.sin(rad) * 12,
        north: center.north + Math.cos(rad) * 12,
      });
      const tail = project(center);
      ctx.strokeStyle = '#f8fafc';
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(tail.x, tail.y);
      ctx.lineTo(tip.x, tip.y);
      ctx.stroke();
    }
  }, [antennaMarks, data.heading, scene]);

  useEffect(() => {
    if (basemap !== 'canvas') return;
    draw();
  }, [draw, follow, basemap]);

  useEffect(() => {
    const onResize = () => draw();
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, [draw]);

  const zoomAt = useCallback((factor: number, px?: number, py?: number) => {
    const canvas = canvasRef.current;
    const view = viewRef.current;
    const rect = canvas?.getBoundingClientRect();
    const width = rect?.width || 1;
    const height = rect?.height || 1;
    const x = px ?? width / 2;
    const y = py ?? height / 2;
    const east = view.east + (x - width / 2) / view.ppm;
    const north = view.north - (y - height / 2) / view.ppm;
    view.ppm = Math.min(400, Math.max(0.15, view.ppm * factor));
    if (!view.follow) {
      view.east = east - (x - width / 2) / view.ppm;
      view.north = north + (y - height / 2) / view.ppm;
    }
    draw();
  }, [draw]);

  useEffect(() => {
    if (basemap !== 'canvas') return;
    const el = wrapRef.current;
    if (!el) return;
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      const rect = el.getBoundingClientRect();
      const factor = event.deltaY > 0 ? 0.9 : 1.1;
      zoomAt(factor, event.clientX - rect.left, event.clientY - rect.top);
    };
    el.addEventListener('wheel', onWheel, { passive: false, capture: true });
    return () => el.removeEventListener('wheel', onWheel, { capture: true });
  }, [zoomAt, basemap]);

  const fitAll = () => {
    if (!scene) return;
    if (basemap === 'gaode') {
      releaseFollow();
      setMapCmd({ id: Date.now(), type: 'fit' });
      return;
    }
    const marks = antennaMarks().filter((m) => m.label.startsWith('电气') || m.label.startsWith('动力'));
    fitTo(viewRef.current, scene, marks);
    viewRef.current.follow = false;
    setFollow(false);
    draw();
  };

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <select
          className="bg-slate-900 border border-slate-700 rounded-lg px-2 py-1.5 text-sm text-slate-100"
          value={selectedYardId ?? ''}
          onChange={(e) => {
            userPickedYard.current = true;
            setSelectedYardId(e.target.value || null);
          }}
        >
          <option value="">选择场地</option>
          {yards.map((y) => (
            <option key={y.yard_id} value={y.yard_id}>{y.yard_id} {y.name}</option>
          ))}
        </select>
        <select
          className="bg-slate-900 border border-slate-700 rounded-lg px-2 py-1.5 text-sm text-slate-100"
          value={selectedVehicleId ?? ''}
          onChange={(e) => setSelectedVehicleId(e.target.value || null)}
        >
          <option value="">选择车辆</option>
          {vehicles.map((v) => (
            <option key={v.vehicle_id} value={v.vehicle_id}>{v.vehicle_id}</option>
          ))}
        </select>
        <button
          type="button"
          className={`px-3 py-1.5 rounded-lg text-sm border ${follow ? 'bg-blue-600/30 border-blue-400 text-blue-100' : 'border-slate-600 text-slate-300'}`}
          onClick={() => setFollow(true)}
        >
          跟随车辆
        </button>
        <button
          type="button"
          className="px-3 py-1.5 rounded-lg text-sm border border-slate-600 text-slate-300"
          onClick={fitAll}
        >
          看全场
        </button>
        <div className="inline-flex overflow-hidden rounded-lg border border-slate-600">
          <button
            type="button"
            className={`px-2.5 py-1.5 text-sm ${basemap === 'canvas' ? 'bg-blue-600/30 text-blue-100' : 'text-slate-300 hover:bg-slate-800'}`}
            onClick={() => setBasemap('canvas')}
          >
            自绘底图
          </button>
          <button
            type="button"
            className={`px-2.5 py-1.5 text-sm border-l border-slate-600 ${basemap === 'gaode' ? 'bg-blue-600/30 text-blue-100' : 'text-slate-300 hover:bg-slate-800'}`}
            onClick={() => setBasemap('gaode')}
          >
            高德底图
          </button>
        </div>
        <div className="inline-flex overflow-hidden rounded-lg border border-slate-600">
          <button
            type="button"
            className="px-2.5 py-1.5 text-sm text-slate-200 hover:bg-slate-800"
            onClick={() => {
              if (basemap === 'gaode') setMapCmd({ id: Date.now(), type: 'in' });
              else zoomAt(1.25);
            }}
          >
            放大
          </button>
          <button
            type="button"
            className="px-2.5 py-1.5 text-sm text-slate-200 border-l border-slate-600 hover:bg-slate-800"
            onClick={() => {
              if (basemap === 'gaode') setMapCmd({ id: Date.now(), type: 'out' });
              else zoomAt(0.8);
            }}
          >
            缩小
          </button>
        </div>
        <span className="text-xs text-slate-400">{hud}</span>
      </div>
      {loadError && <p className="text-sm text-red-400">{loadError}</p>}
      {basemap === 'gaode' ? (
        <GaodeYardMap
          scene={scene}
          data={data}
          follow={follow}
          onReleaseFollow={releaseFollow}
          command={mapCmd}
        />
      ) : (
      <div ref={wrapRef} className="h-[32rem] rounded-xl overflow-hidden border border-slate-700 touch-none overscroll-none">
        <canvas
          className="block w-full h-full"
          ref={canvasRef}
          onPointerDown={(e) => {
            const view = viewRef.current;
            dragRef.current = { x: e.clientX, y: e.clientY, east: view.east, north: view.north };
            e.currentTarget.setPointerCapture(e.pointerId);
            view.follow = false;
            setFollow(false);
          }}
          onPointerMove={(e) => {
            const drag = dragRef.current;
            if (!drag) return;
            const view = viewRef.current;
            view.east = drag.east - (e.clientX - drag.x) / view.ppm;
            view.north = drag.north + (e.clientY - drag.y) / view.ppm;
            draw();
          }}
          onPointerUp={() => {
            dragRef.current = null;
          }}
        />
      </div>
      )}
      <p className="text-xs text-slate-500">
        {basemap === 'gaode'
          ? '高德底图上的点、线和文字由本系统叠加上去。画到高德上之前才把 WGS84 转成火星坐标，贝位、校准和 PLC 仍用原来的坐标。拖动地图会取消跟随。'
          : '北向上、东向右，相对所选场地原点。黄框是场地区域，青色是电气侧贝位，橙色是动力侧贝位，白十字是原点，紫线是电气侧贝位增大方向。绿点是电气房侧天线，橙点是动力房侧天线。蓝色小车天线要等中控重启后才有。拖动平移，滚轮或放大按钮只缩放地图。'}
      </p>
    </div>
  );
}

function fitTo(view: { east: number; north: number; ppm: number }, scene: Scene, extra: En[]) {
  const pts: En[] = [...scene.boundary, ...scene.bays, { east: 0, north: 0 }, ...extra];
  if (!pts.length) return;
  let minE = Infinity;
  let maxE = -Infinity;
  let minN = Infinity;
  let maxN = -Infinity;
  for (const p of pts) {
    minE = Math.min(minE, p.east);
    maxE = Math.max(maxE, p.east);
    minN = Math.min(minN, p.north);
    maxN = Math.max(maxN, p.north);
  }
  const spanE = Math.max(maxE - minE, 20);
  const spanN = Math.max(maxN - minN, 20);
  view.east = (minE + maxE) / 2;
  view.north = (minN + maxN) / 2;
  view.ppm = Math.min(1100 / (spanE * 1.2), 640 / (spanN * 1.2));
}
