import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Cloud, Loader2, Sparkles } from 'lucide-react';
import PageLayout from '../components/ui/PageLayout';
import Switch from '../components/ui/Switch';
import { apiFetch } from '../utils/api';
interface WarpPreset { key: string; title: string; description: string; domains: string[]; state: {total: number; missing: number; extra: number} }
interface GeminiMonitorState {
  enabled: boolean; node_id: number | null; node_name: string;
  result: {status?: string; message?: string; checked_at?: number; ip?: string; country?: string; warp?: string; rotation_note?: string};
}
async function request(path: string, method = 'GET', body?: unknown) {
  const res = await apiFetch('/api/v1/warp/' + path, {method, ...(body ? {headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)} : {})});
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || 'Не удалось выполнить запрос');
  return data;
}
export default function WarpPage() {
  const client = useQueryClient();
  const [notice, setNotice] = useState('');
  const [testResult, setTestResult] = useState<{ip: string; country: string; warp: string} | null>(null);
  const status = useQuery<{enabled: boolean; ipv4_only: boolean; controls_ready: boolean; node_id: number | null; name: string; instruction?: string}>({queryKey: ['warp-status'], queryFn: () => request('status')});
  const presets = useQuery<{presets: WarpPreset[]}>({queryKey: ['warp-presets'], queryFn: () => request('presets')});
  const gemini = useQuery<GeminiMonitorState>({queryKey: ['warp-gemini-monitor'], queryFn: () => request('gemini/monitor'), refetchInterval: 15000});
  const refresh = () => { client.invalidateQueries({queryKey: ['warp-status']}); client.invalidateQueries({queryKey: ['warp-presets']}); client.invalidateQueries({queryKey: ['routingRules']}); };
  const toggle = useMutation({mutationFn: (enabled: boolean) => request('usage', 'PUT', {usage: enabled ? 'rules' : 'off'}), onSuccess: () => {setNotice('Настройки сохранены'); setTestResult(null); refresh();}, onError: (error: Error) => setNotice(error.message)});
  const presetMutation = useMutation({mutationFn: ({key, action}: {key: string; action: string}) => request('presets/' + action, 'POST', {key}), onSuccess: () => {setNotice('Пресет сохранён'); refresh();}, onError: (error: Error) => setNotice(error.message)});
  const test = useMutation({mutationFn: () => {setTestResult(null); return request('test', 'POST');}, onSuccess: data => {setTestResult(data); setNotice('');}, onError: (error: Error) => setNotice(error.message)});
  const ipv4 = useMutation({mutationFn: (ipv4_only: boolean) => request('ipv4', 'PUT', {ipv4_only}), onSuccess: () => {setNotice('Режим адресов сохранён для всех нод'); setTestResult(null); refresh();}, onError: (error: Error) => setNotice(error.message)});
  const rotate = useMutation({mutationFn: () => request('rotate-ip', 'POST'), onSuccess: data => {setNotice(data.changed ? `IP изменён: ${data.old_ip} → ${data.new_ip}` : `WARP переподключён, Cloudflare оставил прежний IP: ${data.new_ip}`); setTestResult(null); refresh();}, onError: (error: Error) => setNotice(error.message)});
  const geminiToggle = useMutation({mutationFn: (enabled: boolean) => request('gemini/monitor', 'PUT', {enabled}), onSuccess: data => {client.setQueryData(['warp-gemini-monitor'], data); setNotice('Автопроверка Gemini ' + (data.enabled ? 'включена' : 'выключена'));}, onError: (error: Error) => setNotice(error.message)});
  const geminiCheck = useMutation({mutationFn: () => request('gemini/check', 'POST'), onSuccess: async () => {setNotice(''); await client.invalidateQueries({queryKey: ['warp-gemini-monitor']});}, onError: (error: Error) => setNotice(error.message)});
  const busy = toggle.isPending || presetMutation.isPending || test.isPending || ipv4.isPending || rotate.isPending || geminiToggle.isPending || geminiCheck.isPending;
  const card = 'ui-card ui-panel';
  const result = gemini.data?.result;
  const countryCode = result?.country?.toUpperCase();
  const countryName = countryCode && /^[A-Z]{2}$/.test(countryCode)
    ? new Intl.DisplayNames(['ru'], {type: 'region'}).of(countryCode) : undefined;
  const resultTitle = result?.status === 'region_blocked' ? 'Региональный отказ Gemini'
    : result?.status === 'page_loaded' ? 'Доступ к Gemini не подтверждён' : 'Результат проверки не определён';
  const resultBorder = result?.status === 'region_blocked' ? 'border-red-600' : 'border-amber-500';
  return <PageLayout title="WARP" description="Выход через активную ноду для выбранных сервисов" icon={Cloud}>
    <section data-autosave className={card}>
      <div className="flex items-center justify-between gap-4"><div><h2 className="font-semibold">{status.isPending ? 'Загрузка WARP…' : status.data?.enabled ? 'WARP включён' : 'WARP выключен'}</h2><p className="mt-1 text-sm text-neutral-500">Нода: {status.data?.name || '—'}</p></div><Switch label="Включить WARP" checked={!!status.data?.enabled} disabled={busy || !status.data?.node_id} onChange={value => toggle.mutate(value)} /></div>
      {status.data?.instruction && <p className="mt-3 text-sm text-amber-700">{status.data.instruction}</p>}
      {status.isError && <p role="alert" className="mt-3 text-sm text-red-700">Не удалось загрузить состояние WARP.</p>}
      {status.data?.node_id && !status.data.controls_ready && <p className="mt-3 text-sm text-amber-700">Для смены IP и режима адресов установите служебный обработчик WARP на ноде.</p>}
      <details className="mt-5 border-t border-neutral-200 pt-4"><summary className="cursor-pointer text-sm font-medium">Подключение и диагностика</summary>
      <div className="mt-4 flex items-center justify-between gap-4"><div><h3 className="text-sm font-semibold">Только IPv4</h3><p className="mt-1 text-xs text-neutral-500">Применяется к WARP на всех нодах, в том числе после переключения выхода.</p></div><Switch label="Только IPv4" checked={!!status.data?.ipv4_only} disabled={busy || !status.data?.controls_ready} onChange={value => ipv4.mutate(value)} /></div>
      <div className="mt-4"><button className="ui-button ui-button-secondary" disabled={busy || !status.data?.controls_ready} onClick={() => rotate.mutate()}>{rotate.isPending ? 'Переподключаем WARP…' : 'Сменить IP'}</button><p className="mt-2 text-xs text-neutral-500">Переподключает WARP активной ноды. Соединения через WARP могут прерваться; новый IP не гарантируется. Регистрация сохраняется.</p></div>

      <div className="flex flex-wrap items-center justify-between gap-3"><h2 className="font-semibold">Проверка IPv4</h2><button className="ui-button ui-button-secondary" disabled={busy || !status.data?.node_id} onClick={() => test.mutate()}>{test.isPending ? 'Проверяем…' : 'Проверить IPv4'}</button></div>
      {testResult && <p role="status" className="mt-4 rounded-xl bg-emerald-50 p-4 text-sm text-emerald-900">IPv4: <b>{testResult.ip}</b> · Страна: <b>{testResult.country || '—'}</b> · WARP: <b>{testResult.warp}</b></p>}

      </details>
    </section>
    <section data-autosave className={card}>
      <div className="mb-4 flex items-center gap-3">
        <Sparkles className="h-5 w-5 text-indigo-600" />
        <div>
          <h2 className="font-semibold">Сервисы через WARP</h2>
          <p className="text-sm text-neutral-500">Выбранные сервисы используют WARP при его включении.</p>
        </div>
      </div>
      {presets.isError && <p role="alert" className="mb-3 text-sm text-red-700">Не удалось загрузить пресеты.</p>}
      {presets.isLoading ? <Loader2 className="h-5 w-5 animate-spin text-neutral-400" /> : <div className="grid gap-x-8 md:grid-cols-2">
        {(presets.data?.presets || []).map(preset => {
          const applied = preset.state.total > 0;
          return <div key={preset.key} className="flex items-start justify-between gap-4 border-b border-neutral-100 py-4">
            <div className="min-w-0"><h3 className="text-sm font-semibold text-neutral-800">{preset.title}</h3>
              <details className="mt-2 text-xs text-neutral-500"><summary className="w-fit cursor-pointer">Подробнее</summary><p className="mt-2 leading-5">{preset.description}</p></details>
              {applied && preset.state.missing > 0 && <p className="mt-1 text-xs text-amber-700">Набор правил неполный. Выключите и включите пресет для обновления.</p>}
            </div>
            <div className="shrink-0"><Switch label={preset.title} checked={applied} disabled={busy || presets.isFetching} onChange={enabled => presetMutation.mutate({key: preset.key, action: enabled ? 'apply' : 'remove'})} /></div>
          </div>;
        })}
      </div>}
      <p className="mt-4 text-xs leading-5 text-neutral-500">Изменения сохраняются автоматически. При выключенном WARP выбранные пресеты сохраняются.</p>
    </section>
    <section data-autosave className={card}>
      <div className="flex items-center justify-between gap-4"><div><h2 className="font-semibold">Автопроверка Gemini</h2><p className="mt-1 text-sm text-neutral-500">Проверяю региональный отказ через WARP активной ноды каждые 5 минут.</p></div><Switch label="Автопроверка Gemini" checked={!!gemini.data?.enabled} disabled={busy || !gemini.data || (!gemini.data.enabled && !status.data?.controls_ready)} onChange={enabled => geminiToggle.mutate(enabled)} /></div>
      <p className="mt-3 text-xs text-neutral-500">При региональном отказе WARP переподключается. Соединения через него могут прерваться.</p>
      {!status.data?.enabled && <p className="mt-3 text-sm text-amber-700">Пока WARP выключен, автопроверка приостановлена.</p>}
      {gemini.isError && <p role="alert" className="mt-3 text-sm text-red-700">Не удалось загрузить состояние автопроверки Gemini.</p>}
      {gemini.data?.result.message && <div role="status" className={`mt-4 border-l-2 ${resultBorder} pl-3 text-sm`}>
        <p className="font-semibold">{resultTitle}</p>
        <p className="mt-2">{gemini.data.result.message}</p>
        <p className="mt-2">Страна IP по Cloudflare: <b>{countryName ? `${countryName} (${countryCode})` : 'не определена'}</b></p>
        {result?.ip && <p className="mt-1">IP выхода: {result.ip}</p>}
        <p className="mt-1">WARP: {result?.warp === 'on' || result?.warp === 'plus' ? 'подтверждён' : result?.warp === 'off' ? 'выключен на проверочном выходе' : 'не проверен'}</p>
        <p className="mt-2 text-xs text-neutral-500">Это страна IP по данным Cloudflare, а не регион аккаунта Google. Доступ к генерации проверяется в Gemini под вашим аккаунтом.</p>
        {gemini.data.result.checked_at && <p className="mt-1 text-xs text-neutral-500">{new Date(gemini.data.result.checked_at * 1000).toLocaleString()} · {gemini.data.node_name}</p>}
        {gemini.data.result.rotation_note && <p className="mt-2">{gemini.data.result.rotation_note}</p>}
      </div>}
      <button className="ui-button ui-button-secondary mt-4" disabled={busy || !status.data?.enabled || !status.data?.node_id} onClick={() => geminiCheck.mutate()}>{geminiCheck.isPending ? 'Проверяем Gemini…' : 'Проверить Gemini'}</button>
      <details className="mt-4 text-xs text-neutral-500"><summary className="cursor-pointer text-sm">Как работает проверка</summary>
      <p className="mt-3 text-sm text-neutral-500">После двух региональных отказов подряд переподключаю WARP. Между попытками — не менее 30 минут, не более 3 попыток за 6 часов для каждой ноды. Соединения через WARP могут прерваться; новый IP не гарантируется.</p>
      <p className="mt-2 text-xs text-neutral-500">Для трафика Gemini включите соответствующий пресет или режим WARP для всего трафика. CAPTCHA, запрос входа и ошибки сети не вызывают смену IP. Проверка без аккаунта Google не подтверждает доступ к генерации и не определяет страну аккаунта.</p>
      <p className="mt-2 text-xs text-neutral-500">Ручная проверка показывает результат и не переподключает WARP.</p>
      </details>
    </section>
    {notice && <p role="status" className="rounded-xl border border-neutral-200 bg-white p-4 text-sm">{notice}</p>}
  </PageLayout>;
}
