import { Activity, Archive, Globe, RefreshCw, Send, Settings2, ShieldCheck, Stethoscope, ScrollText, Users, BookOpen, ArrowUpRight } from 'lucide-react';
import { Link, NavLink, Outlet } from 'react-router-dom';
import PageLayout from '../components/ui/PageLayout';
import { useCurrentUser } from '../contexts/AuthContext';
const groups = [
  { title: 'Панель', admin: true, sections: [
  { path: '/settings', title: 'Основные', icon: Settings2, end: true },
  { path: '/settings/users', title: 'Субадминистраторы', icon: Users },
  { path: '/settings/telegram', title: 'Telegram-бот', icon: Send },
  { path: '/settings/placeholder', title: 'Сайт-заглушка', icon: Globe },
  ] },
  { title: 'Обслуживание', admin: true, sections: [
    { path: '/settings/diagnostics', title: 'Диагностика', icon: Stethoscope },
    { path: '/settings/history', title: 'История нод', icon: Activity },
    { path: '/settings/events', title: 'Журнал событий', icon: ScrollText },
    { path: '/settings/backups', title: 'Резервные копии', icon: Archive },
    { path: '/settings/update', title: 'Обновление панели', icon: RefreshCw },
  ] },
  { title: 'Личное', admin: false, sections: [
    { path: '/settings/account', title: 'Моя учётная запись', icon: ShieldCheck },
  ] },
];

export default function SettingsPage() {
  const isAdmin = useCurrentUser().role === 'admin';
  return <PageLayout title="Настройки" description="Управление панелью, обслуживание и безопасность аккаунта" icon={Settings2}>
    <div className="settings-layout">
      <nav aria-label="Разделы настроек" className="settings-nav ui-card">
        {groups.filter(group => isAdmin || !group.admin).map(group => <div className="settings-nav-group" key={group.title}>
          <p>{group.title}</p>
          {group.sections.map(({ path, title, icon: Icon, ...options }) => <NavLink key={path} to={path} end={'end' in options && options.end} className={({ isActive }) => `settings-nav-link ${isActive ? 'is-active' : ''}`}><Icon size={17} /><span>{title}</span></NavLink>)}
        </div>)}
        {isAdmin && <Link to="/wiki" className="settings-wiki-link"><BookOpen size={17} /><span>Нужна подсказка? Открыть Wiki</span><ArrowUpRight size={15} /></Link>}
      </nav>
      <div className="settings-body"><Outlet /></div>
    </div>
  </PageLayout>;
}
