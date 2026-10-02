import React from 'react';
import ClientsTable from '../components/ClientsTable';

const ClientsPage: React.FC = () => {
  return (
    <div>
      <div className="mb-6">
        <h2 className="text-2xl font-bold text-neutral-800">Управление клиентами</h2>
        <p className="text-neutral-500 text-sm mt-1">Список пользователей VPN, генерация конфигов VLESS Reality и AmneziaWG</p>
      </div>
      <ClientsTable />
    </div>
  );
};

export default ClientsPage;
