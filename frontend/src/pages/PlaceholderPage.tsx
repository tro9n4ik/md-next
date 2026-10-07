import { useEffect, useState } from 'react';
import { FileCode2, RotateCcw, Shuffle, Upload } from 'lucide-react';
import { apiFetch } from '../utils/api';

type Site = { mode: string; filename: string; size: number; updated_at: string | null; can_restore: boolean };
const scope = 'placeholder-settings';

export default function PlaceholderPage() {
  const [site, setSite] = useState<Site | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [inputKey, setInputKey] = useState(0);

  async function readSite() {
    const response = await apiFetch('/api/v1/settings/placeholder');
    if (!response.ok) throw new Error('Не удалось получить настройки заглушки');
    const metadata: Site = await response.json();
    const page = await apiFetch('/api/v1/settings/placeholder/content');
    return { metadata, html: page.ok ? await page.text() : '' };
  }
  async function load() {
    const data = await readSite(); setSite(data.metadata); setPreview(data.html);
  }
  useEffect(() => {
    let active = true;
    readSite().then(data => { if (active) { setSite(data.metadata); setPreview(data.html); } }).catch(e => { if (active) setError(e.message); });
    return () => { active = false; };
  }, []);
  useEffect(() => {
    const element = document.getElementById(scope);
    window.dispatchEvent(new CustomEvent('ui:unsaved-change', { detail: { scope: element, dirty: !!file } }));
    return () => { window.dispatchEvent(new CustomEvent('ui:unsaved-change', { detail: { scope: element, dirty: false } })); };
  }, [file]);

  async function action(kind: 'upload' | 'generate' | 'restore') {
    if (kind === 'upload' && !file) return;
    setBusy(true); setError(''); setMessage('');
    try {
      const url = kind === 'upload' ? `/api/v1/settings/placeholder?filename=${encodeURIComponent(file!.name)}` : `/api/v1/settings/placeholder/${kind}`;
      const response = await apiFetch(url, kind === 'upload' ? { method: 'PUT', body: file, headers: { 'Content-Type': 'text/html' } } : { method: 'POST' }, scope);
      if (!response.ok) {
        const body = await response.json();
        throw new Error(typeof body.detail === 'string' ? body.detail : 'Не удалось заменить страницу');
      }
      setFile(null); setInputKey(n => n + 1);
      await load();
      setMessage(kind === 'restore' ? 'Предыдущая страница восстановлена' : 'Сайт-заглушка обновлён');
    } catch (e) { setError(e instanceof Error ? e.message : 'Ошибка сохранения'); }
    finally { setBusy(false); }
  }
  // Изоляция HTML пользователя: без скриптов, сети, форм и доступа к панели.
  const safePreview = `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:; base-uri 'none'; form-action 'none'">${preview}`;
  return <section id={scope} data-unsaved-controlled className="ui-card space-y-6">
    <div><h2 className="flex items-center gap-2 text-xl font-semibold"><FileCode2 className="h-5 w-5" />Сайт-заглушка</h2>
      <p className="mt-2 text-sm text-neutral-500">Публичная страница основного домена. Адрес панели и профили VPN сохраняются. При новой установке создаётся библиотека файлов со случайным оформлением.</p></div>
    {error && <p role="alert" className="rounded-xl bg-red-50 p-4 text-red-700">{error}</p>}
    {message && <p role="status" className="rounded-xl bg-emerald-50 p-4 text-emerald-700">{message}</p>}
    {site && <div className="rounded-xl border border-neutral-200 bg-neutral-50 p-4 text-sm"><strong>{site.mode === 'custom' ? 'Свой HTML-файл' : site.mode === 'generated' ? 'Сгенерированная библиотека' : 'Текущая страница'}</strong><p className="mt-1 text-neutral-500">{site.filename} · {(site.size / 1024).toFixed(1)} КБ{site.updated_at && ` · ${new Date(site.updated_at).toLocaleString('ru-RU')}`}</p></div>}
    <div><label htmlFor="placeholder-file" className="mb-2 block font-medium">Заменить своей страницей</label>
      <input key={inputKey} id="placeholder-file" type="file" accept=".html,.htm,text/html" disabled={busy} className="block w-full text-sm file:mr-4 file:rounded-lg file:border-0 file:bg-emerald-50 file:px-4 file:py-2 file:text-emerald-800" onChange={async event => {
        const selected = event.target.files?.[0] || null;
        setError(''); setMessage('');
        if (selected && selected.size > 1024 * 1024) { setError('Размер файла не должен превышать 1 МБ'); event.target.value = ''; return; }
        setFile(selected);
        if (selected) setPreview(await selected.text()); else await load().catch(e => setError(e.message));
      }} />
      <p className="mt-2 text-sm text-neutral-500">Один полный HTML-документ в UTF-8, до 1 МБ. Включите стили и изображения в файл. Скрипты и внешние ресурсы в предпросмотре отключены. Файл сохраняется при обновлении панели.</p></div>
    <div><h3 className="mb-3 font-medium">{file ? 'Предпросмотр выбранного файла' : 'Предпросмотр текущей страницы'}</h3><iframe title="Предпросмотр сайта-заглушки" sandbox="" referrerPolicy="no-referrer" srcDoc={safePreview} className="h-[440px] w-full rounded-xl border border-neutral-200 bg-white" /></div>
    <div className="ui-actionbar flex flex-wrap gap-3">
      <button className="ui-button ui-button-primary" disabled={busy || !file} onClick={() => action('upload')}><Upload className="h-4 w-4" />{busy ? 'Подождите…' : 'Сохранить HTML'}</button>
      <button className="ui-button ui-button-secondary" disabled={busy} onClick={() => { if (window.confirm('Заменить текущую страницу новой библиотекой? Предыдущую можно будет восстановить.')) action('generate'); }}><Shuffle className="h-4 w-4" />Создать новую заглушку</button>
      <button className="ui-button ui-button-secondary" disabled={busy || !site?.can_restore} onClick={() => action('restore')}><RotateCcw className="h-4 w-4" />Вернуть предыдущую</button>
    </div>
  </section>;
}
