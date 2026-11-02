import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { ArrowRight, Check, Globe2, Network, Pin, RefreshCw, ShieldCheck } from 'lucide-react';
import { exitRequest as read } from '../utils/clientExit';
import type { ExitNode, ExitRoute, CommonExit, ExitOptions, RouteData } from '../utils/clientExit';
import CountryFlag from './ui/CountryFlag';

const statusText = (node: ExitNode) => !node.selectable ? 'Выключена или не настроена' : node.status === 'healthy'
  ? `Доступна${node.ping_ms != null && node.ping_ms > 0 ? ` · ${node.ping_ms} мс` : ''}` : 'Проверка связи не прошла';
const commonName = (common: CommonExit) => common.node?.name || (common.mode === 'direct' ? 'Прямой выход панели' : 'Выход недоступен');

export function ExitRouteBadge({ route }: { route?: ExitRoute }) {
  if (!route) return null;
  const bad = !['healthy', 'direct'].includes(route.status);
  return <span className={`client-exit-badge ${bad ? 'is-unavailable' : ''}`} title={route.mode === 'node'
    ? 'Закреплённая нода. Общая смена выхода не меняет этот маршрут.' : 'Следует общему маршруту панели.'}>
    {route.mode === 'node' ? <Pin size={12} /> : <Network size={12} />}
    {route.node?.country_code && <CountryFlag code={route.node.country_code} />}
    <span>{route.mode === 'common' ? 'Общий' : 'Своя нода'} · {route.node?.name || (route.status === 'direct' ? 'напрямую' : 'недоступна')}</span>
    <i aria-label={bad ? 'Выход недоступен' : 'Выход доступен'} />
  </span>;
}

export function ExitNodeSelect({ options, value, onChange, disabled = false }: {
  options: ExitOptions; value: number | null; onChange: (id: number | null) => void; disabled?: boolean;
}) {
  return <div className="client-create-exit">
    <label>Выход в интернет<select aria-label="Выход в интернет" value={value ?? ''} disabled={disabled}
      onChange={event => onChange(event.target.value ? Number(event.target.value) : null)}>
      <option value="">Общий маршрут · {commonName(options.common)}</option>
      {options.nodes.map(node => <option key={node.id} value={node.id} disabled={!node.selectable}>
        {node.country_code ? `${node.country_code} · ` : ''}{node.name} · закреплено: {node.assigned_clients ?? 0}{!node.selectable ? ' · выключена' : node.status !== 'healthy' ? ' · нет связи' : ''}
      </option>)}
    </select></label>
    <p className="client-form-hint">Можно оставить общий выход или закрепить клиента за нодой. Подробная настройка — в карточке клиента.</p>
  </div>;
}

export default function ClientExitRoute({ clientId, clientName }: { clientId: number; clientName: string }) {
  const queryClient = useQueryClient();
  const queryKey = ['clientExitRoute', clientId];
  const query = useQuery<RouteData>({ queryKey, queryFn: () => read(`/api/v1/clients/${clientId}/exit-route`), refetchInterval: 15000 });
  const [draft, setDraft] = useState<number | null | undefined>(undefined);
  const [notice, setNotice] = useState('');
  const section = useRef<HTMLElement>(null);
  const saved = query.data?.route.node_id ?? null;
  const selected = draft === undefined ? saved : draft;
  const dirty = selected !== saved;
  useEffect(() => {
    const scope = section.current;
    const notify = (value: boolean) => window.dispatchEvent(new CustomEvent('ui:unsaved-change', { detail: { scope, dirty: value } }));
    notify(dirty);
    return () => { notify(false); };
  }, [dirty]);
  const mutation = useMutation({
    mutationFn: () => read<ExitRoute>(`/api/v1/clients/${clientId}/exit-route`, { method: 'PUT',
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ node_id: selected }) }),
    onSuccess: async route => {
      queryClient.setQueryData<RouteData>(queryKey, previous => previous ? { ...previous, route } : previous);
      setDraft(undefined);
      setNotice('Маршрут сохранён. Ссылка подписки прежняя; для уже открытых соединений переподключите VPN.');
      await Promise.all([queryClient.invalidateQueries({ queryKey }), queryClient.invalidateQueries({ queryKey: ['clients'] }),
        queryClient.invalidateQueries({ queryKey: ['clientExitOptions'] })]);
    },
    onError: (error: Error) => setNotice(error.message),
  });
  const change = (value: number | null) => { setDraft(value); setNotice(''); };
  const data = query.data;
  const chosen = selected === null ? data?.common.node : data?.nodes.find(node => node.id === selected);
  const unavailable = selected !== null && (!chosen?.selectable || chosen.status !== 'healthy');
  const firstNode = data?.nodes.find(node => node.selectable);

  return <section id="client-exit-route" ref={section} data-unsaved-controlled className="ui-card client-exit-editor" aria-labelledby="client-exit-title">
    <header className="client-exit-heading"><span className="client-exit-icon"><Network size={22} /></span><div>
      <span className="client-exit-eyebrow">Персональный маршрут</span><h2 id="client-exit-title">Выход в интернет</h2>
      <p>Распределяйте клиентов между нодами и выбирайте страну выхода для каждого.</p>
    </div>{data && <span className="client-exit-saved">{dirty ? <Pin size={13} /> : <Check size={13} />}{dirty ? 'Есть изменения' : 'Сохранено'}</span>}</header>
    {!data ? <div className="client-exit-loading" role="status">{query.isError ? query.error.message : 'Загрузка маршрутов…'}
      {query.isError && <button className="ui-button ui-button-secondary" onClick={() => query.refetch()}><RefreshCw size={15} />Повторить</button>}</div> : <>
      {query.isError && <p role="alert" className="client-exit-warning">Не удалось обновить состояние нод. <button onClick={() => query.refetch()}>Повторить</button></p>}
      <div className="client-exit-modes" role="group" aria-label="Способ выбора выхода">
        <button type="button" aria-pressed={selected === null} className={selected === null ? 'selected' : ''} disabled={mutation.isPending} onClick={() => change(null)}>
          <Network size={20} /><span><b>Общий маршрут</b><small>Следует выбору в разделе «Узлы»</small></span><span className="client-exit-radio">{selected === null && <Check size={12} />}</span>
        </button>
        <button type="button" aria-pressed={selected !== null} className={selected !== null ? 'selected' : ''} disabled={mutation.isPending || (!firstNode && selected === null)}
          onClick={() => { if (selected === null && firstNode) change(firstNode.id); }}>
          <Pin size={20} /><span><b>Своя нода</b><small>Сохраняет выход при общей смене ноды</small></span><span className="client-exit-radio">{selected !== null && <Check size={12} />}</span>
        </button>
      </div>
      {selected === null ? <div className="client-exit-common"><Globe2 size={21} /><div><b>Сейчас: {commonName(data.common)}</b>
        <p>Клиент использует общие правила маршрутизации, WARP и резервирование панели.</p></div>{data.common.node?.country_code && <CountryFlag code={data.common.node.country_code} />}</div> : <>
        <div className="client-exit-nodes-heading"><h3>Нода клиента</h3><span>Количество подписок помогает распределять клиентов. Это не замер нагрузки.</span></div>
        <div className="client-exit-nodes" role="group" aria-label="Нода клиента">{data.nodes.map(node => <button key={node.id} type="button"
          className={`client-exit-node ${selected === node.id ? 'selected' : ''}`} aria-pressed={selected === node.id}
          disabled={mutation.isPending || (!node.selectable && selected !== node.id)} onClick={() => change(node.id)}>
          <span className="client-exit-node-title"><CountryFlag code={node.country_code} /><b>{node.name}</b><span className="client-exit-radio">{selected === node.id && <Check size={12} />}</span></span>
          <span className={`client-exit-health ${node.selectable && node.status === 'healthy' ? 'healthy' : ''}`}><i />{statusText(node)}</span>
          <span className="client-exit-counts"><span>Закреплено <b>{node.assigned_clients ?? 0}</b></span><span>По общему <b>{node.following_clients ?? 0}</b></span></span>
        </button>)}</div>
        {unavailable && <p className="client-exit-warning" role="alert">Выбранная нода недоступна. Трафик не перейдёт на общий или прямой выход. Выберите другую ноду или дождитесь восстановления связи.</p>}
        <div className="client-exit-path" aria-label="Путь трафика"><span>{clientName}</span><ArrowRight size={15} /><span>Панель</span><ArrowRight size={15} /><strong>{chosen?.name || 'Нода недоступна'}</strong></div>
        <p className="client-exit-policy"><ShieldCheck size={17} /><span>Этот выход имеет приоритет над общими правилами маршрутизации и выходом WARP. Блокировка рекламы и запрещённые направления сохраняются. Промежуточный канал к нодам через WARP продолжает работать, если включён.</span></p>
      </>}
      {!data.nodes.some(node => node.selectable) && <p className="client-exit-warning">Для индивидуального выхода сначала подключите и настройте ноду в разделе «Узлы».</p>}
      <div className="client-exit-details"><p>Работает для VLESS, XHTTP, Hysteria 2, «Обхода БС» и AmneziaWG. Адрес панели, ключи, лимиты и ссылка подписки сохраняются. Telegram MTProto использует общий выход.</p>
        <p>Нода определяет выход только для трафика, который клиент отправляет через VPN. Доступность сервисов также зависит от адреса выхода и региона аккаунта. <Link to="/wiki?article=client-exit">Подробнее в Wiki</Link></p></div>
      <div className="ui-actionbar client-exit-actions"><p role={mutation.isError ? 'alert' : 'status'} className={mutation.isError ? 'client-exit-error' : ''}>{notice || 'Смена выхода применяется на сервере. Уже открытые соединения могут прерваться.'}</p>
        <button className="ui-button ui-button-primary" disabled={!dirty || mutation.isPending || (selected !== null && !chosen?.selectable)} onClick={() => mutation.mutate()}>{mutation.isPending ? 'Применяем…' : 'Сохранить маршрут'}</button></div>
    </>}
  </section>;
}
