import React, { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Plus, Search, Trash2, X, Pause, Play, KeyRound } from 'lucide-react';
import { apiFetch } from '../utils/api';
import { formatBytes, translateProfile } from '../utils/ru';
import SubscriptionFields from './SubscriptionFields';
import { clientStatus, formatSubscriptionDate, subscriptionPayload } from '../utils/subscriptions';
import type { ClientLimits, SubscriptionValues } from '../utils/subscriptions';

type Profile = { id: number; kind: string; is_enabled: boolean };
type Client = ClientLimits & {
  id: number; name: string; phone?: string; email?: string; is_active: boolean;
  traffic_total: number; traffic_limit: number; traffic_up: number; traffic_down: number; profiles: Profile[];
  connection_status: 'online' | 'offline' | 'unknown'; connected_protocols: string[];
};
type SortField = 'status' | 'name' | 'traffic' | 'protocol' | 'created';

async function responseError(response: Response, fallback: string) {
  const body = await response.json().catch(() => ({}));
  return typeof body.detail === 'string' ? body.detail : fallback;
}

const formatTraffic = formatBytes;

const ClientsTable: React.FC = () => {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const [search, setSearch] = useState(params.get('q') || '');
  const [createOpen, setCreateOpen] = useState(false);
  const [name, setName] = useState('');
  const [phone, setPhone] = useState('');
  const [email, setEmail] = useState('');
  const [subscription, setSubscription] = useState<SubscriptionValues>({ period: 'month', date: '', quotaGB: '', cdnQuotaGB: '' });
  const [error, setError] = useState('');

  const q = params.get('q') || '';
  const status = params.get('status') || 'all';
  const sort = (params.get('sort') || 'created') as SortField;
  const order = params.get('order') || 'desc';

  useEffect(() => {
    const timer = window.setTimeout(() => {
      const next = new URLSearchParams(params);
      if (search.trim()) next.set('q', search.trim()); else next.delete('q');
      if (next.toString() !== params.toString()) setParams(next, { replace: true });
    }, 300);
    return () => window.clearTimeout(timer);
  }, [search, params, setParams]);

  const clientsQuery = useQuery<Client[]>({
    queryKey: ['clients', q, status, sort, order],
    refetchInterval: 15000,
    queryFn: async () => {
      const query = new URLSearchParams({ q, status, sort, order });
      const response = await apiFetch(`/api/v1/clients?${query}`);
      if (!response.ok) throw new Error(await responseError(response, 'Не удалось загрузить клиентов'));
      return response.json();
    },
  });

  const createMutation = useMutation({
    mutationFn: async () => {
      const response = await apiFetch('/api/v1/clients', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, phone, email, ...subscriptionPayload(subscription) }),
      });
      if (!response.ok) throw new Error(await responseError(response, 'Не удалось создать клиента'));
      return response.json();
    },
    onSuccess: (result) => {
      setCreateOpen(false); setName(''); setPhone(''); setEmail(''); setError('');
      setSubscription({ period: 'month', date: '', quotaGB: '', cdnQuotaGB: '' });
      navigate(`/clients/${result.client.id}/access`);
    },
    onError: (reason: Error) => setError(reason.message),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ['clients'] }),
  });

  const deleteMutation = useMutation({
    mutationFn: async (id: number) => {
      const response = await apiFetch(`/api/v1/clients/${id}`, { method: 'DELETE' });
      if (!response.ok) throw new Error(await responseError(response, 'Не удалось удалить клиента'));
    },
    onError: (reason: Error) => setError(reason.message),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ['clients'] }),
  });

  const pauseMutation = useMutation({
    mutationFn: async (client: Client) => {
      const response = await apiFetch(`/api/v1/clients/${client.id}`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ is_active: !client.is_active }),
      });
      if (!response.ok) throw new Error(await responseError(response, 'Не удалось изменить доступ клиента'));
    },
    onError: (reason: Error) => setError(reason.message),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ['clients'] }),
  });

  const setFilter = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value); else next.delete(key);
    setParams(next);
  };
  const setSort = (field: SortField) => {
    const nextOrder = sort === field && order === 'asc' ? 'desc' : 'asc';
    const next = new URLSearchParams(params); next.set('sort', field); next.set('order', nextOrder); setParams(next);
  };

  const sortButton = (label: string, field: SortField) => (
    <button onClick={() => setSort(field)} className="hover:text-neutral-900">
      {label}{sort === field ? (order === 'asc' ? ' ↑' : ' ↓') : ''}
    </button>
  );

  return (
    <section className="relative overflow-hidden ui-card">
      {createOpen && <div className="fixed inset-0 z-50 flex items-center justify-center bg-neutral-900/50 p-4">
        <form onSubmit={(event) => { event.preventDefault(); setError(''); createMutation.mutate(); }} className="relative max-h-[90vh] w-full max-w-md space-y-4 overflow-y-auto rounded-2xl bg-white p-6 shadow-xl">
          <button type="button" aria-label="Закрыть создание клиента" onClick={() => setCreateOpen(false)} className="absolute right-4 top-4 text-neutral-400"><X size={20} /></button>
          <h3 className="text-xl font-semibold">Новый клиент</h3>
          <p className="text-sm text-neutral-500">Будут созданы профили всех включённых протоколов.</p>
          {error && <div className="rounded-lg bg-red-50 p-3 text-sm text-red-700">{error}</div>}
          <input required maxLength={64} value={name} onChange={(e) => setName(e.target.value)} placeholder="Имя клиента" className="w-full rounded-lg border p-3" />
          <input value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="Телефон (необязательно)" className="w-full rounded-lg border p-3" />
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="Электронная почта (необязательно)" className="w-full rounded-lg border p-3" />
          <SubscriptionFields value={subscription} onChange={setSubscription} />
          <button disabled={createMutation.isPending} className="ui-button ui-button-primary w-full">Создать клиента</button>
        </form>
      </div>}

      <div className="flex flex-col justify-between gap-4 border-b p-4 sm:flex-row sm:items-center">
        <div className="flex gap-1 rounded-lg bg-neutral-100 p-1">
          {([['all', 'Все'], ['active', 'Доступ разрешён'], ['disabled', 'Доступ закрыт']] as const).map(([value, label]) =>
            <button key={value} onClick={() => setFilter('status', value)} className={`rounded-md px-3 py-1.5 text-sm ${status === value ? 'bg-white text-neutral-900 shadow-sm' : 'text-neutral-500'}`}>{label}</button>
          )}
        </div>
        <div className="flex min-w-0 flex-col gap-3 sm:flex-row">
          <label className="relative min-w-0 flex-1"><Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-neutral-400" /><input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Поиск клиентов" className="w-full sm:w-56 rounded-lg border bg-neutral-50 py-2 pl-9 pr-3 text-sm" /></label>
          <button onClick={() => { setError(''); setCreateOpen(true); }} className="ui-button ui-button-primary"><Plus size={16} /> Добавить</button>
        </div>
      </div>
      {error && !createOpen && <div className="m-4 rounded-lg bg-red-50 p-3 text-sm text-red-700">{error}</div>}
      <p className="px-5 py-3 text-xs text-neutral-500">Онлайн — трафик за последние 3 минуты. Проверка раз в минуту; тихое соединение может отображаться офлайн. «Нет данных» — статистика ещё не получена или недоступна.</p>
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead className="bg-neutral-50 text-xs uppercase text-neutral-500"><tr>
            <th className="px-5 py-3">{sortButton('Статус', 'status')}</th><th className="px-5 py-3">{sortButton('Клиент', 'name')}</th>
            <th className="px-5 py-3">Контакты</th><th className="px-5 py-3">{sortButton('Трафик', 'traffic')}</th>
            <th className="px-5 py-3">{sortButton('Протоколы', 'protocol')}</th><th className="px-5 py-3 text-right">Действия</th>
          </tr></thead>
          <tbody className="divide-y divide-neutral-100">
            {clientsQuery.data?.map((client) => <tr key={client.id} className="hover:bg-neutral-50/60">
              <td className="px-5 py-4"><span className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2 py-1 text-xs ${client.connection_status === 'online' ? 'bg-emerald-50 text-emerald-700' : 'bg-neutral-100 text-neutral-500'}`}><span className="h-1.5 w-1.5 rounded-full bg-current" />{client.connection_status === 'online' ? 'Онлайн' : client.connection_status === 'offline' ? 'Офлайн' : 'Нет данных'}</span><div className="mt-1 text-xs text-neutral-500">{!client.is_active ? 'Приостановлен' : client.access_allowed ? 'Доступ разрешён' : clientStatus(client.blocked_reason)}</div></td>
              <td className="px-5 py-4 font-medium text-neutral-800">{client.name}<div className="mt-1 text-xs font-normal text-neutral-500">{formatSubscriptionDate(client.expires_at)}</div></td>
              <td className="px-5 py-4 text-xs text-neutral-500">{client.phone || '—'}<br />{client.email || ''}</td>
              <td className="px-5 py-4 text-xs text-neutral-600"><div>{formatTraffic(client.monthly_traffic_used)} / {client.monthly_traffic_limit > 0 ? formatTraffic(client.monthly_traffic_limit) : '∞'} за месяц</div><div className="mt-1">Обход БС: {formatTraffic(client.cdn_monthly_traffic_used)} / {client.cdn_monthly_traffic_limit > 0 ? formatTraffic(client.cdn_monthly_traffic_limit) : '∞'}{client.cdn_quota_exhausted && <span className="ml-1 text-amber-700">· лимит исчерпан</span>}</div><div className="mt-1 text-neutral-400">Всего: {formatTraffic(client.traffic_total)}</div><div className="mt-1 text-neutral-400">Обновление: {formatSubscriptionDate(client.traffic_period_end)}</div></td>
              <td className="px-5 py-4"><div className="mb-2 text-xs text-neutral-600">Активность: {client.connected_protocols?.length ? client.connected_protocols.map(translateProfile).join(', ') : '—'}</div><div className="flex flex-wrap gap-1">{client.profiles.filter((profile) => profile.is_enabled).map((profile) => <span key={profile.id} className="rounded-md bg-indigo-50 px-2 py-1 text-[10px] text-indigo-700">{translateProfile(profile.kind)}</span>)}</div></td>
              <td className="px-5 py-4"><div className="flex flex-nowrap justify-end gap-2 whitespace-nowrap"><button onClick={() => navigate(`/clients/${client.id}/access`)} title="Настроить доступ" aria-label="Настроить доступ" className="ui-button ui-button-primary text-xs"><KeyRound size={16} /></button><button disabled={pauseMutation.isPending} onClick={() => { setError(''); if (window.confirm(client.is_active ? `Приостановить доступ «${client.name}»? Профили и ссылки сохранятся.` : `Возобновить доступ «${client.name}»? Срок и лимиты подписки сохранятся.`)) pauseMutation.mutate(client); }} title={client.is_active ? 'Приостановить' : 'Возобновить'} aria-label={client.is_active ? 'Приостановить' : 'Возобновить'} className="ui-button ui-button-secondary text-xs">{client.is_active ? <Pause size={16} /> : <Play size={16} />}</button><button disabled={deleteMutation.isPending} title="Удалить клиента" aria-label={`Удалить клиента ${client.name}`} onClick={() => { if (window.confirm(`Удалить клиента «${client.name}» и его профили? Это действие нельзя отменить.`)) deleteMutation.mutate(client.id); }} className="ui-button ui-button-danger text-xs"><Trash2 size={16} /></button></div></td>
            </tr>)}
            {clientsQuery.isLoading && <tr><td colSpan={6} className="p-10 text-center text-neutral-500">Загрузка клиентов…</td></tr>}
            {!clientsQuery.isLoading && clientsQuery.data?.length === 0 && <tr><td colSpan={6} className="p-10 text-center text-neutral-500">Клиенты не найдены</td></tr>}
          </tbody>
        </table>
      </div>
    </section>
  );
};

export default ClientsTable;
