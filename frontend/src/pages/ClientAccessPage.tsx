import React from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate, useParams } from 'react-router-dom';
import { Users, ArrowLeft, Check, Copy, Download, RefreshCw } from 'lucide-react';
import { QRCodeSVG } from 'qrcode.react';
import PageLayout from '../components/ui/PageLayout';
import { apiFetch } from '../utils/api';
import SubscriptionFields from '../components/SubscriptionFields';
import { clientStatus, formatSubscriptionDate, subscriptionPayload } from '../utils/subscriptions';
import type { ClientLimits, SubscriptionValues } from '../utils/subscriptions';
import { formatBytes } from '../utils/ru';

type AccessProfile = { id: number; kind: string; label: string; is_enabled: boolean; data: string; key_available: boolean };
type AccessData = { client: ClientLimits & { id: number; name: string; is_active: boolean }; profiles: AccessProfile[]; subscription_url: string };

const SubscriptionEditor: React.FC<{ client: AccessData['client']; refresh: () => Promise<void> }> = ({ client, refresh }) => {
  const [values, setValues] = React.useState<SubscriptionValues>({ period: 'keep', date: '', quotaGB: client.monthly_traffic_limit ? String(client.monthly_traffic_limit / 1024 ** 3) : '' });
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
    <h2 className="font-semibold text-neutral-800">Условия подписки</h2>
    <div className="mt-3 grid gap-4 md:grid-cols-2">
      <div className="space-y-2 text-sm text-neutral-600"><p>Состояние: <b>{clientStatus(client.blocked_reason)}</b></p><p>Окончание: <b>{formatSubscriptionDate(client.expires_at)}</b></p><p>За месяц: {formatBytes(client.monthly_traffic_used)} / {client.monthly_traffic_limit ? formatBytes(client.monthly_traffic_limit) : 'без ограничений'}</p><p>Следующее обновление: {formatSubscriptionDate(client.traffic_period_end)}</p></div>
      <form onSubmit={event => { event.preventDefault(); setNotice(''); mutation.mutate(); }} className="space-y-3">
        <SubscriptionFields value={values} onChange={setValues} editing />
        <button disabled={mutation.isPending} className="ui-button ui-button-primary">Сохранить условия</button>
        {notice && <p role="status" className="text-sm text-neutral-600">{notice}</p>}
      </form>
    </div>
  </section>;
};

async function readError(response: Response, fallback: string) {
  const body = await response.json().catch(() => ({}));
  return typeof body.detail === 'string' ? body.detail : fallback;
}

const CopyButton: React.FC<{ value: string }> = ({ value }) => {
  const [copied, setCopied] = React.useState(false);
  return <button onClick={async () => { await navigator.clipboard.writeText(value); setCopied(true); window.setTimeout(() => setCopied(false), 1600); }} className="inline-flex items-center gap-1 rounded-lg border px-3 py-2 text-xs font-medium hover:bg-neutral-50">{copied ? <Check size={14} /> : <Copy size={14} />}{copied ? 'Скопировано' : 'Копировать'}</button>;
};

const ClientAccessPage: React.FC = () => {
  const { id = '' } = useParams();
  const clientId = Number(id);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [error, setError] = React.useState('');
  const accessQuery = useQuery<AccessData>({
    queryKey: ['clientProfiles', clientId],
    queryFn: async () => {
      const response = await apiFetch(`/api/v1/clients/${clientId}/profiles`);
      if (!response.ok) throw new Error(await readError(response, 'Не удалось загрузить профили'));
      return response.json();
    }, enabled: Number.isFinite(clientId) && clientId > 0,
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

  if (accessQuery.isLoading || accessQuery.isError || !accessQuery.data) return <PageLayout title="Доступ клиента" description="Условия подписки, профили подключения и конфигурации" icon={Users}>
    <div className="ui-empty-state" role="status">{accessQuery.isLoading ? 'Загрузка профилей…' : accessQuery.error?.message || 'Клиент не найден'}</div>
  </PageLayout>;
  const data = accessQuery.data;

  return <PageLayout title={`Доступ: ${data.client.name}`} description="Условия подписки, профили подключения и конфигурации" icon={Users}
    actions={<button onClick={() => navigate('/clients')} className="ui-button ui-button-secondary"><ArrowLeft size={16} />К клиентам</button>}>
    {error && <div className="rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</div>}
    <SubscriptionEditor key={`${data.client.id}-${data.client.monthly_traffic_limit}-${data.client.expires_at}`} client={data.client} refresh={refresh} />
    {data.profiles.map((profile) => {
      const size = new TextEncoder().encode(profile.data).length;
      const canQr = Boolean(profile.data) && size <= 2900;
      const isAwg = profile.kind === 'awg';
      return <article key={profile.id} className="grid gap-5 ui-card ui-panel md:grid-cols-[190px_1fr]">
        <div className="flex flex-col items-center justify-center rounded-xl bg-neutral-50 p-4">
          {canQr ? <QRCodeSVG value={profile.data} size={160} level="L" includeMargin /> : <div className="flex h-40 items-center text-center text-xs text-neutral-500">{isAwg && !profile.key_available ? 'Ключ недоступен, нажмите «Перевыпустить»' : profile.data ? 'Конфигурация слишком длинная для QR, скачайте файл' : 'Нет данных профиля'}</div>}
          {isAwg && profile.data && <button onClick={() => download(profile)} className="mt-3 inline-flex items-center gap-1 text-xs font-medium text-emerald-700"><Download size={14} /> Скачать .conf</button>}
        </div>
        <div className="min-w-0 space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-3"><div><h2 className="font-semibold text-neutral-800">{profile.label}</h2><p className="text-xs text-neutral-500">{profile.is_enabled ? 'Профиль включён' : 'Профиль отключён'}</p></div>
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={profile.is_enabled} onChange={(event) => { setError(''); profileMutation.mutate({ profileId: profile.id, action: 'toggle', is_enabled: event.target.checked }); }} /> Включён</label>
          </div>
          {!profile.key_available && isAwg ? <p className="rounded-lg bg-amber-50 p-3 text-sm text-amber-800">Ключ недоступен, нажмите «Перевыпустить»</p> : <textarea readOnly value={profile.data} className="h-28 w-full resize-y rounded-lg border bg-neutral-50 p-3 font-mono text-xs text-neutral-700" />}
          <div className="flex flex-wrap gap-2">{profile.data && <CopyButton value={profile.data} />}<button onClick={() => { if (window.confirm('Перевыпустить профиль? Старые данные перестанут работать.')) { setError(''); profileMutation.mutate({ profileId: profile.id, action: 'regenerate' }); } }} className="inline-flex items-center gap-1 rounded-lg border border-amber-200 px-3 py-2 text-xs font-medium text-amber-800 hover:bg-amber-50"><RefreshCw size={14} /> Перевыпустить</button></div>
        </div>
      </article>;
    })}
    <section className="grid gap-5 ui-card ui-panel md:grid-cols-[190px_1fr]">
      <div className="flex items-center justify-center rounded-xl bg-neutral-50 p-4">{data.subscription_url && <QRCodeSVG value={data.subscription_url} size={160} level="L" includeMargin />}</div>
      <div className="space-y-3"><h2 className="font-semibold text-neutral-800">Ссылка подписки</h2><p className="text-xs text-neutral-500">В подписку входят включённые VLESS и Hysteria 2. AmneziaWG выдаётся отдельным .conf файлом.</p><textarea readOnly value={data.subscription_url} className="h-20 w-full resize-none rounded-lg border bg-neutral-50 p-3 font-mono text-xs" /><div className="flex flex-wrap gap-2"><CopyButton value={data.subscription_url} /><button onClick={() => { if (window.confirm('Перевыпустить ссылку? Старая ссылка перестанет работать.')) { setError(''); subscriptionMutation.mutate(); } }} className="inline-flex items-center gap-1 rounded-lg border border-amber-200 px-3 py-2 text-xs font-medium text-amber-800 hover:bg-amber-50"><RefreshCw size={14} /> Перевыпустить ссылку</button></div></div>
    </section>
  </PageLayout>;
};

export default ClientAccessPage;
