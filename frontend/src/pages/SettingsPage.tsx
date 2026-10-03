import React from 'react';
import Settings from '../components/Settings';

const SettingsPage: React.FC = () => {
  return (
    <div>
      <div className="mb-6">
        <h2 className="text-2xl font-bold text-neutral-800">Настройки системы</h2>
        <p className="text-neutral-500 text-sm mt-1">Пароль администратора и двухфакторная аутентификация</p>
      </div>
      <Settings />
    </div>
  );
};

export default SettingsPage;
