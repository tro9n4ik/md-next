import { useConfirm } from '../contexts/ConfirmationContext';
import Switch from '../components/ui/Switch';
import React from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate, useParams } from 'react-router-dom';
import { Users, ArrowLeft, Check, Copy, Download, RefreshCw, ChevronDown, Link, SlidersHorizontal, Globe, ShieldCheck, ExternalLink, QrCode } from 'lucide-react';
import { QRCodeSVG } from 'qrcode.react';
import PageLayout from '../components/ui/PageLayout';
import { useCurrentUser } from '../contexts/AuthContext';
import { apiFetch } from '../utils/api';
import SubscriptionFields from '../components/SubscriptionFields';
import ClientQuickActions from '../components/ClientQuickActions';
import ClientExitRoute from '../components/ClientExitRoute';
import ClientSubscriptionRoutes from '../components/ClientSubscriptionRoutes';
import { clientStatus, formatSubscriptionDate, subscriptionPayload } from '../utils/subscriptions';
import type { ClientLimits, SubscriptionValues } from '../utils/subscriptions';
import { formatBytes } from '../utils/ru';

type AccessProfile = { id: number; kind: string; label: string; is_enabled: boolean; data: string; key_available: boolean };
type AccessOption = { kind: string; label: string; is_enabled: boolean; available: boolean };
type AccessData = { client: ClientLimits & { id: number; name: string; is_active: boolean }; profiles: AccessProfile[]; access: AccessOption[]; adblock_enabled: boolean; subscription_url: string };

const ProfileAccessEditor: React.FC<{ data: AccessData; refresh: () => Promise<void> }> = ({ data, refresh }) => {
  const [selected, setSelected] = React.useState<Record<string, boolean>>(Object.fromEntries(data.access.map(item => [item.kind, item.is_enabled])));
  const [adblock, setAdblock] = React.useState(data.adblock_enabled ?? false);
  const [notice, setNotice] = React.useState('');
  const mutation = useMutation({
    mutationFn: async () => {
      const profiles = Object.fromEntries(data.access.filter(item => item.available).map(item => [item.kind, selected[item.kind]]));
      const response = await apiFetch(`/api/v1/clients/${data.client.id}/access`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ profiles, adblock_enabled: adblock }),
      });
      if (!response.ok) throw new Error(await readError(response, 'Не удалось сохранить доступ'));
    },
    onSuccess: async () => { setNotice('Доступ сохранён. Клиенту нужно обновить подписку.'); await refresh(); },
    onError: (error: Error) => setNotice(error.message),
  });
  return <section className="ui-card ui-panel">
    <h2 className="font-semibold text-neutral-800">Доступные протоколы</h2>
    <p className="mt-1 text-sm text-neutral-500">Отключённые протоколы недоступны клиенту на сервере. AmneziaWG и Telegram выдаются отдельно от подписки.</p>
    <div className="mt-5 grid gap-3 sm:grid-cols-2">
      {data.access.map(item => <div key={item.kind} className="flex items-center justify-between gap-3 rounded-xl border border-neutral-200 p-4">
        <div><p className="text-sm font-semibold text-neutral-800">{item.label}</p><p className="mt-1 text-xs text-neutral-500">{item.available ? selected[item.kind] ? 'Доступ разрешён' : 'Доступ закрыт' : 'Выключен в настройках панели'}</p></div>
        <Switch label={`Разрешить ${item.label}`} checked={selected[item.kind] ?? false} disabled={!item.available || mutation.isPending} onChange={value => { setSelected({ ...selected, [item.kind]: value }); setNotice(''); }} />
      </div>)}
    </div>
    <div className="mt-5 flex items-center justify-between gap-4 rounded-xl border border-neutral-200 p-4">
      <div><p className="text-sm font-semibold text-neutral-800">Блокировка рекламы</p>
        <p className="mt-1 text-xs text-neutral-500">Блокирует рекламные домены только для этого клиента. Для Happ обновите подписку. Не удаляет рекламу с того же домена, что и контент.</p></div>
      <Switch label="Блокировка рекламы для клиента" checked={adblock} disabled={mutation.isPending} onChange={value => { setAdblock(value); setNotice(''); }} />
    </div>
    <div className="ui-actionbar"><p role="status" className="text-xs text-neutral-500">{notice || 'После изменения доступа клиенту нужно обновить подписку.'}</p>
      <button className="ui-button ui-button-primary shrink-0" disabled={mutation.isPending} onClick={() => { setNotice(''); mutation.mutate(); }}>{mutation.isPending ? 'Сохранение…' : 'Сохранить доступ'}</button>
    </div>
  </section>;
};

const SubscriptionEditor: React.FC<{ client: AccessData['client']; refresh: () => Promise<void> }> = ({ client, refresh }) => {
  const [values, setValues] = React.useState<SubscriptionValues>({ period: 'keep', date: '', quotaGB: client.monthly_traffic_limit ? String(client.monthly_traffic_limit / 1024 ** 3) : '', cdnQuotaGB: client.cdn_monthly_traffic_limit ? String(client.cdn_monthly_traffic_limit / 1024 ** 3) : '' });
  const [notice, setNotice] = React.useState('');
  const mutation = useMutation({
    mutationFn: async () => {
      const response = await apiFetch(`/api/v1/clients/${client.id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(subscriptionPayload(values)) });
      if (!response.ok) throw new Error(await readError(response, 'Не удалось обновить подписку'));
    },
    onSuccess: async () => { setNotice('Условия подписки сохранены'); setValues({ ...values, period: 'keep' }); await refresh(); },
    onError: (error: Error) => setNotice(error.message),
  });
  return <section className="ui-card ui-panel">
    <h2 className="ui-card-title">Срок и трафик</h2><p className="access-card-description">Выберите срок подписки и месячные лимиты для клиента.</p>
    <div className="access-conditions">
      <form onSubmit={event => { event.preventDefault(); setNotice(''); mutation.mutate(); }} className="space-y-3">
        <SubscriptionFields value={values} onChange={setValues} editing columns />
        <div className="ui-actionbar"><span className="text-xs text-neutral-500">Общий лимит действует на всю подписку, отдельный — только на обход БС</span><button disabled={mutation.isPending} className="ui-button ui-button-primary">Сохранить условия</button></div>
        {notice && <p role="status" className="text-sm text-neutral-600">{notice}</p>}
      </form>
    </div>
  </section>;
};

async function readError(response: Response, fallback: string) {
  const body = await response.json().catch(() => ({}));
  return typeof body.detail === 'string' ? body.detail : fallback;
}

const CopyButton: React.FC<{ value: string; primary?: boolean }> = ({ value, primary = false }) => {
  const [copied, setCopied] = React.useState(false);
  return <button onClick={async () => { await navigator.clipboard.writeText(value); setCopied(true); window.setTimeout(() => setCopied(false), 1600); }} className={primary ? 'ui-button ui-button-primary' : 'ui-button ui-button-secondary'}>{copied ? <Check size={14} /> : <Copy size={14} />}{copied ? 'Скопировано' : primary ? 'Скопировать подписку' : 'Копировать'}</button>;
};

type AccessMenuItem = { id: string; title: string; description: string; icon: typeof Globe; content: React.ReactNode };
const AccessWorkspace: React.FC<{ label: string; items: AccessMenuItem[] }> = ({ label, items }) => {
  const [selected, setSelected] = React.useState(items[0].id);
  return <div className="access-workspace">
    <nav className="access-side-menu" aria-label={label}>{items.map(item => <button key={item.id} aria-pressed={selected === item.id} aria-controls={`access-setting-${item.id}`} id={`access-menu-${item.id}`} onClick={() => setSelected(item.id)}><item.icon size={19} aria-hidden="true" /><span><strong>{item.title}</strong><small>{item.description}</small></span><ChevronDown size={15} className="access-menu-arrow" aria-hidden="true" /></button>)}</nav>
    <div className="access-workspace-body">{items.map(item => <section key={item.id} id={`access-setting-${item.id}`} aria-labelledby={`access-menu-${item.id}`} hidden={selected !== item.id} className="access-setting-panel">{item.content}</section>)}</div>
  </div>;
};

const ClientAccessPage: React.FC = () => {
  const confirm = useConfirm();
  const user = useCurrentUser();
  const isAdmin = user.role === 'admin';
  const canIssue = isAdmin || user.can_issue_subscriptions;
  const { id = '' } = useParams();
  const clientId = Number(id);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [error, setError] = React.useState('');
  const [activeView, setActiveView] = React.useState<'connect' | 'countries' | 'manage'>('connect');
  const accessQuery = useQuery<AccessData>({
    queryKey: ['clientProfiles', clientId],
    queryFn: async () => {
      const response = await apiFetch(`/api/v1/clients/${clientId}/profiles`);
      if (!response.ok) throw new Error(await readError(response, 'Не удалось загрузить профили'));
      return response.json();
    }, enabled: canIssue && Number.isFinite(clientId) && clientId > 0,
  });
  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['clientProfiles', clientId] }),
      queryClient.invalidateQueries({ queryKey: ['clients'] }),
    ]);
  };
  const profileMutation = useMutation({
    mutationFn: async ({ profileId, action, is_enabled }: { profileId: number; action: 'toggle' | 'regenerate'; is_enabled?: boolean }) => {
      const response = await apiFetch(`/api/v1/clients/${clientId}/profiles/${profileId}${action === 'regenerate' ? '/regenerate' : ''}`, {
        method: action === 'toggle' ? 'PUT' : 'POST',
        headers: { 'Content-Type': 'application/json' },
        ...(action === 'toggle' ? { body: JSON.stringify({ is_enabled }) } : {}),
      });
      if (!response.ok) throw new Error(await readError(response, 'Не удалось изменить профиль'));
    },
    onError: (reason: Error) => setError(reason.message),
    onSettled: refresh,
  });
  const subscriptionMutation = useMutation({
    mutationFn: async () => {
      const response = await apiFetch(`/api/v1/clients/${clientId}/sub/regenerate`, { method: 'POST' });
      if (!response.ok) throw new Error(await readError(response, 'Не удалось перевыпустить ссылку'));
    },
    onError: (reason: Error) => setError(reason.message),
    onSettled: refresh,
  });
  const download = (profile: AccessProfile) => {
    const blob = new Blob([profile.data], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a'); anchor.href = url; anchor.download = `${accessQuery.data?.client.name || 'client'}.conf`; anchor.click(); URL.revokeObjectURL(url);
  };

  if (!canIssue) return <PageLayout title="Доступ клиента" description="Выдача подписок запрещена администратором" icon={Users}>
    <button onClick={() => navigate('/clients')} className="ui-button ui-button-secondary"><ArrowLeft size={16} />К клиентам</button>
  </PageLayout>;
  if (accessQuery.isLoading || accessQuery.isError || !accessQuery.data) return <PageLayout title="Доступ клиента" description="Условия подписки, профили подключения и конфигурации" icon={Users}>
    <div className="ui-empty-state" role="status">{accessQuery.isLoading ? 'Загрузка профилей…' : accessQuery.error?.message || 'Клиент не найден'}</div>
  </PageLayout>;
  const data = accessQuery.data;

  return <PageLayout title={`Доступ: ${data.client.name}`} description="Подключение, страны и управление подпиской" icon={Users}
    actions={<button onClick={() => navigate('/clients')} className="ui-button ui-button-secondary"><ArrowLeft size={16} />К клиентам</button>}>
    {error && <div className="rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</div>}
    <section className="ui-card access-overview" aria-label="Состояние подписки">
      <div className="access-overview-heading"><span className={`access-state${data.client.access_allowed ? ' is-active' : ''}`}><span aria-hidden="true" />{data.client.access_allowed ? 'Доступ разрешён' : clientStatus(data.client.blocked_reason || 'disabled')}</span><span className="access-client-id">Клиент № {data.client.id}</span></div>
      <div className="access-facts">
        <div><span>Срок подписки</span><strong>{formatSubscriptionDate(data.client.expires_at)}</strong></div>
        <div><span>Осталось трафика на месяц</span><strong>{data.client.monthly_traffic_limit ? formatBytes(Math.max(0, data.client.monthly_traffic_limit - data.client.monthly_traffic_used)) : 'Без ограничений'}</strong><small>Использовано {formatBytes(data.client.monthly_traffic_used)}</small></div>
        <div><span>Обход БС</span><strong>{data.client.cdn_quota_exhausted ? 'Лимит исчерпан' : data.client.cdn_monthly_traffic_limit ? `${formatBytes(Math.max(0, data.client.cdn_monthly_traffic_limit - data.client.cdn_monthly_traffic_used))} осталось` : 'Без ограничений'}</strong></div>
      </div>
      {isAdmin && <ClientQuickActions client={data.client} refresh={refresh} variant="primary" />}
    </section>
    <nav className="access-navigation" aria-label="Разделы доступа">
      {([{id: 'connect', label: 'Подключение', icon: Link}, ...(isAdmin ? [{id: 'countries', label: 'Страны', icon: Globe}, {id: 'manage', label: 'Управление', icon: SlidersHorizontal}] : [])] as const).map(item => <button key={item.id} id={`access-nav-${item.id}`} aria-controls={`access-view-${item.id}`} aria-pressed={activeView === item.id} onClick={() => setActiveView(item.id as typeof activeView)}><item.icon size={17} aria-hidden="true" />{item.label}</button>)}
    </nav>
    <div id="access-view-connect" aria-labelledby="access-nav-connect" hidden={activeView !== 'connect'} className="access-view">
      <section className="ui-card access-delivery">
        <div className="access-delivery-main"><span className="access-eyebrow">Подключение клиента</span><h2>Подписка для VPN-приложения</h2><p>Скопируйте подписку и передайте клиенту. Он добавит её в Happ или другое VPN-приложение.</p>
          <div className="access-delivery-actions"><CopyButton value={data.subscription_url} primary /><a href={`${data.subscription_url}?format=page`} target="_blank" rel="noopener noreferrer" className="ui-button ui-button-secondary"><ExternalLink size={16} />Личная страница</a></div>
          <details className="access-link-details"><summary>Показать ссылку подписки</summary><input aria-label="Ссылка подписки" readOnly value={data.subscription_url} className="access-subscription-url" /></details>
        </div>
        <div className="access-delivery-qr">{data.subscription_url && <QRCodeSVG value={data.subscription_url} size={144} level="L" includeMargin />}<span><QrCode size={14} aria-hidden="true" />Сканировать в приложении</span></div>
      </section>
      <p className="access-help"><ShieldCheck size={16} aria-hidden="true" />Ссылка открывает доступ к подписке. Передавайте её только владельцу.</p>
    <details className="ui-card access-section access-connections" aria-label="Отдельные подключения">
    <summary><span className="access-section-icon"><Link size={20} aria-hidden="true" /></span><span><strong>Отдельные подключения</strong><small>QR-коды, ссылки и файлы включённых протоколов</small></span><ChevronDown className="access-chevron" size={18} aria-hidden="true" /></summary>
    {!data.profiles.some(profile => profile.is_enabled) && <p className="px-5 pb-5 text-sm text-neutral-500">Нет включённых протоколов.</p>}
    {data.profiles.filter(profile => profile.is_enabled).map((profile) => {
      const size = new TextEncoder().encode(profile.data).length;
      const canQr = Boolean(profile.data) && size <= 2900;
      const isAwg = profile.kind === 'awg';
      const isTelegram = profile.kind === 'mtproto';
      return <details key={profile.kind} className="access-profile">
        <summary><span className="access-profile-icon"><Link size={18} aria-hidden="true" /></span><span className="access-profile-title">{profile.label}</span><span className={`access-profile-status${profile.is_enabled ? ' is-enabled' : ''}`}>{profile.is_enabled ? 'Включён' : 'Отключён'}</span><ChevronDown className="access-chevron" size={18} aria-hidden="true" /></summary>
        <div className="access-profile-content">
        <div className="flex flex-col items-center justify-center rounded-xl bg-neutral-50 p-4">
          {canQr ? <QRCodeSVG value={profile.data} size={160} level="L" includeMargin /> : <div className="flex h-40 items-center text-center text-xs text-neutral-500">{isAwg && !profile.key_available ? 'Ключ недоступен, нажмите «Перевыпустить»' : profile.data ? 'Конфигурация слишком длинная для QR, скачайте файл' : 'Нет данных профиля'}</div>}
          {isAwg && profile.data && <button onClick={() => download(profile)} className="mt-3 inline-flex items-center gap-1 text-xs font-medium text-emerald-700"><Download size={14} /> Скачать .conf</button>}
        </div>
        <div className="min-w-0 space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-3"><div><h2 className="font-semibold text-neutral-800">{profile.label}</h2><p className="text-xs text-neutral-500">{profile.is_enabled ? 'Профиль включён' : 'Профиль отключён'}</p></div>

          </div>
          {!profile.key_available && isAwg ? <p className="rounded-lg bg-amber-50 p-3 text-sm text-amber-800">Ключ недоступен, нажмите «Перевыпустить»</p> : <textarea readOnly value={profile.data} className="h-28 w-full resize-y rounded-lg border bg-neutral-50 p-3 font-mono text-xs text-neutral-700" />}
          <div className="flex flex-wrap gap-2">{isTelegram && profile.data && <a className="ui-button ui-button-secondary" href={profile.data}>Подключить Telegram</a>}{profile.data && <CopyButton value={profile.data} />}{isAdmin && profile.kind !== 'cdn' && <button onClick={async () => { if (await confirm({ title: 'Перевыпустить профиль?', description: 'Старые данные подключения перестанут работать. Клиенту потребуется новый профиль.', confirmText: 'Перевыпустить', tone: 'warning' })) { setError(''); profileMutation.mutate({ profileId: profile.id, action: 'regenerate' }); } }} className="inline-flex items-center gap-1 rounded-lg border border-amber-200 px-3 py-2 text-xs font-medium text-amber-800 hover:bg-amber-50"><RefreshCw size={14} /> Перевыпустить</button>}</div>
        </div>
        </div>
      </details>;
    })}
    </details>
    </div>
    {isAdmin && <>
      <div id="access-view-countries" aria-labelledby="access-nav-countries" hidden={activeView !== 'countries'} className="access-view access-country-view">
        <AccessWorkspace label="Настройки стран" items={[
          {id: 'main-route', title: 'Основной маршрут', description: 'Выход по умолчанию', icon: Globe, content: <ClientExitRoute key={clientId} clientId={clientId} clientName={data.client.name} />},
          {id: 'extra-countries', title: 'Выбор стран', description: 'Страны в приложении', icon: Link, content: <ClientSubscriptionRoutes key={`countries-${clientId}`} clientId={clientId} />},
        ]} />
      </div>
      <div id="access-view-manage" aria-labelledby="access-nav-manage" hidden={activeView !== 'manage'} className="access-view">
        <AccessWorkspace label="Управление подпиской" items={[
          {id: 'terms', title: 'Срок и трафик', description: 'Продление и лимиты', icon: SlidersHorizontal, content: <SubscriptionEditor key={`${data.client.id}-${data.client.monthly_traffic_limit}-${data.client.cdn_monthly_traffic_limit}-${data.client.expires_at}`} client={data.client} refresh={refresh} />},
          {id: 'protocols', title: 'Протоколы и защита', description: 'Подключения и реклама', icon: ShieldCheck, content: <ProfileAccessEditor key={`${data.client.id}-${JSON.stringify(data.access)}-${data.adblock_enabled}`} data={data} refresh={refresh} />},
          {id: 'extra-actions', title: 'Дополнительно', description: 'Telegram и расход', icon: Link, content: <ClientQuickActions client={data.client} refresh={refresh} variant="secondary" />},
          {id: 'security', title: 'Ссылка подписки', description: 'Замена при утечке', icon: RefreshCw, content: <section className="ui-card ui-panel"><h2 className="ui-card-title">Замена ссылки подписки</h2>          <div className="pt-4"><p className="mb-4 text-sm text-neutral-500">После перевыпуска старая ссылка перестанет работать. Передайте клиенту новую.</p>{isAdmin && <button onClick={async () => { if (await confirm({ title: 'Перевыпустить ссылку?', description: 'Старая ссылка подписки перестанет работать. Передайте клиенту новую ссылку.', confirmText: 'Перевыпустить ссылку', tone: 'warning' })) { setError(''); subscriptionMutation.mutate(); } }} className="inline-flex items-center gap-1 rounded-lg border border-amber-200 px-3 py-2 text-xs font-medium text-amber-800 hover:bg-amber-50"><RefreshCw size={14} /> Перевыпустить ссылку</button>}</div></section>},
        ]} />
      </div>
    </>}

  </PageLayout>;
};

export default ClientAccessPage;
