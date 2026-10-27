import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Plus, Settings2 } from 'lucide-react';
import Drawer from '../components/ui/Drawer';
import Switch from '../components/ui/Switch';
import { apiFetch } from '../utils/api';

interface Account { id: number; username: string; is_active: boolean; view_all_clients: boolean; totp_enabled: boolean; can_issue_subscriptions: boolean; can_delete_subscriptions: boolean; can_pause_subscriptions: boolean }

async function request(path = '', method = 'GET', body?: unknown) {
  const response = await apiFetch('/api/v1/users' + path, {
    method, ...(body === undefined ? {} : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Проверьте логин и пароль');
  return data;
}

export function AccountEditor({ account, refresh, onClose }: { account: Account; refresh: () => void; onClose: () => void }) {
  const cache = useQueryClient();
  const [password, setPassword] = useState('');
  const [notice, setNotice] = useState('');
  const update = useMutation({
    mutationFn: (body: { is_active?: boolean; password?: string; view_all_clients?: boolean; can_issue_subscriptions?: boolean; can_delete_subscriptions?: boolean; can_pause_subscriptions?: boolean }) => request('/' + account.id, 'PUT', body),
    onSuccess: (data, variables) => { if (variables.password !== undefined) setPassword(''); cache.setQueryData<Account[]>(['subadmins'], previous => previous?.map(item => item.id === data.id ? data : item)); setNotice('Сохранено. Субадминистратору потребуется войти заново.'); refresh(); },
    onError: (error: Error) => setNotice(error.message),
  });
  return <Drawer title={account.username} busy={update.isPending} dirty={!!password} onClose={() => { if (!password || window.confirm('Закрыть форму и не сохранять новый пароль?')) onClose(); }}><article className="space-y-4">
    <div className="flex flex-wrap items-center justify-between gap-3"><div><h3 className="font-semibold">Доступ в панель</h3><p className="text-xs text-neutral-500">Субадминистратор · 2FA {account.totp_enabled ? 'включена' : 'выключена'}</p></div>
      <Switch label={`Доступ в панель: ${account.username}`} checked={account.is_active} disabled={update.isPending} onChange={is_active => update.mutate({ is_active })} />
    </div>
    <div className="flex items-center justify-between gap-4"><div><p className="text-sm font-medium">Видит всех клиентов</p><p className="text-xs text-neutral-500">{account.view_all_clients ? 'Показывать всех клиентов; выдача подключений зависит от разрешения ниже' : 'Показывать только созданных им клиентов'}</p></div>
      <Switch label={`Все клиенты: ${account.username}`} checked={account.view_all_clients} disabled={update.isPending} onChange={view_all_clients => update.mutate({ view_all_clients })} />
    </div>
    <div className="flex items-center justify-between gap-4"><div><p className="text-sm font-medium">Создание и выдача подписок</p><p className="text-xs text-neutral-500">Создание клиентов, получение ссылок, QR-кодов и конфигураций видимых клиентов</p></div>
      <Switch label={`Создание и выдача подписок: ${account.username}`} checked={account.can_issue_subscriptions} disabled={update.isPending} onChange={can_issue_subscriptions => update.mutate({ can_issue_subscriptions })} />
    </div>
    <div className="flex items-center justify-between gap-4"><div><p className="text-sm font-medium">Удаление своих подписок</p><p className="text-xs text-neutral-500">Удаление только созданных им клиентов вместе с профилями; чужие подписки защищены</p></div>
      <Switch label={`Удаление своих подписок: ${account.username}`} checked={account.can_delete_subscriptions} disabled={update.isPending} onChange={can_delete_subscriptions => update.mutate({ can_delete_subscriptions })} />
    </div>
    <div className="flex items-center justify-between gap-4"><div><p className="text-sm font-medium">Приостановка своих подписок</p><p className="text-xs text-neutral-500">Приостановка и возобновление только созданных им клиентов без изменения срока, лимитов и ссылок</p></div>
      <Switch label={`Приостановка своих подписок: ${account.username}`} checked={account.can_pause_subscriptions} disabled={update.isPending} onChange={can_pause_subscriptions => update.mutate({ can_pause_subscriptions })} />
    </div>
    <details className="border-t border-neutral-200 pt-4"><summary className="cursor-pointer text-sm font-medium">Сбросить пароль</summary><form className="mt-4 flex flex-wrap gap-3" onSubmit={event => { event.preventDefault(); update.mutate({ password }); }}>
      <input aria-label={`Новый пароль: ${account.username}`} type="password" autoComplete="new-password" minLength={10} maxLength={72} required value={password} onChange={event => setPassword(event.target.value)} placeholder="Новый пароль" className="min-w-0 flex-1 rounded-lg border p-3 text-sm" />
      <button className="ui-button ui-button-secondary" disabled={update.isPending || password.length < 10}>Сохранить пароль</button>
    </form></details>
    {notice && <p role="status" className="text-sm text-neutral-600">{notice}</p>}
  </article></Drawer>;
}

export default function UsersPage() {
  const queryClient = useQueryClient();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [viewAll, setViewAll] = useState(false);
  const [canIssue, setCanIssue] = useState(true);
  const [canDelete, setCanDelete] = useState(false);
  const [canPause, setCanPause] = useState(false);
  const [creating, setCreating] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [notice, setNotice] = useState('');
  const accounts = useQuery<Account[]>({ queryKey: ['subadmins'], queryFn: () => request() });
  const refresh = () => { void queryClient.invalidateQueries({ queryKey: ['subadmins'] }); };
  const create = useMutation({
    mutationFn: () => request('', 'POST', { username, password, view_all_clients: viewAll, can_issue_subscriptions: canIssue, can_delete_subscriptions: canDelete, can_pause_subscriptions: canPause }),
    onSuccess: () => { setUsername(''); setPassword(''); setViewAll(false); setCanIssue(true); setCanDelete(false); setCanPause(false); setCreating(false); setNotice('Учётная запись создана. Передайте логин и пароль субадминистратору.'); refresh(); },
    onError: (error: Error) => setNotice(error.message),
  });
  const editing = accounts.data?.find(account => account.id === editingId);
  const createDirty = !!(username || password || viewAll || !canIssue || canDelete || canPause);
  return <>
    <div className="flex flex-wrap items-center justify-between gap-3"><div><h2 className="ui-card-title">Субадминистраторы</h2><p className="mt-1 text-sm text-neutral-500">Учётные записи и права на работу с клиентами</p></div><button className="ui-button ui-button-primary" onClick={() => { setNotice(''); setCreating(true); }}><Plus size={17} />Добавить субадминистратора</button></div>
    {creating && <Drawer title="Новый субадминистратор" busy={create.isPending} dirty={createDirty} onClose={() => { if (!createDirty || window.confirm('Закрыть форму без создания учётной записи?')) { setCreating(false); setUsername(''); setPassword(''); setViewAll(false); setCanIssue(true); setCanDelete(false); setCanPause(false); } }}><section className="space-y-4">
      <p className="mt-4 text-sm text-neutral-500">Права на выдачу, удаление и приостановку подписок задаются отдельно. Настройки панели и изменение условий клиентов доступны только администратору. Отзыв выдачи не отключает уже полученные клиентами ссылки.</p>
      <form className="mt-4 space-y-4" onSubmit={event => { event.preventDefault(); create.mutate(); }}>
        <fieldset disabled={create.isPending} className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="text-sm">Логин<input required minLength={3} maxLength={64} pattern="[a-zA-Z0-9_.-]+" autoComplete="off" value={username} onChange={event => setUsername(event.target.value)} className="mt-1 w-full rounded-lg border p-3" /><span className="text-xs text-neutral-500">Латинские буквы, цифры, точка, дефис и подчёркивание</span></label>
          <label className="text-sm">Пароль<input required type="password" autoComplete="new-password" minLength={10} maxLength={72} value={password} onChange={event => setPassword(event.target.value)} className="mt-1 w-full rounded-lg border p-3" /><span className="text-xs text-neutral-500">Минимум 10 символов, максимум 72 байта</span></label>
        </div>
        <div className="flex items-center justify-between gap-4 text-sm"><span>Видит всех клиентов</span><Switch label="Видит всех клиентов" checked={viewAll} onChange={setViewAll} /></div>
        <div className="flex items-center justify-between gap-4 text-sm"><span>Создание и выдача подписок</span><Switch label="Создание и выдача подписок" checked={canIssue} onChange={setCanIssue} /></div>
        <div className="flex items-center justify-between gap-4 text-sm"><span>Удаление своих подписок</span><Switch label="Удаление своих подписок" checked={canDelete} onChange={setCanDelete} /></div>
        <div className="flex items-center justify-between gap-4 text-sm"><span>Приостановка своих подписок</span><Switch label="Приостановка своих подписок" checked={canPause} onChange={setCanPause} /></div>
        <button disabled={create.isPending} className="ui-button ui-button-primary">{create.isPending ? 'Создаём…' : 'Создать субадминистратора'}</button>
        </fieldset>
      </form>
      {notice && <p role="alert" className="text-sm text-red-700">{notice}</p>}
    </section></Drawer>}
      {!creating && notice && <p role="status" className="text-sm text-neutral-600">{notice}</p>}
    <section className="ui-card ui-panel space-y-4">
      {accounts.isLoading && <p>Загрузка…</p>}
      {accounts.isError && <p role="alert">Не удалось загрузить учётные записи. <button onClick={() => accounts.refetch()} className="underline">Повторить</button></p>}
      {accounts.data?.length === 0 && <p className="text-sm text-neutral-500">Субадминистраторы ещё не созданы.</p>}
      <div className="divide-y divide-neutral-100">{accounts.data?.map(account => <article key={account.id} className="flex flex-wrap items-center justify-between gap-3 py-4 first:pt-0 last:pb-0"><div className="min-w-0"><h3 className="break-words text-sm font-semibold">{account.username}</h3><p className="mt-1 text-xs text-neutral-500">{account.view_all_clients ? 'Все клиенты' : 'Только свои клиенты'} · 2FA {account.totp_enabled ? 'включена' : 'выключена'}</p></div><div className="flex items-center gap-3"><span className={`text-xs ${account.is_active ? 'text-emerald-700' : 'text-neutral-500'}`}>{account.is_active ? 'Активен' : 'Отключён'}</span><button className="ui-button ui-button-secondary" aria-label={`Настроить ${account.username}`} onClick={() => setEditingId(account.id)}><Settings2 size={16} />Настроить</button></div></article>)}</div>
    </section>
    {editing && <AccountEditor key={editing.id} account={editing} refresh={refresh} onClose={() => setEditingId(null)} />}
  </>;
}
