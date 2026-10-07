import { useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { Copy, Download, RefreshCw } from 'lucide-react';
import { request } from '../utils/operations';

type Job = { phase: string; message: string; commit?: string; backup?: string };
type Status = { installed: { version: string; commit: string }; job: Job; can_install: boolean };
type Candidate = { commit: string; summary: string; available: boolean; ready: boolean; published_at: string };
const busyPhases = ['queued', 'preparing', 'backup', 'installing', 'checking', 'rolling_back'];

export default function UpdatePage() {
  const [token, setToken] = useState('');
  const [candidate, setCandidate] = useState<Candidate | null>(null);
  const [notice, setNotice] = useState('');
  const status = useQuery<Status>({ queryKey: ['panel-update'], queryFn: () => request('/api/v1/system/updates'), refetchInterval: query => busyPhases.includes(query.state.data?.job.phase || '') ? 3000 : 15000, retry: 1 });
  const busy = busyPhases.includes(status.data?.job.phase || '');
  const check = useMutation({ mutationFn: () => request<Candidate>('/api/v1/system/updates/check', { github_token: token || undefined }), onSuccess: data => { setCandidate(data); setNotice(''); }, onError: error => { setCandidate(null); setNotice(error.message); } });
  const install = useMutation({ mutationFn: () => request<Job>('/api/v1/system/updates/install', { commit: candidate?.commit, confirm: true, github_token: token || undefined }), onSuccess: () => { setToken(''); setCandidate(null); setNotice(''); status.refetch(); }, onError: error => setNotice(error.message) });
  return <>
    <section className="ui-card ui-panel space-y-4">
      <h2 className="ui-card-title">Обновление панели</h2>
      <p className="text-sm text-neutral-600">Установлена версия {status.data?.installed.version || '…'}{status.data?.installed.commit && <> · <code>{status.data.installed.commit.slice(0, 7)}</code></>}. Обновление загружается из официального репозитория MD-Next после прохождения автоматических проверок.</p>
      <details className="text-sm text-neutral-600"><summary className="cursor-pointer">Доступ к приватному репозиторию</summary><label className="mt-3 block">Токен GitHub с правом чтения<input type="password" autoComplete="off" value={token} onChange={event => { setToken(event.target.value); setCandidate(null); }} disabled={busy} className="mt-2 w-full rounded-xl border p-3" /></label><p className="mt-2 text-xs">Оставьте пустым для публичного репозитория или если доступ уже настроен на сервере. Введённый токен не сохраняется в браузере.</p></details>
      <button className="ui-button ui-button-secondary" disabled={busy || check.isPending || install.isPending} onClick={() => check.mutate()}><RefreshCw size={16} />{check.isPending ? 'Проверяем…' : 'Проверить обновления'}</button>
      {candidate && <div className="rounded-xl bg-neutral-50 p-4 space-y-2"><p className="font-medium">{candidate.available ? 'Доступна новая сборка' : 'Установлена актуальная сборка'} · {candidate.commit.slice(0, 7)}</p><p className="text-sm text-neutral-600">{candidate.summary}</p>{!candidate.ready && <p className="text-sm text-amber-700">Сборка ещё не прошла все проверки. Обновление станет доступно после их завершения.</p>}</div>}
    </section>
    <section className="ui-card ui-panel space-y-4">
      <h2 className="ui-card-title">Установка с резервной копией</h2>
      <p className="text-sm text-neutral-600">Сначала будут подготовлены зависимости и интерфейс, затем сохранены файлы панели, база, .env и конфигурации VPN. Если миграция или запуск новой панели завершатся ошибкой, предыдущее состояние восстановится автоматически. Копия остаётся на сервере; сохраните важные данные также на другом устройстве.</p>
      <button className="ui-button ui-button-primary" disabled={busy || install.isPending || !status.data?.can_install || !candidate?.available || !candidate.ready} onClick={() => { if (window.confirm('Обновить панель? Будет создана резервная копия. Панель кратковременно перезапустится; при ошибке будет выполнен откат.')) install.mutate(); }}><Download size={16} />{busy || install.isPending ? 'Обновление выполняется…' : 'Обновить панель'}</button>
      {status.data && !status.data.can_install && <p className="text-sm text-neutral-500">Установка обновлений доступна на серверной панели.</p>}
      <div role="status" aria-live="polite" className="text-sm text-neutral-600">{notice || status.data?.job.message}</div>
      {status.error && <p className="text-sm text-amber-700">{busy ? 'Ожидаем возвращения панели после перезапуска…' : status.error.message}</p>}
      {status.data?.job.backup && <div className="rounded-xl bg-neutral-50 p-3 text-sm"><p className="text-neutral-600">Резервная копия обновления</p><code className="break-all">{status.data.job.backup}</code><button aria-label="Скопировать путь копии" className="ui-button ui-button-secondary mt-2" onClick={() => navigator.clipboard.writeText(status.data!.job.backup!).then(() => setNotice('Путь копии скопирован.')).catch(() => setNotice('Скопируйте путь вручную.'))}><Copy size={15} />Скопировать путь</button></div>}
      {status.data?.job.phase === 'success' && <button className="ui-button ui-button-secondary" onClick={() => window.location.reload()}>Открыть обновлённую панель</button>}
    </section>
  </>;
}
