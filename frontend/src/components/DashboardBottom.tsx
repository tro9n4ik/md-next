import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { Activity, AlertCircle, CheckCircle2, CircleHelp, TerminalSquare } from 'lucide-react';
import { apiFetch } from '../utils/api';
import { formatDateTime, translateCategory, translateStatus } from '../utils/ru';

interface EventItem { id: number; ts: string; level: 'info' | 'warning' | 'error'; category: string; message: string }
interface HealthCheck { key: string; name: string; status: string; description: string }
interface HealthData { status: string; checks: HealthCheck[] }

const DashboardBottom: React.FC = () => {
  const events = useQuery<EventItem[]>({ queryKey: ['events', 20], queryFn: async () => { const r = await apiFetch('/api/v1/events?limit=20'); if (!r.ok) throw new Error('Не удалось загрузить журнал событий'); return r.json(); }, refetchInterval: 15000 });
  const health = useQuery<HealthData>({ queryKey: ['systemHealth'], queryFn: async () => { const r = await apiFetch('/api/v1/system/health'); if (!r.ok) throw new Error('Не удалось проверить состояние сервера'); return r.json(); }, refetchInterval: 15000 });
  const levelClass = { info: 'text-sky-700 bg-sky-50', warning: 'text-amber-800 bg-amber-50', error: 'text-red-700 bg-red-50' };
  const statusClass: Record<string, string> = { ok: 'text-emerald-700 bg-emerald-50', warning: 'text-amber-800 bg-amber-50', error: 'text-red-700 bg-red-50', disabled: 'text-neutral-600 bg-neutral-100', not_configured: 'text-neutral-600 bg-neutral-100' };
  const StatusIcon = (status: string) => status === 'ok' ? CheckCircle2 : status === 'error' ? AlertCircle : CircleHelp;

  return <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
    <section className="flex h-96 flex-col overflow-hidden rounded-2xl border border-neutral-200 bg-white shadow-sm">
      <div className="flex items-center gap-2 border-b border-neutral-100 px-5 py-4"><TerminalSquare className="h-4 w-4 text-neutral-500" /><h3 className="text-sm font-semibold text-neutral-800">Последние события</h3></div>
      <div className="flex-1 space-y-2 overflow-y-auto p-4">
        {events.isLoading && <p className="p-3 text-sm text-neutral-500">Загрузка событий…</p>}
        {!events.isLoading && (events.data || []).length === 0 && <p className="p-3 text-sm text-neutral-500">Событий пока нет</p>}
        {events.data?.map(event => <div key={event.id} className="flex items-start gap-3 rounded-xl border border-neutral-100 p-3">
          <span className={`mt-0.5 rounded-md px-2 py-1 text-[10px] font-semibold uppercase ${levelClass[event.level]}`}>{event.level === 'info' ? 'Информация' : event.level === 'warning' ? 'Предупреждение' : 'Ошибка'}</span>
          <div className="min-w-0 flex-1"><p className="text-sm text-neutral-800">{event.message}</p><p className="mt-1 text-xs text-neutral-400">{formatDateTime(event.ts)} · {translateCategory(event.category)}</p></div>
        </div>)}
      </div>
    </section>

    <section className="rounded-2xl border border-neutral-200 bg-white p-5 shadow-sm">
      <div className="mb-4 flex items-center gap-2 border-b border-neutral-100 pb-3"><Activity className="h-4 w-4 text-neutral-500" /><h3 className="text-sm font-semibold text-neutral-800">Состояние сервера</h3></div>
      <div className="max-h-[21rem] space-y-2 overflow-y-auto">
        {health.isLoading && <p className="p-3 text-sm text-neutral-500">Проверка компонентов…</p>}
        {health.data?.checks.map(check => { const Icon = StatusIcon(check.status); return <div key={check.key} className="flex items-start justify-between gap-3 rounded-xl bg-neutral-50 p-3"><div className="flex min-w-0 items-start gap-3"><Icon className={`mt-0.5 h-4 w-4 shrink-0 ${check.status === 'ok' ? 'text-emerald-600' : check.status === 'error' ? 'text-red-600' : 'text-amber-600'}`} /><div className="min-w-0"><p className="text-sm font-medium text-neutral-800">{check.name}</p><p className="text-xs text-neutral-500">{check.description}</p></div></div><span className={`shrink-0 rounded-md px-2 py-1 text-xs font-medium ${statusClass[check.status] || 'text-neutral-600 bg-neutral-100'}`}>{translateStatus(check.status)}</span></div>; })}
      </div>
    </section>
  </div>;
};

export default DashboardBottom;
