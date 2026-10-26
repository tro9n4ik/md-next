import { useCurrentUser } from '../contexts/AuthContext';
import { Users } from 'lucide-react';
import PageLayout from '../components/ui/PageLayout';
import ClientsTable from '../components/ClientsTable';

export default function ClientsPage() {
  const user = useCurrentUser();
  const description = user.role === 'admin' ? 'Пользователи, условия подписки и профили подключения'
    : user.view_all_clients ? 'Видны все клиенты. Действия определяются правами вашей учётной записи.' : 'Видны только созданные вами клиенты. Действия определяются правами вашей учётной записи.';
  return <PageLayout title="Клиенты" description={description} icon={Users}>
      <ClientsTable />
  </PageLayout>;
}
