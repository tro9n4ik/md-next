import CountryFlag from './ui/CountryFlag';
import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { Activity, Cpu, Network, Server, Users } from 'lucide-react';
import { apiFetch } from '../utils/api';
import { formatBytes, formatNumber, formatSpeed, translateStatus } from '../utils/ru';

interface SystemStats {
  cpu_percent: number;
  memory: { total: number; used: number; percent: number };
  disk: { total: number; used: number; percent: number };
  uptime: number;
  loadavg: number[];
  net_speed: { rx_bytes_per_sec: number; tx_bytes_per_sec: number };
}
interface TrafficStats { today: { total: number }; last_30_days: { total: number }; all_time: { total: number } }
interface DashboardClient { id: number; name: string; is_active: boolean; traffic_total?: number; traffic_used?: number }
interface DashboardNode { country_code?: string | null; id: number; name: string; host: string; status: string; is_active: boolean; is_enabled: boolean; ping_ms: number }
interface RouteInfo { active_node: { id: number; name: string; ip: string } | null }

const card = 'ui-card ui-panel';
const heading = 'mb-4 flex items-center gap-2 text-sm font-semibold text-neutral-700';

const Metrics: React.FC = () => {
  const stats = useQuery<SystemStats>({ queryKey: ['systemStats'], queryFn: async () => { const r = await apiFetch('/api/v1/system/stats'); if (!r.ok) throw new Error('Не удалось получить данные сервера'); return r.json(); }, refetchInterval: 5000 });
  const traffic = useQuery<TrafficStats>({ queryKey: ['systemTraffic'], queryFn: async () => { const r = await apiFetch('/api/v1/system/traffic'); if (!r.ok) throw new Error('Не удалось получить статистику трафика'); return r.json(); }, refetchInterval: 30000 });
  const clients = useQuery<DashboardClient[]>({ queryKey: ['clients'], queryFn: async () => { const r = await apiFetch('/api/v1/clients'); if (!r.ok) return []; return r.json(); }, refetchInterval: 15000 });
  const nodes = useQuery<DashboardNode[]>({ queryKey: ['nodes'], queryFn: async () => { const r = await apiFetch('/api/v1/nodes'); if (!r.ok) return []; return r.json(); }, refetchInterval: 15000 });
  const route = useQuery<RouteInfo>({ queryKey: ['clusterRoute'], queryFn: async () => { const r = await apiFetch('/api/v1/cluster/route'); if (!r.ok) return { active_node: null }; return r.json(); }, refetchInterval: 15000 });

  const data = stats.data;
  const activeCount = (clients.data || []).filter(item => item.is_active).length;
  const topClients = [...(clients.data || [])].sort((a, b) => (b.traffic_total || b.traffic_used || 0) - (a.traffic_total || a.traffic_used || 0)).slice(0, 5);
  const uptime = data?.uptime || 0;
  const uptimeLabel = `${Math.floor(uptime / 86400)} д ${Math.floor((uptime % 86400) / 3600)} ч`;

  return <div className="grid grid-cols-1 gap-4 xl:grid-cols-2 2xl:grid-cols-4">
    <section className={card}>
      <h3 className={heading}><Cpu className="h-4 w-4" />Сервер</h3>
      <div className="mb-3 flex items-end justify-between"><span className="text-sm text-neutral-500">CPU</span><b className="text-xl">{formatNumber(data?.cpu_percent || 0, 1)}%</b></div>
      <div className="space-y-2 text-sm"><div className="flex justify-between"><span className="text-neutral-500">Нагрузка</span><span>{(data?.loadavg || [0, 0, 0]).map(value => Number(value).toLocaleString('ru-RU', { maximumFractionDigits: 2 })).join(' / ')}</span></div><div className="flex justify-between"><span className="text-neutral-500">Память</span><span>{data ? `${formatBytes(data.memory.used)} / ${formatBytes(data.memory.total)}` : '—'}</span></div><div className="flex justify-between"><span className="text-neutral-500">Диск</span><span>{data ? `${formatBytes(data.disk.used)} / ${formatBytes(data.disk.total)}` : '—'}</span></div><div className="flex justify-between"><span className="text-neutral-500">Работает</span><span>{data ? uptimeLabel : '—'}</span></div></div>
    </section>

    <section className={card}>
      <h3 className={heading}><Network className="h-4 w-4" />Сеть и трафик</h3>
      <div className="grid grid-cols-2 gap-3"><div className="rounded-xl bg-neutral-50 p-3"><span className="text-xs text-neutral-500">Входящая скорость</span><div className="mt-1 font-semibold">↓ {formatSpeed(data?.net_speed.rx_bytes_per_sec || 0)}</div></div><div className="rounded-xl bg-neutral-50 p-3"><span className="text-xs text-neutral-500">Исходящая скорость</span><div className="mt-1 font-semibold">↑ {formatSpeed(data?.net_speed.tx_bytes_per_sec || 0)}</div></div></div>
      <div className="mt-3 space-y-2 text-sm"><div className="flex justify-between"><span className="text-neutral-500">Сегодня</span><b>{formatBytes(traffic.data?.today.total || 0)}</b></div><div className="flex justify-between"><span className="text-neutral-500">30 дней</span><b>{formatBytes(traffic.data?.last_30_days.total || 0)}</b></div><div className="flex justify-between"><span className="text-neutral-500">Всего</span><b>{formatBytes(traffic.data?.all_time.total || 0)}</b></div></div>
    </section>

    <section className={card}>
      <h3 className={heading}><Server className="h-4 w-4" />Узлы</h3>
      <p className="mb-3 rounded-xl bg-neutral-50 p-3 text-sm">Выход: <b>{route.data?.active_node?.name || 'прямой'}</b></p>
      {(nodes.data || []).length === 0 ? <p className="text-sm text-neutral-500">Узлов пока нет. <a className="font-medium text-indigo-600 hover:underline" href="#/nodes">Добавить на странице «Узлы»</a></p> : <div className="max-h-52 space-y-2 overflow-y-auto">{nodes.data?.map(node => <div key={node.id} className="flex items-center justify-between gap-2 rounded-lg border border-neutral-100 px-3 py-2 text-sm"><div className="min-w-0"><div className="flex flex-wrap items-center gap-2 font-medium"><CountryFlag code={node.country_code} />{node.name}{route.data?.active_node?.id === node.id && <span className="ml-2 text-xs text-indigo-600">Активная</span>}</div><div className="truncate text-xs text-neutral-500">{node.host}</div></div><div className="shrink-0 text-right"><div className="text-xs">{translateStatus(!node.is_enabled ? 'disabled' : node.status)}</div><div className="text-xs text-neutral-500">{node.ping_ms && node.ping_ms > 0 ? `${formatNumber(node.ping_ms)} мс` : '—'}</div></div></div>)}</div>}
    </section>

    <section className={card}>
      <h3 className={heading}><Users className="h-4 w-4" />Клиенты</h3>
      <div className="mb-3 grid grid-cols-3 gap-2 text-center"><div className="rounded-lg bg-neutral-50 p-2"><b className="block">{clients.data?.length || 0}</b><span className="text-xs text-neutral-500">Всего</span></div><div className="rounded-lg bg-emerald-50 p-2"><b className="block">{activeCount}</b><span className="text-xs text-neutral-500">Активны</span></div><div className="rounded-lg bg-neutral-50 p-2"><b className="block">{Math.max(0, (clients.data?.length || 0) - activeCount)}</b><span className="text-xs text-neutral-500">Отключены</span></div></div>
      <h4 className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase text-neutral-500"><Activity className="h-3.5 w-3.5" />Топ по трафику</h4>
      {topClients.length ? <div className="space-y-2">{topClients.map(item => <div key={item.id} className="flex justify-between gap-2 text-sm"><span className="truncate">{item.name}</span><span className="shrink-0 text-neutral-500">{formatBytes(item.traffic_total || item.traffic_used || 0)}</span></div>)}</div> : <p className="text-sm text-neutral-500">Клиентов пока нет</p>}
    </section>
  </div>;
};

export default Metrics;
