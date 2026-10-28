import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { Cloud, Loader2, Sparkles, ArrowRight, RefreshCw, Globe, Server, ShieldCheck, Network, ChevronRight, Activity, CircleHelp, Gamepad2, Bot } from 'lucide-react';
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
  const [testResult, setTestResult] = useState<{target: string; trace: {ip: string; country: string; warp: string}} | null>(null);
  const status = useQuery<{enabled: boolean; ipv4_only: boolean; controls_ready: boolean; node_id: number | null; name: string; instruction?: string}>({queryKey: ['warp-status'], queryFn: () => request('status'), refetchInterval: 15000});
  const presets = useQuery<{presets: WarpPreset[]}>({queryKey: ['warp-presets'], queryFn: () => request('presets')});
  const gemini = useQuery<GeminiMonitorState>({queryKey: ['warp-gemini-monitor'], queryFn: () => request('gemini/monitor'), refetchInterval: 15000});
  const refresh = () => { client.invalidateQueries({queryKey: ['warp-status']}); client.invalidateQueries({queryKey: ['warp-presets']}); client.invalidateQueries({queryKey: ['warp-gemini-monitor']}); client.invalidateQueries({queryKey: ['routingRules']}); };
  const target = `${status.data?.node_id}:${status.data?.ipv4_only}:${status.data?.enabled}`;
  const exitResult = testResult?.target === target ? testResult.trace : null;
  const toggle = useMutation({mutationFn: (enabled: boolean) => request('usage', 'PUT', {usage: enabled ? 'rules' : 'off'}), onSuccess: () => {setNotice('Настройки сохранены'); setTestResult(null); refresh();}, onError: (error: Error) => setNotice(error.message)});
  const presetMutation = useMutation({mutationFn: ({key, action}: {key: string; action: string}) => request('presets/' + action, 'POST', {key}), onSuccess: () => {setNotice('Пресет сохранён'); refresh();}, onError: (error: Error) => setNotice(error.message)});
  const test = useMutation({mutationFn: async () => {setTestResult(null); const trace = await request('test', 'POST'); return {target, trace};}, onSuccess: data => {setTestResult(data); setNotice('');}, onError: (error: Error) => setNotice(error.message)});
  const ipv4 = useMutation({mutationFn: (ipv4_only: boolean) => request('ipv4', 'PUT', {ipv4_only}), onSuccess: () => {setNotice('Режим адресов сохранён для всех нод'); setTestResult(null); refresh();}, onError: (error: Error) => setNotice(error.message)});
  const rotate = useMutation({mutationFn: () => request('rotate-ip', 'POST'), onSuccess: data => {setNotice(data.changed ? `IP изменён: ${data.old_ip} → ${data.new_ip}` : `WARP переподключён, Cloudflare оставил прежний IP: ${data.new_ip}`); setTestResult(null); refresh();}, onError: (error: Error) => setNotice(error.message)});
  const geminiToggle = useMutation({mutationFn: (enabled: boolean) => request('gemini/monitor', 'PUT', {enabled}), onSuccess: data => {client.setQueryData(['warp-gemini-monitor'], data); setNotice('Автопроверка Gemini ' + (data.enabled ? 'включена' : 'выключена'));}, onError: (error: Error) => setNotice(error.message)});
  const geminiCheck = useMutation({mutationFn: () => request('gemini/check', 'POST'), onSuccess: async () => {setNotice(''); await client.invalidateQueries({queryKey: ['warp-gemini-monitor']});}, onError: (error: Error) => setNotice(error.message)});
  const busy = toggle.isPending || presetMutation.isPending || test.isPending || ipv4.isPending || rotate.isPending || geminiToggle.isPending || geminiCheck.isPending;
  const enabledCount = presets.data?.presets.filter(preset => preset.state.total > 0).length || 0;
  const result = gemini.data?.result;
  const countryCode = result?.country?.toUpperCase();
  const countryName = countryCode && /^[A-Z]{2}$/.test(countryCode)
    ? new Intl.DisplayNames(['ru'], {type: 'region'}).of(countryCode) : undefined;
  const resultTitle = result?.status === 'region_blocked' ? 'Региональный отказ Gemini'
    : result?.status === 'page_loaded' ? 'Доступ к Gemini не подтверждён' : 'Результат проверки не определён';
  const resultBorder = result?.status === 'region_blocked' ? 'border-red-600' : 'border-amber-500';
  return <div className="page-layout warp-page">
    <header className="dashboard-heading"><div><div className="dashboard-eyebrow">ОТДЕЛЬНЫЙ ВЫХОД ДЛЯ ВАШИХ СЕРВИСОВ</div><h1>WARP</h1><p>Управляйте выходом через Cloudflare на активной ноде</p></div><div className="page-actions"><button className="ui-button ui-button-secondary" disabled={busy || status.isFetching || presets.isFetching || gemini.isFetching} onClick={() => { setTestResult(null); refresh(); }}><RefreshCw size={16} />Обновить</button></div></header>

    <section data-autosave className="ui-card warp-hero">
      <div className="warp-hero-heading"><div className="warp-hero-identity"><span className="warp-cloud-icon"><Cloud size={30} strokeWidth={1.6} /></span><div><div className="warp-brand-label">CLOUDFLARE WARP</div><h2>{status.isError ? 'Состояние недоступно' : status.isPending ? 'Загрузка WARP…' : status.data?.enabled ? 'WARP включён' : 'WARP выключен'}</h2><p>{status.data?.enabled ? 'Выбранные сервисы используют отдельный выход' : status.data ? 'Включите WARP, чтобы применить выбранные сервисы' : 'Получаем настройки подключения'}</p></div></div><div className="warp-main-toggle"><span>{status.isError || !status.data ? 'Нет данных' : status.data.enabled ? 'Включён' : 'Выключен'}</span><Switch label="Включить WARP" checked={!!status.data?.enabled} disabled={busy || !status.data?.node_id || status.isError} onChange={value => toggle.mutate(value)} /></div></div>
      <div className="warp-overview">
        <div className="warp-overview-item"><span className="warp-detail-icon"><Server size={18} /></span><div><span>Активная нода</span><b>{status.data?.name || 'Не выбрана'}</b></div><Link to="/nodes" aria-label="Управление нодами"><ArrowRight size={17} /></Link></div>
        <div className="warp-overview-item"><span className="warp-detail-icon"><Network size={18} /></span><div><span>Режим адресов</span><b>{status.data ? status.data.ipv4_only ? 'Только IPv4' : 'IPv4 и IPv6' : '—'}</b></div></div>
        <div className="warp-overview-item"><span className="warp-detail-icon"><Sparkles size={18} /></span><div><span>Выбрано сервисов</span><b>{presets.data ? `${enabledCount} из ${presets.data.presets.length}` : '—'}</b></div></div>
      </div>
      {status.data?.instruction && <p className="warp-inline-notice">{status.data.instruction}</p>}
      {status.isError && <p role="alert" className="warp-inline-error">Не удалось загрузить состояние WARP. <button onClick={() => status.refetch()}>Повторить</button></p>}
    </section>

    {notice && <p role="status" className="warp-notice">{notice}</p>}

    <div className="warp-main-grid">
      <section data-autosave className="ui-card warp-services">
        <div className="warp-section-heading"><span className="warp-section-icon"><Sparkles size={20} /></span><div><h2>Сервисы через WARP</h2><p>Выберите, какой трафик направлять через Cloudflare</p></div><span className="warp-count">{presets.data ? enabledCount : '—'}</span></div>
        {presets.isError && <p role="alert" className="warp-inline-error">Не удалось загрузить пресеты. <button onClick={() => presets.refetch()}>Повторить</button></p>}
        {presets.isLoading ? <p className="warp-loading"><Loader2 size={18} className="animate-spin" />Загружаем сервисы…</p> : <div className="warp-preset-grid">
          {(presets.data?.presets || []).map(preset => {
            const applied = preset.state.total > 0;
            const Icon = preset.key === 'hill-climb-racing-2' ? Gamepad2 : preset.key === 'gemini' ? Sparkles : preset.key === 'claude' ? Bot : preset.key === 'chatgpt' ? Bot : Globe;
            return <article key={preset.key} className={`warp-preset-card ${applied ? 'warp-preset-selected' : ''}`}>
              <div className="warp-preset-top"><span className={`warp-preset-icon warp-preset-icon-${preset.key}`}><Icon size={22} strokeWidth={1.7} /></span><Switch label={preset.title} checked={applied} disabled={busy || presets.isFetching} onChange={enabled => presetMutation.mutate({key: preset.key, action: enabled ? 'apply' : 'remove'})} /></div>
              <h3>{preset.title}</h3><p className="warp-preset-state">{applied ? status.data?.enabled ? 'Выбран для WARP' : 'Выбран · WARP выключен' : 'Не выбран'}</p>
              <details className="warp-preset-details"><summary>Подробнее <ChevronRight size={13} /></summary><p>{preset.description}</p><div className="warp-domain-list">{preset.domains.map(domain => <span key={domain}>{domain}</span>)}</div></details>
              {applied && preset.state.missing > 0 && <p className="warp-preset-warning">Набор правил неполный. Выключите и включите пресет для обновления.</p>}
            </article>;
          })}
        </div>}
        {!presets.isLoading && !presets.isError && presets.data?.presets.length === 0 && <p className="warp-empty">Список сервисов пока пуст.</p>}
        <div className="warp-autosave-note"><ShieldCheck size={15} /><span>Изменения сохраняются автоматически. При выключенном WARP выбранные пресеты сохраняются.</span></div>
      </section>

      <section data-autosave className="ui-card warp-connection">
        <div className="warp-section-heading"><span className="warp-section-icon"><Network size={20} /></span><div><h2>Подключение</h2><p>Выход и параметры сети</p></div></div>
        <div className="warp-address"><span>Выходной IP</span><b>{exitResult?.ip || 'Ещё не проверен'}</b><p>{exitResult ? `Страна по Cloudflare: ${exitResult.country || '—'}` : 'Проверьте адрес текущего выхода WARP'}</p></div>
        <button className="ui-button ui-button-secondary warp-check-button" disabled={busy || !status.data?.node_id} onClick={() => test.mutate()}>{test.isPending ? <><Loader2 size={15} className="animate-spin" />Проверяем…</> : <><Activity size={15} />Проверить IPv4</>}</button>
        {exitResult && <p role="status" className={`warp-exit-result ${exitResult.warp === 'on' || exitResult.warp === 'plus' ? 'warp-exit-confirmed' : ''}`}>WARP: {exitResult.warp === 'on' || exitResult.warp === 'plus' ? 'подтверждён' : exitResult.warp === 'off' ? 'выключен на проверочном выходе' : 'не проверен'}</p>}
        <div className="warp-connection-option"><div><h3>Только IPv4</h3><p>На всех нодах, включая резервные</p></div><Switch label="Только IPv4" checked={!!status.data?.ipv4_only} disabled={busy || !status.data?.controls_ready} onChange={value => ipv4.mutate(value)} /></div>
        <div className="warp-rotation"><h3>Новый адрес выхода</h3><p>Переподключите WARP активной ноды, чтобы попробовать получить другой IP.</p><button className="ui-button ui-button-secondary" disabled={busy || !status.data?.controls_ready} onClick={() => rotate.mutate()}><RefreshCw size={15} />{rotate.isPending ? 'Переподключаем WARP…' : 'Сменить IP'}</button><small>Соединения через WARP могут прерваться; новый IP не гарантируется. Регистрация сохраняется.</small></div>
        {status.data?.node_id && !status.data.controls_ready && <p className="warp-inline-notice">Для смены IP и режима адресов установите служебный обработчик WARP на ноде.</p>}
        <Link to="/nodes" className="warp-text-link">Управление нодами <ArrowRight size={15} /></Link>
      </section>
    </div>

    <section data-autosave className="ui-card warp-gemini">
      <div className="warp-section-heading"><span className="warp-section-icon warp-gemini-icon"><Sparkles size={21} /></span><div><h2>Автопроверка Gemini</h2><p>{gemini.isError ? 'Нет данных' : !gemini.data ? 'Загрузка…' : gemini.data.enabled ? status.data?.enabled ? 'Каждые 5 минут' : 'Ожидает включения WARP' : 'Только вручную'}</p></div><div className="warp-gemini-actions"><button className="ui-button ui-button-secondary" disabled={busy || !status.data?.enabled || !status.data?.node_id} onClick={() => geminiCheck.mutate()}>{geminiCheck.isPending ? 'Проверяем Gemini…' : 'Проверить Gemini'}</button><Switch label="Автопроверка Gemini" checked={!!gemini.data?.enabled} disabled={busy || !gemini.data || (!gemini.data.enabled && !status.data?.controls_ready)} onChange={enabled => geminiToggle.mutate(enabled)} /></div></div>
      {status.data && !status.data.enabled && <p className="warp-inline-notice">Пока WARP выключен, автопроверка приостановлена.</p>}
      {gemini.isError && <p role="alert" className="warp-inline-error">Не удалось загрузить состояние автопроверки Gemini. <button onClick={() => gemini.refetch()}>Повторить</button></p>}
        {gemini.data?.result.message ? <div role="status" className={`warp-gemini-result border-l-2 ${resultBorder}`}>
          <div className="warp-result-summary"><p className="warp-result-title">{resultTitle}</p>{gemini.data.result.checked_at && <span className="warp-result-caption">{new Date(gemini.data.result.checked_at * 1000).toLocaleString('ru-RU')} · {gemini.data.node_name}</span>}</div>
          <details className="warp-result-details"><summary>Подробности результата <ChevronRight size={14} /></summary>
          <p>{gemini.data.result.message}</p>
          <p>Страна IP по Cloudflare: <b>{countryName ? `${countryName} (${countryCode})` : 'не определена'}</b></p>
          {result?.ip && <p>IP выхода: {result.ip}</p>}
          <p>WARP: {result?.warp === 'on' || result?.warp === 'plus' ? 'подтверждён' : result?.warp === 'off' ? 'выключен на проверочном выходе' : 'не проверен'}</p>
          <p className="warp-result-caption">Это страна IP по данным Cloudflare, а не регион аккаунта Google. Доступ к генерации проверяется в Gemini под вашим аккаунтом.</p>
          {gemini.data.result.rotation_note && <p>{gemini.data.result.rotation_note}</p>}
          </details>
        </div> : <p className="warp-gemini-no-result">Результатов пока нет. Запустите проверку вручную или включите автоматическую.</p>}
      <details className="warp-monitor-details"><summary><CircleHelp size={16} />Как работает проверка<ChevronRight size={16} /></summary>
        <p>После двух региональных отказов подряд переподключаю WARP. Между попытками — не менее 30 минут, не более 3 попыток за 6 часов для каждой ноды. Соединения через WARP могут прерваться; новый IP не гарантируется.</p>
        <p>Для трафика Gemini включите соответствующий пресет. CAPTCHA, запрос входа и ошибки сети не вызывают смену IP. Проверка без аккаунта Google не подтверждает доступ к генерации и не определяет страну аккаунта.</p>
        <p>Ручная проверка показывает результат и не переподключает WARP.</p>
      </details>
    </section>
  </div>;
}
