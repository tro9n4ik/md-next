import { Users } from 'lucide-react';
import PageLayout from '../components/ui/PageLayout';
import ClientsTable from '../components/ClientsTable';

export default function ClientsPage() {
  return <PageLayout title="Клиенты" description="Пользователи, условия подписки и профили подключения" icon={Users}>
      <ClientsTable />
  </PageLayout>;
}
