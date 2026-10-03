import React, { useState } from 'react';
import { NavLink } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { LayoutDashboard, Users, Server, Route, Settings, LogOut, CheckCircle2, Menu, X, Cloud, Radio, Network, Send } from 'lucide-react';
import { apiFetch } from '../utils/api';

interface SystemInfo {
  hostname: string;
  version: string;
}
interface HealthSummary { checks: Array<{ status: string }> }

interface SidebarProps {
  onLogout: () => void;
}

const Sidebar: React.FC<SidebarProps> = ({ onLogout }) => {
  const [mobileOpen, setMobileOpen] = useState(false);

  const { data: sysInfo } = useQuery<SystemInfo>({
    queryKey: ['systemInfo'],
    queryFn: async () => {
      const res = await apiFetch('/api/v1/system/info');
      if (!res.ok) return { hostname: 'md-next-master', version: '2.2.0' };
      return res.json();
    }
  });
  const { data: healthSummary, isError: healthError } = useQuery<HealthSummary>({
    queryKey: ['systemHealth'],
    queryFn: async () => { const res = await apiFetch('/api/v1/system/health'); if (!res.ok) throw new Error('Проверка недоступна'); return res.json(); },
    refetchInterval: 15000,
  });
  const states = healthSummary?.checks.map(check => check.status) || [];
  const systemState = healthError || states.includes('error') ? 'error' : states.includes('warning') || states.includes('disabled') || states.includes('not_configured') ? 'warning' : states.length ? 'ok' : 'loading';

  const menuItems = [
    { icon: LayoutDashboard, label: 'Обзор', to: '/' },
    { icon: Users, label: 'Клиенты', to: '/clients' },
    { icon: Server, label: 'Узлы', to: '/nodes' },
    { icon: Radio, label: 'Протоколы', to: '/protocols' },
    { icon: Network, label: 'DNS', to: '/dns' },
    { icon: Route, label: 'Маршрутизация', to: '/routing' },
    { icon: Cloud, label: 'WARP', to: '/warp' },
    { icon: Send, label: 'Telegram-бот', to: '/telegram' },
    { icon: Settings, label: 'Настройки', to: '/settings' },
  ];

  const sidebarContent = (
    <div className="flex flex-col h-full bg-neutral-900 text-white w-64">
      <div className="p-4 border-b border-neutral-800">
        <div className="flex items-center justify-between mb-3">
          <h1 className="text-xl font-bold tracking-tight">MD-Next</h1>
          <span className="bg-emerald-500/10 text-emerald-500 text-xs px-2 py-0.5 rounded-full font-medium">
            v{sysInfo?.version || '2.2.0'}
          </span>
        </div>

        <div className="bg-neutral-800/80 border border-neutral-700/50 rounded-lg p-2.5 text-xs text-neutral-300 flex items-center justify-between">
          <div className="flex items-center space-x-2 truncate">
            <span className="w-2 h-2 rounded-full bg-emerald-500 shrink-0"></span>
            <span className="truncate font-mono">{sysInfo?.hostname || 'localhost'}</span>
          </div>
        </div>
      </div>

      <nav className="flex-1 p-3 space-y-1 overflow-y-auto">
        {menuItems.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            onClick={() => setMobileOpen(false)}
            className={({ isActive }) =>
              `flex items-center space-x-3 px-3 py-2.5 rounded-xl transition-all text-sm font-medium ${
                isActive
                  ? 'bg-emerald-600 text-white shadow-sm'
                  : 'text-neutral-400 hover:bg-neutral-800 hover:text-white'
              }`
            }
          >
            <item.icon className="w-5 h-5 shrink-0" />
            <span>{item.label}</span>
          </NavLink>
        ))}
      </nav>

      <div className="p-4 border-t border-neutral-800 space-y-3">
        <div className="flex items-center space-x-2 text-xs text-neutral-400">
          <CheckCircle2 className={`w-4 h-4 shrink-0 ${systemState === "error" ? "text-red-500" : systemState === "warning" ? "text-amber-500" : "text-emerald-500"}`} />
          <span>{systemState === "error" ? "Есть проблемы" : systemState === "warning" ? "Есть замечания" : systemState === "ok" ? "Все системы работают" : "Проверка состояния"}</span>
        </div>
        <button
          onClick={onLogout}
          className="flex items-center space-x-2 text-neutral-400 hover:text-white transition-colors w-full px-3 py-2 rounded-xl hover:bg-neutral-800 text-sm font-medium"
        >
          <LogOut className="w-5 h-5 shrink-0" />
          <span>Выйти</span>
        </button>
      </div>
    </div>
  );

  return (
    <>
      <div className="lg:hidden fixed top-4 left-4 z-40">
        <button
          onClick={() => setMobileOpen(!mobileOpen)}
          className="p-2 bg-neutral-900 text-white rounded-xl shadow-lg border border-neutral-800"
        >
          {mobileOpen ? <X className="w-6 h-6" /> : <Menu className="w-6 h-6" />}
        </button>
      </div>

      {mobileOpen && (
        <div
          className="lg:hidden fixed inset-0 bg-neutral-900/60 backdrop-blur-sm z-30"
          onClick={() => setMobileOpen(false)}
        >
          <div className="h-full w-64" onClick={(e) => e.stopPropagation()}>
            {sidebarContent}
          </div>
        </div>
      )}

      <div className="hidden lg:block shrink-0 min-h-screen">
        {sidebarContent}
      </div>
    </>
  );
};

export default Sidebar;
