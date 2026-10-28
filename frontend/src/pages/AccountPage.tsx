import { ShieldCheck } from 'lucide-react';
import Settings from '../components/Settings';
import PageLayout from '../components/ui/PageLayout';

export default function AccountPage({ embedded = false }: { embedded?: boolean }) {
  return <PageLayout embedded={embedded} title="Моя учётная запись" description="Пароль и двухфакторная аутентификация" icon={ShieldCheck}><Settings /></PageLayout>;
}
