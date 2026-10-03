import React, { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Activity, Check, CheckCircle2, Cloud, Loader2, Play, Power, ShieldCheck, Sparkles, Trash2 } from 'lucide-react';
import { apiFetch } from '../utils/api';
import { translateStatus } from '../utils/ru';

interface WarpStatus {
  installed: boolean; service_active: boolean; registered: boolean;
  state: string; mode: string; port: number; instruction?: string | null;
  remote?: boolean; name?: string; country?: string;
}
interface WarpTarget { node_id: number | null; name: string; port: number; expected_country: string }
interface WarpNode { id: number; name: string; is_enabled: boolean }
interface WarpUsage { usage: 'off' | 'rules' | 'all' }
interface WarpPresetState { total: number; missing: number; extra: number }
interface WarpPreset {
  key: string; title: string; description: string;
  domains: string[]; state: WarpPresetState;
}
interface PresetApplyResult {
  status: string; title: string; created: string[]; updated: string[];
  removed: string[]; warp_usage: string | null;
}

async function request(path: string, method = 'GET', body?: unknown) {
  const response = await apiFetch(path, {
    method,
    ...(body ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {})
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || 'Не удалось выполнить запрос');
  return data;
}

const WarpPage: React.FC = () => {
  const client = useQueryClient();
  const [mode, setMode] = useState<'proxy' | 'warp'>('proxy');
  const [port, setPort] = useState(40000);
  const [license, setLicense] = useState('');
  const [notice, setNotice] = useState('');
  const [targetDraft, setTargetDraft] = useState<{ nodeId: string; port: number; country: string } | null>(null);
  const [testResult, setTestResult] = useState<{ ip: string; country: string; warp: string } | null>(null);
  const status = useQuery<WarpStatus>({ queryKey: ['warp-status'], queryFn: () => request('/api/v1/warp/status'), refetchInterval: 10000 });
  const usage = useQuery<WarpUsage>({ queryKey: ['warp-usage'], queryFn: () => request('/api/v1/warp/usage') });
  const presets = useQuery<{ presets: WarpPreset[] }>({ queryKey: ['warp-presets'], queryFn: () => request('/api/v1/warp/presets') });
  const target = useQuery<WarpTarget>({ queryKey: ['warp-target'], queryFn: () => request('/api/v1/warp/target') });
  const nodes = useQuery<WarpNode[]>({ queryKey: ['nodes'], queryFn: () => request('/api/v1/nodes') });
  const nodeId = targetDraft?.nodeId ?? (target.data?.node_id ? String(target.data.node_id) : '');
  const targetPort = targetDraft?.port ?? target.data?.port ?? 40000;
  const expectedCountry = targetDraft?.country ?? target.data?.expected_country ?? '';
  const setNodeId = (value: string) => { setTargetDraft({ nodeId: value, port: targetPort, country: expectedCountry }); setTestResult(null); };
  const setTargetPort = (value: number) => { setTargetDraft({ nodeId, port: value, country: expectedCountry }); setTestResult(null); };
  const setExpectedCountry = (value: string) => { setTargetDraft({ nodeId, port: targetPort, country: value }); setTestResult(null); };
  React.useEffect(() => {
    setMode(status.data?.mode === 'warp' ? 'warp' : 'proxy');
    setPort(status.data?.port || 40000);
  }, [status.data?.mode, status.data?.port]);
  const command = useMutation({
    mutationFn: ({ path, body }: { path: string; body?: unknown }) => request(path, 'POST', body),
    onSuccess: (data) => { setNotice(data.message || 'Готово'); client.invalidateQueries({ queryKey: ['warp-status'] }); client.invalidateQueries({ queryKey: ['warp-usage'] }); },
    onError: (error: Error) => setNotice(error.message)
  });
  const usageMutation = useMutation({
    mutationFn: (value: WarpUsage['usage']) => request('/api/v1/warp/usage', 'PUT', { usage: value }),
    onSuccess: (_data, value) => { setNotice('Настройка использования WARP сохранена'); client.setQueryData(['warp-usage'], { usage: value }); },
    onError: (error: Error) => setNotice(error.message)
  });
  const targetMutation = useMutation({
    mutationFn: () => request('/api/v1/warp/target', 'PUT', { node_id: nodeId ? Number(nodeId) : null, port: targetPort, expected_country: expectedCountry }),
    onSuccess: (data) => {
      setNotice(data.message); setTestResult(data);
      client.invalidateQueries({ queryKey: ['warp-target'] });
      client.invalidateQueries({ queryKey: ['warp-status'] });
    },
    onError: (error: Error) => setNotice(error.message)
  });
  const presetMutation = useMutation({
    mutationFn: ({ key, action }: { key: string; action: 'apply' | 'remove' }) =>
      request(`/api/v1/warp/presets/${action}`, 'POST', { key }),
    onSuccess: (data: PresetApplyResult) => {
      const parts: string[] = [];
      if (data.created.length) parts.push(`добавлено правил: ${data.created.length}`);
      if (data.updated.length) parts.push(`обновлено: ${data.updated.length}`);
      if (data.removed.length) parts.push(`удалено: ${data.removed.length}`);
      if (data.warp_usage) parts.push('режим WARP включён автоматически');
      setNotice(`Пресет «${data.title}»: ${parts.length ? parts.join(', ') : 'изменений не потребовалось'}`);
      client.invalidateQueries({ queryKey: ['warp-presets'] });
      client.invalidateQueries({ queryKey: ['warp-usage'] });
      client.invalidateQueries({ queryKey: ['routing-rules'] });
    },
    onError: (error: Error) => setNotice(error.message),
  });
  const busy = command.isPending || usageMutation.isPending || presetMutation.isPending || targetMutation.isPending;
  const card = 'rounded-2xl border border-neutral-200 bg-white p-5 shadow-sm';
  const label = 'mb-1.5 block text-sm font-medium text-neutral-600';
  const input = 'w-full rounded-xl border border-neutral-300 bg-white px-3 py-2 text-sm outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-100';

  return <div className="mx-auto max-w-5xl space-y-6">
    <div><h1 className="text-2xl font-bold text-neutral-900">Cloudflare WARP</h1><p className="mt-1 text-sm text-neutral-500">Включите бесплатный WARP и выберите сервисы, трафик которых нужно направить через него.</p></div>
    <section className={card}>
      <div className="mb-4 flex items-center gap-3"><div className="rounded-xl bg-indigo-50 p-2.5 text-indigo-600"><Cloud className="h-5 w-5" /></div><div><h2 className="font-semibold">Состояние</h2><p className="text-sm text-neutral-500">Обновляется автоматически</p></div></div>
      {status.isLoading ? <Loader2 className="h-5 w-5 animate-spin text-neutral-400" /> : status.data?.remote ? <div className="rounded-xl bg-neutral-50 p-3 text-sm">Выход: <b>{status.data.name}</b> · {translateStatus(status.data.state)} · Страна: <b>{status.data.country || 'не определена'}</b></div> : <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {[["Установлен", status.data?.installed ? 'Да' : 'Нет'], ["Служба warp-svc", status.data?.service_active ? 'Работает' : 'Остановлена'], ["Зарегистрирован", status.data?.registered ? 'Да' : 'Нет'], ["Состояние", translateStatus(status.data?.state)], ["Режим", translateStatus(status.data?.mode)], ["Порт SOCKS5", String(status.data?.port || "—")]].map(([title, value]) => <div key={title} className="rounded-xl bg-neutral-50 p-3"><div className="text-xs text-neutral-500">{title}</div><div className="mt-1 flex items-center gap-2 font-semibold"><span className={`h-2 w-2 rounded-full ${value === 'Да' || value === 'Работает' || value === 'Подключено' ? 'bg-emerald-500' : 'bg-amber-500'}`} />{value}</div></div>)}
      </div>}
      <p className="mt-4 text-sm text-neutral-500">Обычный WARP работает без лицензии. Для зарубежного выхода выберите ноду, на которой установлен WARP. Страну определяет Cloudflare; перед сохранением панель проверит фактический выход. Регистрация через ноду сама по себе не переносит туннель на неё.</p>
      {status.isError && <p role="alert" className="mt-4 text-sm text-red-700">Не удалось получить состояние WARP. Обновите страницу.</p>}
      {status.data?.instruction && <p className="mt-4 rounded-lg bg-amber-50 p-3 text-sm text-amber-800">{status.data.instruction}</p>}
      <div className="mt-4 flex flex-wrap gap-2">
        <button disabled={busy || !status.data?.installed} onClick={() => command.mutate({ path: '/api/v1/warp/setup' })} className="flex items-center gap-2 rounded-xl bg-emerald-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"><Play className="h-4 w-4" />{command.isPending && command.variables?.path === '/api/v1/warp/setup' ? 'Проверка…' : status.data?.remote ? 'Проверить WARP ноды' : 'Включить WARP'}</button>
        {!status.data?.remote && <button disabled={busy || !status.data?.installed} onClick={() => command.mutate({ path: '/api/v1/warp/disconnect' })} className="flex items-center gap-2 rounded-xl border border-neutral-300 px-4 py-2 text-sm font-medium text-neutral-700 disabled:opacity-50"><Power className="h-4 w-4" />Отключить</button>}
      </div>
    </section>

    <section className={card}><h2 className="mb-4 font-semibold">Сервер выхода WARP</h2>
      <div className="grid gap-4 sm:grid-cols-3"><div><label className={label} htmlFor="warp-node">Сервер</label><select id="warp-node" className={input} value={nodeId} onChange={e => setNodeId(e.target.value)}><option value="">Сервер панели</option>{(nodes.data || []).filter(node => node.is_enabled).map(node => <option key={node.id} value={node.id}>{node.name}</option>)}</select></div>
      <div><label className={label} htmlFor="warp-node-port">Порт прокси на сервере</label><input id="warp-node-port" className={input} type="number" min={1} max={65535} value={targetPort} onChange={e => setTargetPort(Number(e.target.value))} /></div>
      <div><label className={label} htmlFor="warp-country">Проверять страну</label><input id="warp-country" className={input} maxLength={2} value={expectedCountry} onChange={e => setExpectedCountry(e.target.value.toUpperCase().replace(/[^A-Z]/g, ''))} placeholder="DE, NL или пусто" /></div></div>
      <p className="mt-3 text-sm text-neutral-500">Код страны — условие проверки, а не переключатель региона Cloudflare. На ноде нужен WARP в режиме proxy; открывать порт SOCKS5 в интернет не требуется. При недоступности ноды автоматического выхода через сервер панели нет.</p>
      <button disabled={busy || target.isLoading || nodes.isLoading} onClick={() => targetMutation.mutate()} className="mt-4 rounded-xl bg-indigo-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50">{targetMutation.isPending ? 'Проверка выхода…' : 'Проверить и сохранить'}</button>
    </section>

    {!status.data?.remote && <details className={card}><summary className="cursor-pointer text-sm font-semibold text-neutral-700">Дополнительные настройки и WARP+ (необязательно)</summary>
    <div className="mt-4 grid gap-6 lg:grid-cols-2">
      <section className={card}><h2 className="mb-4 font-semibold">Режим и порт прокси</h2><div className="grid gap-4 sm:grid-cols-2"><div><label className={label}>Режим</label><select className={input} value={mode} onChange={e => setMode(e.target.value as 'proxy' | 'warp')}><option value="proxy">Прокси</option><option value="warp">WARP</option></select></div><div><label className={label}>Порт SOCKS5</label><input className={input} type="number" min={1} max={65535} value={port} onChange={e => setPort(Number(e.target.value))} /></div></div><button disabled={busy || !status.data?.installed} onClick={() => command.mutate({ path: '/api/v1/warp/mode', body: { mode, port } })} className="mt-4 rounded-xl bg-neutral-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50">Сохранить режим</button></section>
      <section className={card}><h2 className="mb-2 font-semibold">Лицензия WARP+</h2><p className="mb-4 text-sm text-neutral-500">Необязательно. Для бесплатного WARP и готовых правил ключ не нужен.</p><label className={label}>Лицензионный ключ</label><input className={input} type="password" autoComplete="off" value={license} onChange={e => setLicense(e.target.value)} placeholder="Ключ не сохраняется в панели" /><button disabled={busy || !license || !status.data?.installed} onClick={() => command.mutate({ path: '/api/v1/warp/license', body: { key: license } }, { onSuccess: () => setLicense('') })} className="mt-4 rounded-xl border border-neutral-300 px-4 py-2 text-sm font-medium disabled:opacity-50">Применить ключ</button></section>
    </div>
    </details>}

    <section className={card}>
      <div className="mb-4 flex items-center gap-3">
        <Sparkles className="h-5 w-5 text-indigo-600" />
        <div>
          <h2 className="font-semibold">Готовые правила для сервисов</h2>
          <p className="text-sm text-neutral-500">После включения WARP выберите нужные сервисы. Кнопка «Применить» создаст правила и направит через WARP только выбранный трафик.</p>
        </div>
      </div>
      {!status.data?.installed && <p className="mb-4 rounded-lg bg-amber-50 p-3 text-sm text-amber-800">Пресеты можно создать и сейчас, но трафик пойдёт через WARP только после установки пакета cloudflare-warp и подключения.</p>}
      {presets.isLoading ? <Loader2 className="h-5 w-5 animate-spin text-neutral-400" /> : <div className="grid gap-3 sm:grid-cols-2">
        {(presets.data?.presets || []).map(preset => {
          const applied = preset.state.total > 0 && preset.state.missing === 0;
          const stale = preset.state.missing > 0 || preset.state.extra > 0;
          return <div key={preset.key} className="flex flex-col rounded-xl border border-neutral-200 bg-neutral-50 p-4">
            <div className="flex items-start justify-between gap-2">
              <div className="text-sm font-semibold text-neutral-800">{preset.title}</div>
              {applied && !stale && <span className="inline-flex shrink-0 items-center gap-1 rounded-full bg-emerald-100 px-2 py-0.5 text-xs font-medium text-emerald-700"><Check className="h-3 w-3" />применён</span>}
            </div>
            <p className="mt-1.5 flex-1 text-xs leading-5 text-neutral-600">{preset.description}</p>
            <p className="mt-2 text-xs text-neutral-500">{preset.domains.length} доменов{preset.state.total > 0 && ` · в базе ${preset.state.total}`}</p>
            <div className="mt-3 flex flex-wrap gap-2">
              <button disabled={busy} onClick={() => presetMutation.mutate({ key: preset.key, action: 'apply' })} className="rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50">{preset.state.total > 0 ? 'Обновить' : 'Применить'}</button>
              {preset.state.total > 0 && <button disabled={busy} onClick={() => presetMutation.mutate({ key: preset.key, action: 'remove' })} className="inline-flex items-center gap-1 rounded-lg border border-neutral-300 px-3 py-1.5 text-xs font-medium text-neutral-700 disabled:opacity-50"><Trash2 className="h-3 w-3" />Убрать</button>}
            </div>
          </div>;
        })}
      </div>}
      <p className="mt-4 text-xs leading-5 text-neutral-500">Повторное применение не создаёт дублей: уже существующие правила обновляются, а правила, созданные вручную, не затрагиваются. Домены перечислены явно, поэтому пресеты работают без файлов geosite.</p>
    </section>

    <section className={card}><div className="mb-4 flex items-center gap-3"><ShieldCheck className="h-5 w-5 text-indigo-600" /><h2 className="font-semibold">Использование WARP в Xray</h2></div><select className={input} value={usage.data?.usage || 'off'} disabled={busy} onChange={e => usageMutation.mutate(e.target.value as WarpUsage['usage'])}><option value="off">Выключен</option><option value="rules">По правилам маршрутизации</option><option value="all">Весь трафик</option></select><p className="mt-3 text-sm text-neutral-500">В режиме «По правилам» WARP применяется только для правил с действием «WARP». Режим «Весь трафик» использует WARP по умолчанию, включая случай выбранной ноды выхода. Трафик AmneziaWG всегда выходит напрямую с этого сервера.</p></section>

    <section className={card}><div className="mb-3 flex items-center gap-3"><Activity className="h-5 w-5 text-emerald-600" /><h2 className="font-semibold">Проверка соединения</h2></div><p className="mb-4 text-sm text-neutral-500">Проверить внешний IP через выбранный выход WARP.</p><button disabled={busy || !status.data?.installed} onClick={() => command.mutate({ path: '/api/v1/warp/test' }, { onSuccess: setTestResult })} className="rounded-xl bg-indigo-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50">Проверить</button>{testResult && <div className="mt-4 flex flex-wrap gap-4 rounded-xl bg-emerald-50 p-4 text-sm text-emerald-900"><span>IP: <b>{testResult.ip || '—'}</b></span><span>Страна: <b>{testResult.country || '—'}</b></span><span>WARP: <b>{testResult.warp}</b></span><CheckCircle2 className="ml-auto h-5 w-5" /></div>}</section>
    {notice && <div role="status" className="rounded-xl border border-neutral-200 bg-white px-4 py-3 text-sm text-neutral-700">{notice}</div>}
  </div>;
};

export default WarpPage;
