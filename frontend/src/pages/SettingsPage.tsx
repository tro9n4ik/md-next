import { Activity, Archive, Globe, RefreshCw, Send, Settings2, ShieldCheck, Stethoscope, ScrollText, Users, BookOpen, ArrowUpRight } from 'lucide-react';
import { Link, NavLink, Outlet, useLocation } from 'react-router-dom';
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
  const { pathname } = useLocation();
  const visibleGroups = groups.filter(group => isAdmin || !group.admin);
  const currentGroup = visibleGroups.find(group => group.sections.some(section => section.path === pathname)) ?? visibleGroups[0];
  return <PageLayout title="Настройки" description="Управление панелью, обслуживание и безопасность аккаунта" icon={Settings2}>
    <div className="settings-layout">
      <nav aria-label="Разделы настроек" className="settings-navigation">
        <div className="settings-category-row"><div className="settings-categories">{visibleGroups.map(group => <Link key={group.title} to={group.sections[0].path} className={`settings-category ${group === currentGroup ? 'is-active' : ''}`} aria-current={group === currentGroup ? 'true' : undefined}>{group.title}</Link>)}</div>
        {isAdmin && <Link to="/wiki" className="settings-help"><BookOpen size={16} /><span>Помощь</span><ArrowUpRight size={14} /></Link>}</div>
        <div className="settings-section-tabs">{currentGroup.sections.map(({ path, title, icon: Icon, ...options }) => <NavLink key={path} to={path} end={'end' in options && options.end} className={({ isActive }) => `settings-section-tab ${isActive ? 'is-active' : ''}`}><Icon size={16} /><span>{title}</span></NavLink>)}</div>
      </nav>
      <div className="settings-body"><Outlet /></div>
    </div>
  </PageLayout>;
}
