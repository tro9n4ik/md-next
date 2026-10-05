import { Settings2 } from 'lucide-react';
import PageLayout from '../components/ui/PageLayout';
import Settings from '../components/Settings';
import SubscriptionSettings from '../components/SubscriptionSettings';

export default function SettingsPage() {
  return <PageLayout title="Настройки" description="Название подписки, пароль администратора и двухфакторная аутентификация" icon={Settings2}>
      <SubscriptionSettings /><Settings />
  </PageLayout>;
}
