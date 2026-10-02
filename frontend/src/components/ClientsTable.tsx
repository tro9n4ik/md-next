import React, { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Plus, Search, Trash2, X } from 'lucide-react';
import { apiFetch } from '../utils/api';
import { formatBytes } from '../utils/ru';

type Profile = { id: number; kind: string; is_enabled: boolean };
type Client = {
  id: number; name: string; phone?: string; email?: string; is_active: boolean;
  traffic_total: number; traffic_limit: number; traffic_up: number; traffic_down: number; profiles: Profile[];
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
        body: JSON.stringify({ name, phone, email }),
      });
      if (!response.ok) throw new Error(await responseError(response, 'Не удалось создать клиента'));
      return response.json();
    },
    onSuccess: (result) => {
      setCreateOpen(false); setName(''); setPhone(''); setEmail(''); setError('');
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
    <section className="relative overflow-hidden rounded-xl border border-neutral-200/70 bg-white shadow-sm">
      {createOpen && <div className="fixed inset-0 z-50 flex items-center justify-center bg-neutral-900/50 p-4">
        <form onSubmit={(event) => { event.preventDefault(); setError(''); createMutation.mutate(); }} className="relative w-full max-w-md space-y-4 rounded-2xl bg-white p-6 shadow-xl">
          <button type="button" onClick={() => setCreateOpen(false)} className="absolute right-4 top-4 text-neutral-400"><X size={20} /></button>
          <h3 className="text-xl font-semibold">Новый клиент</h3>
          <p className="text-sm text-neutral-500">Будут созданы профили всех включённых протоколов.</p>
          {error && <div className="rounded-lg bg-red-50 p-3 text-sm text-red-700">{error}</div>}
          <input required maxLength={64} value={name} onChange={(e) => setName(e.target.value)} placeholder="Имя клиента" className="w-full rounded-lg border p-3" />
          <input value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="Телефон (необязательно)" className="w-full rounded-lg border p-3" />
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="Email (необязательно)" className="w-full rounded-lg border p-3" />
          <button disabled={createMutation.isPending} className="w-full rounded-lg bg-emerald-600 px-4 py-3 font-medium text-white disabled:opacity-50">Создать клиента</button>
        </form>
      </div>}

      <div className="flex flex-col justify-between gap-4 border-b p-4 sm:flex-row sm:items-center">
        <div className="flex gap-1 rounded-lg bg-neutral-100 p-1">
          {([['all', 'Все'], ['active', 'Работают'], ['disabled', 'Отключены']] as const).map(([value, label]) =>
            <button key={value} onClick={() => setFilter('status', value)} className={`rounded-md px-3 py-1.5 text-sm ${status === value ? 'bg-white text-neutral-900 shadow-sm' : 'text-neutral-500'}`}>{label}</button>
          )}
        </div>
        <div className="flex gap-3">
          <label className="relative"><Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-neutral-400" /><input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Поиск клиентов" className="w-56 rounded-lg border bg-neutral-50 py-2 pl-9 pr-3 text-sm" /></label>
          <button onClick={() => { setError(''); setCreateOpen(true); }} className="flex items-center gap-2 rounded-lg bg-emerald-600 px-3 py-2 text-sm font-medium text-white"><Plus size={16} /> Добавить</button>
        </div>
      </div>
      {error && !createOpen && <div className="m-4 rounded-lg bg-red-50 p-3 text-sm text-red-700">{error}</div>}
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead className="bg-neutral-50 text-xs uppercase text-neutral-500"><tr>
            <th className="px-5 py-3">{sortButton('Статус', 'status')}</th><th className="px-5 py-3">{sortButton('Клиент', 'name')}</th>
            <th className="px-5 py-3">Контакты</th><th className="px-5 py-3">{sortButton('Трафик', 'traffic')}</th>
            <th className="px-5 py-3">{sortButton('Протоколы', 'protocol')}</th><th className="px-5 py-3 text-right">Действия</th>
          </tr></thead>
          <tbody className="divide-y divide-neutral-100">
            {clientsQuery.data?.map((client) => <tr key={client.id} className="hover:bg-neutral-50/60">
              <td className="px-5 py-4"><span className={`inline-flex rounded-full px-2 py-1 text-xs ${client.is_active ? 'bg-emerald-50 text-emerald-700' : 'bg-neutral-100 text-neutral-500'}`}>{client.is_active ? 'Работает' : 'Отключён'}</span></td>
              <td className="px-5 py-4 font-medium text-neutral-800">{client.name}</td>
              <td className="px-5 py-4 text-xs text-neutral-500">{client.phone || '—'}<br />{client.email || ''}</td>
              <td className="px-5 py-4 font-mono text-xs text-neutral-600">{formatTraffic(client.traffic_total)}{client.traffic_limit > 0 ? ` / ${formatTraffic(client.traffic_limit)}` : ''}</td>
              <td className="px-5 py-4"><div className="flex flex-wrap gap-1">{client.profiles.filter((profile) => profile.is_enabled).map((profile) => <span key={profile.id} className="rounded-md bg-indigo-50 px-2 py-1 text-[10px] text-indigo-700">{profile.kind.replaceAll('_', ' ')}</span>)}</div></td>
              <td className="px-5 py-4"><div className="flex justify-end gap-2"><button onClick={() => navigate(`/clients/${client.id}/access`)} className="rounded-md bg-emerald-50 px-3 py-1.5 text-xs font-medium text-emerald-700">Доступ</button><button title="Удалить" onClick={() => deleteMutation.mutate(client.id)} className="rounded p-1.5 text-neutral-400 hover:bg-red-50 hover:text-red-600"><Trash2 size={16} /></button></div></td>
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
