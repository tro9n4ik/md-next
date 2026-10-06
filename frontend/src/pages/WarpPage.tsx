import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Cloud, Loader2, Sparkles } from 'lucide-react';
import PageLayout from '../components/ui/PageLayout';
import Switch from '../components/ui/Switch';
import { apiFetch } from '../utils/api';
interface WarpPreset { key: string; title: string; description: string; domains: string[]; state: {total: number; missing: number; extra: number} }
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
  const status = useQuery<{enabled: boolean; node_id: number | null; name: string; instruction?: string}>({queryKey: ['warp-status'], queryFn: () => request('status')});
  const presets = useQuery<{presets: WarpPreset[]}>({queryKey: ['warp-presets'], queryFn: () => request('presets')});
  const refresh = () => { client.invalidateQueries({queryKey: ['warp-status']}); client.invalidateQueries({queryKey: ['warp-presets']}); client.invalidateQueries({queryKey: ['routingRules']}); };
  const toggle = useMutation({mutationFn: (enabled: boolean) => request('usage', 'PUT', {usage: enabled ? 'rules' : 'off'}), onSuccess: () => {setNotice('Настройки сохранены'); setTestResult(null); refresh();}, onError: (error: Error) => setNotice(error.message)});
  const presetMutation = useMutation({mutationFn: ({key, action}: {key: string; action: string}) => request('presets/' + action, 'POST', {key}), onSuccess: () => {setNotice('Пресет сохранён'); refresh();}, onError: (error: Error) => setNotice(error.message)});
  const test = useMutation({mutationFn: () => {setTestResult(null); return request('test', 'POST');}, onSuccess: data => {setTestResult(data); setNotice('');}, onError: (error: Error) => setNotice(error.message)});
  const busy = toggle.isPending || presetMutation.isPending || test.isPending;
  const card = 'ui-card ui-panel';
  return <PageLayout title="WARP" description="Выход через активную ноду для выбранных сервисов" icon={Cloud}>
    <section data-autosave className={card}>
      <div className="flex items-center justify-between gap-4"><div><h2 className="font-semibold">{status.data?.enabled ? 'WARP включён' : 'WARP выключен'}</h2><p className="mt-1 text-sm text-neutral-500">Нода: {status.data?.name || '—'}</p></div><Switch label="Включить WARP" checked={!!status.data?.enabled} disabled={busy || !status.data?.node_id} onChange={value => toggle.mutate(value)} /></div>
      <p className="mt-3 text-sm text-neutral-500">Используется активная нода из раздела «Узлы». На ней должен работать WARP. Отключение сохраняет пресеты.</p>
      {status.data?.instruction && <p className="mt-3 text-sm text-amber-700">{status.data.instruction}</p>}
      {status.isError && <p role="alert" className="mt-3 text-sm text-red-700">Не удалось загрузить состояние WARP.</p>}
    </section>
    <section data-autosave className={card}>
      <div className="mb-4 flex items-center gap-3">
        <Sparkles className="h-5 w-5 text-indigo-600" />
        <div>
          <h2 className="font-semibold">Готовые правила для сервисов</h2>
          <p className="text-sm text-neutral-500">Выберите сервисы, которые будут использовать WARP при включённом переключателе.</p>
        </div>
      </div>
      {presets.isError && <p role="alert" className="mb-3 text-sm text-red-700">Не удалось загрузить пресеты.</p>}
      {presets.isLoading ? <Loader2 className="h-5 w-5 animate-spin text-neutral-400" /> : <div className="flex flex-col gap-3">
        {(presets.data?.presets || []).map(preset => {
          const applied = preset.state.total > 0;
          return <div key={preset.key} className="flex items-center justify-between gap-4 rounded-xl border border-neutral-200 bg-neutral-50 p-4">
            <div className="min-w-0"><h3 className="text-sm font-semibold text-neutral-800">{preset.title}</h3>
              <p className="mt-1.5 text-xs leading-5 text-neutral-600">{preset.description}</p>
              {applied && preset.state.missing > 0 && <p className="mt-1 text-xs text-amber-700">Набор правил неполный. Выключите и включите пресет для обновления.</p>}
            </div>
            <div className="shrink-0"><Switch label={preset.title} checked={applied} disabled={busy || presets.isFetching} onChange={enabled => presetMutation.mutate({key: preset.key, action: enabled ? 'apply' : 'remove'})} /></div>
          </div>;
        })}
      </div>}
      <p className="mt-4 text-xs leading-5 text-neutral-500">Изменения сохраняются автоматически. При выключенном WARP выбранные пресеты сохраняются.</p>
    </section>
    <section className={card}>
      <div className="flex flex-wrap items-center justify-between gap-3"><h2 className="font-semibold">Проверка IPv4</h2><button className="ui-button ui-button-secondary" disabled={busy || !status.data?.node_id} onClick={() => test.mutate()}>{test.isPending ? 'Проверяем…' : 'Проверить IPv4'}</button></div>
      {testResult && <p role="status" className="mt-4 rounded-xl bg-emerald-50 p-4 text-sm text-emerald-900">IPv4: <b>{testResult.ip}</b> · Страна: <b>{testResult.country || '—'}</b> · WARP: <b>{testResult.warp}</b></p>}
    </section>
    {notice && <p role="status" className="rounded-xl border border-neutral-200 bg-white p-4 text-sm">{notice}</p>}
  </PageLayout>;
}
