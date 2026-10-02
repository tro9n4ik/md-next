import React from 'react';
import { RoutingRules } from '../components/RoutingRules';

const RoutingPage: React.FC = () => {
  return (
    <div>
      <div className="mb-6">
        <h2 className="text-2xl font-bold text-neutral-800">Правила маршрутизации</h2>
        <p className="text-neutral-500 text-sm mt-1">Настройка правил распределения трафика через узел или напрямую</p>
      </div>
      <RoutingRules />
    </div>
  );
};

export default RoutingPage;
