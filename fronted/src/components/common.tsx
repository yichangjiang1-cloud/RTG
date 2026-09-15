import { useState, useEffect, useCallback } from 'react';
import type { ReactNode } from 'react';
import type { GnssStatus } from '../types';
import { clsx } from 'clsx';

// ============================================================
// StatusDot
// ============================================================
interface StatusDotProps {
  status: GnssStatus;
  pulse?: boolean;
}
export function StatusDot({ status, pulse = true }: StatusDotProps) {
  const colors: Record<GnssStatus, string> = {
    fix: 'bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.8)]',
    float: 'bg-amber-500 shadow-[0_0_8px_rgba(245,158,11,0.8)]',
    dgps: 'bg-sky-500 shadow-[0_0_8px_rgba(14,165,233,0.8)]',
    single: 'bg-slate-400 shadow-[0_0_8px_rgba(148,163,184,0.6)]',
    no_signal: 'bg-red-500 shadow-[0_0_8px_rgba(239,68,68,0.8)]',
  };
  return (
    <span
      className={clsx(
        'inline-block w-2.5 h-2.5 rounded-full',
        colors[status] ?? colors.no_signal,
        pulse && status !== 'no_signal' && 'animate-pulse',
      )}
    />
  );
}

// ============================================================
// StatusBadge
// ============================================================
interface StatusBadgeProps {
  status: GnssStatus;
}
export function StatusBadge({ status }: StatusBadgeProps) {
  const cfg: Record<GnssStatus, { label: string; cls: string }> = {
    fix: { label: 'RTK FIXED', cls: 'badge-fix' },
    float: { label: 'RTK FLOAT', cls: 'badge-float' },
    dgps: { label: 'DGPS', cls: 'badge-float' },
    single: { label: '单点 GPS', cls: 'badge-no-signal' },
    no_signal: { label: 'NO SIGNAL', cls: 'badge-no-signal' },
  };
  const { label, cls } = cfg[status] ?? cfg.no_signal;
  return (
    <span className={clsx('badge', cls)}>
      <StatusDot status={status} pulse={false} />
      {label}
    </span>
  );
}

// ============================================================
// StatCard
// ============================================================
interface StatCardProps {
  label: string;
  value: string | ReactNode;
  sub?: string;
  accent?: 'blue' | 'green' | 'yellow' | 'red' | 'slate';
  className?: string;
}
export function StatCard({ label, value, sub, accent = 'blue', className }: StatCardProps) {
  const accentText: Record<string, string> = {
    blue: 'text-blue-400',
    green: 'text-emerald-400',
    yellow: 'text-amber-400',
    red: 'text-red-400',
    slate: 'text-slate-300',
  };
  return (
    <div className={clsx('card p-5', className)}>
      <p className="section-title mb-2">{label}</p>
      <div className={clsx('text-2xl font-mono font-bold leading-tight', accentText[accent])}>
        {value}
      </div>
      {sub && <p className="text-xs text-slate-500 mt-1">{sub}</p>}
    </div>
  );
}

// ============================================================
// Toast
// ============================================================
interface ToastItem {
  id: number;
  message: string;
  type: 'success' | 'error' | 'info';
}

let _toastFn: ((msg: string, type?: ToastItem['type']) => void) | null = null;

export function showToast(msg: string, type: ToastItem['type'] = 'success') {
  _toastFn?.(msg, type);
}

export function ToastContainer() {
  const [toasts, setToasts] = useState<ToastItem[]>([]);

  const addToast = useCallback((msg: string, type: ToastItem['type'] = 'success') => {
    const id = Date.now();
    setToasts((prev) => [...prev, { id, message: msg, type }]);
    setTimeout(() => setToasts((prev) => prev.filter((t) => t.id !== id)), 3500);
  }, []);

  useEffect(() => {
    _toastFn = addToast;
    return () => { _toastFn = null; };
  }, [addToast]);

  const typeStyle: Record<ToastItem['type'], string> = {
    success: 'bg-emerald-900/90 border-emerald-500/50 text-emerald-200',
    error: 'bg-red-900/90 border-red-500/50 text-red-200',
    info: 'bg-blue-900/90 border-blue-500/50 text-blue-200',
  };

  return (
    <div className="fixed bottom-6 right-6 z-[200] flex flex-col gap-2 pointer-events-none">
      {toasts.map((t) => (
        <div
          key={t.id}
          className={clsx(
            'px-5 py-3 rounded-xl border backdrop-blur-md shadow-2xl text-sm font-medium animate-fade-in',
            typeStyle[t.type],
          )}
        >
          {t.message}
        </div>
      ))}
    </div>
  );
}

// ============================================================
// Divider
// ============================================================
export function Divider({ className }: { className?: string }) {
  return <div className={clsx('border-t border-slate-700/60', className)} />;
}

// ============================================================
// InfoRow
// ============================================================
interface InfoRowProps {
  label: string;
  value: string | ReactNode;
  mono?: boolean;
}
export function InfoRow({ label, value, mono = true }: InfoRowProps) {
  return (
    <div className="flex items-center justify-between py-2 border-b border-slate-700/40 last:border-0">
      <span className="text-xs text-slate-400">{label}</span>
      <span className={clsx('text-sm', mono ? 'font-mono text-slate-200' : 'text-slate-200')}>{value}</span>
    </div>
  );
}

// ============================================================
// SectionHeader
// ============================================================
interface SectionHeaderProps {
  title: string;
  subtitle?: string;
  children?: ReactNode;
}
export function SectionHeader({ title, subtitle, children }: SectionHeaderProps) {
  return (
    <div className="flex items-start justify-between mb-6">
      <div>
        <h2 className="text-xl font-bold text-white">{title}</h2>
        {subtitle && <p className="text-sm text-slate-400 mt-1">{subtitle}</p>}
      </div>
      {children && <div className="flex items-center gap-3">{children}</div>}
    </div>
  );
}

// ============================================================
// Compass
// ============================================================
interface CompassProps {
  heading: number;
  size?: number;
}
export function Compass({ heading, size = 120 }: CompassProps) {
  return (
    <div className="flex flex-col items-center gap-2">
      <div
        className="relative rounded-full border-2 border-slate-600 bg-slate-900 flex items-center justify-center"
        style={{ width: size, height: size }}
      >
        {/* tick marks */}
        {[0, 45, 90, 135, 180, 225, 270, 315].map((deg) => (
          <div
            key={deg}
            className="absolute w-0.5 h-2.5 bg-slate-500 origin-bottom"
            style={{
              transform: `rotate(${deg}deg) translateX(-50%)`,
              left: '50%',
              top: 4,
            }}
          />
        ))}
        <div className="absolute top-1.5 text-[9px] text-red-400 font-bold">N</div>
        <div className="absolute bottom-1.5 text-[9px] text-slate-500 font-bold">S</div>
        <div className="absolute right-1.5 text-[9px] text-slate-500 font-bold">E</div>
        <div className="absolute left-1.5 text-[9px] text-slate-500 font-bold">W</div>
        {/* needle */}
        <div
          className="absolute inset-0 flex items-center justify-center transition-transform duration-300"
          style={{ transform: `rotate(${heading}deg)` }}
        >
          <div className="relative w-1 h-full flex flex-col items-center">
            <div className="flex-1 w-0.5 bg-red-500" style={{ maxHeight: '45%' }} />
            <div className="flex-1 w-0.5 bg-slate-500" style={{ maxHeight: '45%' }} />
          </div>
        </div>
        <div className="w-2 h-2 rounded-full bg-white z-10" />
      </div>
      <span className="text-xs font-mono text-slate-400">{heading.toFixed(1)}°</span>
    </div>
  );
}
