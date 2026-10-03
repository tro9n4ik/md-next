import { LayoutDashboard } from 'lucide-react';
import PageLayout from '../components/ui/PageLayout';
import Metrics from '../components/Metrics';
import DashboardBottom from '../components/DashboardBottom';

export default function DashboardPage() {
  return <PageLayout title="Обзор системы" description="Текущее состояние сервера, нагрузка и активные клиенты" icon={LayoutDashboard}>
      <Metrics />
      <DashboardBottom />
  </PageLayout>;
}
