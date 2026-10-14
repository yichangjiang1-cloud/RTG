/**
 * 前端统一日志工具
 * - 开发模式：Console + Vite 终端
 * - 生产模式：默认屏蔽 debug；开启 WS 调试后 info/debug 都打 Console
 *
 * 排查 WebSocket：浏览器控制台执行
 *   localStorage.setItem('rtg_ws_debug', '1'); location.reload();
 * 关闭：
 *   localStorage.removeItem('rtg_ws_debug'); location.reload();
 * 或 URL 加 ?ws_debug=1
 */

/// <reference types="vite/client" />

const isDev = import.meta.env.DEV;

type Level = 'info' | 'warn' | 'error' | 'debug';

const STYLES: Record<Level, string> = {
  info:  'color:#60a5fa;font-weight:bold',
  warn:  'color:#fbbf24;font-weight:bold',
  error: 'color:#f87171;font-weight:bold',
  debug: 'color:#a78bfa;font-weight:bold',
};

function wsDebugEnabled(): boolean {
  try {
    if (typeof window === 'undefined') return false;
    if (new URLSearchParams(window.location.search).get('ws_debug') === '1') return true;
    return localStorage.getItem('rtg_ws_debug') === '1';
  } catch {
    return false;
  }
}

function sendToTerminal(level: Level, module: string, time: string, msg: string, args: unknown[]) {
  fetch('/__dev_log', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ level, module, time, msg, args }),
  }).catch(() => { /* 终端转发失败时静默 */ });
}

const recentLogs = new Map<string, number>();
function isDuplicate(key: string): boolean {
  const now = Date.now();
  const last = recentLogs.get(key);
  if (last && now - last < 100) return true;
  recentLogs.set(key, now);
  if (recentLogs.size > 500) {
    const oldest = [...recentLogs.entries()].sort((a, b) => a[1] - b[1])[0][0];
    recentLogs.delete(oldest);
  }
  return false;
}

function log(level: Level, module: string, msg: string, ...args: unknown[]) {
  const dbg = wsDebugEnabled();
  if (!isDev && level === 'debug' && !dbg) return;
  if ((isDev || dbg) && isDuplicate(`${level}|${module}|${msg}`)) return;
  const d = new Date();
  const time = d.toLocaleTimeString('zh-CN', { hour12: false }) + '.' + String(d.getMilliseconds()).padStart(3, '0');
  const fn = level === 'error' ? console.error
           : level === 'warn'  ? console.warn
           : console.log;
  fn(`%c${time}%c %c[${module}]%c ${msg}`, 'color:#64748b', '', STYLES[level], '', ...args);
  if (isDev) sendToTerminal(level, module, time, msg, args);
}

export const logger = {
  info:  (module: string, msg: string, ...args: unknown[]) => log('info',  module, msg, ...args),
  warn:  (module: string, msg: string, ...args: unknown[]) => log('warn',  module, msg, ...args),
  error: (module: string, msg: string, ...args: unknown[]) => log('error', module, msg, ...args),
  debug: (module: string, msg: string, ...args: unknown[]) => log('debug', module, msg, ...args),
  /** 当前是否开启 WebSocket 调试输出 */
  isWsDebug: wsDebugEnabled,
};
