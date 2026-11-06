import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiFetch } from '../utils/api';
import { panelPolling, panelPollNotice } from '../utils/panelPolling';
import type { NodeTransport } from '../utils/nodeControl';

export function NodeTransportField({ value, onChange }: { value: NodeTransport; onChange: (value: NodeTransport) => void }) {
  return <label className="block text-sm font-medium">Управляющий канал
    <select value={value} onChange={event => onChange(event.target.value as NodeTransport)} className="mt-2 w-full rounded-lg border p-3">
      <option value="xray">Стандартный (текущий)</option>
      <option value="mdnt">KedrLink — Go-агент (экспериментальный)</option>
    </select>
    <span className="mt-2 block text-xs font-normal text-neutral-500">{value === 'mdnt'
      ? 'Агент передаёт состояние ноды и получает запросы проверки через HTTPS. VPN-трафик продолжает идти через Xray.'
      : 'Текущее подключение ноды без установки дополнительного агента.'}</span>
  </label>;
}

interface ControlState {
  transport: NodeTransport;
  state: 'standard' | 'unpaired' | 'waiting' | 'online' | 'offline';
  seen_at: string | null;
  agent_version: string | null;
  uptime_ms: number | null;
  command: { id: string; kind: 'status'; status: 'pending' | 'completed' | 'expired'; result: { agent_version: string; uptime_ms: number } | null } | null;
}

export function NodeControl({ nodeId }: { nodeId: number }) {
  const client = useQueryClient();
  const key = ['nodeControl', nodeId];
  const query = useQuery({ queryKey: key, ...panelPolling<ControlState>(`/api/v1/nodes/${nodeId}/control`), refetchInterval: 5000 });
  const notice = panelPollNotice(query);
  const command = useMutation({
    mutationFn: async () => {
      const response = await apiFetch(`/api/v1/nodes/${nodeId}/control/commands`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ kind: 'status' }),
      });
      if (!response.ok) throw new Error('Не удалось запросить проверку агента');
      return response.json() as Promise<ControlState>;
    },
    onSuccess: data => { client.setQueryData(key, data); },
  });
  const data = query.data;
  const labels = { standard: 'Стандартный канал', unpaired: 'Нужно повторно подключить агента по новому приглашению', waiting: 'Ожидаем подключения агента', online: 'Агент на связи', offline: 'Агент не на связи' };
  return <div className="mt-2 space-y-1 text-xs">
    <p>{data ? labels[data.state] : 'Проверяем связь с агентом…'}{data?.agent_version ? ` · ${data.agent_version}` : ''}</p>
    {data?.seen_at && <p className="text-neutral-500">Последняя связь: {new Date(data.seen_at).toLocaleString('ru-RU')}</p>}
    {data?.command?.status === 'pending' && <p role="status">Проверка ожидает ответа ноды…</p>}
    {data?.command?.status === 'completed' && <p>Проверка выполнена · время работы {Math.floor((data.command.result?.uptime_ms ?? 0) / 1000)} с</p>}
    {data?.command?.status === 'expired' && <p>Нода не ответила на проверку за 60 секунд.</p>}
    {notice && <p role="status" className="text-amber-700">{notice}</p>}
    {command.isError && <p role="alert" className="text-red-700">{command.error.message}</p>}
    <button className="ui-button ui-button-secondary !min-h-8 !px-2 !py-1 text-xs" onClick={() => command.mutate()}
      disabled={command.isPending || data?.command?.status === 'pending' || data?.state === 'unpaired' || !data || !!notice}>Проверить агент</button>
  </div>;
}
