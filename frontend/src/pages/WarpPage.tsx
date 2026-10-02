import React, { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Activity, CheckCircle2, Cloud, Loader2, Play, Power, ShieldCheck } from 'lucide-react';
import { apiFetch } from '../utils/api';
import { translateStatus } from '../utils/ru';

interface WarpStatus {
  installed: boolean; service_active: boolean; registered: boolean;
  state: string; mode: string; port: number; instruction?: string | null;
}
interface WarpUsage { usage: 'off' | 'rules' | 'all' }

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
  const [testResult, setTestResult] = useState<{ ip: string; country: string; warp: string } | null>(null);
  const status = useQuery<WarpStatus>({ queryKey: ['warp-status'], queryFn: () => request('/api/v1/warp/status'), refetchInterval: 10000 });
  const usage = useQuery<WarpUsage>({ queryKey: ['warp-usage'], queryFn: () => request('/api/v1/warp/usage') });
  React.useEffect(() => {
    if (status.data) { setMode(status.data.mode === 'warp' ? 'warp' : 'proxy'); setPort(status.data.port || 40000); }
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
  const busy = command.isPending || usageMutation.isPending;
  const card = 'rounded-2xl border border-neutral-200 bg-white p-5 shadow-sm';
  const label = 'mb-1.5 block text-sm font-medium text-neutral-600';
  const input = 'w-full rounded-xl border border-neutral-300 bg-white px-3 py-2 text-sm outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-100';

  return <div className="mx-auto max-w-5xl space-y-6">
    <div><h1 className="text-2xl font-bold text-neutral-900">Cloudflare WARP</h1><p className="mt-1 text-sm text-neutral-500">Состояние службы, подключение и маршрутизация трафика.</p></div>
    <section className={card}>
      <div className="mb-4 flex items-center gap-3"><div className="rounded-xl bg-indigo-50 p-2.5 text-indigo-600"><Cloud className="h-5 w-5" /></div><div><h2 className="font-semibold">Состояние</h2><p className="text-sm text-neutral-500">Обновляется автоматически</p></div></div>
      {status.isLoading ? <Loader2 className="h-5 w-5 animate-spin text-neutral-400" /> : <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {[["Установлен", status.data?.installed ? 'Да' : 'Нет'], ["Служба warp-svc", status.data?.service_active ? 'Работает' : 'Остановлена'], ["Зарегистрирован", status.data?.registered ? 'Да' : 'Нет'], ["Состояние", translateStatus(status.data?.state)], ["Режим", translateStatus(status.data?.mode)], ["Порт SOCKS5", String(status.data?.port || "—")]].map(([title, value]) => <div key={title} className="rounded-xl bg-neutral-50 p-3"><div className="text-xs text-neutral-500">{title}</div><div className="mt-1 flex items-center gap-2 font-semibold"><span className={`h-2 w-2 rounded-full ${value === 'Да' || value === 'Работает' || value === 'Подключено' ? 'bg-emerald-500' : 'bg-amber-500'}`} />{value}</div></div>)}
      </div>}
      {status.data?.instruction && <p className="mt-4 rounded-lg bg-amber-50 p-3 text-sm text-amber-800">{status.data.instruction}</p>}
      <div className="mt-4 flex flex-wrap gap-2">
        <button disabled={busy || !status.data?.installed} onClick={() => command.mutate({ path: '/api/v1/warp/register' })} className="rounded-xl bg-indigo-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50">Зарегистрировать</button>
        <button disabled={busy || !status.data?.installed} onClick={() => command.mutate({ path: '/api/v1/warp/connect' })} className="flex items-center gap-2 rounded-xl bg-emerald-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"><Play className="h-4 w-4" />Подключить</button>
        <button disabled={busy || !status.data?.installed} onClick={() => command.mutate({ path: '/api/v1/warp/disconnect' })} className="flex items-center gap-2 rounded-xl border border-neutral-300 px-4 py-2 text-sm font-medium text-neutral-700 disabled:opacity-50"><Power className="h-4 w-4" />Отключить</button>
      </div>
    </section>

    <div className="grid gap-6 lg:grid-cols-2">
      <section className={card}><h2 className="mb-4 font-semibold">Режим и порт прокси</h2><div className="grid gap-4 sm:grid-cols-2"><div><label className={label}>Режим</label><select className={input} value={mode} onChange={e => setMode(e.target.value as 'proxy' | 'warp')}><option value="proxy">Прокси</option><option value="warp">WARP</option></select></div><div><label className={label}>Порт SOCKS5</label><input className={input} type="number" min={1} max={65535} value={port} onChange={e => setPort(Number(e.target.value))} /></div></div><button disabled={busy || !status.data?.installed} onClick={() => command.mutate({ path: '/api/v1/warp/mode', body: { mode, port } })} className="mt-4 rounded-xl bg-neutral-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50">Сохранить режим</button></section>
      <section className={card}><h2 className="mb-4 font-semibold">Лицензия WARP+</h2><label className={label}>Лицензионный ключ</label><input className={input} type="password" autoComplete="off" value={license} onChange={e => setLicense(e.target.value)} placeholder="Ключ не сохраняется в панели" /><button disabled={busy || !license || !status.data?.installed} onClick={() => command.mutate({ path: '/api/v1/warp/license', body: { key: license } }, { onSuccess: () => setLicense('') })} className="mt-4 rounded-xl border border-neutral-300 px-4 py-2 text-sm font-medium disabled:opacity-50">Применить ключ</button></section>
    </div>

    <section className={card}><div className="mb-4 flex items-center gap-3"><ShieldCheck className="h-5 w-5 text-indigo-600" /><h2 className="font-semibold">Использование WARP в Xray</h2></div><select className={input} value={usage.data?.usage || 'off'} disabled={busy} onChange={e => usageMutation.mutate(e.target.value as WarpUsage['usage'])}><option value="off">Выключен</option><option value="rules">По правилам маршрутизации</option><option value="all">Весь трафик</option></select><p className="mt-3 text-sm text-neutral-500">В режиме «По правилам» WARP применяется только для правил с действием «WARP». Режим «Весь трафик» использует WARP по умолчанию. Если выбрана активная нода выхода, Xray продолжит направлять трафик через неё. Трафик AmneziaWG всегда выходит напрямую с этого сервера.</p></section>

    <section className={card}><div className="mb-3 flex items-center gap-3"><Activity className="h-5 w-5 text-emerald-600" /><h2 className="font-semibold">Проверка соединения</h2></div><p className="mb-4 text-sm text-neutral-500">Проверить внешний IP через локальный SOCKS5-прокси WARP.</p><button disabled={busy || !status.data?.installed} onClick={() => command.mutate({ path: '/api/v1/warp/test' }, { onSuccess: setTestResult })} className="rounded-xl bg-indigo-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50">Проверить</button>{testResult && <div className="mt-4 flex flex-wrap gap-4 rounded-xl bg-emerald-50 p-4 text-sm text-emerald-900"><span>IP: <b>{testResult.ip || '—'}</b></span><span>Страна: <b>{testResult.country || '—'}</b></span><span>WARP: <b>{testResult.warp}</b></span><CheckCircle2 className="ml-auto h-5 w-5" /></div>}</section>
    {notice && <div role="status" className="rounded-xl border border-neutral-200 bg-white px-4 py-3 text-sm text-neutral-700">{notice}</div>}
  </div>;
};

export default WarpPage;
