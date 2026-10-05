import { useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { request } from '../utils/operations';
import QueryError from './ui/QueryError';

type Template = { id: string; name: string; period: string; monthly_traffic_limit: number };
function Editor({ data, saved }: { data: Template[]; saved: () => unknown }) {
  const [rows, setRows] = useState(data.map(t => ({ ...t, quota: t.monthly_traffic_limit ? String(t.monthly_traffic_limit / 1024 ** 3) : '' })));
  const [notice, setNotice] = useState('');
  const mutation = useMutation({ mutationFn: () => request('/api/v1/operations/templates', { templates: rows.map(({ quota, ...t }) => ({ ...t, monthly_traffic_limit: quota === '' ? 0 : Math.round(Number(quota) * 1024 ** 3) })) }, 'PUT'), onSuccess: () => { setNotice('Шаблоны сохранены'); saved(); }, onError: e => setNotice(e.message) });
  const update = (index: number, field: string, value: string) => setRows(rows.map((r, i) => i === index ? { ...r, [field]: value } : r));
  return <form onSubmit={e => { e.preventDefault(); mutation.mutate(); }} className="ui-card ui-panel space-y-4" data-editable><h2 className="ui-card-title">Шаблоны подписок</h2><p className="text-sm text-neutral-500">Доступны в Telegram: «Шаблоны». Пустой лимит означает отсутствие ограничений.</p>{rows.map((r, i) => <div key={r.id} className="grid gap-3 rounded-xl border border-neutral-200 p-4 sm:grid-cols-4"><label className="text-sm">Название<input required maxLength={64} className="mt-1 w-full border p-2" value={r.name} onChange={e => update(i, 'name', e.target.value)} /></label><label className="text-sm">Срок<select className="mt-1 w-full border p-2" value={r.period} onChange={e => update(i, 'period', e.target.value)}><option value="week">Неделя</option><option value="month">Месяц</option><option value="year">Год</option><option value="unlimited">Без срока</option></select></label><label className="text-sm">Лимит, ГБ<input type="number" min="0" max="8388607" step="any" className="mt-1 w-full border p-2" value={r.quota} onChange={e => update(i, 'quota', e.target.value)} placeholder="Без лимита" /></label><button type="button" className="ui-button ui-button-danger self-end" onClick={() => setRows(rows.filter((_, index) => index !== i))}>Удалить</button></div>)}<div className="flex flex-wrap gap-3"><button type="button" disabled={rows.length >= 20} className="ui-button ui-button-secondary" onClick={() => setRows([...rows, { id: 't_' + Date.now().toString(36), name: 'Новый шаблон', period: 'month', monthly_traffic_limit: 0, quota: '' }])}>Добавить шаблон</button><button disabled={mutation.isPending} className="ui-button ui-button-primary">Сохранить шаблоны</button></div><p role="status" className="text-sm text-neutral-600">{notice}</p></form>;
}
export default function TemplateSettings() {
  const query = useQuery({ queryKey: ['templates'], queryFn: () => request<Template[]>('/api/v1/operations/templates') });
  if (query.error) return <QueryError error={query.error} retry={() => query.refetch()} />;
  return query.data ? <Editor data={query.data} saved={() => query.refetch()} /> : <p>Загрузка шаблонов…</p>;
}
