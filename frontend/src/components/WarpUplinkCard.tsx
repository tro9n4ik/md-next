import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { Activity, ArrowRight, CheckCircle2, Cloud, Download, Globe, Loader2, Network, Server } from 'lucide-react';
import Switch from './ui/Switch';
import { apiFetch } from '../utils/api';

interface UplinkState {
  enabled: boolean; ready: boolean; paused: boolean; preparing: boolean; can_prepare: boolean;
  node_id: number | null; node_name: string;
  job: {phase: string; message: string; installed?: boolean; finished_at?: number};
  check: {ok: boolean; message: string; checked_at: number} | null;
}

async function request(path = '', method = 'GET', body?: unknown) {
  const response = await apiFetch('/api/v1/warp/uplink' + path, {method,
    ...(body ? {headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)} : {})});
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || 'Не удалось изменить канал к нодам');
  return data;
}

export default function WarpUplinkCard({disabled, onChanged}: {disabled: boolean; onChanged: () => void}) {
  const client = useQueryClient();
  const [error, setError] = useState('');
  const state = useQuery<UplinkState>({queryKey: ['warp-uplink'], queryFn: () => request(),
    refetchInterval: query => query.state.data?.preparing ? 2000 : 15000});
  const value = state.data;
  const refresh = () => client.invalidateQueries({queryKey: ['warp-uplink']});
  const toggle = useMutation({mutationFn: (enabled: boolean) => request('', 'PUT', {enabled}),
    onSuccess: data => {setError(''); client.setQueryData(['warp-uplink'], data); onChanged();},
    onError: (cause: Error) => {setError(cause.message); refresh();}});
  const prepare = useMutation({mutationFn: () => request('/prepare', 'POST'),
    onSuccess: job => {setError(''); client.setQueryData<UplinkState>(['warp-uplink'], old => old ? {...old, preparing: true, job} : old); refresh();},
    onError: (cause: Error) => setError(cause.message)});
  const check = useMutation({mutationFn: () => request('/check', 'POST'),
    onSuccess: data => {setError(''); client.setQueryData<UplinkState>(['warp-uplink'], old => old ? {...old, check: data} : old); refresh();},
    onError: (cause: Error) => setError(cause.message)});
  const mutationBusy = disabled || toggle.isPending || prepare.isPending || check.isPending;
  const busy = mutationBusy || !!value?.preparing;
  const failedJob = value?.job.phase === 'failed' && !(value.check?.ok && value.check.checked_at > (value.job.finished_at || 0));
  const badge = state.isError ? 'Нет данных' : !value ? 'Загрузка…' : value.preparing ? 'Подготовка…'
    : value.paused ? 'Ожидает ноду' : value.enabled ? value.ready ? 'Включён' : 'Нет соединения' : value.ready ? 'Готов к включению' : 'Выключен';
  return <section data-autosave className="ui-card warp-uplink" aria-labelledby="warp-uplink-title">
    <div className="warp-uplink-heading">
      <div className="warp-section-heading"><span className="warp-section-icon"><Network size={21} /></span><div>
        <span className="warp-brand-label">ДОПОЛНИТЕЛЬНЫЙ ПУТЬ МЕЖДУ СЕРВЕРАМИ</span>
        <h2 id="warp-uplink-title">Канал к нодам через WARP</h2>
        <p>Другой путь для соединения основного сервера с подключёнными нодами</p>
      </div></div>
      <div className="warp-uplink-control"><span className={`warp-uplink-badge ${value?.enabled && value.ready ? 'is-enabled' : ''}`}>{badge}</span>
        <Switch label="Канал к нодам через WARP" checked={!!value?.enabled}
          disabled={mutationBusy || !value || state.isError || (!value.enabled && (value.preparing || !value.ready || !value.node_id))}
          onChange={enabled => toggle.mutate(enabled)} />
      </div>
    </div>

    <p className="warp-uplink-intro">Если прямое соединение между серверами замедляется или теряет пакеты, этот режим может улучшить работу YouTube, загрузок и других сервисов. Он меняет путь к нодам сразу для всех клиентов, чей трафик проходит через них.</p>
    <ol className="warp-uplink-route" aria-label="Маршрут при включённом канале WARP">
      {[{title: 'Основной сервер', subtitle: 'Принимает подключения', Icon: Server},
        {title: 'Локальный WARP', subtitle: 'Промежуточный канал', Icon: Cloud},
        {title: value?.node_id ? value.node_name : 'Выбранная нода', subtitle: 'Прежняя точка выхода', Icon: Network},
        {title: 'Интернет', subtitle: 'Сайты и приложения', Icon: Globe}].map(({title, subtitle, Icon}, index) =>
        <li key={index}><div><Icon size={19} /><span><b>{title}</b><small>{subtitle}</small></span></div>{index < 3 && <ArrowRight size={17} aria-hidden="true" />}</li>)}
    </ol>

    <div className="warp-uplink-explanation">
      <div><h3>Выход остаётся на выбранной ноде</h3><p>WARP на основном сервере используется как промежуточный участок. Нода, клиентские ссылки и правила выхода для сервисов сохраняются. Страна промежуточного WARP не становится страной выхода клиента.</p></div>
      <div><h3>Отдельно от пресетов сервисов</h3><p>Этот переключатель меняет канал всех нод, включая резервные. Верхний переключатель WARP и пресеты выбирают, какие сервисы используют Cloudflare уже на выходной ноде.</p></div>
      <div><h3>Ускорение зависит от сети</h3><p>Режим полезен при плохом прямом канале, но не гарантирует увеличение скорости. Проверяйте загрузку и видео до и после включения. Прямой трафик основного сервера и роутера этим режимом не меняется.</p></div>
      <div><h3>Проверки следуют за трафиком</h3><p>Доступность нод и управление их WARP проверяются через тот же путь. При отказе промежуточного WARP действуют настройки резервирования из раздела «Узлы».</p></div>
    </div>

    {state.isError && <div role="alert" className="warp-inline-error">Не удалось получить состояние канала. <button onClick={() => refresh()}>Повторить</button></div>}
    {error && <p role="alert" className="warp-inline-error">{error}</p>}
    {value?.preparing && <div role="status" className="warp-uplink-progress"><Loader2 size={18} className="animate-spin" /><div><b>{value.job.message}</b><p>Подготовка продолжится, даже если закрыть страницу. После завершения включите канал переключателем.</p></div></div>}
    {failedJob && <p role="alert" className="warp-inline-error">{value.job.message}</p>}
    {value?.check && <div role="status" className={`warp-uplink-check ${value.check.ok ? 'is-success' : ''}`}>
      {value.check.ok && <CheckCircle2 size={17} />}<div><p>{value.check.message}</p><small>{new Date(value.check.checked_at * 1000).toLocaleString('ru-RU')}</small></div>
    </div>}
    {value?.paused && <p className="warp-inline-notice">Выбран прямой выход. Настройка канала сохранена и будет применяться после выбора ноды.</p>}

    <footer className="warp-uplink-footer"><div>
      {!value?.ready ? <><h3>Сначала подготовьте локальное подключение</h3><p>Панель установит клиент из официального репозитория Cloudflare, зарегистрирует WARP через выбранную ноду и подключит локальный прокси. Лицензия WARP+ не требуется. Автоподготовка доступна на Ubuntu 22.04, 24.04 и 26.04 и Debian 12–13.</p></>
        : <><h3>Изменения сохраняются автоматически</h3><p>Перед включением проверяем WARP и выход через ноду. При переключении соединения могут кратковременно переподключиться. Отключение возвращает обычный канал и сохраняет регистрацию.</p></>}
      {value && !value.node_id && <Link to="/nodes" className="warp-text-link">Выбрать активную ноду <ArrowRight size={14} /></Link>}
    </div><div className="warp-uplink-buttons">
      {value && (!value.ready || failedJob || value.check?.ok === false || value.job.installed === false || !!error) && <button className="ui-button ui-button-primary" disabled={busy || !value.can_prepare} onClick={() => prepare.mutate()}><Download size={16} />{prepare.isPending ? 'Запускаем…' : failedJob ? 'Повторить подготовку' : 'Подготовить WARP'}</button>}
      <button className="ui-button ui-button-secondary" disabled={busy || !value?.ready || !value.node_id} onClick={() => check.mutate()}>{check.isPending ? <Loader2 size={16} className="animate-spin" /> : <Activity size={16} />}{check.isPending ? 'Проверяем…' : 'Проверить канал'}</button>
    </div></footer>
  </section>;
}
