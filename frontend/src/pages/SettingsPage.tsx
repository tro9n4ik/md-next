import { Settings2 } from 'lucide-react';
import PageLayout from '../components/ui/PageLayout';
import Settings from '../components/Settings';

export default function SettingsPage() {
  return <PageLayout title="Настройки" description="Пароль администратора и двухфакторная аутентификация" icon={Settings2}>
      <Settings />
  </PageLayout>;
}
