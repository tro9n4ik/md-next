import { TelegramSettings } from '../components/TelegramSettings';

export default function TelegramPage() {
  return <div className="space-y-6">
    <div>
      <h2 className="text-2xl font-bold text-neutral-800">Telegram-бот</h2>
      <p className="text-neutral-500 text-sm mt-1">Управление панелью и уведомления для администратора</p>
    </div>
    <TelegramSettings />
    <div className="max-w-3xl bg-white border border-neutral-200 rounded-xl p-6">
      <h3 className="font-bold mb-3">Что умеет бот</h3>
      <dl className="text-sm space-y-3">
        <div><dt className="font-mono">/start</dt><dd className="text-neutral-500">Справка по командам.</dd></div>
        <div><dt className="font-mono">/status</dt><dd className="text-neutral-500">Текущий выход и состояние нод.</dd></div>
        <div><dt className="font-mono">/failover</dt><dd className="text-neutral-500">Переключение клиентов на другую активную ноду.</dd></div>
        <div><dt className="font-mono">/add_vless</dt><dd className="text-neutral-500">Создание VLESS-клиента: ссылка подключения и QR-код.</dd></div>
        <div><dt className="font-mono">/add_awg</dt><dd className="text-neutral-500">Создание AmneziaWG-клиента: файл конфигурации.</dd></div>
      </dl>
      <p className="text-xs text-neutral-500 mt-4">Уведомления: недоступность ноды, автоматическое переключение выхода и отключение клиента при превышении лимита трафика. Управление сроками подписки, оплатами и месячными лимитами через команды бота пока не реализовано.</p>
    </div>
  </div>;
}
