import { useEffect, useRef, useState } from 'react';
import { Crosshair, Wifi, WifiOff, Car, Map, Menu, X } from 'lucide-react';
import { clsx } from 'clsx';
import { StatusBadge } from './common';
import { useAppContext } from '../context/AppContext';
import type { TabId, GnssStatus } from '../types';

interface NavbarProps {
  activeTab: TabId;
  onTabChange: (tab: TabId) => void;
  gnssStatus: GnssStatus;
  isMock: boolean;
  connStatus: 'connecting' | 'connected' | 'disconnected';
}

const TABS: { id: TabId; label: string }[] = [
  { id: 'dashboard',   label: '仪表盘' },
  { id: 'settings',    label: '工作配置' },
  { id: 'config',      label: '参数配置' },
  { id: 'mapping',     label: '场地建图' },
  { id: 'calibration', label: '车辆校准' },
  { id: 'manual',      label: '系统说明' },
];

export default function Navbar({ activeTab, onTabChange, gnssStatus, isMock, connStatus }: NavbarProps) {
  const { vehicles, yards, selectedVehicleId, selectedYardId } = useAppContext();
  const selectedVehicle = vehicles.find(v => v.vehicle_id === selectedVehicleId);
  const selectedYard    = yards.find(y => y.yard_id === selectedYardId);
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setMenuOpen(false);
  }, [activeTab]);

  useEffect(() => {
    if (!menuOpen) return;
    const onPointerDown = (e: PointerEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setMenuOpen(false);
    };
    document.addEventListener('pointerdown', onPointerDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [menuOpen]);

  const selectTab = (tab: TabId) => {
    onTabChange(tab);
    setMenuOpen(false);
  };

  const liveLabel = isMock
    ? 'MOCK'
    : connStatus === 'connected'
      ? 'LIVE'
      : connStatus === 'connecting'
        ? '连接中'
        : '已断开';

  return (
    <nav className="sticky top-0 z-50 bg-slate-900/90 backdrop-blur-xl border-b border-slate-700/60 shadow-2xl">
      {/* 第一行：品牌 + 状态（不再把导航夹在中间挤扁） */}
      <div className="max-w-screen-2xl mx-auto px-3 sm:px-6 h-12 sm:h-14 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 sm:gap-3 shrink-0 min-w-0">
          <div className="w-8 h-8 bg-blue-600/20 border border-blue-500/40 rounded-lg flex items-center justify-center shrink-0">
            <Crosshair className="w-4 h-4 text-blue-400" />
          </div>
          <div className="min-w-0">
            <div className="text-xs sm:text-sm font-bold tracking-widest uppercase leading-none text-white truncate">
              RTG <span className="text-blue-400">POSITIONING</span>
            </div>
            <div className="hidden lg:block text-[9px] text-slate-500 tracking-widest uppercase leading-none mt-0.5 truncate">
              Precision Calibration System v2.5
            </div>
          </div>
        </div>

        <div className="flex items-center gap-1.5 sm:gap-2 shrink-0">
          {/* 窄屏：下拉菜单；宽屏改用第二行横向 Tab */}
          <div className="relative md:hidden" ref={menuRef}>
            <button
              type="button"
              onClick={() => setMenuOpen((v) => !v)}
              className={clsx(
                'flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium border',
                menuOpen
                  ? 'bg-blue-600/20 border-blue-500/40 text-blue-300'
                  : 'bg-slate-800/60 border-slate-700/50 text-slate-200',
              )}
              aria-expanded={menuOpen}
              aria-label="打开导航菜单"
            >
              {menuOpen ? <X className="w-4 h-4" /> : <Menu className="w-4 h-4" />}
              <span className="whitespace-nowrap">
                {TABS.find((t) => t.id === activeTab)?.label ?? '菜单'}
              </span>
            </button>
            {menuOpen && (
              <div className="absolute right-0 top-[calc(100%+0.4rem)] w-48 rounded-xl border border-slate-700/70 bg-slate-900 p-1.5 shadow-2xl z-50">
                {TABS.map((tab) => (
                  <button
                    key={tab.id}
                    type="button"
                    onClick={() => selectTab(tab.id)}
                    className={clsx(
                      'w-full text-left px-3 py-2.5 rounded-lg text-sm whitespace-nowrap',
                      activeTab === tab.id
                        ? 'bg-blue-600/20 text-blue-300'
                        : 'text-slate-300 hover:bg-slate-800',
                    )}
                  >
                    {tab.label}
                  </button>
                ))}
              </div>
            )}
          </div>

          <button
            type="button"
            onClick={() => selectTab('settings')}
            className={clsx(
              'hidden sm:flex items-center gap-1.5 px-2 py-1 rounded-lg text-xs border whitespace-nowrap',
              selectedVehicle
                ? 'bg-blue-900/30 border-blue-500/40 text-blue-300'
                : 'bg-slate-800/60 border-slate-700/60 text-slate-500',
            )}
            title="工作配置 · 车辆"
          >
            <Car className="w-3 h-3 shrink-0" />
            <span className="font-mono font-bold">{selectedVehicle ? selectedVehicle.vehicle_id : '未选车'}</span>
          </button>

          <button
            type="button"
            onClick={() => selectTab('settings')}
            className={clsx(
              'hidden sm:flex items-center gap-1.5 px-2 py-1 rounded-lg text-xs border whitespace-nowrap',
              selectedYard
                ? selectedYard.mapped
                  ? 'bg-emerald-900/30 border-emerald-500/40 text-emerald-300'
                  : 'bg-amber-900/30 border-amber-500/40 text-amber-300'
                : 'bg-slate-800/60 border-slate-700/60 text-slate-500',
            )}
            title="工作配置 · 场地"
          >
            <Map className="w-3 h-3 shrink-0" />
            <span className="font-mono font-bold max-w-[6.5rem] truncate">
              {selectedYard ? selectedYard.yard_id : '未选场地'}
            </span>
          </button>

          <button
            type="button"
            onClick={() => selectTab('settings')}
            title="工作配置 · 连接"
            className={clsx(
              'flex items-center gap-1.5 px-2 py-1 rounded-lg text-xs border whitespace-nowrap',
              isMock
                ? 'bg-slate-800/60 border-slate-700/60 text-slate-400'
                : connStatus === 'connected'
                  ? 'bg-emerald-900/30 border-emerald-500/40 text-emerald-300'
                  : connStatus === 'connecting'
                    ? 'bg-amber-900/30 border-amber-500/40 text-amber-300 animate-pulse'
                    : 'bg-red-900/30 border-red-500/40 text-red-400',
            )}
          >
            {isMock ? <WifiOff className="w-3.5 h-3.5 shrink-0" /> : <Wifi className="w-3.5 h-3.5 shrink-0" />}
            <span className="font-bold">{liveLabel}</span>
            {!isMock && connStatus === 'connected' && (
              <span className="w-1.5 h-1.5 bg-emerald-400 rounded-full animate-pulse" />
            )}
          </button>

          <div className="hidden sm:block">
            <StatusBadge status={gnssStatus} />
          </div>
        </div>
      </div>

      {/* 第二行：导航独占整宽，横向滚动，绝不竖排挤字 */}
      <div className="hidden md:block border-t border-slate-800/80">
        <div className="max-w-screen-2xl mx-auto px-3 sm:px-6 py-1.5">
          <div
            className="nav-tabs flex flex-nowrap items-center gap-1 overflow-x-auto scrollbar-none
                       bg-slate-800/40 p-1 rounded-xl border border-slate-700/40"
            role="tablist"
          >
            {TABS.map((tab) => (
              <button
                key={tab.id}
                type="button"
                role="tab"
                aria-selected={activeTab === tab.id}
                onClick={() => selectTab(tab.id)}
                className={clsx(
                  'tab-btn text-sm',
                  activeTab === tab.id && 'tab-btn-active',
                )}
              >
                {tab.label}
              </button>
            ))}
          </div>
        </div>
      </div>
    </nav>
  );
}
