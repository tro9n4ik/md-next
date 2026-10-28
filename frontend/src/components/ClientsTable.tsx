import { useConfirm } from '../contexts/ConfirmationContext';
import React, { useEffect, useState } from 'react';
import { flushSync } from 'react-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Plus, Search, Trash2, Pause, Play, KeyRound, Users, Folder, FolderPlus, SlidersHorizontal, ArrowRight, RefreshCw } from 'lucide-react';
import { useCurrentUser } from '../contexts/AuthContext';
import { apiFetch } from '../utils/api';
import { formatBytes, translateProfile } from '../utils/ru';
import SubscriptionFields from './SubscriptionFields';
import Drawer from './ui/Drawer';
import { GroupEditor } from './ClientGroups';
import { groupRequest } from '../utils/clientGroups';
import type { ClientGroup, GroupSummary } from './ClientGroups';
import { clientStatus, formatSubscriptionDate, subscriptionPayload } from '../utils/subscriptions';
import { lastOnlineLabel } from '../utils/clientPresence';
import type { ClientLimits, SubscriptionValues } from '../utils/subscriptions';

type Profile = { id: number; kind: string; is_enabled: boolean };
type Client = ClientLimits & {
  id: number; name: string; phone?: string; email?: string; is_active: boolean;
  traffic_total: number; traffic_limit: number; traffic_up: number; traffic_down: number; profiles: Profile[];
  connection_status: 'online' | 'offline' | 'unknown'; connected_protocols: string[];
  can_delete: boolean; can_pause: boolean; can_change_group: boolean;
  group_id: number | null; group: ClientGroup | null; last_seen_at: string | null;
};
type SortField = 'status' | 'name' | 'traffic' | 'protocol' | 'created';

async function responseError(response: Response, fallback: string) {
  const body = await response.json().catch(() => ({}));
  return typeof body.detail === 'string' ? body.detail : fallback;
}
const formatTraffic = formatBytes;

const ClientsTable: React.FC<{ description?: string }> = ({ description = 'Пользователи, условия подписки и профили подключения' }) => {
  const confirm = useConfirm();
  const queryClient = useQueryClient();
  const user = useCurrentUser();
  const isAdmin = user.role === 'admin';
  const canIssue = isAdmin || user.can_issue_subscriptions;
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const [search, setSearch] = useState(params.get('q') || '');
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const createOpen = createModalOpen || params.get('create') === '1';
  const setCreateOpen = (open: boolean) => {
    setCreateModalOpen(open);
    if (!open && params.has('create')) {
      const next = new URLSearchParams(params); next.delete('create'); setParams(next, { replace: true });
    }
  };
  const [name, setName] = useState('');
  const [phone, setPhone] = useState('');
  const [email, setEmail] = useState('');
  const [newGroupId, setNewGroupId] = useState<string | null>(null);
  const [subscription, setSubscription] = useState<SubscriptionValues>({ period: 'month', date: '', quotaGB: '', cdnQuotaGB: '' });
  const [error, setError] = useState('');
  const [groupEditor, setGroupEditor] = useState<ClientGroup | 'new' | null>(null);
  const q = params.get('q') || '';
  const status = params.get('status') || 'all';
  const sort = (params.get('sort') || 'created') as SortField;
  const order = params.get('order') || 'desc';
  const groupFilter = params.get('group') || '';

  useEffect(() => {
    const timer = window.setTimeout(() => {
      const next = new URLSearchParams(params);
      if (search.trim()) next.set('q', search.trim()); else next.delete('q');
      if (next.toString() !== params.toString()) setParams(next, { replace: true });
    }, 300);
    return () => window.clearTimeout(timer);
  }, [search, params, setParams]);

  const groups = useQuery({
    queryKey: ['clientGroups'],
    queryFn: () => groupRequest<GroupSummary>('/api/v1/client-groups'),
    refetchInterval: 15000,
  });
  const clientsQuery = useQuery<Client[]>({
    queryKey: groupFilter ? ['clients', q, status, sort, order, groupFilter] : ['clients', q, status, sort, order],
    refetchInterval: 15000,
    queryFn: async () => {
      const query = new URLSearchParams({ q, status, sort, order });
      if (groupFilter) query.set('group_id', groupFilter);
      const response = await apiFetch(`/api/v1/clients?${query}`);
      if (!response.ok) throw new Error(await responseError(response, 'Не удалось загрузить клиентов'));
      return response.json();
    },
  });
  const refreshClients = () => {
    void queryClient.invalidateQueries({ queryKey: ['clients'] });
    void queryClient.invalidateQueries({ queryKey: ['clientGroups'] });
  };
  const createMutation = useMutation({
    mutationFn: async () => {
      const selectedGroup = newGroupId ?? (groupFilter !== '0' ? groupFilter : '');
      const response = await apiFetch('/api/v1/clients', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, phone, email, group_id: selectedGroup ? Number(selectedGroup) : null, ...subscriptionPayload(subscription) }),
      }, 'new-client-drawer');
      if (!response.ok) throw new Error(await responseError(response, 'Не удалось создать клиента'));
      return response.json();
    },
    onSuccess: (result) => {
      // Закрываем форму до перехода, чтобы снять защиту несохранённых изменений.
      flushSync(() => setCreateOpen(false));
      setName(''); setPhone(''); setEmail(''); setError(''); setNewGroupId(null);
      setSubscription({ period: 'month', date: '', quotaGB: '', cdnQuotaGB: '' });
      navigate(`/clients/${result.client.id}/access`);
    },
    onError: (reason: Error) => setError(reason.message),
    onSettled: refreshClients,
  });
  const deleteMutation = useMutation({
    mutationFn: async (id: number) => {
      const response = await apiFetch(`/api/v1/clients/${id}`, { method: 'DELETE' });
      if (!response.ok) throw new Error(await responseError(response, 'Не удалось удалить клиента'));
    },
    onError: (reason: Error) => setError(reason.message), onSettled: refreshClients,
  });
  const pauseMutation = useMutation({
    mutationFn: async (client: Client) => {
      const response = await apiFetch(`/api/v1/clients/${client.id}/status`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ is_active: !client.is_active }),
      });
      if (!response.ok) throw new Error(await responseError(response, 'Не удалось изменить доступ клиента'));
    },
    onError: (reason: Error) => setError(reason.message), onSettled: refreshClients,
  });
  const assignGroup = useMutation({
    mutationFn: ({ id, groupId }: { id: number; groupId: string }) => groupRequest(`/api/v1/clients/${id}/group`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ group_id: groupId ? Number(groupId) : null }),
    }),
    onError: (reason: Error) => setError(reason.message), onSettled: refreshClients,
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
  const sortButton = (label: string, field: SortField) => <button onClick={() => setSort(field)}>{label}{sort === field ? (order === 'asc' ? ' ↑' : ' ↓') : ''}</button>;
  const selectedGroup = groups.data?.groups.find(item => String(item.id) === groupFilter);
  const rows = clientsQuery.data || [];
  const createDirty = !!(name || phone || email || newGroupId !== null || subscription.date || subscription.quotaGB || subscription.cdnQuotaGB || subscription.period !== 'month');
  const closeCreate = async () => { if (!createDirty || await confirm({ title: 'Закрыть форму клиента?', description: 'Клиент не будет создан. Заполненные данные останутся в черновике до ухода со страницы.', confirmText: 'Закрыть форму', cancelText: 'Продолжить заполнение', tone: 'warning' })) { setError(''); setCreateOpen(false); } };

  return <div className="clients-workspace">
    <header className="dashboard-heading"><div><div className="dashboard-eyebrow">ЛЮДИ И ПОДКЛЮЧЕНИЯ</div><h1>Клиенты</h1><p>{description}</p></div><div className="page-actions">
      {isAdmin && <button className="ui-button ui-button-secondary" onClick={() => setGroupEditor('new')}><FolderPlus size={17} />Новая группа</button>}
      {canIssue && <button onClick={() => { setError(''); setNewGroupId(null); setCreateOpen(true); }} className="ui-button ui-button-primary"><Plus size={17} />Добавить клиента</button>}
    </div></header>

    <nav className="client-groups" aria-label="Группы клиентов">
      <button className={`client-group-card ${!groupFilter ? 'selected' : ''}`} aria-pressed={!groupFilter} onClick={() => setFilter('group', '')}><span className="client-group-icon"><Users size={19} /></span><span>Все клиенты<b>{groups.data?.total ?? '—'}</b></span></button>
      {groups.data?.groups.map(group => <div key={group.id} className={`client-group-card group-${group.color} ${groupFilter === String(group.id) ? 'selected' : ''}`}>
        <button className="client-group-select" aria-pressed={groupFilter === String(group.id)} onClick={() => setFilter('group', String(group.id))}><span className="client-group-icon"><Folder size={19} /></span><span>{group.name}<b>{group.count}</b></span></button>
        {isAdmin && <button className="client-group-edit" title={`Настроить группу ${group.name}`} aria-label={`Настроить группу ${group.name}`} onClick={() => setGroupEditor(group)}><SlidersHorizontal size={15} /></button>}
      </div>)}
      <button className={`client-group-card group-slate ${groupFilter === '0' ? 'selected' : ''}`} aria-pressed={groupFilter === '0'} onClick={() => setFilter('group', '0')}><span className="client-group-icon"><Folder size={19} /></span><span>Без группы<b>{groups.data?.ungrouped_count ?? '—'}</b></span></button>
    </nav>
    {groups.isError && <div role="alert" className="client-form-error">Не удалось загрузить группы. <button onClick={() => groups.refetch()}>Повторить</button></div>}
    {groupEditor && isAdmin && <GroupEditor group={groupEditor === 'new' ? null : groupEditor} onClose={() => setGroupEditor(null)} />}

    {canIssue && createOpen && <Drawer id="new-client-drawer" title="Новый клиент" onClose={closeCreate} busy={createMutation.isPending} dirty={createDirty}>
      <form onSubmit={event => { event.preventDefault(); setError(''); createMutation.mutate(); }} className="client-editor-form">
        <p className="client-form-hint">Создадим подписку и профили всех включённых протоколов.</p>
        {error && <div role="alert" className="client-form-error">{error}</div>}
        <label>Имя клиента<input autoFocus required maxLength={64} value={name} onChange={event => setName(event.target.value)} placeholder="Имя или название" /></label>
        <label>Группа<select aria-label="Группа" disabled={groups.isLoading || groups.isError} value={newGroupId ?? (groupFilter !== '0' ? groupFilter : '')} onChange={event => setNewGroupId(event.target.value)}><option value="">Без группы</option>{groups.data?.groups.map(group => <option key={group.id} value={group.id}>{group.name}</option>)}</select></label>
        <label>Телефон <span>необязательно</span><input value={phone} onChange={event => setPhone(event.target.value)} placeholder="+7 …" /></label>
        <label>Электронная почта <span>необязательно</span><input type="email" value={email} onChange={event => setEmail(event.target.value)} placeholder="mail@example.com" /></label>
        <div className="client-subscription-fields"><h3>Условия подписки</h3><SubscriptionFields value={subscription} onChange={setSubscription} /></div>
        <button disabled={createMutation.isPending || groups.isLoading || groups.isError} className="ui-button ui-button-primary">{createMutation.isPending ? 'Создаём…' : 'Создать клиента'}<ArrowRight size={16} /></button>
      </form>
    </Drawer>}

    <section className="ui-card client-list-card">
      <div className="client-list-heading"><div><h2>{selectedGroup?.name || (groupFilter === '0' ? 'Без группы' : groupFilter ? 'Группа' : 'Все клиенты')}<span>{clientsQuery.data ? rows.length : '…'}</span></h2><p>Управляйте подписками и доступом в одном месте</p></div><button className="client-refresh" title="Обновить список" aria-label="Обновить список" disabled={clientsQuery.isFetching} onClick={() => { void clientsQuery.refetch(); void groups.refetch(); }}><RefreshCw size={17} className={clientsQuery.isFetching ? 'animate-spin' : ''} /></button></div>
      <div className="client-toolbar">
        <div className="client-status-tabs" aria-label="Фильтр по доступу">
          {([['all', 'Все'], ['active', 'Доступ разрешён'], ['disabled', 'Доступ закрыт']] as const).map(([value, label]) => <button key={value} aria-pressed={status === value} onClick={() => setFilter('status', value)} className={status === value ? 'selected' : ''}>{label}</button>)}
        </div>
        <label className="client-search"><Search size={17} /><input aria-label="Поиск клиентов" value={search} onChange={event => setSearch(event.target.value)} placeholder="Имя, телефон или почта" /></label>
      </div>
      {error && !createOpen && <div role="alert" className="client-form-error mx-5 my-3">{error}</div>}
      {clientsQuery.isError && <div role="alert" className="client-form-error mx-5 my-3">Не удалось загрузить клиентов. <button onClick={() => clientsQuery.refetch()}>Повторить</button></div>}
      <div className="client-table-scroll"><table className="client-table"><thead><tr>
        <th aria-sort={sort === 'name' ? order === 'asc' ? 'ascending' : 'descending' : 'none'}>{sortButton('Клиент', 'name')}</th>
        <th aria-sort={sort === 'status' ? order === 'asc' ? 'ascending' : 'descending' : 'none'}>{sortButton('Активность и доступ', 'status')}</th>
        <th aria-sort={sort === 'traffic' ? order === 'asc' ? 'ascending' : 'descending' : 'none'}>{sortButton('Трафик', 'traffic')}</th>
        <th aria-sort={sort === 'protocol' ? order === 'asc' ? 'ascending' : 'descending' : 'none'}>{sortButton('Протоколы', 'protocol')}</th><th className="client-actions-heading">Действия</th>
      </tr></thead><tbody>
        {rows.map(client => <tr key={client.id}>
          <td data-label="Клиент"><div className="client-identity"><span className={`client-avatar group-${client.group?.color || 'slate'}`}>{client.name.charAt(0).toUpperCase()}</span><div><b>{client.name}</b><span className="client-contact">{client.email || client.phone || `ID ${client.id}`}</span></div></div>
            <div className={`client-group-badge group-${client.group?.color || 'slate'}`}><i />{(isAdmin || (canIssue && client.can_change_group)) ? <select aria-label={`Группа клиента ${client.name}`} value={client.group_id ?? ''} disabled={assignGroup.isPending || groups.isError || groups.isLoading} onChange={event => { setError(''); assignGroup.mutate({ id: client.id, groupId: event.target.value }); }}><option value="">Без группы</option>{groups.data?.groups.map(group => <option key={group.id} value={group.id}>{group.name}</option>)}</select> : <span>{client.group?.name || 'Без группы'}</span>}</div>
          </td>
          <td data-label="Активность и доступ"><span className={`client-presence ${client.connection_status === 'online' ? 'is-online' : ''}`}><i />{client.connection_status === 'online' ? 'Онлайн' : client.connection_status === 'unknown' ? 'Нет данных' : client.last_seen_at ? 'Был онлайн' : 'Офлайн'}</span>
            <p className="client-last-seen">{client.last_seen_at ? <time dateTime={client.last_seen_at}>{lastOnlineLabel(client.last_seen_at)}</time> : 'Активность ещё не зафиксирована'}</p>
            <span className={`client-access ${!client.is_active || !client.access_allowed ? 'is-blocked' : ''}`}>{!client.is_active ? 'Приостановлен' : client.access_allowed ? 'Доступ разрешён' : clientStatus(client.blocked_reason)}</span>
            <p className="client-expiry">{client.expires_at ? 'До ' : ''}{formatSubscriptionDate(client.expires_at)}</p>
          </td>
          <td data-label="Трафик"><div className="client-traffic-value"><b>{formatTraffic(client.monthly_traffic_used)}</b><span> / {client.monthly_traffic_limit > 0 ? formatTraffic(client.monthly_traffic_limit) : '∞'}</span></div>
            {client.monthly_traffic_limit > 0 && <progress className="client-quota" aria-label={`Использованный месячный трафик ${client.name}`} max={client.monthly_traffic_limit} value={Math.min(client.monthly_traffic_used, client.monthly_traffic_limit)} />}
            <p className="client-traffic-detail">Обход БС: {formatTraffic(client.cdn_monthly_traffic_used)} / {client.cdn_monthly_traffic_limit > 0 ? formatTraffic(client.cdn_monthly_traffic_limit) : '∞'}{client.cdn_quota_exhausted && <span className="text-amber-700"> · лимит исчерпан</span>}</p>
            <p className="client-traffic-detail">Всего: {formatTraffic(client.traffic_total)}</p><p className="client-traffic-detail">Обновление: {formatSubscriptionDate(client.traffic_period_end)}</p>
          </td>
          <td data-label="Протоколы"><div className="client-protocols">{client.profiles.filter(profile => profile.is_enabled).map(profile => <span key={profile.id}>{translateProfile(profile.kind)}</span>)}</div><p className="client-traffic-detail">Активность: {client.connected_protocols?.length ? client.connected_protocols.map(translateProfile).join(', ') : '—'}</p></td>
          <td data-label="Действия"><div className="client-row-actions">
            {canIssue && <button onClick={() => navigate(`/clients/${client.id}/access`)} title={isAdmin ? 'Настроить доступ' : 'Выдать подключение'} aria-label={isAdmin ? 'Настроить доступ' : 'Выдать подключение'} className="ui-button ui-button-secondary"><KeyRound size={16} /></button>}
            {(isAdmin || (user.can_pause_subscriptions && client.can_pause)) && <button disabled={pauseMutation.isPending} onClick={async () => { setError(''); if (await confirm({ title: client.is_active ? 'Приостановить доступ?' : 'Возобновить доступ?', description: client.is_active ? `Доступ клиента «${client.name}» будет приостановлен. Профили и ссылки сохранятся.` : `Доступ клиента «${client.name}» будет возобновлён. Срок и лимиты подписки сохранятся.`, confirmText: client.is_active ? 'Приостановить' : 'Возобновить', tone: client.is_active ? 'warning' : 'default' })) pauseMutation.mutate(client); }} title={client.is_active ? 'Приостановить' : 'Возобновить'} aria-label={client.is_active ? 'Приостановить' : 'Возобновить'} className="ui-button ui-button-secondary">{client.is_active ? <Pause size={16} /> : <Play size={16} />}</button>}
            {(isAdmin || (user.can_delete_subscriptions && client.can_delete)) && <button disabled={deleteMutation.isPending} title="Удалить клиента" aria-label={`Удалить клиента ${client.name}`} onClick={async () => { if (await confirm({ title: 'Удалить клиента?', description: `Клиент «${client.name}» и его профили будут удалены. Это действие нельзя отменить.`, confirmText: 'Удалить клиента', tone: 'danger' })) deleteMutation.mutate(client.id); }} className="ui-button client-delete-action"><Trash2 size={16} /></button>}
          </div></td>
        </tr>)}
        {clientsQuery.isLoading && <tr><td colSpan={5} className="client-list-empty">Загрузка клиентов…</td></tr>}
        {!clientsQuery.isLoading && !clientsQuery.isError && !rows.length && <tr><td colSpan={5} className="client-list-empty"><Users size={30} /><b>Клиенты не найдены</b><span>{q || status !== 'all' ? 'Попробуйте изменить поиск или фильтр.' : groupFilter ? 'Добавьте клиента в эту группу или выберите другую.' : 'Создайте первую подписку, чтобы начать работу.'}</span></td></tr>}
      </tbody></table></div>
      <footer className="client-list-footer"><span>Онлайн — трафик за последние 3 минуты. Проверка раз в минуту.</span><span>Время активности — по часовому поясу устройства</span></footer>
    </section>
  </div>;
};

export default ClientsTable;
