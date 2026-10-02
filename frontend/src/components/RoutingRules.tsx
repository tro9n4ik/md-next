import React, { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Route, Plus, Trash2, RefreshCw } from 'lucide-react';
import { apiFetch } from '../utils/api';
import { translateStatus } from '../utils/ru';

interface RoutingRule {
  id: number;
  domain_or_ip: string;
  target_node_id: number | null;
  action: string;
  description: string | null;
  is_active: boolean;
}
interface NodeOption { id: number; name: string; host: string; is_enabled: boolean }

export const RoutingRules: React.FC = () => {
  const queryClient = useQueryClient();
  const [domainOrIp, setDomainOrIp] = useState('');
  const [action, setAction] = useState('proxy');
  const [targetNodeId, setTargetNodeId] = useState<number | ''>('');
  const [description, setDescription] = useState('');

  const { data: rules = [] } = useQuery<RoutingRule[]>({
    queryKey: ['routing_rules'],
    queryFn: async () => {
      const res = await apiFetch('/api/v1/routing/rules');
      if (!res.ok) throw new Error('Не удалось загрузить правила');
      return res.json();
    }
  });
  const { data: nodes = [] } = useQuery<NodeOption[]>({
    queryKey: ['nodes'],
    queryFn: async () => { const res = await apiFetch('/api/v1/nodes'); if (!res.ok) return []; const data = await res.json(); return Array.isArray(data) ? data : (data.nodes || []); }
  });

  const createRuleMutation = useMutation({
    mutationFn: async (payload: any) => {
      const res = await apiFetch('/api/v1/routing/rules', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      if (!res.ok) { const data = await res.json().catch(() => ({})); throw new Error(data.detail || 'Не удалось создать правило'); }
      return res.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['routing_rules'] });
      setDomainOrIp('');
      setDescription('');
      setTargetNodeId('');
    }
  });

  const deleteRuleMutation = useMutation({
    mutationFn: async (id: number) => {
      const res = await apiFetch(`/api/v1/routing/rules/${id}`, { method: 'DELETE' });
      if (!res.ok) throw new Error('Не удалось удалить правило');
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['routing_rules'] })
  });

  const applyRulesMutation = useMutation({
    mutationFn: async () => {
      const res = await apiFetch('/api/v1/routing/apply', { method: 'POST' });
      if (!res.ok) { const data = await res.json().catch(() => ({})); throw new Error(data.detail || 'Не удалось применить правила'); }
      return res.json();
    }
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!domainOrIp) return;
    createRuleMutation.mutate({
      domain_or_ip: domainOrIp,
      action,
      target_node_id: targetNodeId !== '' ? Number(targetNodeId) : null,
      description,
      is_active: true
    });
  };

  return (
    <div className="bg-white rounded-xl border border-neutral-200/60 shadow-sm p-6 mb-6">
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center space-x-3">
          <div className="p-2.5 bg-indigo-50 text-indigo-600 rounded-xl">
            <Route className="w-5 h-5" />
          </div>
          <div>
            <h3 className="text-lg font-bold text-neutral-800">Правила маршрутизации</h3>
            <p className="text-xs text-neutral-500">Управление проксированием и обходом блокировок через ноды</p>
          </div>
        </div>

        <button
          onClick={() => applyRulesMutation.mutate()}
          disabled={applyRulesMutation.isPending}
          className="flex items-center space-x-2 px-3.5 py-2 bg-neutral-900 text-white text-xs font-medium rounded-xl hover:bg-neutral-800 transition-colors shadow-sm disabled:opacity-50"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${applyRulesMutation.isPending ? 'animate-spin' : ''}`} />
          <span>Применить правила</span>
        </button>
      </div>

      <form onSubmit={handleSubmit} className="grid grid-cols-1 sm:grid-cols-5 gap-3 mb-6 p-4 bg-neutral-50/50 rounded-xl border border-neutral-100">
        <div>
          <label className="block text-xs font-semibold text-neutral-500 mb-1">Домен / IP / Geosite</label>
          <input
            type="text"
            required
            value={domainOrIp}
            onChange={(e) => setDomainOrIp(e.target.value)}
            placeholder="domain:instagram.com"
            className="w-full px-3 py-1.5 bg-white border border-neutral-200 rounded-lg text-xs focus:outline-none focus:ring-2 focus:ring-neutral-900"
          />
        </div>

        <div>
          <label className="block text-xs font-semibold text-neutral-500 mb-1">Действие</label>
          <select
            value={action}
            onChange={(e) => setAction(e.target.value)}
            className="w-full px-3 py-1.5 bg-white border border-neutral-200 rounded-lg text-xs focus:outline-none focus:ring-2 focus:ring-neutral-900"
          >
            <option value="proxy">Через ноду</option>
            <option value="direct">Напрямую</option>
            <option value="block">Блокировать</option>
            <option value="warp">WARP</option>
          </select>
        </div>

        {action === "proxy" && <div>
          <label className="block text-xs font-semibold text-neutral-500 mb-1">Нода выхода</label>
          <select required value={targetNodeId} onChange={(e) => setTargetNodeId(e.target.value ? Number(e.target.value) : "")} className="w-full px-3 py-1.5 bg-white border border-neutral-200 rounded-lg text-xs">
            <option value="">Выберите ноду</option>
            {nodes.filter((node) => node.is_enabled !== false).map((node) => <option key={node.id} value={node.id}>{node.name} ({node.host})</option>)}
          </select>
        </div>}

        <div>
          <label className="block text-xs font-semibold text-neutral-500 mb-1">Описание</label>
          <input
            type="text"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Обход блокировки IG"
            className="w-full px-3 py-1.5 bg-white border border-neutral-200 rounded-lg text-xs focus:outline-none focus:ring-2 focus:ring-neutral-900"
          />
        </div>

        <div className="flex items-end">
          <button
            type="submit"
            disabled={createRuleMutation.isPending}
            className="w-full py-1.5 bg-emerald-600 hover:bg-emerald-700 text-white font-medium text-xs rounded-lg transition-colors flex items-center justify-center space-x-1"
          >
            <Plus className="w-4 h-4" />
            <span>Добавить правило</span>
          </button>
        </div>
      </form>
      {(createRuleMutation.isError || applyRulesMutation.isError) && <p role="alert" className="mb-4 rounded-lg bg-red-50 p-3 text-sm text-red-700">{(createRuleMutation.error || applyRulesMutation.error as Error)?.message}</p>}
      {applyRulesMutation.isSuccess && <p className="mb-4 rounded-lg bg-emerald-50 p-3 text-sm text-emerald-700">{applyRulesMutation.data?.message || "Rules applied"}</p>}

      <div className="overflow-x-auto">
        <table className="w-full text-xs text-left">
          <thead className="text-neutral-500 bg-neutral-50 uppercase font-medium">
            <tr>
              <th className="px-4 py-2.5">Правило</th>
              <th className="px-4 py-2.5">Действие</th>
              <th className="px-4 py-2.5">Описание</th>
              <th className="px-4 py-2.5 text-right">Удаление</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-neutral-100">
            {rules.map((rule) => (
              <tr key={rule.id} className="hover:bg-neutral-50/50">
                <td className="px-4 py-3 font-mono text-neutral-800">{rule.domain_or_ip}</td>
                <td className="px-4 py-3">
                  <span className={`px-2 py-0.5 rounded text-[10px] font-semibold uppercase ${
                    rule.action === 'proxy' ? 'bg-indigo-50 text-indigo-700' : rule.action === 'direct' ? 'bg-emerald-50 text-emerald-700' : 'bg-red-50 text-red-700'
                  }`}>
                    {translateStatus(rule.action)}
                  </span>
                </td>
                <td className="px-4 py-3 text-neutral-500">{rule.description || '—'}</td>
                <td className="px-4 py-3 text-right">
                  <button
                    onClick={() => deleteRuleMutation.mutate(rule.id)}
                    className="p-1 text-neutral-400 hover:text-red-600 rounded transition-colors"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </td>
              </tr>
            ))}
            {rules.length === 0 && (
              <tr>
                <td colSpan={4} className="px-4 py-6 text-center text-neutral-400">
                  Правила маршрутизации не заданы
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
};
