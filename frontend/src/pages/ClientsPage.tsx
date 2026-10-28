import { useCurrentUser } from '../contexts/AuthContext';
import ClientsTable from '../components/ClientsTable';

export default function ClientsPage() {
  const user = useCurrentUser();
  const description = user.role === 'admin' ? 'Пользователи, условия подписки и профили подключения'
    : user.view_all_clients ? 'Видны все клиенты. Действия определяются правами вашей учётной записи.' : 'Видны только созданные вами клиенты. Действия определяются правами вашей учётной записи.';
  return <div className="page-layout clients-page"><ClientsTable description={description} /></div>;
}
