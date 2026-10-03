import { Route } from 'lucide-react';
import PageLayout from '../components/ui/PageLayout';
import { RoutingRules } from '../components/RoutingRules';

export default function RoutingPage() {
  return <PageLayout title="Маршрутизация" description="Правила распределения трафика через ноды, напрямую и через WARP" icon={Route}>
      <RoutingRules />
  </PageLayout>;
}
