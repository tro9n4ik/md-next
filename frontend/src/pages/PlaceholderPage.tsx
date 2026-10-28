import { useConfirm } from '../contexts/ConfirmationContext';
import { useEffect, useState } from 'react';
import { FileCode2, RotateCcw, Shuffle, Upload } from 'lucide-react';
import { apiFetch } from '../utils/api';
import PageLayout from '../components/ui/PageLayout';

type Site = { mode: string; filename: string; size: number; updated_at: string | null; can_restore: boolean };
const scope = 'placeholder-settings';

export default function PlaceholderPage() {
  const confirm = useConfirm();
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
  return <PageLayout embedded title="Сайт-заглушка" description="Публичная страница основного домена и загрузка собственного HTML" icon={FileCode2}><div id={scope} data-unsaved-controlled className="page-content placeholder-settings">
    {error && <p role="alert" className="rounded-xl bg-red-50 p-4 text-red-700">{error}</p>}
    {message && <p role="status" className="rounded-xl bg-emerald-50 p-4 text-emerald-700">{message}</p>}
    <section className="ui-card ui-panel space-y-4"><h3 className="ui-card-title">Текущая страница</h3>
      <p className="text-sm leading-6 text-neutral-500">Заглушка открывается на основном домене. Адрес панели и профили VPN сохраняются. При новой установке создаётся библиотека файлов со случайным оформлением.</p>
      {site && <div className="rounded-xl border border-neutral-200 bg-neutral-50 p-4 text-sm"><strong>{site.mode === 'custom' ? 'Свой HTML-файл' : site.mode === 'generated' ? 'Сгенерированная библиотека' : 'Текущая страница'}</strong><p className="mt-1 break-all text-neutral-500">{site.filename} · {(site.size / 1024).toFixed(1)} КБ{site.updated_at && ` · ${new Date(site.updated_at).toLocaleString('ru-RU')}`}</p></div>}
    </section>
    <section className="ui-card ui-panel space-y-4"><h3 className="ui-card-title">Загрузить свою страницу</h3>
      <label htmlFor="placeholder-file" className="block text-sm font-medium">HTML-файл</label>
      <input key={inputKey} id="placeholder-file" type="file" accept=".html,.htm,text/html" disabled={busy} className="ui-file-input" aria-describedby="placeholder-file-help" onChange={async event => {
        const selected = event.target.files?.[0] || null;
        setError(''); setMessage('');
        if (selected && selected.size > 1024 * 1024) { setError('Размер файла не должен превышать 1 МБ'); event.target.value = ''; return; }
        setFile(selected);
        if (selected) setPreview(await selected.text()); else await load().catch(e => setError(e.message));
      }} />
      <p id="placeholder-file-help" className="text-sm leading-6 text-neutral-500">Один полный HTML-документ в UTF-8, до 1 МБ. Включите стили и изображения в файл. Файл сохраняется при обновлении панели.</p>
      <div className="placeholder-actions border-t border-neutral-200 pt-4">
      <button className="ui-button ui-button-primary" disabled={busy || !file} onClick={() => action('upload')}><Upload className="h-4 w-4" />{busy ? 'Подождите…' : 'Сохранить HTML'}</button>
      </div>
    </section>
    <section className="ui-card ui-panel space-y-4"><h3 className="ui-card-title">{file ? 'Предпросмотр выбранного файла' : 'Предпросмотр текущей страницы'}</h3><p className="text-sm leading-6 text-neutral-500">Скрипты, формы и внешние ресурсы в предпросмотре отключены.</p><iframe title="Предпросмотр сайта-заглушки" sandbox="" referrerPolicy="no-referrer" srcDoc={safePreview} className="block h-[360px] w-full rounded-xl border border-neutral-200 bg-white sm:h-[440px]" /></section>
    <section className="ui-card ui-panel space-y-5"><h3 className="ui-card-title">Другие способы замены</h3>
      <div className="placeholder-alternative"><div><h4>Создать библиотеку файлов</h4><p>Новое оформление и описание будут сгенерированы автоматически. Перед заменой потребуется подтверждение.</p></div><button className="ui-button ui-button-secondary" disabled={busy} onClick={async () => { if (await confirm({ title: 'Создать новую заглушку?', description: 'Текущая страница будет заменена новой библиотекой. Предыдущую можно будет восстановить.', confirmText: 'Создать заглушку' })) action('generate'); }}><Shuffle className="h-4 w-4" />Создать заглушку</button></div>
      <div className="placeholder-alternative"><div><h4>Восстановить предыдущую страницу</h4><p>{site?.can_restore ? 'Заменит текущую заглушку последней сохранённой версией.' : 'Действие станет доступно после первой замены страницы.'}</p></div><button className="ui-button ui-button-secondary" disabled={busy || !site?.can_restore} onClick={() => action('restore')}><RotateCcw className="h-4 w-4" />Восстановить страницу</button></div>
    </section>
  </div></PageLayout>;
}
