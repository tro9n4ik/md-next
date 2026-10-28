import { useQuery } from '@tanstack/react-query';
import { Link, useLocation } from 'react-router-dom';
import { ChevronRight } from 'lucide-react';
import { useCurrentUser } from '../contexts/AuthContext';
import { apiFetch } from '../utils/api';

const names: Record<string, string> = { clients: 'Клиенты', nodes: 'Узлы', protocols: 'Протоколы', dns: 'DNS', routing: 'Маршрутизация', warp: 'WARP', bypass: 'Обход БС', diagnostics: 'Диагностика', history: 'История нод', events: 'Журнал событий', settings: 'Настройки', account: 'Моя учётная запись', help: 'Помощь' };
export default function Topbar() {
  const user = useCurrentUser();
  const { pathname } = useLocation();
  const health = useQuery({ queryKey: ['systemHealth'], enabled: user.role === 'admin', queryFn: async () => { const response = await apiFetch('/api/v1/system/health'); if (!response.ok) throw new Error('Статус недоступен'); return response.json() as Promise<{ status: string }> ; }, refetchInterval: 15000 });
  const status = health.isError ? 'unknown' : health.data?.status;
  return <div className="app-topbar"><div className="topbar-breadcrumb"><span>MD-Next</span><ChevronRight size={13} /><b>{names[pathname.split('/')[1]] || 'Обзор'}</b></div><div className="topbar-account">{user.role === 'admin' && <span className="topbar-health"><i className={`status-dot ${status === 'error' ? 'status-error' : status === 'warning' ? 'status-warning' : status !== 'ok' ? 'status-unknown' : ''}`} />{status === 'ok' ? 'Система работает' : status === 'error' ? 'Есть проблемы' : status === 'warning' ? 'Есть замечания' : status === 'unknown' ? 'Статус недоступен' : 'Проверка системы'}</span>}<Link to="/account" className="account-avatar" aria-label="Моя учётная запись">{user.username.charAt(0).toUpperCase()}</Link></div></div>;
}
