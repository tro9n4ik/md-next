import React from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Activity, Check, Circle, Network, Radio, Save, Shield, Sparkles } from 'lucide-react';
import { apiFetch } from '../utils/api';

type ProfileKind = 'vless_reality_tcp' | 'vless_xhttp_reality' | 'vless_xhttp_tls' | 'hysteria2' | 'awg';
type XhttpMode = 'auto' | 'packet-up' | 'stream-up' | 'stream-one';
type ProtocolSettings = {
  profiles: { kind: ProfileKind; enabled: boolean }[];
  ports: { vless_xhttp_reality: number; hysteria2: number };
  paths: { vless_xhttp_reality: string; vless_xhttp_tls: string };
  reality: {
    server_address: string;
    target: string;
    server_name: string;
    fingerprint: string;
    short_id: string;
    public_key: string;
    private_key_set: boolean;
    private_key?: string;
    flow: string;
  };
  modes: { vless_xhttp_reality: XhttpMode; vless_xhttp_tls: XhttpMode };
};

type SaveVariables = ProtocolSettings & { confirm_link_identity_change: boolean };

const labels: Record<ProfileKind, string> = {
  vless_reality_tcp: 'VLESS Reality TCP',
  vless_xhttp_reality: 'VLESS XHTTP Reality',
  vless_xhttp_tls: 'VLESS XHTTP TLS',
  hysteria2: 'Hysteria 2',
  awg: 'AmneziaWG',
};
const descriptions: Record<ProfileKind, string> = {
  vless_reality_tcp: 'TCP · Reality · Vision',
  vless_xhttp_reality: 'XHTTP · Reality',
  vless_xhttp_tls: 'XHTTP · TLS через Nginx',
  hysteria2: 'QUIC · UDP · TLS',
  awg: 'UDP-туннель AmneziaWG 3.1',
};
const modes: { value: XhttpMode; label: string }[] = [
  { value: 'auto', label: 'Автоматически' },
  { value: 'packet-up', label: 'Передача пакетами' },
  { value: 'stream-up', label: 'Потоковая передача' },
  { value: 'stream-one', label: 'Один поток' },
];

const card = 'ui-card ui-panel';
const input = 'mt-1 w-full rounded-xl border border-neutral-200 bg-white px-3 py-2.5 text-sm text-neutral-800 outline-none transition focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500/10';
const label = 'block text-xs font-semibold text-neutral-600';

const ProtocolSettingsTab: React.FC = () => {
  const client = useQueryClient();
  const [value, setValue] = React.useState<ProtocolSettings | null>(null);
  const [message, setMessage] = React.useState<{ kind: 'success' | 'error'; text: string } | null>(null);
  const query = useQuery<ProtocolSettings>({
    queryKey: ['protocolSettings'],
    queryFn: async () => {
      const response = await apiFetch('/api/v1/settings/protocols');
      if (!response.ok) throw new Error('Не удалось загрузить параметры протоколов');
      const settings = await response.json() as ProtocolSettings;
      setValue(settings);
      return settings;
    },
  });
  const [pendingIdentityChange, setPendingIdentityChange] = React.useState<string | null>(null);
  const save = useMutation<ProtocolSettings, Error, SaveVariables>({
    mutationFn: async ({ confirm_link_identity_change, ...settings }) => {
      const response = await apiFetch('/api/v1/settings/protocols', {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          enabled: Object.fromEntries(settings.profiles.map(profile => [profile.kind, profile.enabled])),
          ports: settings.ports,
          paths: settings.paths,
          reality: Object.fromEntries(Object.entries(settings.reality).filter(([key]) => key !== 'private_key_set')),
          modes: settings.modes,
          confirm_link_identity_change,
        }),
      });
      if (!response.ok) {
        const result = await response.json().catch(() => ({}));
        if (response.status === 409) setPendingIdentityChange(typeof result.detail === 'string' ? result.detail : 'Изменение затронет все клиентские ссылки.');
        throw new Error(result.detail || 'Не удалось применить параметры протоколов');
      }
      return response.json() as Promise<ProtocolSettings>;
    },
    onSuccess: (settings, variables) => {
      setValue(settings);
      setPendingIdentityChange(null);
      setMessage({
        kind: 'success',
        text: variables.confirm_link_identity_change
          ? 'Параметры сохранены. Ссылки клиентов перестроены, прежние ссылки больше не действуют.'
          : 'Параметры сохранены, конфигурация Xray применена',
      });
      client.invalidateQueries({ queryKey: ['clients'] });
      client.invalidateQueries({ queryKey: ['clusterRoute'] });
    },
    onError: (error: Error) => setMessage({ kind: 'error', text: error.message }),
  });

  const update = <K extends keyof ProtocolSettings>(section: K, key: keyof ProtocolSettings[K] & string, next: string | number | boolean) => {
    setValue(current => current ? {
      ...current,
      [section]: { ...current[section], [key]: next },
    } : current);
  };
  const toggle = (kind: ProfileKind, enabled: boolean) => setValue(current => current ? ({
    ...current,
    profiles: current.profiles.map(profile => profile.kind === kind ? { ...profile, enabled } : profile),
  }) : current);

  if (!value) return <div className={`${card} text-sm text-neutral-500`}>{query.isError ? 'Не удалось загрузить параметры протоколов. Обновите страницу.' : 'Загрузка параметров…'}</div>;
  const isEnabled = (kind: ProfileKind) => value.profiles.find(profile => profile.kind === kind)?.enabled ?? false;

  return <div className="space-y-6">
    <section className={card}>
      <div className="mb-5 flex items-start gap-3">
        <div className="rounded-xl bg-emerald-50 p-2.5 text-emerald-700"><Network className="h-5 w-5" /></div>
        <div><h2 className="text-lg font-bold text-neutral-900">Рабочие каналы</h2><p className="mt-1 text-sm text-neutral-500">Состояние, параметры подключений и каналы, которые выдаются клиентам.</p></div>
      </div>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
        {value.profiles.map(profile => <label key={profile.kind} className={`flex min-h-28 cursor-pointer flex-col justify-between rounded-xl border p-4 transition ${profile.enabled ? 'border-emerald-200 bg-emerald-50/60' : 'border-neutral-200 bg-neutral-50/70'}`}>
          <span className="flex items-start justify-between gap-2"><span><span className="block text-sm font-bold text-neutral-800">{labels[profile.kind]}</span><span className="mt-1 block text-[11px] text-neutral-500">{descriptions[profile.kind]}</span></span><input aria-label={`Включить ${labels[profile.kind]}`} type="checkbox" checked={profile.enabled} onChange={event => toggle(profile.kind, event.target.checked)} className="mt-0.5 h-4 w-4 accent-emerald-600" /></span>
          <span className={`mt-4 flex items-center gap-1.5 text-[11px] font-semibold ${profile.enabled ? 'text-emerald-700' : 'text-neutral-400'}`}>{profile.enabled ? <Check className="h-3.5 w-3.5" /> : <Circle className="h-3 w-3" />}{profile.enabled ? 'Включён' : 'Отключён'}</span>
        </label>)}
      </div>
      <p className="mt-4 rounded-xl bg-blue-50 px-4 py-3 text-xs leading-5 text-blue-800">Изменения применяются к серверной конфигурации. При включении канала профили создаются для клиентов, а ссылки и подписки строятся с текущими параметрами.</p>
      <p className="mt-2 rounded-xl bg-emerald-50 px-4 py-3 text-xs leading-5 text-emerald-800">Замена ноды не затрагивает ссылки клиентов: адрес в ссылке, SNI, короткий идентификатор и ключи задают панель, а нода — расходный выход. При смерти ноды трафик клиентов уходит на резервный маршрут, и подписки продолжают работать.</p>
    </section>

    <section className={card}>
      <div className="mb-5 flex items-center gap-3"><Shield className="h-5 w-5 text-indigo-600" /><div><h2 className="font-bold text-neutral-900">Общие параметры Reality</h2><p className="mt-1 text-xs text-neutral-500">Используются в VLESS Reality TCP и VLESS XHTTP Reality.</p></div></div>
      <div className="grid gap-4 md:grid-cols-2">
        <label className={label}>Публичный адрес для ссылок<input className={input} value={value.reality.server_address} onChange={event => update('reality', 'server_address', event.target.value)} placeholder="vpn.example.com" /></label>
        <label className={label}>Адрес маскировки Reality<input className={input} value={value.reality.target} onChange={event => update('reality', 'target', event.target.value)} placeholder="127.0.0.1:8080" /></label>
        <label className={label}>Reality SNI<input className={input} value={value.reality.server_name} onChange={event => update('reality', 'server_name', event.target.value)} placeholder="example.com" /></label>
        <label className={label}>Отпечаток браузера<select className={input} value={value.reality.fingerprint} onChange={event => update('reality', 'fingerprint', event.target.value)}>{['chrome', 'firefox', 'safari', 'ios', 'android', 'edge', 'randomized'].map(item => <option key={item} value={item}>{item === 'randomized' ? 'Случайный' : item}</option>)}</select></label>
        <label className={label}>Открытый ключ Reality<input className={`${input} font-mono`} value={value.reality.public_key} onChange={event => update('reality', 'public_key', event.target.value)} autoComplete="off" /></label>
        <label className={label}>Короткий идентификатор<input className={`${input} font-mono`} value={value.reality.short_id} onChange={event => update('reality', 'short_id', event.target.value)} placeholder="Пустое значение — без короткого идентификатора" maxLength={16} /></label>
        <label className={label}>Закрытый ключ Reality<input className={`${input} font-mono`} type="password" autoComplete="new-password" value={value.reality.private_key || ''} onChange={event => update('reality', 'private_key', event.target.value)} placeholder={value.reality.private_key_set ? 'Ключ задан, введите новый только для замены' : 'Вставьте закрытый ключ'} /><span className="mt-1 block font-normal text-neutral-400">{value.reality.private_key_set ? 'Ключ задан; API его не возвращает. Пустое поле оставит текущий ключ.' : 'Ключ будет сохранён и скрыт после применения.'}</span></label>
        <label className={label}>Режим XTLS (только Reality TCP)<select className={input} value={value.reality.flow} onChange={event => update('reality', 'flow', event.target.value)}><option value="xtls-rprx-vision">xtls-rprx-vision</option><option value="">Без Vision</option></select></label>
      </div>
    </section>

    <div className="grid gap-6 xl:grid-cols-2">
      <section className={card}>
        <div className="mb-5 flex items-center gap-3"><Activity className="h-5 w-5 text-sky-600" /><div><h2 className="font-bold text-neutral-900">Каналы XHTTP</h2><p className="mt-1 text-xs text-neutral-500">Публичные порты, пути и режим передачи.</p></div></div>
        <div className="space-y-5">
          <div className="grid gap-3 sm:grid-cols-2">
            <label className={label}>VLESS XHTTP Reality · TCP-порт<input className={input} type="number" min={1} max={65535} value={value.ports.vless_xhttp_reality} onChange={event => update('ports', 'vless_xhttp_reality', Number(event.target.value))} /></label>
            <label className={label}>Публичный путь Reality<input className={input} value={value.paths.vless_xhttp_reality} onChange={event => update('paths', 'vless_xhttp_reality', event.target.value)} /></label>
          </div>
          <label className={label}>Режим XHTTP Reality<select className={input} value={value.modes.vless_xhttp_reality} onChange={event => update('modes', 'vless_xhttp_reality', event.target.value)}>{modes.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label>
          <div className="border-t border-neutral-100 pt-5">
            <div className="mb-3 flex items-center justify-between"><div><h3 className="text-sm font-semibold text-neutral-800">VLESS XHTTP TLS</h3><p className="mt-1 text-[11px] text-neutral-500">Внешний порт 443 · TLS завершается в Nginx</p></div><span className="rounded-full bg-neutral-100 px-2.5 py-1 text-[10px] font-semibold text-neutral-600">TCP 443</span></div>
            <div className="grid gap-3 sm:grid-cols-2">
              <label className={label}>Секретный путь<input className={input} value={value.paths.vless_xhttp_tls} onChange={event => update('paths', 'vless_xhttp_tls', event.target.value)} /></label>
              <label className={label}>Режим XHTTP TLS<select className={input} value={value.modes.vless_xhttp_tls} onChange={event => update('modes', 'vless_xhttp_tls', event.target.value)}>{modes.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label>
            </div>
          </div>
        </div>
      </section>

      <section className={card}>
        <div className="mb-5 flex items-center gap-3"><Radio className="h-5 w-5 text-violet-600" /><div><h2 className="font-bold text-neutral-900">Hysteria 2 и AmneziaWG</h2><p className="mt-1 text-xs text-neutral-500">Параметры активных UDP-каналов и TLS.</p></div></div>
        <div className="space-y-5">
          <div className="grid gap-3 sm:grid-cols-2">
            <label className={label}>Hysteria 2 · UDP-порт<input className={input} type="number" min={1} max={65535} value={value.ports.hysteria2} onChange={event => update('ports', 'hysteria2', Number(event.target.value))} /></label>
            <div className="rounded-xl border border-neutral-200 bg-neutral-50 p-3"><div className="text-xs font-semibold text-neutral-600">TLS-сертификат</div><div className="mt-1 text-sm font-medium text-neutral-800">SNI: {value.reality.server_address}</div><div className="mt-1 text-[11px] text-neutral-500">Путь к сертификату задаётся установщиком сервера.</div></div>
          </div>
          {isEnabled('hysteria2') && <div className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs leading-5 text-amber-800">Откройте UDP-порт {value.ports.hysteria2} в системном файрволе и панели хостинга.</div>}
          <div className="border-t border-neutral-100 pt-4">
            <div className="flex items-center justify-between gap-3"><div><h3 className="text-sm font-semibold text-neutral-800">AmneziaWG 3.1</h3><p className="mt-1 text-[11px] text-neutral-500">Сетевые параметры AWG настраиваются отдельно от Xray.</p></div><span className="rounded-full bg-emerald-50 px-2.5 py-1 text-[10px] font-semibold text-emerald-700">{isEnabled('awg') ? 'Включён' : 'Отключён'}</span></div>
            <p className="mt-3 text-xs leading-5 text-neutral-500">Выключение AWG убирает его из новых профилей. Выход через выбранную ноду, резервирование и правила WARP применяются на сервере; менять конфигурацию клиента при смене ноды не нужно.</p>
          </div>
        </div>
      </section>
    </div>

    {message && <div className={`rounded-xl border px-4 py-3 text-sm ${message.kind === 'success' ? 'border-emerald-200 bg-emerald-50 text-emerald-800' : 'border-red-200 bg-red-50 text-red-700'}`}>{message.text}</div>}
    {pendingIdentityChange && <div className="rounded-2xl border border-amber-300 bg-amber-50 p-4 shadow-sm">
      <div className="text-sm font-bold text-amber-900">Требуется подтверждение</div>
      <p className="mt-1 text-xs leading-5 text-amber-800">{pendingIdentityChange}</p>
      <div className="mt-3 flex flex-wrap gap-2">
        <button onClick={() => { const confirm = true; setPendingIdentityChange(null); setMessage(null); save.mutate({ ...value, confirm_link_identity_change: confirm }); }} className="inline-flex items-center gap-2 rounded-xl bg-amber-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-amber-700 disabled:opacity-50" disabled={save.isPending}>{save.isPending ? 'Применение…' : 'Подтверждаю смену ссылок'}</button>
        <button onClick={() => setPendingIdentityChange(null)} className="rounded-xl border border-amber-300 bg-white px-4 py-2 text-sm font-semibold text-amber-800 transition hover:bg-amber-100">Отмена</button>
      </div>
    </div>}
    <div className="ui-actionbar sticky bottom-4">
      <p className="max-w-2xl text-xs leading-5 text-neutral-500"><Sparkles className="mr-1 inline h-3.5 w-3.5 text-emerald-600" />Панель проверит конфигурацию Xray до применения. Закрытый ключ не возвращается в API и не показывается после сохранения.</p>
      <button disabled={save.isPending || !value} onClick={() => { setMessage(null); setPendingIdentityChange(null); save.mutate({ ...value, confirm_link_identity_change: false }); }} className="ui-button ui-button-primary shrink-0"><Save className="h-4 w-4" />{save.isPending ? 'Проверка и применение…' : 'Проверить и применить'}</button>
    </div>
  </div>;
};

export default ProtocolSettingsTab;
