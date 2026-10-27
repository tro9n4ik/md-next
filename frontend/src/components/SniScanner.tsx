import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { apiFetch } from '../utils/api';

type Result = {
  domain: string; target: string; eligible: boolean; reason: string;
  ip?: string | null; tls_version?: string | null; alpn?: string | null;
  latency_ms?: number | null; http_status?: number | null;
};
type Scan = { results: Result[]; checked_at: string; note: string };

export default function SniScanner({ onSelect, disabled }: { onSelect: (domain: string, target: string) => void; disabled: boolean }) {
  const [domains, setDomains] = useState('www.microsoft.com\nwww.apple.com\nwww.bing.com\nwww.wikipedia.org');
  const [selected, setSelected] = useState('');
  const scan = useMutation<Scan, Error, string[]>({
    mutationFn: async candidates => {
      const response = await apiFetch('/api/v1/settings/protocols/sni/check', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ domains: candidates }),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : 'Не удалось выполнить проверку доменов');
      return body;
    },
    onMutate: () => { setSelected(''); },
  });
  const candidates = domains.split(/[\s,;]+/).filter(Boolean);
  return <details className="mt-5 border-t border-neutral-200 pt-4" data-sni-scanner>
    <summary className="cursor-pointer text-sm font-medium">Подобрать SNI и target</summary>
    <div className="mt-4 space-y-4">
      <p className="text-xs leading-5 text-neutral-500">Проверка с сервера панели: TLS 1.3, HTTP/2, сертификат домена и HTTP-ответ без перенаправления. Укажите до 12 публичных доменов, по одному на строке. Проверяется только порт 443.</p>
      <label className="block text-xs font-semibold text-neutral-600">Домены для проверки<textarea rows={4} value={domains} maxLength={4096} disabled={scan.isPending} onChange={event => { setDomains(event.target.value); scan.reset(); setSelected(''); }} className="mt-2 w-full rounded-xl border border-neutral-200 bg-white p-3 font-mono text-sm text-neutral-800" /></label>
      <button type="button" className="ui-button ui-button-secondary" disabled={disabled || scan.isPending || !candidates.length || candidates.length > 12} onClick={() => scan.mutate(candidates)}>{scan.isPending ? 'Проверяю домены…' : 'Проверить домены'}</button>
      {candidates.length > 12 && <p role="alert" className="text-xs text-red-700">За один запуск можно проверить до 12 доменов.</p>}
      {scan.isError && <p role="alert" className="text-sm text-red-700">{scan.error.message}</p>}
      {scan.data && !scan.isPending && <div>
        <p className="mb-3 text-xs text-neutral-500">{scan.data.note} Проверено: {new Date(scan.data.checked_at).toLocaleString('ru-RU')}.</p>
        <ul aria-label="Результаты проверки SNI" className="divide-y divide-neutral-100">{scan.data.results.map(row => <li key={row.domain} className="flex flex-wrap items-center justify-between gap-3 py-3">
          <div className="min-w-0 flex-1"><p className="break-all text-sm font-semibold">{row.domain}</p><p className="mt-1 break-all text-xs text-neutral-500">{[row.ip, row.tls_version, row.alpn, row.latency_ms != null ? `${row.latency_ms} мс` : null, row.http_status ? `HTTP ${row.http_status}` : null].filter(Boolean).join(' · ')}</p><p className={`mt-1 text-xs ${row.eligible ? 'text-emerald-700' : 'text-amber-700'}`}>{row.reason}</p></div>
          <button type="button" aria-label={`Выбрать SNI: ${row.domain}`} disabled={!row.eligible || disabled} className="ui-button ui-button-secondary" onClick={() => { onSelect(row.domain, row.target); setSelected(row.domain); }}>Выбрать</button>
        </li>)}</ul>
      </div>}
      {selected && <p role="status" className="text-xs text-emerald-700">SNI и target для {selected} перенесены в форму. Для применения сохраните параметры; клиентам потребуется обновить подписку.</p>}
      <p className="text-xs leading-5 text-neutral-500">Базовая совместимость не гарантирует незаметность или доступность из сети клиента. Проверка не меняет конфигурацию. При выборе сохраняются адрес панели, ключи и short ID.</p>
    </div>
  </details>;
}
