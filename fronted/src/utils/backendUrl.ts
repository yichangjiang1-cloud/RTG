import type { BackendConfig } from '../types';

/**
 * 后端 HTTP 根地址。
 * - host 为 `auto`（或空）：同源相对路径，走 Nginx 反代 /api（Docker 部署推荐）
 * - 否则：http://host:port（本机 Vite 开发填 localhost:8000）
 */
export function httpBase(cfg: BackendConfig): string {
  const host = (cfg.host || '').trim();
  if (!host || host.toLowerCase() === 'auto') {
    return '';
  }
  return `http://${host}:${cfg.port}`;
}

/**
 * WebSocket 完整 URL（含 path + query）。
 * auto 时使用当前页面的 host（经 Nginx /ws 反代）。
 */
export function wsUrl(cfg: BackendConfig, pathAndQuery: string): string {
  const path = pathAndQuery.startsWith('/') ? pathAndQuery : `/${pathAndQuery}`;
  const host = (cfg.host || '').trim();
  if (!host || host.toLowerCase() === 'auto') {
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${proto}//${window.location.host}${path}`;
  }
  return `ws://${host}:${cfg.port}${path}`;
}
