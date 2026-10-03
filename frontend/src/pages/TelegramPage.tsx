import { Send } from 'lucide-react';
import PageLayout from '../components/ui/PageLayout';
import { TelegramSettings } from '../components/TelegramSettings';

export default function TelegramPage() {
  return <PageLayout title="Telegram-бот" description="Подключение, выход через ноду и уведомления администратора" icon={Send}>
    <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1.35fr)_minmax(0,1fr)]">
    <TelegramSettings />
    <div className="ui-card ui-panel">
      <h3 className="font-bold mb-3">Что умеет бот</h3>
      <dl className="text-sm space-y-3">
        <div><dt className="font-mono">/start · /menu</dt><dd className="text-neutral-500">Инлайн-меню: статус, клиенты, выбор ноды и помощь. Создание клиента и переключение требуют подтверждения.</dd></div>
        <div><dt className="font-mono">/status</dt><dd className="text-neutral-500">Текущий выход и состояние нод.</dd></div>
        <div><dt className="font-mono">/failover</dt><dd className="text-neutral-500">Переключение клиентов на другую активную ноду.</dd></div>
        <div><dt className="font-mono">/add_vless</dt><dd className="text-neutral-500">Создание VLESS-клиента: ссылка подключения и QR-код.</dd></div>
        <div><dt className="font-mono">/add_awg</dt><dd className="text-neutral-500">Создание AmneziaWG-клиента: файл конфигурации.</dd></div>
      </dl>
      <p className="text-xs text-neutral-500 mt-4">Уведомления: недоступность ноды, автоматическое переключение выхода и отключение клиента при превышении лимита трафика. Управление сроками подписки, оплатами и месячными лимитами через команды бота пока не реализовано.</p>
    </div>
    </div>
  </PageLayout>;
}
