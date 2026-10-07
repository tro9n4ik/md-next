import { Archive, Globe, RefreshCw, Send, Settings2 } from 'lucide-react';
import { NavLink, Outlet } from 'react-router-dom';
import PageLayout from '../components/ui/PageLayout';
const sections = [
  { path: '/settings', title: 'Основные', icon: Settings2, end: true },
  { path: '/settings/telegram', title: 'Telegram-бот', icon: Send },
  { path: '/settings/placeholder', title: 'Сайт-заглушка', icon: Globe },
  { path: '/settings/backups', title: 'Резервные копии', icon: Archive },
  { path: '/settings/update', title: 'Обновление панели', icon: RefreshCw },
];

export default function SettingsPage() {
  return <PageLayout title="Настройки" description="Параметры панели, публичный сайт, Telegram-бот, резервные копии и обновления" icon={Settings2}>
    <nav aria-label="Разделы настроек" className="grid grid-cols-2 gap-2 xl:grid-cols-5">
      {sections.map(({ path, title, icon: Icon, end }) => <NavLink key={path} to={path} end={end} className={({ isActive }) => `ui-button justify-center ${isActive ? 'ui-button-primary' : 'ui-button-secondary'}`}><Icon size={17} /><span>{title}</span></NavLink>)}
    </nav>
    <Outlet />
  </PageLayout>;
}
