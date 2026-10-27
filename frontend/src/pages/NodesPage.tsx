import { Server } from 'lucide-react';
import PageLayout from '../components/ui/PageLayout';
import { NodesManager } from '../components/NodesManager';

export default function NodesPage() {
  return <PageLayout title="Узлы" description="Активный выход и резервирование" icon={Server}>
      <NodesManager />
  </PageLayout>;
}
