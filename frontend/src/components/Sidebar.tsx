import React, { useState } from 'react';
import { NavLink } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { LayoutDashboard, Users, Server, Route, Settings, LogOut, CheckCircle2, Menu, X, Cloud, Radio, Network, ShieldCheck, PanelLeftClose, PanelLeftOpen, CircleHelp, Stethoscope, Activity, ScrollText, Moon, Sun } from 'lucide-react';
import { useCurrentUser } from '../contexts/AuthContext';
import { apiFetch } from '../utils/api';

interface SystemInfo {
  hostname: string;
  version: string;
}
interface HealthSummary { status: string; checks: Array<{ status: string }> }

interface SidebarProps {
  onLogout: () => void;
}

const Sidebar: React.FC<SidebarProps> = ({ onLogout }) => {
  const user = useCurrentUser();
  const isAdmin = user.role === "admin";
  const [mobileOpen, setMobileOpen] = useState(false);
  const [dark, setDark] = useState(() => localStorage.getItem('theme') === 'dark');
  React.useEffect(() => { document.documentElement.dataset.theme = dark ? 'dark' : 'light'; localStorage.setItem('theme', dark ? 'dark' : 'light'); }, [dark]);
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem('sidebar-collapsed') === 'true');
  const toggleCollapsed = () => setCollapsed(value => { localStorage.setItem('sidebar-collapsed', String(!value)); return !value; });

  const { data: sysInfo } = useQuery<SystemInfo>({
    queryKey: ['systemInfo'], enabled: isAdmin,
    queryFn: async () => {
      const res = await apiFetch('/api/v1/system/info');
      if (!res.ok) return { hostname: 'md-next-master', version: '' };
      return res.json();
    }
  });
  const { data: healthSummary, isError: healthError } = useQuery<HealthSummary>({
    queryKey: ['systemHealth'], enabled: isAdmin,
    queryFn: async () => { const res = await apiFetch('/api/v1/system/health'); if (!res.ok) throw new Error('Проверка недоступна'); return res.json(); },
    refetchInterval: 15000,
  });
  const systemState = healthError ? 'error' : healthSummary?.status || 'loading';

  const menuItems = [
    { icon: LayoutDashboard, label: 'Обзор', to: '/' },
    { icon: Users, label: 'Клиенты', to: '/clients' },
    { icon: Server, label: 'Узлы', to: '/nodes' },
    { icon: Radio, label: 'Протоколы', to: '/protocols' },
    { icon: Network, label: 'DNS', to: '/dns' },
    { icon: Route, label: 'Маршрутизация', to: '/routing' },
    { icon: Cloud, label: 'WARP', to: '/warp' },
    { icon: ShieldCheck, label: 'Обход БС', to: '/bypass' },
    { icon: Stethoscope, label: 'Диагностика', to: '/diagnostics' },
    { icon: Activity, label: 'История нод', to: '/history' },
    { icon: ScrollText, label: 'Журнал событий', to: '/events' },
    { icon: Settings, label: 'Настройки', to: '/settings' },
    { icon: ShieldCheck, label: 'Моя учётная запись', to: '/account' },
    { icon: CircleHelp, label: 'Помощь', to: '/help' },
  ].filter(item => isAdmin || item.to === '/clients' || item.to === '/account');

  const sidebarContent = (
    <div className={`app-sidebar flex flex-col h-full text-white w-60 transition-[width] ${collapsed ? 'lg:w-20 sidebar-collapsed' : ''}`}>
      <button onClick={toggleCollapsed} aria-label={collapsed ? 'Развернуть меню' : 'Свернуть меню'} title={collapsed ? 'Развернуть меню' : 'Свернуть меню'} aria-expanded={!collapsed} className="hidden lg:flex justify-center items-center gap-2 p-3 text-neutral-300 hover:text-white hover:bg-white/5">
        {collapsed ? <PanelLeftOpen size={20} /> : <><PanelLeftClose size={20} /><span className="text-xs">Свернуть меню</span></>}
      </button>
      <div className="sidebar-brand p-4 border-b border-white/10">
        <div className={`flex items-center justify-between mb-3 ${collapsed ? 'lg:justify-center' : ''}`}>
          <div className="flex items-center gap-2.5"><span className="flex h-8 w-8 items-center justify-center rounded-xl bg-emerald-500 text-sm font-bold text-white">M</span><span className={`text-lg font-semibold tracking-tight ${collapsed ? 'lg:hidden' : ''}`}>MD-Next</span></div>
          <span className={`bg-emerald-500/10 text-emerald-500 text-xs px-2 py-0.5 rounded-full font-medium ${collapsed ? 'lg:hidden' : ''}`}>
            {isAdmin ? sysInfo?.version ? `v${sysInfo.version}` : '…' : 'Субадмин'}
          </span>
        </div>

        <div className={`bg-white/5 border border-white/10 rounded-lg p-2.5 text-xs text-neutral-300 flex items-center justify-between ${collapsed ? 'lg:hidden' : ''}`}>
          <div className="flex items-center space-x-2 truncate">
            <span className="w-2 h-2 rounded-full bg-emerald-500 shrink-0"></span>
            <span className="truncate font-mono">{isAdmin ? sysInfo?.hostname || 'localhost' : user.username}</span>
          </div>
        </div>
      </div>

      <nav aria-label="Основная навигация" className="sidebar-scroll flex-1 p-3 space-y-1 overflow-y-auto">
        {menuItems.map((item) => (
          <React.Fragment key={item.to}>
          {['/', '/protocols', '/diagnostics'].includes(item.to) && <div className={`sidebar-group-label ${collapsed ? 'lg:hidden' : ''}`}>{item.to === '/' ? 'Управление' : item.to === '/protocols' ? 'Сеть' : 'Система'}</div>}
          <NavLink
            key={item.to}
            to={item.to}
            end={item.to === '/'}
            title={item.label}
            aria-label={item.label}
            onClick={() => setMobileOpen(false)}
            className={({ isActive }) =>
              `flex items-center gap-3 px-3 py-2.5 rounded-xl transition-all text-sm font-medium ${collapsed ? 'lg:justify-center lg:gap-0' : ''} ${
                isActive
                  ? 'sidebar-item-active text-white'
                  : 'text-neutral-300 hover:bg-white/5 hover:text-white'
              }`
            }
          >
            <item.icon className="w-5 h-5 shrink-0" />
            <span className={collapsed ? 'lg:hidden' : ''}>{item.label}</span>
          </NavLink>
          </React.Fragment>
        ))}
      </nav>

      <div className="sidebar-footer">
        <NavLink to="/account" title={user.username} onClick={() => setMobileOpen(false)} className="sidebar-account sidebar-footer-row"><span className="account-avatar">{user.username.charAt(0).toUpperCase()}</span><span className="sidebar-footer-label"><b>{user.username}</b><small>{isAdmin ? 'Администратор' : 'Субадминистратор'}</small></span></NavLink>
        <button onClick={() => setDark(!dark)} title={dark ? 'Светлая тема' : 'Тёмная тема'} aria-label={dark ? 'Светлая тема' : 'Тёмная тема'} className="sidebar-footer-row sidebar-footer-action"><span className="sidebar-footer-icon">{dark ? <Sun size={18} /> : <Moon size={18} />}</span><span className="sidebar-footer-label">{dark ? 'Светлая тема' : 'Тёмная тема'}</span></button>
        {isAdmin && <div className="sidebar-footer-row sidebar-footer-status" title={systemState === "error" ? "Есть проблемы" : systemState === "warning" ? "Есть замечания" : systemState === "ok" ? "Все системы работают" : "Проверка состояния"}>
          <span className="sidebar-footer-icon"><CheckCircle2 size={17} className={systemState === "error" ? "text-red-500" : systemState === "warning" ? "text-amber-500" : systemState === "ok" ? "text-emerald-500" : "text-neutral-400"} /></span>
          <span className="sidebar-footer-label">{systemState === "error" ? "Есть проблемы" : systemState === "warning" ? "Есть замечания" : systemState === "ok" ? "Все системы работают" : "Проверка состояния"}</span>
        </div>}
        <button
          onClick={onLogout}
          title="Выйти"
          aria-label="Выйти"
          className="sidebar-footer-row sidebar-footer-action"
        >
          <span className="sidebar-footer-icon"><LogOut size={18} /></span>
          <span className="sidebar-footer-label">Выйти</span>
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
