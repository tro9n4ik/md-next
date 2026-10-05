import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ScrollText } from 'lucide-react';
import PageLayout from '../components/ui/PageLayout';
import QueryError from '../components/ui/QueryError';
import { request } from '../utils/operations';
import { translateCategory } from '../utils/ru';

type Event = { id: number; ts: string; level: string; category: string; message: string; meta: Record<string, unknown> | null };
export default function EventsPage() {
  const [values, setValues] = useState({ level: '', category: '', since: '', until: '', client_id: '', node_id: '' });
  const [filter, setFilter] = useState(''); const [page, setPage] = useState(0);
  const query = useQuery({ queryKey: ['event-log', filter, page], queryFn: () => request<Event[]>(`/api/v1/events?limit=50&offset=${page * 50}${filter}`) });
  function apply() { const params = new URLSearchParams(); Object.entries(values).forEach(([key, value]) => { if (value) params.set(key, key === 'since' || key === 'until' ? new Date(value).toISOString() : value); }); setFilter('&' + params); setPage(0); }
  return <PageLayout title="Журнал событий" description="История ошибок, клиентов, нод и изменений настроек; хранение до 30 дней и 5000 записей" icon={ScrollText}>
    <form onSubmit={e => { e.preventDefault(); apply(); }} className="ui-card ui-panel grid gap-3 sm:grid-cols-3"><label>Уровень<select value={values.level} onChange={e => setValues({ ...values, level: e.target.value })} className="mt-1 w-full border p-2"><option value="">Все</option><option value="error">Ошибки</option><option value="warning">Предупреждения</option><option value="info">Информация</option></select></label><label>Категория<input className="mt-1 w-full border p-2" value={values.category} onChange={e => setValues({ ...values, category: e.target.value })} placeholder="client, node, settings…" /></label>{(['since', 'until', 'client_id', 'node_id'] as const).map(key => <label key={key}>{({ since: 'От', until: 'До', client_id: 'ID клиента', node_id: 'ID ноды' })[key]}<input type={key.includes('_id') ? 'number' : 'datetime-local'} min="1" className="mt-1 w-full border p-2" value={values[key]} onChange={e => setValues({ ...values, [key]: e.target.value })} /></label>)}<button className="ui-button ui-button-primary">Применить фильтры</button></form>
    {query.error && <QueryError error={query.error} retry={() => query.refetch()} />}
    <section className="ui-card ui-panel">{query.isLoading ? <p>Загрузка…</p> : query.data?.length ? query.data.map(e => <article key={e.id} className="border-b border-neutral-100 py-4"><div className="flex flex-wrap gap-3 text-xs text-neutral-500"><time>{new Date(e.ts).toLocaleString('ru')}</time><span>{translateCategory(e.category)}</span><span className={e.level === 'error' ? 'text-red-600' : e.level === 'warning' ? 'text-amber-700' : ''}>{({ info: 'Информация', warning: 'Предупреждение', error: 'Ошибка' })[e.level as 'info'] || e.level}</span></div><p className="mt-2 text-sm">{e.message}</p>{e.meta && <pre className="mt-2 whitespace-pre-wrap break-all text-xs text-neutral-500">{JSON.stringify(e.meta, null, 2)}</pre>}</article>) : !query.error && <p className="text-neutral-500">По выбранным фильтрам событий нет.</p>}<div className="mt-4 flex justify-between gap-3"><button className="ui-button ui-button-secondary" disabled={!page || query.isFetching} onClick={() => setPage(page - 1)}>Назад</button><span className="text-sm">Страница {page + 1}</span><button className="ui-button ui-button-secondary" disabled={query.data?.length !== 50 || query.isFetching} onClick={() => setPage(page + 1)}>Далее</button></div></section>
  </PageLayout>;
}
