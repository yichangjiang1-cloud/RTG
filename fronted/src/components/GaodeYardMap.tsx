import { useEffect, useRef, useState, type FormEvent } from 'react';
import type { GantryAntennaPoint, RealtimeData } from '../types';
import { loadGaode, type GaodeMap, type GaodeNamespace, type GaodeOverlay } from '../gaode/loadGaode';
import { wgs84ToGcj02 } from '../gaode/wgs84ToGcj02';
import { logger } from '../utils/logger';

const KEY_STORE = 'rtg.gaode.key';
const SECURITY_STORE = 'rtg.gaode.security';

export interface GaodeScene {
  originLat: number;
  originLon: number;
  boundary: { lat: number; lon: number }[];
  bays: { bay: number; side: 'elec' | 'diesel'; lat: number; lon: number; east: number; north: number }[];
}

const ANTENNAS: { key: string; label: string; color: string }[] = [
  { key: 'a1', label: '电气1', color: '#34d399' },
  { key: 'a2', label: '电气2', color: '#6ee7b7' },
  { key: 'b1', label: '动力1', color: '#fb923c' },
  { key: 'b2', label: '动力2', color: '#fdba74' },
];

function gcjPair(lat: number, lon: number): [number, number] {
  const p = wgs84ToGcj02(lat, lon);
  return [p.lon, p.lat];
}

function moveMeters(lat: number, lon: number, eastM: number, northM: number) {
  const latRad = (lat * Math.PI) / 180;
  return {
    lat: lat + (northM / 6378137) * (180 / Math.PI),
    lon: lon + (eastM / (6378137 * Math.cos(latRad))) * (180 / Math.PI),
  };
}

function usable(p?: GantryAntennaPoint | null): p is GantryAntennaPoint {
  if (!p) return false;
  if (p.valid === false) return false;
  return Number.isFinite(p.lat) && Number.isFinite(p.lon) && (Math.abs(p.lat) > 1e-6 || Math.abs(p.lon) > 1e-6);
}

function textStyle(color: string): Record<string, string> {
  return {
    'background-color': 'transparent',
    border: 'none',
    color,
    'font-size': '12px',
    'text-shadow': '0 0 3px #000, 0 0 2px #000',
    padding: '0',
  };
}

function satelliteLayers(amap: GaodeNamespace) {
  return [new amap.TileLayer.Satellite(), new amap.TileLayer.RoadNet()];
}

export default function GaodeYardMap({
  scene,
  data,
  follow,
  onReleaseFollow,
  command,
}: {
  scene: GaodeScene | null;
  data: RealtimeData;
  follow: boolean;
  onReleaseFollow: () => void;
  command: { id: number; type: 'in' | 'out' | 'fit' } | null;
}) {
  const hostRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<GaodeMap | null>(null);
  const sceneRef = useRef(scene);
  sceneRef.current = scene;
  const [key, setKey] = useState(() => localStorage.getItem(KEY_STORE) ?? '');
  const [security, setSecurity] = useState(() => localStorage.getItem(SECURITY_STORE) ?? '');
  const [activeKey, setActiveKey] = useState(() => localStorage.getItem(KEY_STORE) ?? '');
  const [activeSecurity, setActiveSecurity] = useState(() => localStorage.getItem(SECURITY_STORE) ?? '');
  const [editing, setEditing] = useState(() => !localStorage.getItem(KEY_STORE));
  const [error, setError] = useState('');
  const [readyTick, setReadyTick] = useState(0);
  const [imagery, setImagery] = useState<'satellite' | 'normal'>('satellite');

  useEffect(() => {
    if (editing || !activeKey) return;
    const host = hostRef.current;
    if (!host) return;
    let cancelled = false;
    setError('');
    loadGaode(activeKey, activeSecurity)
      .then(() => {
        if (cancelled || !hostRef.current || !window.AMap) return;
        const amap = window.AMap;
        const current = sceneRef.current;
        const center = current
          ? gcjPair(current.originLat, current.originLon)
          : gcjPair(32.2, 119.05);
        const map = new amap.Map(hostRef.current, {
          viewMode: '2D',
          zoom: 16,
          center,
          layers: [new amap.TileLayer.Satellite(), new amap.TileLayer.RoadNet()],
          resizeEnable: true,
        });
        map.on('dragstart', () => {
          if (!cancelled) onReleaseFollow();
        });
        mapRef.current = map;
        setReadyTick((n) => n + 1);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          const message = err instanceof Error ? err.message : '高德底图加载失败';
          setError(message);
          logger.warn('Gaode', message);
        }
      });
    return () => {
      cancelled = true;
      mapRef.current?.destroy();
      mapRef.current = null;
    };
  }, [activeKey, activeSecurity, editing, onReleaseFollow]);

  useEffect(() => {
    const map = mapRef.current;
    const amap = window.AMap;
    if (!map || !amap || !readyTick || editing) return;
    map.setLayers(imagery === 'satellite' ? satelliteLayers(amap) : [new amap.TileLayer()]);
  }, [imagery, readyTick, editing]);

  useEffect(() => {
    const map = mapRef.current;
    const amap = window.AMap;
    if (!map || !amap || !readyTick || editing) return;
    const created: GaodeOverlay[] = [];
    const add = (overlay: GaodeOverlay) => {
      created.push(overlay);
      overlay.setMap(map);
    };
    if (scene && scene.boundary.length >= 3) {
      add(new amap.Polygon({
        path: scene.boundary.map((p) => gcjPair(p.lat, p.lon)),
        strokeColor: '#fbbf24',
        strokeWeight: 2,
        fillColor: '#fbbf24',
        fillOpacity: 0.16,
        bubble: true,
        zIndex: 10,
      }));
    }
    const elec = scene ? scene.bays.filter((b) => b.side !== 'diesel') : [];
    const diesel = scene ? scene.bays.filter((b) => b.side === 'diesel') : [];
    const stroke = (bays: GaodeScene['bays'], color: string) => {
      if (bays.length < 2) return;
      add(new amap.Polyline({
        path: bays.map((p) => gcjPair(p.lat, p.lon)),
        strokeColor: color,
        strokeWeight: 3,
        bubble: true,
        zIndex: 20,
      }));
    };
    stroke(elec, '#38bdf8');
    stroke(diesel, '#fb923c');
    if (scene) {
      for (const bay of scene.bays) {
        const dieselSide = bay.side === 'diesel';
        const group = dieselSide ? diesel : elec;
        add(new amap.CircleMarker({
          center: gcjPair(bay.lat, bay.lon),
          radius: 4,
          strokeColor: dieselSide ? '#fb923c' : '#38bdf8',
          strokeWeight: 1,
          fillColor: dieselSide ? '#fb923c' : '#38bdf8',
          fillOpacity: 1,
          bubble: true,
          zIndex: 21,
        }));
        const showLabel = group.length <= 16 || bay.bay === 1 || bay.bay % 5 === 0;
        if (!showLabel) continue;
        add(new amap.Text({
          text: String(bay.bay),
          position: gcjPair(bay.lat, bay.lon),
          offset: new amap.Pixel(6, dieselSide ? 6 : -16),
          style: textStyle(dieselSide ? '#fdba74' : '#e0f2fe'),
          bubble: true,
          zIndex: 22,
        }));
      }
      const origin = gcjPair(scene.originLat, scene.originLon);
      const west = moveMeters(scene.originLat, scene.originLon, -4, 0);
      const east = moveMeters(scene.originLat, scene.originLon, 4, 0);
      const south = moveMeters(scene.originLat, scene.originLon, 0, -4);
      const north = moveMeters(scene.originLat, scene.originLon, 0, 4);
      add(new amap.Polyline({
        path: [gcjPair(west.lat, west.lon), gcjPair(east.lat, east.lon)],
        strokeColor: '#f8fafc',
        strokeWeight: 2,
        bubble: true,
        zIndex: 30,
      }));
      add(new amap.Polyline({
        path: [gcjPair(south.lat, south.lon), gcjPair(north.lat, north.lon)],
        strokeColor: '#f8fafc',
        strokeWeight: 2,
        bubble: true,
        zIndex: 30,
      }));
      add(new amap.Text({
        text: '原点',
        position: origin,
        offset: new amap.Pixel(8, -16),
        style: textStyle('#f8fafc'),
        bubble: true,
        zIndex: 31,
      }));
      const axis = elec.length >= 2 ? elec : scene.bays;
      if (axis.length >= 2) {
        const a = axis[0];
        const b = axis[axis.length - 1];
        const de = b.east - a.east;
        const dn = b.north - a.north;
        const len = Math.hypot(de, dn) || 1;
        const tip = moveMeters(scene.originLat, scene.originLon, (de / len) * 18, (dn / len) * 18);
        const tipGcj = gcjPair(tip.lat, tip.lon);
        add(new amap.Polyline({
          path: [origin, tipGcj],
          strokeColor: '#a78bfa',
          strokeWeight: 2,
          bubble: true,
          zIndex: 30,
        }));
        add(new amap.Text({
          text: '+Y 贝位增大',
          position: tipGcj,
          offset: new amap.Pixel(6, -14),
          style: textStyle('#c4b5fd'),
          bubble: true,
          zIndex: 31,
        }));
      }
    }
    return () => {
      for (const overlay of created) overlay.setMap(null);
    };
  }, [scene, readyTick, editing]);

  useEffect(() => {
    const map = mapRef.current;
    const amap = window.AMap;
    if (!map || !amap || !readyTick || editing) return;
    const created: GaodeOverlay[] = [];
    const add = (overlay: GaodeOverlay) => {
      created.push(overlay);
      overlay.setMap(map);
    };
    const marks: { lat: number; lon: number; label: string; color: string }[] = [];
    const ants = data.gantry_antennas || {};
    for (const item of ANTENNAS) {
      const point = ants[item.key];
      if (!usable(point)) continue;
      marks.push({ lat: point.lat, lon: point.lon, label: item.label, color: item.color });
    }
    const trolley = data.trolley_antennas;
    for (const [point, label, color] of [
      [trolley?.primary, '小车1', '#60a5fa'],
      [trolley?.secondary, '小车2', '#93c5fd'],
    ] as const) {
      if (!usable(point)) continue;
      marks.push({ lat: point.lat, lon: point.lon, label, color });
    }
    const gantry = marks.filter((m) => m.label.startsWith('电气') || m.label.startsWith('动力'));
    const order = ['电气1', '电气2', '动力2', '动力1'];
    const ring = order
      .map((label) => gantry.find((m) => m.label === label))
      .filter((m): m is (typeof marks)[number] => Boolean(m));
    if (ring.length >= 3) {
      add(new amap.Polygon({
        path: ring.map((p) => gcjPair(p.lat, p.lon)),
        strokeColor: '#e2e8f0',
        strokeWeight: 2,
        fillColor: '#34d399',
        fillOpacity: 0.25,
        bubble: true,
        zIndex: 40,
      }));
    }
    for (const mark of marks) {
      const at = gcjPair(mark.lat, mark.lon);
      add(new amap.CircleMarker({
        center: at,
        radius: 6,
        strokeColor: '#0f172a',
        strokeWeight: 1,
        fillColor: mark.color,
        fillOpacity: 1,
        bubble: true,
        zIndex: 50,
      }));
      add(new amap.Text({
        text: mark.label,
        position: at,
        offset: new amap.Pixel(8, -6),
        style: textStyle(mark.color),
        bubble: true,
        zIndex: 51,
      }));
    }
    if (gantry.length && Number.isFinite(data.heading)) {
      const lat = gantry.reduce((s, p) => s + p.lat, 0) / gantry.length;
      const lon = gantry.reduce((s, p) => s + p.lon, 0) / gantry.length;
      const rad = (data.heading * Math.PI) / 180;
      const tip = moveMeters(lat, lon, Math.sin(rad) * 12, Math.cos(rad) * 12);
      add(new amap.Polyline({
        path: [gcjPair(lat, lon), gcjPair(tip.lat, tip.lon)],
        strokeColor: '#f8fafc',
        strokeWeight: 3,
        bubble: true,
        zIndex: 52,
      }));
      if (follow) map.setCenter(gcjPair(lat, lon));
    }
    return () => {
      for (const overlay of created) overlay.setMap(null);
    };
  }, [data, follow, readyTick, editing]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !command || editing) return;
    if (command.type === 'in') map.zoomIn();
    else if (command.type === 'out') map.zoomOut();
    else map.setFitView(undefined, false, [28, 28, 28, 28]);
  }, [command, editing]);

  useEffect(() => {
    const onResize = () => mapRef.current?.resize();
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, [readyTick]);

  const saveKey = (event: FormEvent) => {
    event.preventDefault();
    const nextKey = key.trim();
    const nextSecurity = security.trim();
    if (!nextKey) {
      setError('请填写高德 Web 端 Key');
      return;
    }
    if (window.AMap && activeKey && activeKey !== nextKey) {
      localStorage.setItem(KEY_STORE, nextKey);
      localStorage.setItem(SECURITY_STORE, nextSecurity);
      window.location.reload();
      return;
    }
    localStorage.setItem(KEY_STORE, nextKey);
    localStorage.setItem(SECURITY_STORE, nextSecurity);
    setActiveKey(nextKey);
    setActiveSecurity(nextSecurity);
    setEditing(false);
    setError('');
  };

  if (editing) {
    return (
      <form
        className="h-[32rem] rounded-xl border border-slate-700 bg-slate-950 flex flex-col items-center justify-center gap-3 px-6"
        onSubmit={saveKey}
      >
        <p className="text-sm text-slate-300 text-center max-w-md">
          高德底图需要 Web 端 Key。保存之后才加载高德脚本和瓦片，其它页面不会加载。
        </p>
        <input
          className="w-full max-w-md bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-100"
          placeholder="高德 Key"
          value={key}
          onChange={(e) => setKey(e.target.value)}
          autoComplete="off"
        />
        <input
          className="w-full max-w-md bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-100"
          placeholder="安全密钥，新版 Key 需要"
          value={security}
          onChange={(e) => setSecurity(e.target.value)}
          autoComplete="off"
        />
        {error && <p className="text-sm text-red-400">{error}</p>}
        <button type="submit" className="px-4 py-1.5 rounded-lg text-sm bg-blue-600 text-white">
          加载高德底图
        </button>
      </form>
    );
  }

  return (
    <div className="relative h-[32rem] rounded-xl overflow-hidden border border-slate-700">
      <div ref={hostRef} className="absolute inset-0" />
      <div className="absolute top-2 left-2 z-10 inline-flex overflow-hidden rounded-lg border border-slate-600 bg-slate-950/80">
        <button
          type="button"
          className={`px-2.5 py-1 text-xs ${imagery === 'satellite' ? 'bg-blue-600/40 text-blue-100' : 'text-slate-300'}`}
          onClick={() => setImagery('satellite')}
        >
          卫星
        </button>
        <button
          type="button"
          className={`px-2.5 py-1 text-xs border-l border-slate-600 ${imagery === 'normal' ? 'bg-blue-600/40 text-blue-100' : 'text-slate-300'}`}
          onClick={() => setImagery('normal')}
        >
          标准
        </button>
      </div>
      <button
        type="button"
        className="absolute top-2 right-2 z-10 px-2.5 py-1 rounded-lg text-xs border border-slate-600 bg-slate-950/80 text-slate-300"
        onClick={() => setEditing(true)}
      >
        密钥
      </button>
      {error && (
        <p className="absolute bottom-2 left-2 right-2 z-10 text-sm text-red-300 bg-slate-950/80 rounded-lg px-3 py-2">
          {error}
        </p>
      )}
    </div>
  );
}
