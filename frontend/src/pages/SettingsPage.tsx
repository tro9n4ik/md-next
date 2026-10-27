import { Archive, Globe, RefreshCw, Send, Settings2, Users } from 'lucide-react';
import { NavLink, Outlet } from 'react-router-dom';
import PageLayout from '../components/ui/PageLayout';
const sections = [
  { path: '/settings', title: 'Основные', icon: Settings2, end: true },
  { path: '/settings/users', title: 'Субадминистраторы', icon: Users },
  { path: '/settings/telegram', title: 'Telegram-бот', icon: Send },
  { path: '/settings/placeholder', title: 'Сайт-заглушка', icon: Globe },
  { path: '/settings/backups', title: 'Резервные копии', icon: Archive },
  { path: '/settings/update', title: 'Обновление панели', icon: RefreshCw },
];

export default function SettingsPage() {
  return <PageLayout title="Настройки" description="Доступ, безопасность и обслуживание панели" icon={Settings2}>
    <nav aria-label="Разделы настроек" className="flex gap-1 overflow-x-auto border-b border-neutral-200">
      {sections.map(({ path, title, icon: Icon, end }) => <NavLink key={path} to={path} end={end} className={({ isActive }) => `flex shrink-0 items-center gap-2 whitespace-nowrap border-b-2 px-3 py-3 text-sm font-medium transition-colors ${isActive ? 'border-emerald-600 text-emerald-700' : 'border-transparent text-neutral-500 hover:text-neutral-800'}`}><Icon size={16} /><span>{title}</span></NavLink>)}
    </nav>
    <Outlet />
  </PageLayout>;
}
