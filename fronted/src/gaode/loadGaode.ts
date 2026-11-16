/**
 * 动态插入高德 JS。调用方必须是现场页且用户已选择高德底图。
 */
export interface GaodeOverlay {
  setMap(map: GaodeMap | null): void;
}

export interface GaodeMap {
  destroy(): void;
  setCenter(center: [number, number]): void;
  zoomIn(): void;
  zoomOut(): void;
  setFitView(overlays?: GaodeOverlay[] | null, immediately?: boolean, margins?: number[]): void;
  setLayers(layers: unknown[]): void;
  on(event: string, handler: () => void): void;
  resize(): void;
}

export interface GaodeNamespace {
  Map: new (el: HTMLElement, opts: Record<string, unknown>) => GaodeMap;
  Polygon: new (opts: Record<string, unknown>) => GaodeOverlay;
  Polyline: new (opts: Record<string, unknown>) => GaodeOverlay;
  CircleMarker: new (opts: Record<string, unknown>) => GaodeOverlay;
  Text: new (opts: Record<string, unknown>) => GaodeOverlay;
  Pixel: new (x: number, y: number) => unknown;
  TileLayer: {
    new (opts?: Record<string, unknown>): unknown;
    Satellite: new () => unknown;
    RoadNet: new () => unknown;
  };
}

declare global {
  interface Window {
    AMap?: GaodeNamespace;
    _AMapSecurityConfig?: { securityJsCode: string };
  }
}

const SCRIPT_ID = 'rtg-gaode-script';
let pending: Promise<void> | null = null;
let loadedKey = '';

export function loadGaode(key: string, securityJsCode: string): Promise<void> {
  if (window.AMap) {
    if (loadedKey === key) return Promise.resolve();
    return Promise.reject(new Error('密钥已更换，请刷新页面后再加载高德底图'));
  }
  if (pending) return pending;
  pending = new Promise((resolve, reject) => {
    if (securityJsCode) window._AMapSecurityConfig = { securityJsCode };
    const script = document.createElement('script');
    script.id = SCRIPT_ID;
    script.async = true;
    script.src = `https://webapi.amap.com/maps?v=2.0&key=${encodeURIComponent(key)}`;
    script.onload = () => {
      if (window.AMap) {
        loadedKey = key;
        resolve();
      } else {
        pending = null;
        script.remove();
        reject(new Error('高德脚本已下载，但没有初始化'));
      }
    };
    script.onerror = () => {
      pending = null;
      script.remove();
      reject(new Error('高德脚本加载失败'));
    };
    document.head.appendChild(script);
  });
  return pending;
}
