import { Send } from 'lucide-react';
import PageLayout from '../components/ui/PageLayout';
import { TelegramSettings } from '../components/TelegramSettings';

export default function TelegramPage() {
  return <PageLayout title="Telegram-бот" description="Подписки, инлайн-меню, выход через ноду и уведомления" icon={Send}>
    <section className="ui-card ui-panel grid gap-5 md:grid-cols-2">
      <div><h2 className="ui-card-title mb-3">Управление из Telegram</h2>
        <p className="text-sm leading-6 text-neutral-600">Подписки с контактами, сроком и месячным трафиком. Перед созданием — итоговая карточка. В списке — состояние, расход и данные подключения.</p>
        <details className="mt-3 text-sm"><summary className="cursor-pointer font-medium text-emerald-700">Команды и возможности</summary>
          <dl className="mt-3 space-y-2 text-neutral-600">
            <div><dt className="font-mono text-neutral-800">/menu</dt><dd>Главное меню.</dd></div>
            <div><dt className="font-mono text-neutral-800">/new_subscription</dt><dd>Имя, телефон, почта, срок и лимит. Контакты можно пропустить.</dd></div>
            <div><dt className="font-mono text-neutral-800">/subscriptions</dt><dd>Карточки подписок, ссылка с QR-кодом и файл AmneziaWG.</dd></div>
            <div><dt className="font-mono text-neutral-800">/status · /failover</dt><dd>Состояние и переключение выхода.</dd></div>
          </dl>
          <p className="mt-3 text-xs text-neutral-500">Трафик по умолчанию без ограничений. Месячный период — от даты создания. /add_vless и /add_awg также открывают мастер.</p>
        </details>
      </div>
      <div aria-label="Предпросмотр меню бота" className="grid grid-cols-2 gap-2 rounded-xl bg-neutral-50 p-4 text-center text-sm text-emerald-900">
        <span className="col-span-2 rounded-lg bg-emerald-100 p-3">➕ Новая подписка</span>
        {['📋 Подписки', '📊 Статус', '🌐 Ноды', '❔ Помощь'].map(item => <span key={item} className="rounded-lg bg-emerald-100 p-3">{item}</span>)}
      </div>
    </section>
    <TelegramSettings />
  </PageLayout>;
}
