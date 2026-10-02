import React from 'react';
import { NodesManager } from '../components/NodesManager';

const NodesPage: React.FC = () => {
  return (
    <div>
      <div className="mb-6">
        <h2 className="text-2xl font-bold text-neutral-800">Кластерные узлы (Ноды)</h2>
        <p className="text-neutral-500 text-sm mt-1">Управление удаленными нодами и одноразовыми приглашениями</p>
      </div>
      <NodesManager />
    </div>
  );
};

export default NodesPage;
