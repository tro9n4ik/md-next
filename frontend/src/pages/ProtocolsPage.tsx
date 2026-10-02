import React from 'react';
import ProtocolSettingsTab from '../components/ProtocolSettingsTab';

const ProtocolsPage: React.FC = () => (
  <div className="mx-auto max-w-6xl space-y-6">
    <header>
      <h2 className="text-2xl font-bold tracking-tight text-neutral-900">Протоколы и подключения</h2>
      <p className="mt-1 text-sm text-neutral-500">
        Текущие параметры каналов, Reality и сетевых портов. Изменения проверяются и применяются к конфигурации сервера.
      </p>
    </header>
    <ProtocolSettingsTab />
  </div>
);

export default ProtocolsPage;
