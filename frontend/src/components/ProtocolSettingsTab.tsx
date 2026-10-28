import Switch from './ui/Switch';
import SniScanner from './SniScanner';
import React from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, ChevronRight } from 'lucide-react';
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

type SettingsTab = 'reality' | 'xhttp' | 'udp';
const tabs: { key: SettingsTab; title: string }[] = [{ key: 'reality', title: 'Reality' }, { key: 'xhttp', title: 'XHTTP' }, { key: 'udp', title: 'UDP' }];
const input = 'protocol-input';
const label = 'protocol-label';

const ProtocolSettingsTab: React.FC = () => {
  const client = useQueryClient();
  const root = React.useRef<HTMLDivElement>(null);
  const [draft, setDraft] = React.useState<ProtocolSettings | null>(null);
  const [tab, setTab] = React.useState<SettingsTab>('reality');
  const [message, setMessage] = React.useState<{ kind: 'success' | 'error'; text: string } | null>(null);
  const query = useQuery<ProtocolSettings>({
    queryKey: ['protocolSettings'],
    refetchOnWindowFocus: false,
    queryFn: async () => {
      const response = await apiFetch('/api/v1/settings/protocols');
      if (!response.ok) throw new Error('Не удалось загрузить параметры протоколов');
      return response.json() as Promise<ProtocolSettings>;
    },
  });
  const value = draft ?? query.data ?? null;
  const dirty = !!value && !!query.data && JSON.stringify(value) !== JSON.stringify(query.data);
  React.useEffect(() => {
    const scope = root.current;
    if (!scope) return;
    window.dispatchEvent(new CustomEvent('ui:unsaved-change', { detail: { scope, dirty } }));
    return () => { window.dispatchEvent(new CustomEvent('ui:unsaved-change', { detail: { scope, dirty: false } })); };
  }, [dirty]);
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
      }, 'protocol-settings-editor');
      if (!response.ok) {
        const result = await response.json().catch(() => ({}));
        if (response.status === 409) setPendingIdentityChange(typeof result.detail === 'string' ? result.detail : 'Изменение затронет все клиентские ссылки.');
        throw new Error(result.detail || 'Не удалось применить параметры протоколов');
      }
      return response.json() as Promise<ProtocolSettings>;
    },
    onSuccess: (settings, variables) => {
      client.setQueryData(['protocolSettings'], settings);
      setDraft(null);
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
    setPendingIdentityChange(null);
    setMessage(null);
    setDraft(current => {
      const base = current ?? query.data;
      return base ? { ...base, [section]: { ...base[section], [key]: next } } : null;
    });
  };
  const toggle = (kind: ProfileKind, enabled: boolean) => {
    setPendingIdentityChange(null);
    setMessage(null);
    setDraft(current => {
      const base = current ?? query.data;
      return base ? { ...base, profiles: base.profiles.map(profile => profile.kind === kind ? { ...profile, enabled } : profile) } : null;
    });
  };

  if (!value) return <div className="ui-card protocol-loading" role={query.isError ? 'alert' : 'status'}>{query.isError ? <><p>Не удалось загрузить параметры протоколов.</p><button className="ui-button ui-button-secondary" onClick={() => query.refetch()}>Повторить</button></> : 'Загрузка параметров…'}</div>;
  const isEnabled = (kind: ProfileKind) => value.profiles.find(profile => profile.kind === kind)?.enabled ?? false;

  return <div ref={root} id="protocol-settings-editor" data-unsaved-controlled className="protocol-workspace">
    <section className="ui-card protocol-channels" aria-labelledby="protocol-channels-heading">
      <header><h2 id="protocol-channels-heading">Каналы</h2><span>{value.profiles.filter(profile => profile.enabled).length} из {value.profiles.length} включены</span></header>
      <div className="protocol-channel-list">{value.profiles.map(profile => <div key={profile.kind} className="protocol-channel-row">
        <div><h3>{labels[profile.kind]}</h3><p>{descriptions[profile.kind]}</p></div>
        <Switch label={`Включить ${labels[profile.kind]}`} checked={profile.enabled} disabled={save.isPending} onChange={checked => toggle(profile.kind, checked)} />
      </div>)}</div>
      <p className="protocol-channel-note">Изменения применяются после проверки.</p>
    </section>

    <form className="ui-card protocol-editor" onSubmit={event => { event.preventDefault(); if (save.isPending || !dirty || pendingIdentityChange) return; setMessage(null); save.mutate({ ...value, confirm_link_identity_change: false }); }}>
      <div className="protocol-tabs" role="tablist" aria-label="Параметры подключения">{tabs.map((item, index) => <button key={item.key} type="button" role="tab" id={`protocol-tab-${item.key}`} aria-controls={`protocol-panel-${item.key}`} aria-selected={tab === item.key} tabIndex={tab === item.key ? 0 : -1} onClick={() => setTab(item.key)} onKeyDown={event => {
        const offset = event.key === 'ArrowRight' ? 1 : event.key === 'ArrowLeft' ? -1 : 0;
        const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : offset ? (index + offset + tabs.length) % tabs.length : null;
        if (next === null) return;
        event.preventDefault(); setTab(tabs[next].key); document.getElementById(`protocol-tab-${tabs[next].key}`)?.focus();
      }}>{item.title}</button>)}</div>

      <fieldset className="protocol-fields" disabled={save.isPending}>
        <div role="tabpanel" id={`protocol-panel-${tab}`} aria-labelledby={`protocol-tab-${tab}`} tabIndex={0}>
          {tab === 'reality' && <>
            <p className="protocol-context">Общие параметры для VLESS Reality TCP и VLESS XHTTP Reality.</p>
            <div className="protocol-field-grid">
              <label className={label}>Публичный адрес для ссылок<input className={input} value={value.reality.server_address} onChange={event => update('reality', 'server_address', event.target.value)} placeholder="vpn.example.com" /></label>
              <label className={label}>Reality SNI<input className={input} value={value.reality.server_name} onChange={event => update('reality', 'server_name', event.target.value)} placeholder="example.com" /></label>
              <label className={label}>Адрес маскировки Reality<input className={input} value={value.reality.target} onChange={event => update('reality', 'target', event.target.value)} placeholder="example.com:443" /></label>
              <label className={label}>Отпечаток браузера<input className={input} value="Firefox" readOnly /></label>
            </div>
            <SniScanner disabled={save.isPending} onSelect={(domain, target) => {
              setDraft(current => { const base = current ?? query.data; return base ? { ...base, reality: { ...base.reality, server_name: domain, target } } : null; });
              setPendingIdentityChange(null); setMessage(null);
            }} />
            <details className="protocol-advanced"><summary><span><b>Дополнительно</b><small>Ключи, идентификаторы и XTLS</small></span><ChevronRight size={18} /></summary>
              <div className="protocol-field-grid">
                <label className={label}>Открытый ключ Reality<input className={input} value={value.reality.public_key} onChange={event => update('reality', 'public_key', event.target.value)} autoComplete="off" /></label>
                <label className={label}>Короткий идентификатор<input className={input} value={value.reality.short_id} onChange={event => update('reality', 'short_id', event.target.value)} placeholder="Без короткого идентификатора" maxLength={16} /></label>
                <label className={label}>Закрытый ключ Reality<input className={input} type="password" autoComplete="new-password" value={value.reality.private_key || ''} onChange={event => update('reality', 'private_key', event.target.value)} placeholder={value.reality.private_key_set ? 'Новый ключ — только для замены' : 'Вставьте закрытый ключ'} /><span className="protocol-field-hint">{value.reality.private_key_set ? 'Ключ задан. Пустое поле оставит текущий ключ.' : 'Ключ будет скрыт после применения.'}</span></label>
                <label className={label}>Режим XTLS (только Reality TCP)<select className={input} aria-label="Режим XTLS (только Reality TCP)" value={value.reality.flow} onChange={event => update('reality', 'flow', event.target.value)}><option value="xtls-rprx-vision">xtls-rprx-vision</option><option value="">Без Vision</option></select></label>
              </div>
              <p className="protocol-field-hint">Закрытый ключ не возвращается в API и не показывается после сохранения.</p>
            </details>
          </>}

          {tab === 'xhttp' && <>
            <p className="protocol-context">Порты, пути и режимы каналов XHTTP.</p>
            <h3 className="protocol-subheading">VLESS XHTTP Reality</h3>
            <div className="protocol-field-grid">
              <label className={label}>TCP-порт<input className={input} required type="number" min={1} max={65535} value={value.ports.vless_xhttp_reality} onChange={event => update('ports', 'vless_xhttp_reality', Number(event.target.value))} /></label>
              <label className={label}>Публичный путь Reality<input className={input} value={value.paths.vless_xhttp_reality} onChange={event => update('paths', 'vless_xhttp_reality', event.target.value)} /></label>
              <label className={label}>Режим XHTTP Reality<select className={input} aria-label="Режим XHTTP Reality" value={value.modes.vless_xhttp_reality} onChange={event => update('modes', 'vless_xhttp_reality', event.target.value)}>{modes.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label>
            </div>
            <div className="protocol-field-section"><h3 className="protocol-subheading">VLESS XHTTP TLS</h3><p className="protocol-field-hint">Внешний порт 443 · TLS завершается в Nginx.</p>
              <div className="protocol-field-grid">
                <label className={label}>Секретный путь<input className={input} value={value.paths.vless_xhttp_tls} onChange={event => update('paths', 'vless_xhttp_tls', event.target.value)} /></label>
                <label className={label}>Режим XHTTP TLS<select className={input} aria-label="Режим XHTTP TLS" value={value.modes.vless_xhttp_tls} onChange={event => update('modes', 'vless_xhttp_tls', event.target.value)}>{modes.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label>
              </div>
            </div>
          </>}

          {tab === 'udp' && <>
            <p className="protocol-context">Параметры Hysteria 2 и AmneziaWG.</p>
            <h3 className="protocol-subheading">Hysteria 2</h3>
            <div className="protocol-field-grid">
              <label className={label}>UDP-порт<input className={input} required type="number" min={1} max={65535} value={value.ports.hysteria2} onChange={event => update('ports', 'hysteria2', Number(event.target.value))} /></label>
              <div className="protocol-certificate"><span>TLS-сертификат</span><b>SNI: {value.reality.server_address}</b><p>Путь к сертификату задаётся установщиком сервера.</p></div>
            </div>
            {isEnabled('hysteria2') && <p className="protocol-inline-warning">Откройте UDP-порт {value.ports.hysteria2} в системном файрволе и панели хостинга.</p>}
            <div className="protocol-field-section"><h3 className="protocol-subheading">AmneziaWG 3.1 <span>{isEnabled('awg') ? 'Включён' : 'Отключён'}</span></h3><p className="protocol-help">Сетевые параметры AWG настраиваются отдельно от Xray. Выключение AWG убирает его из новых профилей. Выход через выбранную ноду, резервирование и правила WARP применяются на сервере; менять конфигурацию клиента при смене ноды не нужно.</p></div>
          </>}
        </div>
      </fieldset>

      {message && !pendingIdentityChange && <p role={message.kind === 'error' ? 'alert' : 'status'} className={`protocol-message protocol-message-${message.kind}`}>{message.text}</p>}
      {pendingIdentityChange && <div role="alert" className="protocol-confirmation"><b>Требуется подтверждение</b><p>{pendingIdentityChange}</p><div>
        <button type="button" className="ui-button ui-button-primary" disabled={save.isPending} onClick={() => { setMessage(null); save.mutate({ ...value, confirm_link_identity_change: true }); }}>{save.isPending ? 'Применение…' : 'Подтверждаю смену ссылок'}</button>
        <button type="button" className="ui-button ui-button-secondary" disabled={save.isPending} onClick={() => { setPendingIdentityChange(null); setMessage(null); }}>Отмена</button>
      </div></div>}
      <footer className="protocol-editor-footer"><div><p className={dirty ? 'protocol-draft-state' : ''}>{dirty ? 'Есть неприменённые изменения' : 'Все изменения сохранены'}</p><small>Смена адреса, SNI или ключей потребует подтверждения.</small></div><button type="submit" className="ui-button ui-button-primary" disabled={save.isPending || !dirty || !!pendingIdentityChange}><Check size={16} />{save.isPending ? 'Проверка и применение…' : 'Проверить и применить'}</button></footer>
    </form>
  </div>;
};

export default ProtocolSettingsTab;
