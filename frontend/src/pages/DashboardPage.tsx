import { useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { Activity, ArrowDown, ArrowDownUp, ArrowRight, CircleAlert, Cpu, Globe, Plus, RefreshCw, Server, Users } from 'lucide-react';
import DashboardBottom from '../components/DashboardBottom';
import { apiFetch } from '../utils/api';
import { formatBytes, formatNumber, formatSpeed, translateStatus } from '../utils/ru';

type Stats = { cpu_percent: number; memory: { used: number; total: number }; disk: { used: number; total: number }; uptime: number; net_speed: { rx_bytes_per_sec: number; tx_bytes_per_sec: number } };
type Client = { id: number; connection_status: string; is_active: boolean; expires_at: string | null };
type Node = { id: number; is_enabled: boolean; is_active: boolean; status: string };
type RouteNode = { name: string; ping_ms: number; status: string };
type Route = { server: { hostname: string }; active_node: RouteNode | null; next_candidate: RouteNode | null; failover_mode: string };
type Sample = { time: number; rx: number; tx: number };
async function get<T>(url: string): Promise<T> {
  const response = await apiFetch(url);
  if (!response.ok) throw new Error('Не удалось загрузить данные');
  return response.json();
}

function TrafficChart({ samples }: { samples: Sample[] }) {
  const ceiling = Math.max(1024, ...samples.flatMap(point => [point.rx, point.tx])) * 1.15;
  const x = (index: number) => 48 + index * 702 / Math.max(1, samples.length - 1);
  const y = (value: number) => 206 - value / ceiling * 174;
  const points = (key: 'rx' | 'tx') => samples.map((point, index) => `${x(index)},${y(point[key])}`).join(' ');
  const timeLabel = (time: number) => new Date(time).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  return <div className="traffic-plot">
    <svg viewBox="0 0 770 240" preserveAspectRatio="none" role="img" aria-label="Входящая и исходящая скорость сети за время открытия страницы">
      <defs><linearGradient id="traffic-rx" x1="0" y1="0" x2="0" y2="1"><stop stopColor="#07966c" stopOpacity=".19" /><stop offset="1" stopColor="#07966c" stopOpacity=".01" /></linearGradient></defs>
      {[0, 1, 2, 3, 4].map(tick => <g key={tick}><line x1="48" x2="750" y1={32 + tick * 43.5} y2={32 + tick * 43.5} className="chart-grid" /><text x="38" y={36 + tick * 43.5} textAnchor="end">{formatNumber(ceiling * (4 - tick) / 4 / 1024, 0)}</text></g>)}
      <text x="48" y="17">КБ/с</text>
      {samples.length > 1 && <><polygon points={`${x(0)},206 ${points('rx')} ${x(samples.length - 1)},206`} fill="url(#traffic-rx)" /><polyline points={points('rx')} fill="none" stroke="#07966c" strokeWidth="2.4" strokeLinejoin="round" /><polyline points={points('tx')} fill="none" stroke="#508be8" strokeWidth="2" strokeLinejoin="round" /></>}
      {samples.length > 1 && [0, Math.floor((samples.length - 1) / 2), samples.length - 1].map((index, i) => <text key={i} x={x(index)} y="231" textAnchor={i === 0 ? 'start' : i === 2 ? 'end' : 'middle'}>{timeLabel(samples[index].time)}</text>)}
    </svg>
    {samples.length < 2 && <div className="chart-placeholder">Собираем данные о скорости…<span>График появится после следующего измерения</span></div>}
  </div>;
}

export default function DashboardPage() {
  const queryClient = useQueryClient();
  const stats = useQuery({ queryKey: ['systemStats'], queryFn: () => get<Stats>('/api/v1/system/stats'), refetchInterval: 5000 });
  const traffic = useQuery({ queryKey: ['systemTraffic'], queryFn: () => get<{ last_30_days: { total: number }; today: { total: number } }>('/api/v1/system/traffic'), refetchInterval: 30000 });
  const clients = useQuery({ queryKey: ['clients'], queryFn: () => get<Client[]>('/api/v1/clients'), refetchInterval: 15000 });
  const nodes = useQuery({ queryKey: ['nodes'], queryFn: () => get<Node[]>('/api/v1/nodes'), refetchInterval: 15000 });
  const route = useQuery({ queryKey: ['clusterRoute'], queryFn: () => get<Route>('/api/v1/cluster/route'), refetchInterval: 15000 });
  const [samples, setSamples] = useState<Sample[]>([]);
  useEffect(() => queryClient.getQueryCache().subscribe(event => {
    if (event.query.queryKey[0] !== 'systemStats' || event.type !== 'updated' || event.action.type !== 'success') return;
    const data = event.query.state.data as Stats;
    const point = { time: event.query.state.dataUpdatedAt, rx: data.net_speed.rx_bytes_per_sec, tx: data.net_speed.tx_bytes_per_sec };
    setSamples(previous => previous.at(-1)?.time === point.time ? previous : [...previous, point].filter(sample => sample.time >= point.time - 600000).slice(-120));
  }), [queryClient]);
  const queries = [stats, traffic, clients, nodes, route];
  const refreshing = queries.some(query => query.isFetching);
  const expiring = clients.data?.filter(client => client.is_active && client.expires_at && Date.parse(client.expires_at) > clients.dataUpdatedAt && Date.parse(client.expires_at) <= clients.dataUpdatedAt + 3 * 86400000).length || 0;
  const available = nodes.data?.filter(node => node.is_enabled && node.is_active && node.status === 'healthy').length;
  const current = route.data?.active_node;
  return <div className="page-layout dashboard-page">
    <header className="dashboard-heading"><div><div className="dashboard-eyebrow">ВАША ИНФРАСТРУКТУРА ПОД КОНТРОЛЕМ</div><h1>Обзор системы</h1><p>Контроль подключений и состояния инфраструктуры</p></div><div className="page-actions"><button className="ui-button ui-button-secondary" disabled={refreshing} onClick={() => { void Promise.all([...queries.map(query => query.refetch()), queryClient.refetchQueries({ queryKey: ['systemHealth'] }), queryClient.refetchQueries({ queryKey: ['events'] })]); }}><RefreshCw size={16} className={refreshing ? 'animate-spin' : ''} />Обновить</button><Link to="/clients?create=1" className="ui-button ui-button-primary"><Plus size={18} />Добавить клиента</Link></div></header>
    {expiring > 0 && <Link to="/clients" className="dashboard-notice"><CircleAlert size={19} /><span>Подписки истекают в ближайшие 3 дня: <b>{expiring}</b></span><span className="notice-action">Открыть клиентов <ArrowRight size={16} /></span></Link>}
    <div className="dashboard-metrics">
      <Link to="/clients" className="overview-metric"><span className="metric-icon"><Users size={21} /></span><div><h2>Клиенты</h2><strong>{clients.data ? clients.data.length : '—'}</strong><p><i className="status-dot" />{clients.data ? `${clients.data.filter(client => client.connection_status === 'online').length} онлайн` : clients.isError ? 'Данные недоступны' : 'Загрузка…'}</p></div></Link>
      <section className="overview-metric"><span className="metric-icon"><ArrowDownUp size={21} /></span><div><h2>Трафик за 30 дней</h2><strong>{traffic.data ? formatBytes(traffic.data.last_30_days.total) : '—'}</strong><p>{traffic.data ? `Сегодня: ${formatBytes(traffic.data.today.total)}` : traffic.isError ? 'Данные недоступны' : 'Загрузка…'}</p></div></section>
      <Link to="/nodes" className="overview-metric"><span className="metric-icon"><Server size={21} /></span><div><h2>Узлы</h2><strong>{nodes.data ? <>{available}<span className="metric-divider"> / {nodes.data.length}</span></> : '—'}</strong><p>{nodes.data ? nodes.data.length === 0 ? 'Узлы ещё не добавлены' : available === nodes.data.length ? 'Все доступны' : 'Требуют внимания' : nodes.isError ? 'Данные недоступны' : 'Загрузка…'}</p></div></Link>
      <section className="overview-metric"><span className="metric-icon"><Cpu size={21} /></span><div><h2>Нагрузка CPU</h2><strong>{stats.data ? `${formatNumber(stats.data.cpu_percent, 1)}%` : '—'}</strong><p>{stats.data ? `Память ${formatBytes(stats.data.memory.used)} / ${formatBytes(stats.data.memory.total)}` : stats.isError ? 'Данные недоступны' : 'Загрузка…'}</p></div></section>
    </div>
    {queries.some(query => query.isError) && <div role="alert" className="dashboard-notice"><CircleAlert size={18} /><span>Часть данных недоступна. Попробуйте обновить страницу кнопкой «Обновить».</span></div>}
    <div className="dashboard-middle">
      <section className="ui-card traffic-card"><div className="dashboard-card-heading"><div><h2>Сетевой трафик</h2><p>Скорость сети · последние 10 минут с открытия страницы</p></div><span className="live-label"><i className="status-dot" />В реальном времени</span></div><TrafficChart samples={samples} /><div className="chart-legend"><span><i style={{ background: '#07966c' }} />Входящий <b>{stats.data ? formatSpeed(stats.data.net_speed.rx_bytes_per_sec) : '—'}</b></span><span><i style={{ background: '#508be8' }} />Исходящий <b>{stats.data ? formatSpeed(stats.data.net_speed.tx_bytes_per_sec) : '—'}</b></span></div><div className="server-details"><span>Диск <b>{stats.data ? `${formatBytes(stats.data.disk.used)} / ${formatBytes(stats.data.disk.total)}` : '—'}</b></span><span>Время работы <b>{stats.data ? `${Math.floor(stats.data.uptime / 86400)} д ${Math.floor(stats.data.uptime % 86400 / 3600)} ч` : '—'}</b></span></div></section>
      <section className="ui-card route-card"><div className="dashboard-card-heading"><h2>Активный маршрут</h2><Activity size={18} /></div>{route.data ? <><div className="route-flow"><div className="route-stop"><span className="route-symbol"><Users size={21} /></span><div><b>Клиенты</b><span>Подключения к VPN</span></div></div><ArrowDown className="route-arrow" size={17} /><div className="route-stop"><span className="route-symbol"><Server size={21} /></span><div><b>Основной сервер</b><span>{route.data.server.hostname}</span></div></div><ArrowDown className="route-arrow" size={17} /><div className="route-stop"><span className="route-symbol route-symbol-active"><Globe size={22} /></span><div><b>{current?.name || 'Прямой выход'}</b><span><i className={`status-dot ${current && current.status !== 'healthy' ? 'status-warning' : ''}`} />{current ? translateStatus(current.status) : 'Через основной сервер'}</span>{current && <span>Задержка: {current.ping_ms > 0 ? `${formatNumber(current.ping_ms)} мс` : '—'}</span>}</div></div></div><div className="route-backup"><Server size={16} /><span>{route.data.next_candidate ? `Резерв: ${route.data.next_candidate.name} · ${formatNumber(route.data.next_candidate.ping_ms)} мс` : 'Резервный узел недоступен'}<small>{route.data.failover_mode === 'auto' ? 'Автоматическое переключение' : 'Ручное переключение'}</small></span></div></> : <p className="dashboard-empty">{route.isError ? 'Маршрут недоступен' : 'Загрузка маршрута…'}</p>}<Link className="dashboard-link" to="/nodes">Управление узлами <ArrowRight size={16} /></Link></section>
    </div>
    <DashboardBottom />
    <footer className="dashboard-footer"><RefreshCw size={12} />{stats.dataUpdatedAt ? `Данные сервера обновлены в ${new Date(stats.dataUpdatedAt).toLocaleTimeString('ru-RU')}` : 'Ожидание данных сервера'} · автообновление каждые 5 секунд</footer>
  </div>;
}
