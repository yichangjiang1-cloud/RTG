import { useState, useRef, useEffect, useCallback } from 'react';
import Navbar from './components/Navbar';
import Dashboard from './components/Dashboard';
import VehicleConfig from './components/VehicleConfig';
import Settings from './components/Settings';
import Mapping from './components/Mapping';
import Calibration from './components/Calibration';
import Manual from './components/Manual';
import { ToastContainer } from './components/common';
import { useWebSocket } from './hooks/useWebSocket';
import { AppProvider, useAppContext } from './context/AppContext';
import type { TabId, RealtimeData } from './types';
import { wsUrl as buildWsUrl } from './utils/backendUrl';

const MAX_HISTORY = 120;

const VALID_TABS = new Set<TabId>(['dashboard', 'settings', 'config', 'mapping', 'calibration', 'manual']);

function getTabFromHash(): TabId {
  const hash = window.location.hash.slice(1) as TabId;
  return VALID_TABS.has(hash) ? hash : 'settings';
}

// 内층组件（可使用 context）
function AppInner() {
  const [activeTab, setActiveTab] = useState<TabId>(getTabFromHash);
  const { selectedVehicleId, useMock, setConnStatus, setIsMock, backendConfig } = useAppContext();
  const historyRef = useRef<RealtimeData[]>([]);

  // 切换 tab 时同步写入 URL hash
  const handleTabChange = useCallback((tab: TabId) => {
    setActiveTab(tab);
    window.location.hash = tab;
  }, []);

  // 监听浏览器前进/后退
  useEffect(() => {
    const onHashChange = () => setActiveTab(getTabFromHash());
    window.addEventListener('hashchange', onHashChange);
    return () => window.removeEventListener('hashchange', onHashChange);
  }, []);

  // 切换模式或换车时清空历史，避免旧数据残留在图表
  const clearHistory = useCallback(() => { historyRef.current = []; }, []);
  useEffect(() => { clearHistory(); }, [useMock, selectedVehicleId, clearHistory]);

  /**
   * 架构：前端 <-> 后端 WebSocket <-> MQTT Broker <-> 中控机
   * 前端不直接连接中控机，而是通过后端统一接入。
   * 后端通过 vehicle_id 查询参数订阅对应车辆的 MQTT Topic 并转发给前端。
   */
  const realtimeWsUrl = selectedVehicleId && !useMock
    ? buildWsUrl(backendConfig, `/ws/realtime?vehicle_id=${selectedVehicleId}`)
    : null;

  const { data, isMock, connStatus } = useWebSocket({ url: realtimeWsUrl, forceMock: useMock });

  // 同步 WS 状态到 context，让 Settings 等页面可以读到
  useEffect(() => { setConnStatus(connStatus); }, [connStatus, setConnStatus]);
  useEffect(() => { setIsMock(isMock); }, [isMock, setIsMock]);

  historyRef.current = [...historyRef.current, data].slice(-MAX_HISTORY);

  const renderTab = () => {
    switch (activeTab) {
      case 'dashboard':
        return <Dashboard data={data} history={historyRef.current} isMock={isMock} connStatus={connStatus} />;
      case 'settings':
        return <Settings />;
      case 'config':
        return <VehicleConfig liveData={data} connStatus={connStatus} isMock={isMock} />;
      case 'mapping':
        return <Mapping liveData={data} />;
      case 'calibration':
        return <Calibration liveData={data} />;
      case 'manual':
        return <Manual />;
      default:
        return null;
    }
  };

  return (
    <div className="min-h-screen flex flex-col">
      <Navbar
        activeTab={activeTab}
        onTabChange={handleTabChange}
        gnssStatus={data.status}
        isMock={isMock}
        connStatus={connStatus}
      />
      <main className="flex-1 max-w-screen-2xl mx-auto w-full px-6 py-6">
        {renderTab()}
      </main>
      <ToastContainer />
    </div>
  );
}

export default function App() {
  return (
    <AppProvider>
      <AppInner />
    </AppProvider>
  );
}
