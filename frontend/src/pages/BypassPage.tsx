import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Cloud, Loader2, Save, ShieldCheck, FlaskConical } from 'lucide-react';
import PageLayout from '../components/ui/PageLayout';
import Switch from '../components/ui/Switch';
import { apiFetch } from '../utils/api';

type CdnDraft = { enabled: boolean; state: string; domain: string; origin_path: string; mode: string; message: string };
const input = 'mt-2 w-full rounded-xl border border-neutral-200 bg-white px-3 py-2.5 text-sm outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500/10';

async function readResponse(response: Response): Promise<CdnDraft> {
  const result = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = result.detail;
    throw new Error(typeof detail === 'string' ? detail : 'Не удалось обработать настройки CDN. Проверьте доменное имя.');
  }
  return result;
}

export default function BypassPage() {
  const cache = useQueryClient();
  const [domain, setDomain] = useState<string | null>(null);
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [notice, setNotice] = useState<{ error: boolean; text: string } | null>(null);
  const query = useQuery<CdnDraft>({
    queryKey: ['cdnDraft'],
    queryFn: async () => readResponse(await apiFetch('/api/v1/cdn')),
  });
  const save = useMutation({
    mutationFn: async () => readResponse(await apiFetch('/api/v1/cdn', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ domain: (domain ?? query.data?.domain ?? '').trim(), enabled: enabled ?? query.data?.enabled ?? false }),
    })),
    onSuccess: result => {
      cache.setQueryData(['cdnDraft'], result);
      setDomain(null);
      setEnabled(null);
      setNotice({ error: false, text: result.enabled ? 'Обход БС включён. Обновите подписку в Happ и выберите профиль «Обход БС». Проверьте его работу в вашей сети.' : 'Настройки сохранены. Профиль CDN отключён. После обновления подписки он исчезнет из списка.' });
    },
    onError: (error: Error) => setNotice({ error: true, text: error.message }),
  });
  const check = useMutation({
    mutationFn: async () => {
      const response = await apiFetch('/api/v1/cdn/check', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ domain: (domain ?? query.data?.domain ?? '').trim() }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Не удалось проверить CDN.');
      return result as { ok: boolean; message: string };
    },
    onSuccess: result => setNotice({ error: !result.ok, text: result.message }),
    onError: (error: Error) => setNotice({ error: true, text: error.message }),
  });
  const busy = save.isPending || check.isPending;
  const draft = query.data;

  return <PageLayout title="Обход БС" description="Подключение через CDN в сетях с белыми списками" icon={ShieldCheck}>
    <section className="ui-card ui-panel">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex items-center gap-3">
          <div className="rounded-xl bg-sky-50 p-3 text-sky-600"><Cloud size={24} /></div>
          <div><h2 className="font-bold text-neutral-900">CDN</h2><p className="mt-1 text-sm text-neutral-500">Дополнительный профиль VLESS · XHTTP GET · TLS</p></div>
        </div>
        <span className={`rounded-full px-3 py-1 text-xs font-semibold ${draft?.enabled ? 'bg-emerald-50 text-emerald-800' : 'bg-neutral-100 text-neutral-600'}`}>{query.isPending ? 'Загрузка…' : query.isError ? 'Нет данных' : draft?.enabled ? 'Включён' : 'Выключен'}</span>
      </div>
      <div className="mt-5 rounded-xl bg-neutral-50 p-4 text-sm leading-6 text-neutral-600">
        Клиент подключается к вашему домену CDN, а CDN передаёт запросы на сервер панели. Доступность CDN при ограничениях зависит от сети оператора и проверяется отдельно.
      </div>
      {query.isPending && <p className="mt-5 flex items-center gap-2 text-sm text-neutral-500"><Loader2 size={16} className="animate-spin" />Загрузка настроек…</p>}
      {query.isError && <div role="alert" className="mt-5 text-sm text-red-700">{(query.error as Error).message}</div>}
      {draft && <>
        <label className="mt-5 block text-sm font-semibold text-neutral-700">Домен CDN
          <input className={input} value={domain ?? draft.domain} onChange={event => { setDomain(event.target.value); setNotice(null); }} placeholder="cdn.example.com" autoComplete="off" spellCheck={false} disabled={busy} />
          <span className="mt-2 block text-xs font-normal text-neutral-500">Укажите доменное имя без https://, порта и пути. DNS и сертификат настраиваются у провайдера.</span>
        </label>
        <div className="mt-5 flex items-start justify-between gap-4 rounded-xl border border-neutral-200 p-4">
          <div><h3 className="text-sm font-semibold text-neutral-800">Включить обход БС</h3><p className="mt-1 text-xs leading-5 text-neutral-500">Добавляет профиль CDN в подписки клиентов с XHTTP TLS. При сохранении проверяются HTTPS и XHTTP GET. Работа в ограниченной сети проверяется с телефона.</p></div>
          <Switch label="Включить обход БС" checked={enabled ?? draft.enabled} disabled={busy} onChange={value => { setEnabled(value); setNotice(null); }} />
        </div>
      </>}
    </section>
    <section className="ui-card ui-panel">
      <h2 className="font-bold text-neutral-900">Подготовка подключения</h2>
      <ol className="mt-4 space-y-3 text-sm leading-6 text-neutral-600 list-decimal pl-5">
        <li>Создайте CDN-ресурс и направьте CNAME вашего поддомена на адрес, выданный провайдером.</li>
        <li>Подключите действующий сертификат HTTPS для домена CDN.</li>
        <li>Разрешите GET, отключите кеширование и сохраните заголовки, путь и параметры запросов. Тело GET не используется.</li>
        <li>Проверьте передачу данных и длительное соединение через CDN, затем доступ из ограниченной сети.</li>
      </ol>
      {draft && <dl className="mt-5 grid gap-4 rounded-xl bg-neutral-50 p-4 sm:grid-cols-2">
        <div><dt className="text-xs text-neutral-500">Режим XHTTP</dt><dd className="mt-1 text-sm font-mono text-neutral-800">{draft.mode}</dd></div>
        <div><dt className="text-xs text-neutral-500">Путь на сервере</dt><dd className="mt-1 break-all text-sm font-mono text-neutral-800">{draft.origin_path}</dd></div>
      </dl>}
    </section>
    {notice && <div role={notice.error ? 'alert' : 'status'} className={`rounded-xl border px-4 py-3 text-sm ${notice.error ? 'border-red-200 bg-red-50 text-red-700' : 'border-emerald-200 bg-emerald-50 text-emerald-800'}`}>{notice.text}</div>}
    <div className="ui-actionbar">
      <p className="text-xs leading-5 text-neutral-500">CDN использует существующую подписку, её срок, лимит трафика и выбранную выходную ноду.</p>
      <button className="ui-button ui-button-secondary shrink-0" disabled={!draft || busy} onClick={() => { setNotice(null); check.mutate(); }}>
        {check.isPending ? <Loader2 size={16} className="animate-spin" /> : <FlaskConical size={16} />}{check.isPending ? 'Проверка…' : 'Проверить CDN'}
      </button>
      <button className="ui-button ui-button-primary shrink-0" disabled={!draft || busy} onClick={() => { setNotice(null); save.mutate(); }}>
        {save.isPending ? <Loader2 size={16} className="animate-spin" /> : <Save size={16} />}{save.isPending ? 'Сохранение…' : 'Сохранить'}
      </button>
    </div>
  </PageLayout>;
}
