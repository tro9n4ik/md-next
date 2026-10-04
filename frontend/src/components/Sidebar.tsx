import React, { useState } from 'react';
import { NavLink } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { LayoutDashboard, Users, Server, Route, Settings, LogOut, CheckCircle2, Menu, X, Cloud, Radio, Network, Send, ShieldCheck, PanelLeftClose, PanelLeftOpen, CircleHelp } from 'lucide-react';
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
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem('sidebar-collapsed') === 'true');
  const toggleCollapsed = () => setCollapsed(value => { localStorage.setItem('sidebar-collapsed', String(!value)); return !value; });

  const { data: sysInfo } = useQuery<SystemInfo>({
    queryKey: ['systemInfo'],
    queryFn: async () => {
      const res = await apiFetch('/api/v1/system/info');
      if (!res.ok) return { hostname: 'md-next-master', version: '' };
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
    { icon: ShieldCheck, label: 'Обход БС', to: '/bypass' },
    { icon: Send, label: 'Telegram-бот', to: '/telegram' },
    { icon: Settings, label: 'Настройки', to: '/settings' },
    { icon: CircleHelp, label: 'Помощь', to: '/help' },
  ];

  const sidebarContent = (
    <div className={`flex flex-col h-full bg-[#122b29] text-white w-60 transition-[width] ${collapsed ? 'lg:w-20' : ''}`}>
      <button onClick={toggleCollapsed} aria-label={collapsed ? 'Развернуть меню' : 'Свернуть меню'} title={collapsed ? 'Развернуть меню' : 'Свернуть меню'} aria-expanded={!collapsed} className="hidden lg:flex justify-center items-center gap-2 p-3 text-neutral-300 hover:text-white hover:bg-white/5">
        {collapsed ? <PanelLeftOpen size={20} /> : <><PanelLeftClose size={20} /><span className="text-xs">Свернуть меню</span></>}
      </button>
      <div className="p-4 border-b border-white/10">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2.5"><span className="flex h-8 w-8 items-center justify-center rounded-xl bg-emerald-500 text-sm font-bold text-white">M</span><span className={`text-lg font-semibold tracking-tight ${collapsed ? 'lg:hidden' : ''}`}>MD-Next</span></div>
          <span className={`bg-emerald-500/10 text-emerald-500 text-xs px-2 py-0.5 rounded-full font-medium ${collapsed ? 'lg:hidden' : ''}`}>
            {sysInfo?.version ? `v${sysInfo.version}` : '…'}
          </span>
        </div>

        <div className={`bg-white/5 border border-white/10 rounded-lg p-2.5 text-xs text-neutral-300 flex items-center justify-between ${collapsed ? 'lg:hidden' : ''}`}>
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
            end={item.to === '/'}
            title={item.label}
            aria-label={item.label}
            onClick={() => setMobileOpen(false)}
            className={({ isActive }) =>
              `flex items-center space-x-3 px-3 py-2.5 rounded-xl transition-all text-sm font-medium ${collapsed ? 'lg:justify-center lg:space-x-0' : ''} ${
                isActive
                  ? 'bg-emerald-600 text-white shadow-sm'
                  : 'text-neutral-300 hover:bg-white/5 hover:text-white'
              }`
            }
          >
            <item.icon className="w-5 h-5 shrink-0" />
            <span className={collapsed ? 'lg:hidden' : ''}>{item.label}</span>
          </NavLink>
        ))}
      </nav>

      <div className="p-4 border-t border-white/10 space-y-3">
        <div className="flex items-center space-x-2 text-xs text-neutral-400">
          <CheckCircle2 className={`w-4 h-4 shrink-0 ${systemState === "error" ? "text-red-500" : systemState === "warning" ? "text-amber-500" : "text-emerald-500"}`} />
          <span className={collapsed ? 'lg:hidden' : ''}>{systemState === "error" ? "Есть проблемы" : systemState === "warning" ? "Есть замечания" : systemState === "ok" ? "Все системы работают" : "Проверка состояния"}</span>
        </div>
        <button
          onClick={onLogout}
          title="Выйти"
          aria-label="Выйти"
          className="flex items-center space-x-2 text-neutral-400 hover:text-white transition-colors w-full px-3 py-2 rounded-xl hover:bg-neutral-800 text-sm font-medium"
        >
          <LogOut className="w-5 h-5 shrink-0" />
          <span className={collapsed ? 'lg:hidden' : ''}>Выйти</span>
        </button>
      </div>
    </div>
  );

  return (
    <>
      <div className="lg:hidden fixed top-4 left-4 z-40">
        <button
          onClick={() => setMobileOpen(!mobileOpen)}
          aria-label={mobileOpen ? 'Закрыть меню' : 'Открыть меню'}
          aria-expanded={mobileOpen}
          className="p-2 bg-[#122b29] text-white rounded-xl shadow-lg border border-white/10"
        >
          {mobileOpen ? <X className="w-6 h-6" /> : <Menu className="w-6 h-6" />}
        </button>
      </div>

      {mobileOpen && (
        <div
          className="lg:hidden fixed inset-0 bg-neutral-900/60 backdrop-blur-sm z-30"
          onClick={() => setMobileOpen(false)}
        >
          <div className="h-full w-60" onClick={(e) => e.stopPropagation()}>
            {sidebarContent}
          </div>
        </div>
      )}

      <div className="hidden lg:block shrink-0 sticky top-0 h-dvh">
        {sidebarContent}
      </div>
    </>
  );
};

export default Sidebar;
