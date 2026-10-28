import SubscriptionSettings from '../components/SubscriptionSettings';
import PageLayout from '../components/ui/PageLayout';
import { Settings2 } from 'lucide-react';
export default function GeneralSettingsPage() {
  return <PageLayout embedded title="Основные" description="Общие параметры подписок в VPN-приложениях" icon={Settings2}><SubscriptionSettings /></PageLayout>;
}
