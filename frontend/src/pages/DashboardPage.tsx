import React from 'react';
import Metrics from '../components/Metrics';
import DashboardBottom from '../components/DashboardBottom';

const DashboardPage: React.FC = () => {
  return (
    <div className="space-y-6">
      <div className="mb-6">
        <h2 className="text-2xl font-bold text-neutral-800">Обзор системы</h2>
        <p className="text-neutral-500 text-sm mt-1">Текущее состояние сервера, нагрузка и активные клиенты</p>
      </div>

      <Metrics />
      <DashboardBottom />
    </div>
  );
};

export default DashboardPage;
