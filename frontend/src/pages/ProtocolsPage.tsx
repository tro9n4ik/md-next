import { Radio } from 'lucide-react';
import PageLayout from '../components/ui/PageLayout';
import ProtocolSettingsTab from '../components/ProtocolSettingsTab';

export default function ProtocolsPage() {
  return <PageLayout title="Протоколы" description="Каналы подключения, параметры Reality и сетевые порты" icon={Radio}>
      <ProtocolSettingsTab />
  </PageLayout>;
}
