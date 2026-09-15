import { useEffect, useRef, useState, useCallback } from 'react';
import type { RealtimeData } from '../types';
import { logger } from '../utils/logger';

// 空白数据（未连接时的默认占位）
const BLANK_DATA: RealtimeData = {
  timestamp: '',
  lpoint_geo: { lat: 0, lon: 0, alt: 0 },
  local_coord: { x: 0, y: 0, z: 0 },
  ref_antenna: { lat: 0, lon: 0, alt: 0, receiver_id: '' },
  heading: 0,
  roll: 0,
  pitch: 0,
  speed: 0,
  status: 'no_signal',
  antenna2: { lat: 0, lon: 0, alt: 0, valid: false },
  receivers: [],
  gantry: {
    elec_lpoint: { x: 0, y: 0, valid: false },
    diesel_lpoint: { x: 0, y: 0, valid: false },
    receiver_left: { x: 0, y: 0, valid: false },
    receiver_right: { x: 0, y: 0, valid: false },
    elec_center: { x: 0, y: 0, valid: false },
    diesel_center: { x: 0, y: 0, valid: false },
    leg_offsets: { fl: 0, fr: 0, rl: 0, rr: 0 },
    speed: 0,
  },
  trolley: {
    valid: false,
    position: { x: 0, y: 0, z: 0 },
    travel: 0,
    speed: 0,
  },
};

// 模拟数据生成（无后端时使用）
function generateMockData(): RealtimeData {
  const now = new Date();
  const t = now.getTime() / 1000;
  const trolleyTravel = 8.5 + Math.sin(t * 0.3) * 6.0;
  const lpointX = 123.45 + Math.sin(t * 0.2) * 0.02;
  const lpointY = 234.56 + Math.cos(t * 0.15) * 0.02;
  const lpointZ = 1.23 + Math.sin(t * 0.5) * 0.01;
  return {
    timestamp: now.toISOString(),
    lpoint_geo: {
      lat: 22.123450 + Math.sin(t * 0.1) * 0.00001,
      lon: 114.234560 + Math.cos(t * 0.1) * 0.00001,
      alt: 12.300 + Math.sin(t * 0.3) * 0.05,
    },
    ref_antenna: {
      lat: 22.123456 + Math.sin(t * 0.1) * 0.00001,
      lon: 114.234567 + Math.cos(t * 0.1) * 0.00001,
      alt: 12.345 + Math.sin(t * 0.3) * 0.05,
      receiver_id: 'gantry_a1+gantry_a2',
    },
    local_coord: { x: lpointX, y: lpointY, z: lpointZ },
    heading: (180.5 + Math.sin(t * 0.05) * 0.5 + 360) % 360,
    roll: 0.25 + Math.sin(t * 0.3) * 0.05,
    pitch: 0.12 + Math.cos(t * 0.4) * 0.03,
    speed: Math.abs(Math.sin(t * 0.1)) * 0.8,
    status: 'fix',
    receivers: [
      { id: 'recv1', label: 'Recv-1 (大车左前)', position: 'FL', status: 'fix', satellites: 14, hdop: 0.8 },
      { id: 'recv2', label: 'Recv-2 (大车右后)', position: 'RR', status: 'fix', satellites: 13, hdop: 0.9 },
      { id: 'recv3', label: 'Recv-3 (小车)', position: 'T',  status: 'fix', satellites: 15, hdop: 0.7 },
    ],
    gantry: {
      elec_lpoint: {
        x: 123.45 + Math.sin(t * 0.2) * 0.02,
        y: 5.12 + Math.sin(t * 0.1) * 0.005,
        valid: true,
      },
      diesel_lpoint: {
        x: 123.45 + Math.sin(t * 0.2) * 0.02,
        y: -5.12 + Math.cos(t * 0.1) * 0.005,
        valid: true,
      },
      receiver_left: {
        x: 123.45 + Math.sin(t * 0.2) * 0.02,
        y: 5.12 + Math.sin(t * 0.1) * 0.005,
        valid: true,
      },
      receiver_right: {
        x: 123.45 + Math.sin(t * 0.2) * 0.02,
        y: -5.12 + Math.cos(t * 0.1) * 0.005,
        valid: true,
      },
      elec_center: {
        x: 123.45 + Math.sin(t * 0.2) * 0.02,
        y: 5.12 + Math.sin(t * 0.1) * 0.005,
        valid: true,
      },
      diesel_center: {
        x: 123.45 + Math.sin(t * 0.2) * 0.02,
        y: -5.12 + Math.cos(t * 0.1) * 0.005,
        valid: true,
      },
      leg_offsets: {
        fl:  0.008 + Math.sin(t * 0.3) * 0.004,
        fr: -0.012 + Math.cos(t * 0.3) * 0.004,
        rl:  0.010 + Math.sin(t * 0.4) * 0.004,
        rr: -0.009 + Math.cos(t * 0.4) * 0.004,
      },
      speed: Math.sin(t * 0.1) * 0.8,
    },
    trolley: {
      valid: true,
      position: {
        x: Math.sin(t * 0.2) * 0.02,
        y: trolleyTravel,
        z: Math.cos(t * 0.2) * 0.01,
      },
      travel: trolleyTravel,
      speed: Math.cos(t * 0.3) * 0.5,
    },
  };
}

interface UseWebSocketOptions {
  /** WebSocket 完整地址，如 "ws://192.168.1.101:8000/ws/realtime"。为 null 时不尝试连接 */
  url: string | null;
  /** 强制使用模拟数据，忽略 url */
  forceMock: boolean;
  /** 收到新设备 discovery 时回调 */
  onDiscovery?: (dev: { temp_id: string; sn: string }) => void;
}

interface UseWebSocketReturn {
  data: RealtimeData;
  /** 是否已与中控机建立真实 WebSocket 连接 */
  connected: boolean;
  /** 当前是否处于模拟数据模式 */
  isMock: boolean;
  /** 连接状态：connecting | connected | disconnected */
  connStatus: 'connecting' | 'connected' | 'disconnected';
}

export function useWebSocket({ url, forceMock, onDiscovery }: UseWebSocketOptions): UseWebSocketReturn {
  const [data, setData] = useState<RealtimeData>(BLANK_DATA);
  const [connected, setConnected] = useState(false);
  const [connStatus, setConnStatus] = useState<'connecting' | 'connected' | 'disconnected'>('disconnected');
  const wsRef = useRef<WebSocket | null>(null);
  const mockTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const onDiscoveryRef = useRef(onDiscovery);
  onDiscoveryRef.current = onDiscovery;

  const startMock = useCallback(() => {
    if (mockTimerRef.current) return;
    mockTimerRef.current = setInterval(() => {
      setData(generateMockData());
    }, 200);
  }, []);

  const stopMock = useCallback(() => {
    if (mockTimerRef.current) {
      clearInterval(mockTimerRef.current);
      mockTimerRef.current = null;
    }
  }, []);

  useEffect(() => {
    // 关闭已有连接
    wsRef.current?.close();
    wsRef.current = null;
    setConnected(false);

    // 强制模拟模式
    if (forceMock) {
      logger.info('WebSocket', '模拟模式已启用');
      setConnStatus('disconnected');
      startMock();
      return () => { stopMock(); };
    }

    // 未指定 url（未选车辆）：停止模拟，重置为空白数据
    if (!url) {
      logger.debug('WebSocket', '未指定 URL，重置为空白数据');
      stopMock();
      setData(BLANK_DATA);
      setConnStatus('disconnected');
      return () => { stopMock(); };
    }

    // 真实连接模式
    logger.info('WebSocket', '准备连接', { url });
    stopMock();
    setConnStatus('connecting');

    let retryTimer: ReturnType<typeof setTimeout>;
    let linkTimer: ReturnType<typeof setTimeout>;      // 链路保活（任意 WS 帧）
    let telemetryTimer: ReturnType<typeof setTimeout>; // 遥测超时（仅 telemetry）
    let ws: WebSocket | null = null;
    let cancelled = false;

    const LINK_SILENCE_MS = 30_000;
    const TELEMETRY_SILENCE_MS = 15_000;

    /** 任意 WS 帧重置链路计时（connected / status 也算在线） */
    const resetLinkTimer = () => {
      clearTimeout(linkTimer);
      linkTimer = setTimeout(() => {
        if (cancelled) return;
        logger.warn('WebSocket', '30s 内无任何 WS 帧，主动重连', { url });
        ws?.close();
      }, LINK_SILENCE_MS);
    };

    /** 收到 telemetry 后才开始遥测超时检测 */
    const resetTelemetryTimer = () => {
      clearTimeout(telemetryTimer);
      telemetryTimer = setTimeout(() => {
        if (cancelled) return;
        logger.warn(
          'WebSocket',
          '15s 内无 telemetry，可能 MQTT 未连通或中控未推送，保持连接并重试',
          { url },
        );
        // 不主动断线，仅告警；链路仍由 resetLinkTimer 保护
      }, TELEMETRY_SILENCE_MS);
    };

    const connect = () => {
      if (cancelled) return;
      try {
        ws = new WebSocket(url);
        wsRef.current = ws;

        ws.onopen = () => {
          if (cancelled) { ws?.close(); return; }
          logger.info('WebSocket', '连接成功', { url });
          setConnected(true);
          setConnStatus('connected');
          stopMock();
          resetLinkTimer();
        };

        ws.onmessage = (e) => {
          resetLinkTimer();
          try {
            const msg = JSON.parse(e.data);
            // 后端 WebSocket 会推送多种消息类型：telemetry / status / discovery / connected
            if (msg.type === 'connected') {
              logger.info('WebSocket', '后端 WS 就绪', { vehicle_id: msg.vehicle_id });
              return;
            }
            if (msg.type === 'discovery') {
              const d = msg.data ?? {};
              const tempId = d.temp_id as string | undefined;
              if (tempId) {
                logger.info('WebSocket', '发现新设备', { temp_id: tempId, sn: d.sn });
                onDiscoveryRef.current?.({ temp_id: tempId, sn: String(d.sn ?? '') });
              }
              return;
            }
            if (msg.type === 'status') {
              logger.debug('WebSocket', '设备状态', msg.data);
              return;
            }
            if (msg.type && msg.type !== 'telemetry') {
              logger.debug('WebSocket', `非 telemetry 消息，跳过: type=${msg.type}`);
              return;
            }
            resetTelemetryTimer();
            const raw = msg.type === 'telemetry' ? msg.data : msg;
            const lpointGeo = raw.lpoint_geo ?? raw.gps_coord;
            const refAntenna = raw.ref_antenna ?? raw.gps_coord;
            if (!lpointGeo || !raw.local_coord) {
              logger.warn('WebSocket', '数据帧缺少必要字段，跳过', { keys: Object.keys(raw) });
              return;
            }
            const gantryRaw = raw.gantry ?? {};
            const trolleyRaw = raw.trolley ?? {};
            const trolleyPos = trolleyRaw.position ?? {
              x: typeof trolleyRaw.travel === 'number' ? 0 : 0,
              y: trolleyRaw.travel ?? 0,
              z: 0,
            };
            const elecLp = gantryRaw.elec_lpoint ?? gantryRaw.receiver_left ?? gantryRaw.elec_center;
            const dieselLp = gantryRaw.diesel_lpoint ?? gantryRaw.receiver_right ?? gantryRaw.diesel_center;
            const ant2Raw = raw.antenna2;
            const payload: RealtimeData = {
              ...BLANK_DATA,
              ...raw,
              gantry_antennas: raw.gantry_antennas ?? {},
              lpoint_geo: lpointGeo,
              ref_antenna: {
                lat: refAntenna.lat,
                lon: refAntenna.lon,
                alt: refAntenna.alt,
                receiver_id: raw.ref_antenna?.receiver_id ?? '',
                ecef_x: raw.ref_antenna?.ecef_x,
                ecef_y: raw.ref_antenna?.ecef_y,
                ecef_z: raw.ref_antenna?.ecef_z,
              },
              antenna2: ant2Raw ? {
                lat: ant2Raw.lat ?? 0,
                lon: ant2Raw.lon ?? 0,
                alt: ant2Raw.alt ?? 0,
                ecef_x: ant2Raw.ecef_x,
                ecef_y: ant2Raw.ecef_y,
                ecef_z: ant2Raw.ecef_z,
                valid: ant2Raw.valid === true || (
                  Math.abs(ant2Raw.lat ?? 0) > 1e-6
                  && Math.abs(ant2Raw.lon ?? 0) > 1e-6
                  && (
                    Math.abs((ant2Raw.lat ?? 0) - (refAntenna.lat ?? 0)) > 1e-7
                    || Math.abs((ant2Raw.lon ?? 0) - (refAntenna.lon ?? 0)) > 1e-7
                  )
                ),
              } : BLANK_DATA.antenna2,
              gantry: {
                ...BLANK_DATA.gantry,
                ...gantryRaw,
                elec_lpoint: elecLp ?? BLANK_DATA.gantry.elec_center,
                diesel_lpoint: dieselLp ?? BLANK_DATA.gantry.diesel_center,
                receiver_left: elecLp ?? BLANK_DATA.gantry.receiver_left,
                receiver_right: dieselLp ?? BLANK_DATA.gantry.receiver_right,
                elec_center: gantryRaw.elec_center ?? elecLp ?? BLANK_DATA.gantry.elec_center,
                diesel_center: gantryRaw.diesel_center ?? dieselLp ?? BLANK_DATA.gantry.diesel_center,
              },
              trolley: {
                valid: trolleyRaw.valid,
                position: {
                  x: Number(trolleyPos.x ?? 0),
                  y: Number(trolleyPos.y ?? 0),
                  z: Number(trolleyPos.z ?? 0),
                },
                body_position: trolleyRaw.body_position ? {
                  x: Number(trolleyRaw.body_position.x ?? 0),
                  y: Number(trolleyRaw.body_position.y ?? 0),
                  z: Number(trolleyRaw.body_position.z ?? 0),
                } : undefined,
                origin_body: trolleyRaw.origin_body ? {
                  x: Number(trolleyRaw.origin_body.x ?? 0),
                  y: Number(trolleyRaw.origin_body.y ?? 0),
                  z: Number(trolleyRaw.origin_body.z ?? 0),
                } : undefined,
                origin_mode: trolleyRaw.origin_mode,
                travel: Number(trolleyRaw.travel ?? trolleyPos.y ?? 0),
                speed: Number(trolleyRaw.speed ?? 0),
              },
            };
            logger.debug('WebSocket', '收到数据帧', { ts: payload.timestamp, status: payload.status });
            setData(payload);
          } catch {
            logger.warn('WebSocket', '数据帧解析失败', { raw: e.data?.slice?.(0, 80) });
          }
        };

        ws.onerror = (ev) => {
          logger.warn('WebSocket', '连接出错，等待重连', { url, ev });
          setConnStatus('connecting');
        };

        ws.onclose = (ev) => {
          if (cancelled) return;
          clearTimeout(linkTimer);
          clearTimeout(telemetryTimer);
          logger.warn('WebSocket', `连接关闭 (code=${ev.code})，5s 后重连`, { url });
          setConnected(false);
          setConnStatus('connecting');
          retryTimer = setTimeout(connect, 5000);
        };
      } catch {
        setConnStatus('connecting');
        startMock();
        retryTimer = setTimeout(connect, 5000);
      }
    };

    connect();

    return () => {
      cancelled = true;
      clearTimeout(retryTimer);
      clearTimeout(linkTimer);
      clearTimeout(telemetryTimer);
      stopMock();
      ws?.close();
    };
  }, [url, forceMock, startMock, stopMock]);

  // isMock 仅在强制模拟时为 true；
  // url=null（未选车辆）或连接中 均属于"未连接"状态，不算模拟
  const isMock = forceMock;

  return { data, connected, isMock, connStatus };
}
