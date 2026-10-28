import React from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { ArrowRight, AlertCircle, CheckCircle2, CircleHelp, Info } from 'lucide-react';
import { apiFetch } from '../utils/api';
import { formatDateTime, translateCategory, translateStatus } from '../utils/ru';

interface EventItem { id: number; ts: string; level: 'info' | 'warning' | 'error'; category: string; message: string }
interface HealthCheck { key: string; name: string; status: string; description: string }
interface HealthData { status: string; checks: HealthCheck[] }

const DashboardBottom: React.FC = () => {
  const events = useQuery<EventItem[]>({ queryKey: ['events', 20], queryFn: async () => { const r = await apiFetch('/api/v1/events?limit=20'); if (!r.ok) throw new Error('Не удалось загрузить журнал событий'); return r.json(); }, refetchInterval: 15000 });
  const health = useQuery<HealthData>({ queryKey: ['systemHealth'], queryFn: async () => { const r = await apiFetch('/api/v1/system/health'); if (!r.ok) throw new Error('Не удалось проверить состояние сервера'); return r.json(); }, refetchInterval: 15000 });
  const levelClass = { info: 'event-info', warning: 'event-warning', error: 'event-error' };
  const statusClass: Record<string, string> = { ok: 'text-emerald-700 bg-emerald-50', warning: 'text-amber-800 bg-amber-50', error: 'text-red-700 bg-red-50', disabled: 'text-neutral-600 bg-neutral-100', not_configured: 'text-neutral-600 bg-neutral-100' };
  const StatusIcon = (status: string) => status === 'ok' ? CheckCircle2 : status === 'error' ? AlertCircle : CircleHelp;

  return <div className="dashboard-bottom">
    <section className="ui-card dashboard-events">
      <div className="dashboard-card-heading"><h2>Последние события</h2><Link to="/events" className="dashboard-link">Все события <ArrowRight size={16} /></Link></div>
      <div className="dashboard-event-list">
        {events.isLoading && <p className="p-3 text-sm text-neutral-500">Загрузка событий…</p>}
        {events.error && <div role="alert" className="p-3 text-sm text-red-700">{events.error.message}<button className="ui-button ui-button-secondary mt-2" onClick={() => events.refetch()}>Повторить</button></div>}
        {!events.isLoading && !events.error && (events.data || []).length === 0 && <p className="p-3 text-sm text-neutral-500">Событий пока нет</p>}
        {events.data?.slice(0, 4).map(event => <div key={event.id} className="dashboard-event">
          <span title={event.level === 'info' ? 'Информация' : event.level === 'warning' ? 'Предупреждение' : 'Ошибка'} className={`event-symbol ${levelClass[event.level]}`}>{event.level === 'info' ? <Info size={19} /> : <AlertCircle size={19} />}</span>
          <div className="event-description"><p>{event.message}</p><span>{translateCategory(event.category)}</span></div><time dateTime={event.ts}>{formatDateTime(event.ts)}</time>
        </div>)}
      </div>
    </section>

    <section className="ui-card dashboard-services">
      <div className="dashboard-card-heading"><h2>Состояние служб</h2><Link to="/diagnostics" className="dashboard-link" aria-label="Открыть диагностику"><ArrowRight size={17} /></Link></div>
      <div className="dashboard-service-list">
        {health.isLoading && <p className="p-3 text-sm text-neutral-500">Проверка компонентов…</p>}
        {health.error && <div role="alert" className="p-3 text-sm text-red-700">{health.error.message}<button className="ui-button ui-button-secondary mt-2" onClick={() => health.refetch()}>Повторить</button></div>}
        {health.data?.checks.map(check => { const Icon = StatusIcon(check.status); return <div key={check.key} className="dashboard-service" title={check.description}><div><Icon size={17} className={check.status === 'ok' ? 'text-emerald-600' : check.status === 'error' ? 'text-red-600' : 'text-neutral-400'} /><span>{check.name}</span></div><span className={`service-status ${statusClass[check.status] || 'text-neutral-600 bg-neutral-100'}`}>{check.status === 'ok' ? 'Работает' : translateStatus(check.status)}</span></div>; })}
      </div>
    </section>
  </div>;
};

export default DashboardBottom;
