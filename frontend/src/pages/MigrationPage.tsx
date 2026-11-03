import { useQuery } from '@tanstack/react-query';
import { ArrowRightLeft, CheckCircle2, CircleAlert, Clock3 } from 'lucide-react';
import PageLayout from '../components/ui/PageLayout';
import { apiFetch } from '../utils/api';
import MigrationTransfer from '../components/MigrationTransfer';

type Check = { key: string; title: string; status: string; message: string };
type Report = { checked_at: string; counts: Record<string, number>; checks: Check[];
  addresses: (Check & {host: string})[]; preserve: string[]; steps: string[] };
function Status({ status }: {status: string}) {
  const Icon = status === 'ok' ? CheckCircle2 : status === 'pending' ? Clock3 : CircleAlert;
  return <Icon size={18} aria-label={status === 'ok' ? 'Проверено' : status === 'pending' ? 'Ожидает проверки' : 'Требует внимания'} className={status === 'ok' ? 'text-emerald-600' : 'text-amber-600'} />;
}
export default function MigrationPage() {
  const report = useQuery<Report>({queryKey: ['migration-source'], retry: false, refetchOnWindowFocus: false,
    queryFn: async () => { const response = await apiFetch('/api/v1/migration/source'); if (!response.ok) throw new Error('Не удалось проверить текущую установку'); return response.json(); }});
  const data = report.data;
  return <PageLayout embedded title="Переезд на другой VPS" description="Подготовка переноса с сохранением клиентских подключений" icon={ArrowRightLeft}>
    <section className="ui-card ui-panel migration-intro"><div><span className="settings-section-label">ПЕРЕЕЗД В ДВА ДЕЙСТВИЯ</span><h3 className="ui-card-title mt-2">Подготовьте новый сервер и проверьте перенос</h3><p>Сначала мастер установит чистый проверенный билд, создаст зашифрованный бэкап и восстановит данные на новом VPS. Финальная синхронизация и активация выполняются после вашего подтверждения.</p></div></section>
    {report.isPending && <p role="status">Проверяем адреса и состав данных…</p>}
    {report.error && <p role="alert" className="client-form-error">{report.error.message}</p>}
    <MigrationTransfer />
    {data && <>
      <div className="migration-counts">{[['clients','Клиентов'],['profiles','Профилей'],['nodes','Нод'],['pinned_clients','С закреплённым выходом']].map(([key,label]) => <div className="ui-card" key={key}><span>{label}</span><b>{data.counts[key]}</b></div>)}</div>
      <section className="ui-card ui-panel"><h3 className="ui-card-title">Что проверено</h3><div className="migration-checks">{data.checks.map(check => <div key={check.key}><Status status={check.status} /><div><b>{check.title}</b><p>{check.message}</p></div></div>)}</div></section>
      <section className="ui-card ui-panel"><h3 className="ui-card-title">Адреса подключений</h3><div className="migration-checks">{data.addresses.map(item => <div key={item.key}><Status status={item.status} /><div><b>{item.title}</b><code>{item.host || 'Адрес не задан'}</code><p>{item.message}</p></div></div>)}</div><p className="migration-note">Домен позволяет сохранить адрес подключения, но действующие соединения не переедут автоматически. Возможен короткий перерыв и переподключение.</p></section>
      <p className="migration-note">Рабочий сервер продолжает обслуживать подключения до финальной синхронизации. DNS переключается вручную после активации. Проверено: {new Date(data.checked_at).toLocaleString('ru-RU')}</p>
    </>}
  </PageLayout>;
}
