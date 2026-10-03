import React from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, Globe2, Loader2, Save, ShieldCheck } from 'lucide-react';
import PageLayout from '../components/ui/PageLayout';
import { apiFetch } from '../utils/api';

type DnsSettings = {
  remote_type: 'DoH' | 'DoU';
  remote_domain: string;
  remote_ip: string;
  domestic_type: 'DoH' | 'DoU';
  domestic_domain: string;
  domestic_ip: string;
  domain_strategy: 'AsIs' | 'IPIfNonMatch' | 'IPOnDemand';
  fake_dns: boolean;
};

const defaults: DnsSettings = {
  remote_type: 'DoH', remote_domain: 'https://cloudflare-dns.com/dns-query', remote_ip: '1.1.1.1',
  domestic_type: 'DoU', domestic_domain: '', domestic_ip: '8.8.8.8',
  domain_strategy: 'IPIfNonMatch', fake_dns: false,
};

const card = 'ui-card ui-panel';
const input = 'mt-1 w-full rounded-xl border border-neutral-200 bg-white px-3 py-2.5 text-sm text-neutral-800 outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500/10';
const label = 'block text-xs font-semibold text-neutral-600';

const DnsPage: React.FC = () => {
  const queryClient = useQueryClient();
  const [settings, setSettings] = React.useState<DnsSettings>(defaults);
  const [notice, setNotice] = React.useState<{ error: boolean; text: string } | null>(null);
  const query = useQuery({
    queryKey: ['dnsSettings'],
    queryFn: async () => {
      const response = await apiFetch('/api/v1/settings/dns');
      if (!response.ok) throw new Error('Не удалось загрузить настройки DNS');
      const result = await response.json() as DnsSettings;
      setSettings(result);
      return result;
    },
  });
  const save = useMutation({
    mutationFn: async () => {
      const response = await apiFetch('/api/v1/settings/dns', {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(settings),
      });
      if (!response.ok) {
        const result = await response.json().catch(() => ({}));
        throw new Error(result.detail || 'Не удалось сохранить настройки DNS');
      }
      return response.json() as Promise<DnsSettings>;
    },
    onSuccess: result => {
      setSettings(result);
      setNotice({ error: false, text: 'Настройки сохранены. Happ получит профиль DNS при следующем обновлении подписки.' });
      queryClient.invalidateQueries({ queryKey: ['dnsSettings'] });
    },
    onError: (error: Error) => setNotice({ error: true, text: error.message }),
  });

  const update = <K extends keyof DnsSettings>(key: K, value: DnsSettings[K]) => setSettings(current => ({ ...current, [key]: value }));
  const dnsFields = (typeKey: 'remote_type' | 'domestic_type', domainKey: 'remote_domain' | 'domestic_domain', ipKey: 'remote_ip' | 'domestic_ip') => (
    <div className="grid gap-4 md:grid-cols-3">
      <label className={label}>Протокол
        <select className={input} value={settings[typeKey]} onChange={event => update(typeKey, event.target.value as 'DoH' | 'DoU')}>
          <option value="DoH">DoH — DNS по HTTPS</option><option value="DoU">DoU — DNS по UDP</option>
        </select>
      </label>
      <label className={label}>{settings[typeKey] === 'DoH' ? 'Адрес DoH' : 'Адрес DoU'}
        <input className={input} value={settings[domainKey]} onChange={event => update(domainKey, event.target.value)} placeholder="https://cloudflare-dns.com/dns-query" disabled={settings[typeKey] === 'DoU'} />
      </label>
      <label className={label}>IP DNS-сервера
        <input className={input} value={settings[ipKey]} onChange={event => update(ipKey, event.target.value)} placeholder="1.1.1.1" />
      </label>
    </div>
  );

  if (query.isLoading) return <div className={`${card} text-sm text-neutral-500`}>Загрузка настроек DNS…</div>;
  if (query.isError && !query.data) return <div className={`${card} text-sm text-red-700`}>{(query.error as Error).message}</div>;

  return <PageLayout title="DNS" description="Удалённый и локальный DNS для профилей Happ и клиентской маршрутизации" icon={Globe2}>
    <section className={card}>
      <div className="mb-5 flex items-center gap-3"><Globe2 className="h-5 w-5 text-sky-600" /><div><h3 className="font-bold text-neutral-900">Удалённый DNS</h3><p className="mt-1 text-xs text-neutral-500">Используется Happ для запросов сайтов, которые идут через прокси.</p></div></div>
      {dnsFields('remote_type', 'remote_domain', 'remote_ip')}
    </section>
    <section className={card}>
      <div className="mb-5 flex items-center gap-3"><ShieldCheck className="h-5 w-5 text-emerald-600" /><div><h3 className="font-bold text-neutral-900">Локальный DNS</h3><p className="mt-1 text-xs text-neutral-500">Используется для запросов, которые правила Happ отправляют напрямую.</p></div></div>
      {dnsFields('domestic_type', 'domestic_domain', 'domestic_ip')}
    </section>
    <section className={card}>
      <div className="grid gap-4 md:grid-cols-2">
        <label className={label}>Стратегия доменов
          <select className={input} value={settings.domain_strategy} onChange={event => update('domain_strategy', event.target.value as DnsSettings['domain_strategy'])}>
            <option value="AsIs">AsIs — передавать домен без разрешения</option>
            <option value="IPIfNonMatch">IPIfNonMatch — разрешать, если нет совпадения с правилом</option>
            <option value="IPOnDemand">IPOnDemand — разрешать при проверке правил</option>
          </select>
        </label>
        <label className="flex cursor-pointer items-start gap-3 rounded-xl border border-neutral-200 bg-neutral-50 p-4">
          <input className="mt-0.5 h-4 w-4 accent-emerald-600" type="checkbox" checked={settings.fake_dns} onChange={event => update('fake_dns', event.target.checked)} />
          <span><span className="block text-sm font-semibold text-neutral-800">Fake DNS</span><span className="mt-1 block text-xs leading-5 text-neutral-500">Подменять адреса виртуальными, чтобы запросы обрабатывались Xray.</span></span>
        </label>
      </div>
    </section>
    {notice && <div className={`rounded-xl border px-4 py-3 text-sm ${notice.error ? 'border-red-200 bg-red-50 text-red-700' : 'border-emerald-200 bg-emerald-50 text-emerald-800'}`}>{notice.text}</div>}
    <div className="ui-actionbar sticky bottom-4">
      <p className="text-xs leading-5 text-neutral-500">Профиль DNS передаётся Happ через заголовок подписки `routing`; ссылки VLESS остаются совместимыми с другими клиентами.</p>
      <button disabled={save.isPending} onClick={() => { setNotice(null); save.mutate(); }} className="ui-button ui-button-primary shrink-0">
        {save.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : notice && !notice.error ? <Check className="h-4 w-4" /> : <Save className="h-4 w-4" />}
        {save.isPending ? 'Сохранение…' : 'Сохранить'}
      </button>
    </div>
  </PageLayout>;
};

export default DnsPage;
