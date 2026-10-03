import { Server } from 'lucide-react';
import PageLayout from '../components/ui/PageLayout';
import { NodesManager } from '../components/NodesManager';

export default function NodesPage() {
  return <PageLayout title="Узлы" description="Выход в интернет, резервирование и подключение новых нод" icon={Server}>
      <NodesManager />
  </PageLayout>;
}
