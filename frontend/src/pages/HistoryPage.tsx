import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Activity } from 'lucide-react';
import PageLayout from '../components/ui/PageLayout';
import QueryError from '../components/ui/QueryError';
import { request } from '../utils/operations';

type History = { points: { ts: string; healthy: boolean; ping_ms: number; reason: string }[]; sample_count: number; availability: number | null; last_failure: string | null; events: { ts: string; message: string; reason: string | null }[] };
export default function HistoryPage() {
  const [node, setNode] = useState(''); const [hours, setHours] = useState('24');
  const nodes = useQuery({ queryKey: ['history-nodes'], queryFn: () => request<{ id: number; name: string }[]>('/api/v1/nodes') });
  const id = node || String(nodes.data?.[0]?.id || '');
  const history = useQuery({ queryKey: ['node-history', id, hours], queryFn: () => request<History>(`/api/v1/operations/history/${id}?hours=${hours}`), enabled: !!id, refetchInterval: 60000 });
  const data = history.data; const max = Math.max(1, ...(data?.points.map(p => p.ping_ms) || []));
  const reasons: Record<string, string> = { port: 'Порт недоступен', egress: 'Нет выхода в интернет', latency: 'Высокая задержка', ok: 'Доступна' };
  return <PageLayout title="История нод" description="Доступность, задержка и причины переключений за последние 30 дней" icon={Activity}>
    <div className="ui-card ui-panel flex flex-wrap gap-4"><label>Нода<select className="ml-3 border p-2" value={id} onChange={e => setNode(e.target.value)}>{nodes.data?.map(n => <option key={n.id} value={n.id}>{n.name}</option>)}</select></label><label>Период<select className="ml-3 border p-2" value={hours} onChange={e => setHours(e.target.value)}><option value="1">Час</option><option value="24">Сутки</option><option value="168">Неделя</option><option value="720">30 дней</option></select></label></div>
    {nodes.error && <QueryError error={nodes.error} retry={() => nodes.refetch()} />}{history.error && <QueryError error={history.error} retry={() => history.refetch()} />}
    {history.isFetching && <p className="text-sm text-neutral-500">Обновляем историю…</p>}
    {!nodes.isLoading && !nodes.error && !id && <p className="ui-empty-state">Добавьте ноду для записи истории.</p>}
    {data && <><section className="ui-card ui-panel"><div className="flex flex-wrap gap-6 text-sm"><p>Доступность: <b>{data.availability === null ? 'Нет данных' : data.availability + '%'}</b></p><p>Проверок: {data.sample_count}</p><p>Последний сбой: {data.last_failure ? new Date(data.last_failure).toLocaleString('ru') : 'Не зафиксирован за период'}</p></div>
      {data.points.length ? <><svg viewBox="0 0 960 200" className="mt-6 h-52 w-full" preserveAspectRatio="none" role="img" aria-label="График задержки; красные отметки показывают сбои"><line x1="0" y1="180" x2="960" y2="180" stroke="currentColor" opacity=".15" /><polyline fill="none" stroke="#059669" strokeWidth="2" points={data.points.map((p, i) => `${i * 960 / Math.max(1, data.points.length - 1)},${180 - p.ping_ms / max * 150}`).join(' ')} />{data.points.map((p, i) => !p.healthy && <rect key={i} x={i * 950 / Math.max(1, data.points.length - 1)} y="185" width="5" height="10" fill="#ef4444"><title>{new Date(p.ts).toLocaleString('ru')} · {reasons[p.reason]}</title></rect>)}</svg><div className="flex justify-between text-xs text-neutral-500"><span>{new Date(data.points[0].ts).toLocaleString('ru')}</span><span>Максимум {max} мс · красный: сбой</span><span>{new Date(data.points.at(-1)!.ts).toLocaleString('ru')}</span></div></> : <p className="mt-5 text-sm text-neutral-500">История начнёт собираться после установки обновления.</p>}
    </section><section className="ui-card ui-panel"><h2 className="ui-card-title mb-4">Сбои и переключения</h2>{data.events.length ? data.events.map((e, i) => <div key={i} className="border-b border-neutral-100 py-3 text-sm"><p>{e.message}{e.reason ? ` · ${e.reason}` : ''}</p><p className="text-xs text-neutral-500">{new Date(e.ts).toLocaleString('ru')}</p></div>) : <p className="text-sm text-neutral-500">Событий за период нет</p>}</section></>}
  </PageLayout>;
}
