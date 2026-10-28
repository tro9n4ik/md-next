import Switch from './ui/Switch';
import React, { useEffect, useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Copy, Check, Plus, Trash2, AlertTriangle, ArrowRight, ArrowUp, ArrowDown, RefreshCw, Server, ShieldCheck, Users, Globe, ChevronRight, KeyRound } from 'lucide-react';
import CountryFlag from './ui/CountryFlag';
import Drawer from './ui/Drawer';
import { apiFetch } from '../utils/api';

interface NodeData {
  id: number;
  name: string;
  host: string;
  port: number;
  protocol: string;
  is_active: boolean;
  is_enabled: boolean;
  status: string;
  priority: number;
  ping_ms: number;
  last_seen?: string;
  country_code?: string | null;
}

interface FailoverSettings {
  mode: 'manual' | 'auto';
  ping_threshold_ms: number;
  failure_count: number;
  interval_s: number;
  failback: boolean;
  failback_stable_checks: number;
  cooldown_s: number;
  fallback_action: 'direct' | 'keep';
}

interface RouteData {
  server: { hostname: string; public_ip: string };
  vpn: { xray: boolean; awg: boolean };
  active_node: { id: number; name: string; ip: string; ping_ms: number; status: string } | null;
  manual_direct: boolean;
  next_candidate: { id: number; name: string; ip: string; ping_ms: number; status: string } | null;
  failover_mode: string;
  fallback_action: string;
}

interface RouteCheckResult {
  active_node: { id: number; name: string; host: string } | null;
  manual_direct: boolean;
  exit_ip: string;
  country?: string | null;
  warp?: string | null;
  checked_at: string;
}

interface NodeInviteItem {
  id: number;
  name: string;
  created_at: string;
  expires_at: string;
  used_at?: string;
  revoked: boolean;
}

interface GeneratedInvite {
  id: number;
  name: string;
  token: string;
  expires_at: string;
}

const RouteStep: React.FC<{ title: string; detail: string; state: 'ok' | 'warn' | 'bad' | 'wait' }> = ({ title, detail, state }) => {
  const colors = {
    ok: 'border-emerald-200 bg-emerald-50 text-emerald-700',
    warn: 'border-amber-200 bg-amber-50 text-amber-700',
    bad: 'border-red-200 bg-red-50 text-red-700',
    wait: 'border-neutral-200 bg-neutral-50 text-neutral-500',
  };
  return <div className={`node-route-step min-w-0 flex-1 rounded-xl border px-3 py-3 ${colors[state]}`}>
    <div className="flex items-center gap-2 text-xs font-bold"><span className="h-2 w-2 shrink-0 rounded-full bg-current" />{title}</div>
    <div className="mt-1 break-words text-[11px] opacity-80">{detail}</div>
  </div>;
};

const NumberField: React.FC<{ label: string; value: number; onChange: (value: number) => void; hint?: string; min?: number; max?: number }> = ({ label, value, onChange, hint, min = 0, max }) => (
  <label className="text-xs font-medium text-neutral-600">{label}
    <input required type="number" min={min} max={max} value={value} onChange={event => onChange(Number(event.target.value))} className="mt-1 w-full rounded-lg border border-neutral-200 p-2 text-sm" />
    {hint && <span className="mt-1 block text-[10px] font-normal text-neutral-400">{hint}</span>}
  </label>
);

export const NodesManager: React.FC = () => {
  const queryClient = useQueryClient();
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState('');
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [inviteName, setFormInviteName] = useState('Новый узел');
  const [ttlHours, setFormTtlHours] = useState(24);
  const [generatedInvite, setGeneratedInvite] = useState<GeneratedInvite | null>(null);
  const [inviteCleanupNotice, setInviteCleanupNotice] = useState('');

  const [nodeToDelete, setNodeToDelete] = useState<NodeData | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [clusterError, setClusterError] = useState<string | null>(null);
  const [failoverNotice, setFailoverNotice] = useState('');
  const [failoverDraft, setFailoverForm] = useState<FailoverSettings | null>(null);
  const [routeCheckState, setRouteCheckState] = useState<{ key: string; result: RouteCheckResult } | null>(null);
  const [now, setNow] = useState(Date.now);

  const { data: nodes = [], isPending: nodesLoading, isError: nodesError, refetch: reloadNodes } = useQuery<NodeData[]>({
    queryKey: ['nodes'],
    queryFn: async () => {
      const res = await apiFetch('/api/v1/nodes');
      if (!res.ok) throw new Error('Не удалось загрузить список узлов');
      return res.json();
    },
    refetchInterval: 15000
  });

  const { data: route, isError: routeError, refetch: reloadRoute } = useQuery<RouteData>({
    queryKey: ['clusterRoute'],
    queryFn: async () => {
      const res = await apiFetch('/api/v1/cluster/route');
      if (!res.ok) throw new Error('Не удалось загрузить схему маршрута');
      return res.json();
    },
    refetchInterval: 15000,
  });

  const { data: failoverSettings, isError: failoverError, refetch: reloadFailover } = useQuery<FailoverSettings>({
    queryKey: ['clusterFailover'],
    queryFn: async () => {
      const res = await apiFetch('/api/v1/cluster/failover');
      if (!res.ok) throw new Error('Не удалось загрузить настройки автопереключения');
      return res.json();
    },
  });

  const failoverForm = failoverDraft ?? failoverSettings;
  const routeKey = `${route?.active_node?.id}:${route?.manual_direct}`;
  const routeCheck = routeCheckState?.key === routeKey ? routeCheckState.result : null;
  const setRouteCheck = (result: RouteCheckResult | null) => setRouteCheckState(result ? { key: `${result.active_node?.id}:${result.manual_direct}`, result } : null);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  const activeNodeMutation = useMutation({
    mutationFn: async (nodeId: number | null) => {
      const res = await apiFetch('/api/v1/cluster/active-node', {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ node_id: nodeId }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail || 'Не удалось переключить маршрут');
      return body;
    },
    onSuccess: () => {
      setClusterError(null);
      setRouteCheck(null);
      queryClient.invalidateQueries({ queryKey: ['clusterRoute'] });
      queryClient.invalidateQueries({ queryKey: ['nodes'] });
      queryClient.invalidateQueries({ queryKey: ['warp-target'] });
      queryClient.invalidateQueries({ queryKey: ['warp-status'] });
      queryClient.invalidateQueries({ queryKey: ['telegramSettings'] });
    },
    onError: (error: Error) => setClusterError(error.message),
  });

  const routeCheckMutation = useMutation({
    mutationFn: async () => {
      const res = await apiFetch('/api/v1/cluster/route/check', { method: 'POST' });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail || 'Не удалось проверить фактический выход');
      return body as RouteCheckResult;
    },
    onSuccess: result => { setRouteCheck(result); setClusterError(null); },
    onError: (error: Error) => setClusterError(error.message),
  });

  const reorderMutation = useMutation({
    mutationFn: async (ids: number[]) => {
      const res = await apiFetch('/api/v1/nodes/reorder', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ids }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail || 'Не удалось изменить приоритет нод');
    },
    onSuccess: () => { void queryClient.invalidateQueries({ queryKey: ['nodes'] }); void queryClient.invalidateQueries({ queryKey: ['clusterRoute'] }); },
    onError: (error: Error) => setClusterError(error.message),
  });

  const failoverMutation = useMutation({
    onMutate: () => setFailoverNotice(''),
    mutationFn: async (settings: FailoverSettings) => {
      const res = await apiFetch('/api/v1/cluster/failover', {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(settings),
      }, 'nodes-failover-form');
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail || 'Не удалось сохранить настройки');
      return body as FailoverSettings;
    },
    onSuccess: (data) => {
      setFailoverForm(data);
      setFailoverNotice('Настройки сохранены.');
      setClusterError(null);
      queryClient.invalidateQueries({ queryKey: ['clusterFailover'] });
      queryClient.invalidateQueries({ queryKey: ['clusterRoute'] });
    },
    onError: (error: Error) => setClusterError(error.message),
  });

  const { data: invites = [], isPending: invitesLoading, isError: invitesError, refetch: reloadInvites } = useQuery<NodeInviteItem[]>({
    queryKey: ['nodeInvites'],
    queryFn: async () => {
      const res = await apiFetch('/api/v1/nodes/invites');
      if (!res.ok) throw new Error('Не удалось загрузить приглашения');
      return res.json();
    }
  });

  const createInviteMutation = useMutation({
    mutationFn: async (payload: { name: string; ttl_hours: number }) => {
      const res = await apiFetch('/api/v1/nodes/invites', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      if (!res.ok) throw new Error('Не удалось создать инвайт');
      return res.json();
    },
    onSuccess: (data: GeneratedInvite) => {
      queryClient.invalidateQueries({ queryKey: ['nodeInvites'] });
      setGeneratedInvite(data);
    }
  });

  const revokeInviteMutation = useMutation({
    mutationFn: async (id: number) => {
      const res = await apiFetch(`/api/v1/nodes/invites/${id}`, { method: 'DELETE' });
      if (!res.ok) throw new Error('Не удалось отозвать инвайт');
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['nodeInvites'] })
  });

  const clearInviteHistoryMutation = useMutation({
    mutationFn: async () => {
      const res = await apiFetch('/api/v1/nodes/invites/history', { method: 'DELETE' });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Не удалось очистить историю приглашений');
      return data as { deleted: number };
    },
    onMutate: () => setInviteCleanupNotice(''),
    onSuccess: (data) => {
      setInviteCleanupNotice(data.deleted ? `Удалено приглашений: ${data.deleted}. Действующие приглашения сохранены.` : 'Нет завершённых приглашений для удаления. Действующие приглашения сохранены.');
      void queryClient.invalidateQueries({ queryKey: ['nodeInvites'] });
    },
    onError: (error: Error) => setInviteCleanupNotice(error.message),
  });

  const deleteNodeMutation = useMutation({
    mutationFn: async (id: number) => {
      setDeleteError(null);
      const res = await apiFetch(`/api/v1/nodes/${id}`, { method: 'DELETE' });
      if (!res.ok) {
        let errMessage = 'Не удалось удалить ноду';
        try {
          const data = await res.json();
          if (data.detail) errMessage = data.detail;
        } catch {}
        throw new Error(errMessage);
      }
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['nodes'] });
      queryClient.invalidateQueries({ queryKey: ['clusterRoute'] });
      setNodeToDelete(null);
      setDeleteError(null);
    },
    onError: (err: Error) => {
      setDeleteError(err.message);
    }
  });

  const joinCommand = generatedInvite
    ? `curl -sSL ${window.location.origin}/api/v1/nodes/join?token=${generatedInvite.token} | bash`
    : '';

  const handleCopyCommand = async () => {
    if (!joinCommand) return;
    try {
      await navigator.clipboard.writeText(joinCommand);
      setCopyError(''); setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch { setCopyError('Не удалось скопировать. Выделите команду и скопируйте вручную.'); }
  };

  const handleCloseModal = () => {
    setCopied(false); setCopyError(''); createInviteMutation.reset();
    setCreateModalOpen(false);
    setGeneratedInvite(null);
    setFormInviteName('Новый узел');
    setFormTtlHours(24);
  };

  const activeCountry = nodes.find(node => node.id === route?.active_node?.id)?.country_code;
  const activeInvites = invites.filter(invite => !invite.revoked && !invite.used_at && new Date(invite.expires_at).getTime() > now).length;
  const draftInvite = !generatedInvite && (inviteName !== 'Новый узел' || ttlHours !== 24);
  const closeInvite = () => { if (!draftInvite || window.confirm('Закрыть форму без создания приглашения?')) handleCloseModal(); };

  return <div className="nodes-workspace">
    <header className="dashboard-heading"><div><div className="dashboard-eyebrow">ВАША СЕТЬ БЕЗ ПЕРЕРЫВОВ</div><h1>Узлы</h1><p>Серверы, активный выход и резервирование</p></div><div className="page-actions"><button className="ui-button ui-button-secondary" onClick={() => { void reloadNodes(); void reloadRoute(); void reloadFailover(); }} disabled={nodesLoading}><RefreshCw size={16} />Обновить</button><button onClick={() => { createInviteMutation.reset(); setCreateModalOpen(true); }} className="ui-button ui-button-primary"><Plus size={17} />Добавить ноду</button></div></header>
    <div className="nodes-summary">
      <section className="node-summary-card"><span className="node-summary-icon"><Server size={21} /></span><div><h2>Всего узлов</h2><b>{nodesError || nodesLoading ? '—' : nodes.length}</b><p>Подключённые серверы</p></div></section>
      <section className="node-summary-card"><span className="node-summary-icon"><Globe size={21} /></span><div><h2>Доступны</h2><b>{nodesError || nodesLoading ? '—' : <>{nodes.filter(node => node.is_enabled && node.is_active && node.status === 'healthy').length}<span> / {nodes.length}</span></>}</b><p>{nodesError ? 'Данные недоступны' : 'По последней проверке'}</p></div></section>
      <section className="node-summary-card"><span className="node-summary-icon"><ShieldCheck size={21} /></span><div><h2>Автопереключение</h2><b className="node-mode-label">{failoverError ? 'Нет данных' : failoverSettings ? failoverSettings.mode === 'auto' ? 'Включено' : 'Ручной режим' : 'Загрузка…'}</b><p>{route?.next_candidate ? `Резерв: ${route.next_candidate.name}` : 'Резерв не выбран'}</p></div></section>
    </div>
    <section className="ui-card ui-panel node-route-card">
      <div className="node-section-heading"><div><span className="node-section-icon"><ArrowRight size={20} /></span><h2>Текущий маршрут</h2></div><button className="ui-button ui-button-secondary" disabled={!route || routeCheckMutation.isPending} onClick={() => routeCheckMutation.mutate()}>{routeCheckMutation.isPending ? 'Проверка…' : 'Проверить внешний IP'}</button></div>
      <div className="node-current-route">
        <div className="node-route-stop"><span className="node-stop-icon"><Users size={22} /></span><div><b>Клиенты</b><small>VPN-подключения</small></div></div><ArrowRight className="node-flow-arrow" size={20} />
        <div className="node-route-stop"><span className="node-stop-icon"><Server size={22} /></span><div><b>Основной сервер</b><small>{route?.server.hostname || 'Нет данных'}</small></div></div><ArrowRight className="node-flow-arrow" size={20} />
        <div className="node-route-stop node-route-stop-active"><span className="node-stop-icon">{route?.active_node && activeCountry ? <CountryFlag code={activeCountry} /> : <Globe size={22} />}</span><div><small>Текущий выход</small><b>{route ? route.active_node?.name || 'Прямой выход' : routeError ? 'Нет данных' : 'Загрузка…'}</b><small>{route?.active_node ? `${route.active_node.status === 'healthy' ? 'Доступна' : 'Недоступна'} · ${route.active_node.ping_ms > 0 ? `${route.active_node.ping_ms} мс` : 'Задержка неизвестна'}` : route ? 'Через сервер панели' : 'Получаем маршрут'}</small></div></div>
      </div>
      <div className="node-route-caption"><span>{route?.active_node ? route.active_node.ip : route ? route.server.public_ip || route.server.hostname : 'Ожидание данных'}</span><span>{route ? route.failover_mode === 'auto' ? 'Автопереключение включено' : 'Ручной режим' : 'Получаем маршрут'}</span></div>
      {routeError && <p role="alert" className="mt-3 text-sm text-red-700">Не удалось загрузить маршрут. <button className="underline" onClick={() => reloadRoute()}>Повторить</button></p>}
      {route && !route.vpn.xray && <p role="alert" className="mt-3 text-sm text-red-700">Xray не работает. Проверьте состояние сервиса.</p>}
      {route?.manual_direct && <p className="mt-3 text-xs text-amber-700">Прямой выход выбран вручную. Автоматика не заменит его нодой.</p>}
      {routeCheck && <p role="status" className="mt-4 border-l-2 border-emerald-600 pl-3 text-sm">Внешний IP через Xray: <b>{routeCheck.exit_ip}</b>{routeCheck.country ? ` · ${routeCheck.country}` : ''}<span className="mt-1 block text-xs text-neutral-500">{new Date(routeCheck.checked_at).toLocaleString('ru-RU')} · {routeCheck.manual_direct ? 'Прямой выход' : routeCheck.active_node?.name || 'Прямой выход'}</span></p>}
      <details className="mt-4 border-t border-neutral-200 pt-4"><summary className="cursor-pointer text-sm font-medium">Схема подключения и прямой выход</summary>
        <div className="mt-4 flex flex-col gap-2 md:flex-row md:items-stretch">
          <RouteStep title="Клиент" detail="VPN-профиль" state="wait" /><ArrowRight className="hidden self-center text-neutral-300 md:block" />
          <RouteStep title="Этот сервер" detail={route?.server.hostname || 'Нет данных'} state={route ? 'ok' : 'wait'} /><ArrowRight className="hidden self-center text-neutral-300 md:block" />
          <RouteStep title="VPN-уровень" detail={`Xray ${route?.vpn.xray ? 'работает' : 'нет'} · AWG ${route?.vpn.awg ? 'работает' : 'нет'}`} state={!route ? 'wait' : route.vpn.xray ? 'ok' : 'bad'} /><ArrowRight className="hidden self-center text-neutral-300 md:block" />
          <RouteStep title={route?.active_node ? 'Нода выхода' : 'Прямой выход'} detail={route?.active_node?.name || 'Через сервер панели'} state={!route ? 'wait' : route.active_node ? route.active_node.status === 'healthy' ? 'ok' : 'bad' : 'warn'} /><ArrowRight className="hidden self-center text-neutral-300 md:block" />
          <RouteStep title="Интернет" detail={routeCheck ? 'Выход Xray проверен' : 'Выход Xray не проверен'} state={routeCheck ? 'ok' : 'wait'} />
        </div>
        <p className="mt-3 text-xs leading-5 text-neutral-500">Схема показывает настроенный маршрут. Выбранная нода используется Xray и AmneziaWG; правила и WARP применяются к обоим типам подключения. Проверка внешнего IP выполняется с сервера через Xray и не заменяет проверку VPN на устройстве.</p>
        {route?.next_candidate && <p className="mt-2 text-xs text-neutral-500">Следующий кандидат: {route.next_candidate.name} · {route.next_candidate.ip} · {route.next_candidate.ping_ms} мс</p>}
        <button className="ui-button ui-button-secondary mt-4" onClick={() => activeNodeMutation.mutate(null)} disabled={activeNodeMutation.isPending || !route?.active_node}>Прямой выход</button>
      </details>
    </section>

    {clusterError && <p role="alert" className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-700">{clusterError}</p>}

    <section className="ui-card ui-panel node-list-card">
      <div className="node-section-heading"><div><span className="node-section-icon"><Server size={20} /></span><h2>Подключённые узлы <span className="node-count">{nodes.length}</span></h2></div><span className="node-priority-hint">Порядок определяет приоритет резервирования</span></div>
      {nodesError && <p role="alert" className="mb-3 text-sm text-red-700">Не удалось загрузить узлы. <button className="underline" onClick={() => reloadNodes()}>Повторить</button></p>}
      {nodesLoading && <p className="text-sm text-neutral-500">Загрузка нод…</p>}
      {!nodesLoading && !nodesError && !nodes.length && <p className="py-5 text-sm text-neutral-500">Узлы ещё не подключены. Добавьте ноду по одноразовому приглашению.</p>}
      <div className="node-list-columns" aria-hidden="true"><span>Узел и состояние</span><span>Адрес</span><span>Задержка</span><span>Приоритет и действия</span></div>
      <ul aria-label="Список нод" className="node-list">{nodes.map((node, index) => <li key={node.id} className={`node-list-row ${route?.active_node?.id === node.id ? 'node-list-row-active' : ''}`}>
        <div className="col-span-2 min-w-0 md:col-span-1"><div className="flex flex-wrap items-center gap-2"><CountryFlag code={node.country_code} /><h3 className="break-all text-sm font-semibold">{node.name}</h3>{route?.active_node?.id === node.id && <span className="text-xs font-medium text-emerald-700">Активная</span>}</div><p className={`mt-1 text-xs ${node.status === 'healthy' ? 'text-emerald-700' : node.status === 'disabled' ? 'text-neutral-500' : 'text-red-700'}`}>{node.status === 'healthy' ? 'Доступна' : node.status === 'disabled' ? 'Выключена' : 'Недоступна'}</p></div>
        <div className="min-w-0"><p className="break-all font-mono text-xs text-neutral-700">{node.host}</p><p className="mt-1 text-xs text-neutral-500">{node.port} · {node.protocol.toUpperCase()}</p></div>
        <p className="text-right text-xs text-neutral-500">{node.ping_ms ? `${node.ping_ms} мс` : '—'}</p>
        <div className="col-span-2 flex flex-wrap items-center gap-1.5 md:col-span-1 md:grid md:grid-cols-[2rem_2.25rem_2.25rem_9rem_2.25rem] md:items-center"><span className="mr-1 text-xs text-neutral-400">#{node.priority + 1}</span>
          <button aria-label={`Поднять приоритет: ${node.name}`} disabled={index === 0 || reorderMutation.isPending} onClick={() => { const ids = nodes.map(item => item.id); [ids[index - 1], ids[index]] = [ids[index], ids[index - 1]]; reorderMutation.mutate(ids); }} className="ui-button ui-button-secondary !min-h-9 !p-2"><ArrowUp size={16} /></button>
          <button aria-label={`Опустить приоритет: ${node.name}`} disabled={index === nodes.length - 1 || reorderMutation.isPending} onClick={() => { const ids = nodes.map(item => item.id); [ids[index + 1], ids[index]] = [ids[index], ids[index + 1]]; reorderMutation.mutate(ids); }} className="ui-button ui-button-secondary !min-h-9 !p-2"><ArrowDown size={16} /></button>
          {route?.active_node?.id === node.id ? <span className="node-active-pill">Активная</span> : <button aria-label={`Сделать активной: ${node.name}`} onClick={() => activeNodeMutation.mutate(node.id)} disabled={!node.is_enabled || node.status !== 'healthy' || activeNodeMutation.isPending || !route} className="ui-button ui-button-secondary !min-h-9 !px-3 !py-1 text-xs">Сделать активной</button>}
          <button aria-label={`Удалить ноду: ${node.name}`} onClick={() => { setNodeToDelete(node); setDeleteError(null); }} className="ui-button ui-button-secondary !min-h-9 !p-2 text-neutral-500"><Trash2 size={16} /></button>
        </div>
      </li>)}</ul>
    </section>

    <details className="ui-card ui-panel node-accordion"><summary><span className="node-section-icon"><ShieldCheck size={20} /></span><span className="node-accordion-title">Автопереключение <small>{failoverError ? 'Ошибка загрузки' : failoverSettings ? failoverSettings.mode === 'auto' ? 'Включено' : 'Ручной режим' : 'Нет данных'}</small></span><ChevronRight size={18} className="node-accordion-chevron" /></summary>
      {failoverError && <p role="alert" className="mt-4 text-sm text-red-700">Не удалось загрузить настройки. <button className="underline" onClick={() => reloadFailover()}>Повторить</button></p>}
      {failoverForm && <form id="nodes-failover-form" data-editable className="mt-5 space-y-4" onSubmit={event => { event.preventDefault(); failoverMutation.mutate(failoverForm); }}>
        <fieldset disabled={failoverMutation.isPending} className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <label className="text-xs font-medium text-neutral-600">Режим<select className="mt-1 w-full rounded-lg border p-2 text-sm" value={failoverForm.mode} onChange={e => setFailoverForm({ ...failoverForm, mode: e.target.value as FailoverSettings['mode'] })}><option value="manual">Ручной</option><option value="auto">Автоматический</option></select></label>
          <NumberField label="Порог задержки, мс" value={failoverForm.ping_threshold_ms} min={1} max={60000} onChange={value => setFailoverForm({...failoverForm, ping_threshold_ms: value})} />
          <NumberField label="Сбоев подряд" value={failoverForm.failure_count} min={1} max={20} onChange={value => setFailoverForm({...failoverForm, failure_count: value})} />
          <NumberField label="Проверка каждые, сек" value={failoverForm.interval_s} min={5} max={3600} onChange={value => setFailoverForm({...failoverForm, interval_s: value})} />
          <NumberField label="Стабильных проверок для возврата" value={failoverForm.failback_stable_checks} min={1} max={20} onChange={value => setFailoverForm({...failoverForm, failback_stable_checks: value})} />
          <NumberField label="Пауза после переключения, сек" value={failoverForm.cooldown_s} max={86400} onChange={value => setFailoverForm({...failoverForm, cooldown_s: value})} />
          <div className="flex items-center justify-between gap-3 text-sm"><span>Возвращаться на основную ноду</span><Switch label="Возвращаться на основную ноду" checked={failoverForm.failback} onChange={checked => setFailoverForm({...failoverForm, failback: checked})} /></div>
          <label className="text-xs font-medium text-neutral-600">Если все ноды недоступны<select className="mt-1 w-full rounded-lg border p-2 text-sm" value={failoverForm.fallback_action} onChange={e => setFailoverForm({...failoverForm, fallback_action: e.target.value as FailoverSettings['fallback_action']})}><option value="direct">Перейти на прямой выход</option><option value="keep">Оставить текущий маршрут</option></select></label>
        </fieldset>
        <p className="text-xs text-neutral-500">В ручном режиме проверки только фиксируют сбои и отправляют уведомления.</p>
        <button className="ui-button ui-button-primary" disabled={failoverMutation.isPending}>{failoverMutation.isPending ? 'Сохранение…' : 'Сохранить настройки'}</button>
        {failoverNotice && <p role="status" className="text-sm text-emerald-700">{failoverNotice}</p>}
      </form>}
    </details>

    <details className="ui-card ui-panel node-accordion"><summary><span className="node-section-icon"><KeyRound size={20} /></span><span className="node-accordion-title">Одноразовые приглашения <small>{invitesError ? 'Ошибка загрузки' : invitesLoading ? 'Загрузка…' : activeInvites ? `Активных: ${activeInvites}` : 'Нет активных'}</small></span><ChevronRight size={18} className="node-accordion-chevron" /></summary>
      {invitesError && <p role="alert" className="mt-4 text-sm text-red-700">Не удалось загрузить приглашения. <button className="underline" onClick={() => reloadInvites()}>Повторить</button></p>}
      {invitesLoading && <p className="mt-4 text-sm text-neutral-500">Загрузка приглашений…</p>}
      <ul className="mt-4 divide-y divide-neutral-100">{invites.map(invite => <li key={invite.id} className="flex flex-wrap items-center justify-between gap-3 py-3"><div className="min-w-0"><p className="break-words text-sm font-medium">{invite.name}</p><p className="mt-1 text-xs text-neutral-500">{invite.revoked ? 'Отозван' : invite.used_at ? 'Использован' : new Date(invite.expires_at).getTime() <= now ? 'Истёк' : 'Активен'} · До {new Date(invite.expires_at).toLocaleString('ru-RU')}</p></div>{!invite.revoked && !invite.used_at && <button aria-label={`Отозвать приглашение: ${invite.name}`} disabled={revokeInviteMutation.isPending} onClick={() => revokeInviteMutation.mutate(invite.id)} className="ui-button ui-button-secondary !min-h-9 !p-2"><Trash2 size={16} /></button>}</li>)}</ul>
      {!invitesLoading && !invitesError && !invites.length && <p className="text-sm text-neutral-500">Нет созданных приглашений</p>}
      {revokeInviteMutation.isError && <p role="alert" className="mt-3 text-sm text-red-700">{revokeInviteMutation.error.message}</p>}
      <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-neutral-200 pt-4"><p role={clearInviteHistoryMutation.isError ? 'alert' : 'status'} className={`text-xs ${clearInviteHistoryMutation.isError ? 'text-red-700' : 'text-neutral-500'}`}>{inviteCleanupNotice || 'Действующие приглашения и ноды сохраняются. Очистка удаляет использованные, отозванные и истёкшие приглашения.'}</p><button type="button" disabled={!invites.length || clearInviteHistoryMutation.isPending || createInviteMutation.isPending || revokeInviteMutation.isPending} onClick={() => { if (window.confirm('Очистить историю использованных, отозванных и истёкших приглашений? Действующие приглашения и подключённые ноды сохранятся.')) clearInviteHistoryMutation.mutate(); }} className="ui-button ui-button-secondary"><Trash2 size={16} />{clearInviteHistoryMutation.isPending ? 'Очищаем…' : 'Очистить историю'}</button></div>
    </details>

    {nodeToDelete && <Drawer title="Удаление ноды" busy={deleteNodeMutation.isPending} onClose={() => { setNodeToDelete(null); setDeleteError(null); }}>
      <p className="text-sm leading-6">Удалить ноду <strong>{nodeToDelete.name}</strong> ({nodeToDelete.host})? Конфигурация Xray будет перестроена.</p>
      {deleteError && <p role="alert" className="mt-4 text-sm text-red-700">{deleteError}</p>}
      <div className="mt-6 flex justify-end gap-3"><button className="ui-button ui-button-secondary" disabled={deleteNodeMutation.isPending} onClick={() => { setNodeToDelete(null); setDeleteError(null); }}>Отмена</button><button className="ui-button ui-button-danger" disabled={deleteNodeMutation.isPending} onClick={() => deleteNodeMutation.mutate(nodeToDelete.id)}>{deleteNodeMutation.isPending ? 'Удаление…' : 'Удалить'}</button></div>
    </Drawer>}

    {createModalOpen && <Drawer title="Добавить ноду" busy={createInviteMutation.isPending} dirty={draftInvite} onClose={closeInvite}>
      {!generatedInvite ? <form className="space-y-4" onSubmit={event => { event.preventDefault(); createInviteMutation.mutate({name: inviteName, ttl_hours: ttlHours}); }}>
        <p className="text-sm text-neutral-500">Создайте одноразовую команду и выполните её на сервере новой ноды.</p>
        <fieldset disabled={createInviteMutation.isPending} className="space-y-4"><label className="block text-sm font-medium">Название будущего узла<input required type="text" value={inviteName} onChange={event => setFormInviteName(event.target.value)} className="mt-2 w-full rounded-lg border p-3" /></label><label className="block text-sm font-medium">Срок действия токена<select className="mt-2 w-full rounded-lg border p-3" value={ttlHours} onChange={event => setFormTtlHours(Number(event.target.value))}><option value={1}>1 час</option><option value={24}>24 часа (1 день)</option><option value={168}>7 дней</option></select></label></fieldset>
        {createInviteMutation.isError && <p role="alert" className="text-sm text-red-700">{createInviteMutation.error.message}</p>}
        <button className="ui-button ui-button-primary w-full" disabled={createInviteMutation.isPending}>{createInviteMutation.isPending ? 'Создаём…' : 'Сгенерировать одноразовую команду'}</button>
      </form> : <div className="space-y-4">
        <p className="flex gap-2 rounded-xl bg-amber-50 p-3 text-sm text-amber-800"><AlertTriangle size={20} className="shrink-0" />Сохраните команду сейчас: токен показывается только один раз.</p>
        <pre className="whitespace-pre-wrap break-all rounded-xl bg-neutral-950 p-4 font-mono text-xs text-emerald-400">{joinCommand}</pre>
        <button className="ui-button ui-button-secondary" onClick={handleCopyCommand}>{copied ? <Check size={16} /> : <Copy size={16} />}{copied ? 'Скопировано' : 'Скопировать'}</button>
        {copyError && <p role="alert" className="text-sm text-red-700">{copyError}</p>}
        <button className="ui-button ui-button-primary w-full" onClick={handleCloseModal}>Готово / Закрыть</button>
      </div>}
    </Drawer>}
  </div>;
};
