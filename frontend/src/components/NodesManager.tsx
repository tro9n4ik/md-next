import Switch from './ui/Switch';
import React, { useEffect, useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Server, Copy, Check, Plus, Trash2, X, AlertTriangle, ArrowRight, ArrowUp, ArrowDown, Radio, Network } from 'lucide-react';
import CountryFlag from './ui/CountryFlag';
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
  return <div className={`min-w-0 flex-1 rounded-xl border px-3 py-3 ${colors[state]}`}>
    <div className="flex items-center gap-2 text-xs font-bold"><span className="h-2 w-2 shrink-0 rounded-full bg-current" />{title}</div>
    <div className="mt-1 break-words text-[11px] opacity-80">{detail}</div>
  </div>;
};

const NumberField: React.FC<{ label: string; value: number; onChange: (value: number) => void; hint?: string }> = ({ label, value, onChange, hint }) => (
  <label className="text-xs font-medium text-neutral-600">{label}
    <input type="number" min={0} value={value} onChange={event => onChange(Number(event.target.value))} className="mt-1 w-full rounded-lg border border-neutral-200 p-2 text-sm" />
    {hint && <span className="mt-1 block text-[10px] font-normal text-neutral-400">{hint}</span>}
  </label>
);

export const NodesManager: React.FC = () => {
  const queryClient = useQueryClient();
  const [copied, setCopied] = useState(false);
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [inviteName, setFormInviteName] = useState('Новый узел');
  const [ttlHours, setFormTtlHours] = useState(24);
  const [generatedInvite, setGeneratedInvite] = useState<GeneratedInvite | null>(null);

  const [nodeToDelete, setNodeToDelete] = useState<NodeData | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [clusterError, setClusterError] = useState<string | null>(null);
  const [failoverForm, setFailoverForm] = useState<FailoverSettings | null>(null);
  const [routeCheck, setRouteCheck] = useState<RouteCheckResult | null>(null);

  const { data: nodes = [] } = useQuery<NodeData[]>({
    queryKey: ['nodes'],
    queryFn: async () => {
      const res = await apiFetch('/api/v1/nodes');
      if (!res.ok) throw new Error('Не удалось загрузить список узлов');
      return res.json();
    },
    refetchInterval: 15000
  });

  const { data: route } = useQuery<RouteData>({
    queryKey: ['clusterRoute'],
    queryFn: async () => {
      const res = await apiFetch('/api/v1/cluster/route');
      if (!res.ok) throw new Error('Не удалось загрузить схему маршрута');
      return res.json();
    },
    refetchInterval: 15000,
  });

  const { data: failoverSettings } = useQuery<FailoverSettings>({
    queryKey: ['clusterFailover'],
    queryFn: async () => {
      const res = await apiFetch('/api/v1/cluster/failover');
      if (!res.ok) throw new Error('Не удалось загрузить настройки автопереключения');
      return res.json();
    },
  });

  useEffect(() => {
    if (failoverSettings) setFailoverForm(failoverSettings);
  }, [failoverSettings]);

  useEffect(() => {
    setRouteCheck(null);
  }, [route?.active_node?.id, route?.manual_direct]);

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
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['nodes'] }),
    onError: (error: Error) => setClusterError(error.message),
  });

  const failoverMutation = useMutation({
    mutationFn: async (settings: FailoverSettings) => {
      const res = await apiFetch('/api/v1/cluster/failover', {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(settings),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail || 'Не удалось сохранить настройки');
      return body as FailoverSettings;
    },
    onSuccess: (data) => {
      setFailoverForm(data);
      setClusterError(null);
      queryClient.invalidateQueries({ queryKey: ['clusterFailover'] });
      queryClient.invalidateQueries({ queryKey: ['clusterRoute'] });
    },
    onError: (error: Error) => setClusterError(error.message),
  });

  const { data: invites = [] } = useQuery<NodeInviteItem[]>({
    queryKey: ['nodeInvites'],
    queryFn: async () => {
      const res = await apiFetch('/api/v1/nodes/invites');
      if (!res.ok) return [];
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

  const handleCopyCommand = () => {
    if (!joinCommand) return;
    navigator.clipboard.writeText(joinCommand);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleCloseModal = () => {
    setCreateModalOpen(false);
    setGeneratedInvite(null);
    setFormInviteName('Новый узел');
    setFormTtlHours(24);
  };

  return (
    <div className="space-y-6">
      <section className="ui-card ui-panel">
        <div className="flex flex-wrap items-center gap-3 mb-4">
          <Network className="w-5 h-5 text-blue-600" />
          <h3 className="ui-card-title">Схема маршрута</h3>
          <span className={`sm:ml-auto rounded-full px-2.5 py-1 text-xs font-semibold ${route?.failover_mode === 'auto' ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-700'}`}>
            Автопереключение: {route?.failover_mode === 'auto' ? 'включено' : 'ручной режим'}
          </span>

        </div>
        <div className="flex flex-col md:flex-row md:items-stretch gap-2">
          <RouteStep title="Клиент" detail="VPN-профиль" state="ok" />
          <ArrowRight className="hidden md:block self-center text-neutral-300" />
          <RouteStep title="Этот сервер" detail={`${route?.server.hostname || '…'} · ${route?.server.public_ip || 'IP не задан'}`} state={route ? 'ok' : 'wait'} />
          <ArrowRight className="hidden md:block self-center text-neutral-300" />
          <RouteStep title="VPN-уровень" detail={`Xray ${route?.vpn.xray ? 'работает' : 'нет'} · AWG ${route?.vpn.awg ? 'работает' : 'нет'}`} state={route?.vpn.xray ? 'ok' : 'bad'} />
          <ArrowRight className="hidden md:block self-center text-neutral-300" />
          {route?.active_node ? (
            <RouteStep title="Нода выхода" detail={`${route.active_node.name} · ${route.active_node.ip} · ${route.active_node.ping_ms} мс`} state={route.active_node.status === 'healthy' ? 'ok' : 'bad'} />
          ) : (
            <RouteStep title="Прямой выход" detail="Через мастер-сервер" state="warn" />
          )}
          <ArrowRight className="hidden md:block self-center text-neutral-300" />
          <RouteStep title="Интернет" detail="Выходной трафик Xray" state="ok" />
        </div>
        <p className="mt-4 rounded-lg bg-blue-50 px-3 py-2 text-xs text-blue-800">
          Выбранная нода используется клиентами Xray и AmneziaWG. Правила маршрутизации и WARP применяются к обоим типам подключения.
          Проверка внешнего IP выполняется с сервера через текущий маршрут Xray и не заменяет проверку соединения на устройстве.
          {route?.next_candidate && <> Следующий кандидат: <b>{route.next_candidate.name}</b> ({route.next_candidate.ip}, {route.next_candidate.ping_ms} мс).</>}
          {route?.manual_direct && <> Прямой выход выбран вручную и не будет автоматически заменён нодой.</>}
        </p>
        {routeCheck && <p className="mt-2 rounded-lg border border-neutral-200 bg-neutral-50 px-3 py-2 text-xs text-neutral-700">
          Фактический внешний IP через Xray: <b>{routeCheck.exit_ip}</b>{routeCheck.country ? ` · ${routeCheck.country}` : ''}. Проверено: {new Date(routeCheck.checked_at).toLocaleString('ru-RU')}.
          {' '}Маршрут: <b>{routeCheck.manual_direct ? 'прямой выход' : routeCheck.active_node?.name || 'прямой выход'}</b>.
        </p>}
      </section>

      {failoverForm && (
        <section className="ui-card ui-panel">
          <div className="flex items-center gap-2 mb-4">
            <Radio className="w-5 h-5 text-blue-600" />
            <h3 className="ui-card-title">Автопереключение</h3>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            <label className="text-xs font-medium text-neutral-600">Режим
              <select className="mt-1 w-full rounded-lg border border-neutral-200 p-2 text-sm" value={failoverForm.mode} onChange={e => setFailoverForm({ ...failoverForm, mode: e.target.value as FailoverSettings['mode'] })}>
                <option value="manual">Ручной</option><option value="auto">Автоматический</option>
              </select>
            </label>
            <NumberField label="Порог задержки, мс" value={failoverForm.ping_threshold_ms} onChange={value => setFailoverForm({ ...failoverForm, ping_threshold_ms: value })} hint="Нода будет считаться плохой при превышении." />
            <NumberField label="Сбоев подряд" value={failoverForm.failure_count} onChange={value => setFailoverForm({ ...failoverForm, failure_count: value })} hint="Или сразу при неудачном TCP-подключении." />
            <NumberField label="Проверка каждые, сек" value={failoverForm.interval_s} onChange={value => setFailoverForm({ ...failoverForm, interval_s: value })} hint="Watchdog перечитывает настройки каждый цикл." />
            <NumberField label="Стабильных проверок для возврата" value={failoverForm.failback_stable_checks} onChange={value => setFailoverForm({ ...failoverForm, failback_stable_checks: value })} />
            <NumberField label="Пауза после переключения, сек" value={failoverForm.cooldown_s} onChange={value => setFailoverForm({ ...failoverForm, cooldown_s: value })} />
            <label className="flex items-center gap-2 text-sm text-neutral-700 sm:pt-6">
              <Switch label="Возвращаться на основную ноду" checked={failoverForm.failback} onChange={checked => setFailoverForm({ ...failoverForm, failback: checked })} />
              Возвращаться на основную ноду при восстановлении
            </label>
            <label className="text-xs font-medium text-neutral-600">Если все ноды недоступны
              <select className="mt-1 w-full rounded-lg border border-neutral-200 p-2 text-sm" value={failoverForm.fallback_action} onChange={e => setFailoverForm({ ...failoverForm, fallback_action: e.target.value as FailoverSettings['fallback_action'] })}>
                <option value="direct">Перейти на прямой выход</option><option value="keep">Оставить текущий маршрут</option>
              </select>
            </label>
          </div>
          <div className="mt-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <p className="text-xs text-neutral-500">В ручном режиме watchdog только фиксирует сбои и отправляет уведомления.</p>

          </div>
        </section>
      )}

      {clusterError && <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{clusterError}</div>}

      <div className="ui-card ui-panel">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-6">
          <div className="flex items-center space-x-3">
            <div className="p-2.5 bg-blue-50 text-blue-600 rounded-xl">
              <Server className="w-5 h-5" />
            </div>
            <div>
              <h3 className="ui-card-title">Кластерные узлы (Ноды)</h3>
              <p className="text-xs text-neutral-500">Автоматическое одноразовое подключение exit-нод по уникальной ссылке</p>
            </div>
          </div>

          <button
            onClick={() => setCreateModalOpen(true)}
            className="ui-button ui-button-primary w-fit"
          >
            <Plus className="w-4 h-4" />
            <span>Сгенерировать ссылку для ноды</span>
          </button>
          <button onClick={() => activeNodeMutation.mutate(null)} disabled={activeNodeMutation.isPending || !route?.active_node} className="rounded-xl border border-neutral-200 px-3.5 py-2 text-xs font-semibold text-neutral-700 hover:bg-neutral-50 disabled:opacity-50">Прямой выход</button>
        </div>

        {/* Таблица активных нод */}
        <div className="overflow-x-auto border border-neutral-100 rounded-xl">
          <table className="w-full text-xs text-left">
            <thead className="text-neutral-500 bg-neutral-50 uppercase font-medium">
              <tr>
                <th className="px-4 py-2.5">Статус</th>
                <th className="px-4 py-2.5">Название узла</th>
                <th className="px-4 py-2.5">Публичный IP / Хост</th>
                <th className="px-4 py-2.5">Порт / Протокол</th>
                <th className="px-4 py-2.5 text-right">Пинг</th>
                <th className="px-4 py-2.5 text-right">Действия</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-neutral-100">
              {nodes.map((node) => (
                <tr key={node.id} className="hover:bg-neutral-50/50">
                  <td className="px-4 py-3">
                    <span className={`inline-flex items-center px-2 py-0.5 rounded text-[10px] font-semibold ${
                      node.status === 'healthy' ? 'bg-emerald-50 text-emerald-700' : node.status === 'disabled' ? 'bg-neutral-100 text-neutral-500' : 'bg-red-50 text-red-700'
                    }`}>
                      {node.status === 'healthy' ? 'Доступна' : node.status === 'disabled' ? 'Выключена' : 'Недоступна'}
                      {route?.active_node?.id === node.id && ' · Активная'}
                    </span>
                  </td>
                  <td className="px-4 py-3 font-medium text-neutral-800"><span className="inline-flex items-center gap-2"><CountryFlag code={node.country_code} />{node.name}</span></td>
                  <td className="px-4 py-3 font-mono text-neutral-600">{node.host}</td>
                  <td className="px-4 py-3 text-neutral-500">{node.port} / <span className="uppercase">{node.protocol}</span></td>
                  <td className="px-4 py-3 text-right font-mono text-neutral-500">{node.ping_ms ? `${node.ping_ms} ms` : '-'}</td>
                  <td className="px-4 py-3 text-right">
                    <div className="flex items-center justify-end gap-1.5">
                    <span className="mr-1 text-[10px] text-neutral-400">#{node.priority + 1}</span>
                    <button aria-label="Поднять приоритет" disabled={node.priority === 0 || reorderMutation.isPending} onClick={() => {
                      const ids = nodes.map(item => item.id); const index = ids.indexOf(node.id);
                      if (index > 0) { [ids[index - 1], ids[index]] = [ids[index], ids[index - 1]]; reorderMutation.mutate(ids); }
                    }} className="rounded p-1 text-neutral-400 hover:text-blue-600 disabled:opacity-30"><ArrowUp className="h-4 w-4" /></button>
                    <button aria-label="Опустить приоритет" disabled={node.priority === nodes.length - 1 || reorderMutation.isPending} onClick={() => {
                      const ids = nodes.map(item => item.id); const index = ids.indexOf(node.id);
                      if (index >= 0 && index < ids.length - 1) { [ids[index + 1], ids[index]] = [ids[index], ids[index + 1]]; reorderMutation.mutate(ids); }
                    }} className="rounded p-1 text-neutral-400 hover:text-blue-600 disabled:opacity-30"><ArrowDown className="h-4 w-4" /></button>
                    {route?.active_node?.id !== node.id && <button onClick={() => activeNodeMutation.mutate(node.id)} disabled={!node.is_enabled || node.status !== 'healthy' || activeNodeMutation.isPending} className="rounded-lg border border-emerald-200 px-2 py-1 text-[10px] font-semibold text-emerald-700 hover:bg-emerald-50 disabled:opacity-40">Сделать активной</button>}
                    <button
                      onClick={() => {
                        setNodeToDelete(node);
                        setDeleteError(null);
                      }}
                      className="p-1 text-neutral-400 hover:text-red-600 rounded"
                      title="Удалить ноду"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                    </div>
                  </td>
                </tr>
              ))}
              {nodes.length === 0 && (
                <tr>
                  <td colSpan={6} className="px-4 py-6 text-center text-neutral-400">
                    Узлы ещё не подключены. Сгенерируйте одноразовую ссылку выше.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Таблица одноразовых инвайтов */}
      <div className="ui-card ui-panel">
        <h4 className="text-sm font-bold text-neutral-800 mb-4">Активные одноразовые приглашения</h4>
        <div className="overflow-x-auto border border-neutral-100 rounded-xl">
          <table className="w-full text-xs text-left">
            <thead className="text-neutral-500 bg-neutral-50 uppercase font-medium">
              <tr>
                <th className="px-4 py-2.5">Название</th>
                <th className="px-4 py-2.5">Статус</th>
                <th className="px-4 py-2.5">Истекает</th>
                <th className="px-4 py-2.5 text-right">Действия</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-neutral-100">
              {invites.map((inv) => {
                const isUsed = !!inv.used_at;
                const isExpired = new Date(inv.expires_at) < new Date();
                let statusLabel = 'Активен';
                let statusStyle = 'bg-emerald-50 text-emerald-700';

                if (inv.revoked) {
                  statusLabel = 'Отозван';
                  statusStyle = 'bg-red-50 text-red-700';
                } else if (isUsed) {
                  statusLabel = 'Использован';
                  statusStyle = 'bg-blue-50 text-blue-700';
                } else if (isExpired) {
                  statusLabel = 'Истёк';
                  statusStyle = 'bg-neutral-100 text-neutral-500';
                }

                return (
                  <tr key={inv.id} className="hover:bg-neutral-50/50">
                    <td className="px-4 py-3 font-medium text-neutral-800">{inv.name}</td>
                    <td className="px-4 py-3">
                      <span className={`inline-flex items-center px-2 py-0.5 rounded text-[10px] font-semibold ${statusStyle}`}>
                        {statusLabel}
                      </span>
                    </td>
                    <td className="px-4 py-3 font-mono text-neutral-500">
                      {new Date(inv.expires_at).toLocaleString('ru-RU')}
                    </td>
                    <td className="px-4 py-3 text-right">
                      {!inv.revoked && !isUsed && (
                        <button
                          onClick={() => revokeInviteMutation.mutate(inv.id)}
                          className="p-1 text-neutral-400 hover:text-red-600 rounded"
                          title="Отозвать"
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
              {invites.length === 0 && (
                <tr>
                  <td colSpan={4} className="px-4 py-6 text-center text-neutral-400">
                    Нет созданных приглашений
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Модальное окно подтверждения удаления ноды */}
      {nodeToDelete && (
        <div className="fixed inset-0 bg-neutral-900/60 backdrop-blur-sm flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-2xl max-w-md w-full p-6 shadow-2xl border border-neutral-100 relative">
            <button
              onClick={() => {
                setNodeToDelete(null);
                setDeleteError(null);
              }}
              className="absolute top-4 right-4 text-neutral-400 hover:text-neutral-600"
            >
              <X className="w-5 h-5" />
            </button>

            <h3 className="ui-card-title mb-2">Удаление ноды</h3>
            <p className="text-xs text-neutral-600 mb-4">
              Вы действительно хотите удалить ноду <strong>{nodeToDelete.name}</strong> ({nodeToDelete.host})?
              Это действие перестроит конфигурацию Xray.
            </p>

            {deleteError && (
              <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded-xl text-red-700 text-xs">
                {deleteError}
              </div>
            )}

            <div className="flex justify-end space-x-3">
              <button
                onClick={() => {
                  setNodeToDelete(null);
                  setDeleteError(null);
                }}
                className="px-4 py-2 text-xs font-semibold text-neutral-600 hover:bg-neutral-100 rounded-xl transition-colors"
              >
                Отмена
              </button>
              <button
                onClick={() => deleteNodeMutation.mutate(nodeToDelete.id)}
                disabled={deleteNodeMutation.isPending}
                className="px-4 py-2 text-xs font-semibold bg-red-600 hover:bg-red-700 text-white rounded-xl transition-colors disabled:opacity-50"
              >
                {deleteNodeMutation.isPending ? 'Удаление...' : 'Удалить'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Модальное окно генерации ссылки */}
      {createModalOpen && (
        <div className="fixed inset-0 bg-neutral-900/60 backdrop-blur-sm flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-2xl max-w-lg w-full p-6 shadow-2xl border border-neutral-100 relative">
            <button onClick={handleCloseModal} className="absolute top-4 right-4 text-neutral-400 hover:text-neutral-600">
              <X className="w-5 h-5" />
            </button>

            <h3 className="text-xl font-bold text-neutral-800 mb-1">Генерация ссылки подключения ноды</h3>
            <p className="text-xs text-neutral-500 mb-4">
              Токен отображается **ровно один раз**. После вызова скрипта токен аннулируется.
            </p>

            {!generatedInvite ? (
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  createInviteMutation.mutate({ name: inviteName, ttl_hours: ttlHours });
                }}
                className="space-y-4"
              >
                <div>
                  <label className="block text-xs font-semibold text-neutral-500 uppercase mb-1">Название будущего узла</label>
                  <input
                    type="text"
                    required
                    value={inviteName}
                    onChange={(e) => setFormInviteName(e.target.value)}
                    className="w-full px-3 py-2 bg-neutral-50 border border-neutral-200 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/20 focus:border-emerald-500"
                  />
                </div>

                <div>
                  <label className="block text-xs font-semibold text-neutral-500 uppercase mb-1">Срок действия токена</label>
                  <select
                    value={ttlHours}
                    onChange={(e) => setFormTtlHours(Number(e.target.value))}
                    className="w-full px-3 py-2 bg-neutral-50 border border-neutral-200 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/20 focus:border-emerald-500"
                  >
                    <option value={1}>1 час</option>
                    <option value={24}>24 часа (1 день)</option>
                    <option value={168}>7 дней</option>
                  </select>
                </div>

                <button
                  type="submit"
                  disabled={createInviteMutation.isPending}
                  className="ui-button ui-button-primary w-full"
                >
                  {createInviteMutation.isPending ? 'Сборка...' : 'Сгенерировать одноразовую команду'}
                </button>
              </form>
            ) : (
              <div className="space-y-4">
                <div className="p-3 bg-amber-50 border border-amber-200 rounded-xl text-amber-800 text-xs flex items-center space-x-2">
                  <AlertTriangle className="w-5 h-5 text-amber-600 shrink-0" />
                  <span>ВНИМАНИЕ: Скопируйте эту команду сейчас. Она демонстрируется только один раз!</span>
                </div>

                <div className="bg-neutral-900 p-3 rounded-xl border border-neutral-800">
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-[10px] font-mono text-neutral-400 uppercase">Однострочная команда для ноды</span>
                    <button
                      onClick={handleCopyCommand}
                      className="flex items-center space-x-1 px-2.5 py-1 bg-neutral-800 hover:bg-neutral-700 text-neutral-200 text-xs rounded-lg border border-neutral-700"
                    >
                      {copied ? (
                        <>
                          <Check className="w-3.5 h-3.5 text-emerald-400" />
                          <span className="text-emerald-400">Скопировано</span>
                        </>
                      ) : (
                        <>
                          <Copy className="w-3.5 h-3.5" />
                          <span>Скопировать</span>
                        </>
                      )}
                    </button>
                  </div>
                  <div className="bg-neutral-950 p-2.5 rounded-lg font-mono text-xs text-emerald-400 overflow-x-auto select-all break-all">
                    {joinCommand}
                  </div>
                </div>

                <button
                  onClick={handleCloseModal}
                  className="ui-button ui-button-primary w-full"
                >
                  Готово / Закрыть
                </button>
              </div>
            )}
          </div>
        </div>
      )}
<div className="ui-actionbar"><p className="text-sm text-neutral-500">Настройки резервирования и проверка текущего выхода</p><div className="flex flex-col gap-2 sm:flex-row">          <button onClick={() => routeCheckMutation.mutate()} disabled={routeCheckMutation.isPending} className="ui-button ui-button-secondary w-full sm:w-auto">
            {routeCheckMutation.isPending ? 'Проверка…' : 'Проверить внешний IP'}
          </button>{failoverForm && (            <button onClick={() => failoverMutation.mutate(failoverForm)} disabled={failoverMutation.isPending} className="ui-button ui-button-primary">
              {failoverMutation.isPending ? 'Сохранение…' : 'Сохранить'}
            </button>)}</div></div>
    </div>
  );
};
