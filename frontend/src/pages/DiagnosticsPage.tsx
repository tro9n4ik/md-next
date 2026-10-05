import { useState } from 'react';
import { Stethoscope, Download } from 'lucide-react';
import { useMutation } from '@tanstack/react-query';
import PageLayout from '../components/ui/PageLayout';
import { request, saveFile } from '../utils/operations';

type Report = { checked_at: string; notice: string; checks: { key: string; name: string; ok: boolean | null; detail: string; elapsed_ms: number | null }[] };
export default function DiagnosticsPage() {
  const [report, setReport] = useState<Report | null>(null);
  const run = useMutation({ mutationFn: () => request<Report>('/api/v1/operations/diagnostics', {}), onSuccess: setReport });
  return <PageLayout title="Диагностика" description="Проверки DNS, выхода через Xray, WARP и CDN" icon={Stethoscope}>
    <section className="ui-card ui-panel space-y-4"><p className="text-sm text-neutral-600">Проверка выполняется на сервере. Для подтверждения работы в сети с белыми списками нужен отдельный тест с телефона.</p>
      <button disabled={run.isPending} onClick={() => run.mutate()} className="ui-button ui-button-primary">{run.isPending ? 'Проверяем сервер…' : 'Запустить проверку'}</button>
      {run.error && <p role="alert" className="text-red-700">{run.error.message}</p>}
    </section>
    {report && <section className="ui-card ui-panel space-y-4"><div className="flex flex-wrap justify-between gap-3"><p className="text-sm text-neutral-500">{new Date(report.checked_at).toLocaleString('ru')}</p><button className="ui-button ui-button-secondary" onClick={() => saveFile(JSON.stringify(report, null, 2), 'md-next-diagnostics.json')}><Download size={16} />Скачать отчёт</button></div>
      {report.checks.map(check => <div key={check.key} className="rounded-xl border border-neutral-200 p-4"><h2 className="font-medium text-neutral-800">{check.ok === null ? '○' : check.ok ? '✓' : '✕'} {check.name}</h2><p className="mt-1 text-sm text-neutral-600">{check.detail}</p>{check.elapsed_ms !== null && <span className="text-xs text-neutral-500">{check.elapsed_ms} мс</span>}</div>)}
      <p className="text-sm text-amber-700">{report.notice}</p>
    </section>}
  </PageLayout>;
}
