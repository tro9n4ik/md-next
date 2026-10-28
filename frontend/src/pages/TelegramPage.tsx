import { Send } from 'lucide-react';
import PageLayout from '../components/ui/PageLayout';
import { TelegramSettings } from '../components/TelegramSettings';
import TemplateSettings from '../components/TemplateSettings';

export default function TelegramPage({ embedded = false }: { embedded?: boolean }) {
  const content = <>
    <section className="ui-card ui-panel grid gap-5 md:grid-cols-2">
      <div><h2 className="ui-card-title mb-3">Управление из Telegram</h2>
        <p className="text-sm leading-6 text-neutral-600">Один рабочий экран вместо переписки. Администратор управляет подписками, клиент видит только свой доступ. После обработки команды и введённые значения убираются из личного чата.</p>
        <section className="mt-4 text-sm"><h3 className="font-medium text-emerald-700">Простые действия</h3>
          <dl className="mt-3 space-y-2 text-neutral-600">
            <div><dt className="font-medium text-neutral-800">Новая подписка</dt><dd>Имя → проверка условий → подтверждение. Контакты и ограничения меняются по желанию.</dd></div>
            <div><dt className="font-medium text-neutral-800">Подписки</dt><dd>Поиск, срок, общий лимит и лимит обхода БС, контакты и доступ к профилям.</dd></div>
            <div><dt className="font-medium text-neutral-800">Система</dt><dd>Выбор выхода, диагностика и последние события.</dd></div>
            <div><dt className="font-mono text-neutral-800">/menu</dt><dd>Возврат на главный экран, в том числе после удаления переписки.</dd></div>
          </dl>
          <p className="mt-3 text-xs text-neutral-500">Быстрое создание предлагает месяц и трафик без ограничений; перед сохранением условия видны целиком. Настроенные шаблоны также доступны в мастере.</p>
        </section>
      </div>
      <div aria-label="Предпросмотр меню бота" className="grid grid-cols-2 gap-2 rounded-xl bg-neutral-50 p-4 text-center text-sm text-emerald-900">
        <span className="col-span-2 rounded-lg bg-emerald-100 p-3">➕ Новая подписка</span>
        <span className="col-span-2 rounded-lg bg-emerald-100 p-3">📋 Подписки</span>
        {['⚙️ Система', '❔ Помощь'].map(item => <span key={item} className="rounded-lg bg-emerald-100 p-3">{item}</span>)}
      </div>
    </section>
    <TelegramSettings />
    <section className="ui-card ui-panel"><h2 className="ui-card-title">Личный кабинет клиента</h2><p className="mt-3 text-sm leading-6 text-neutral-600">В карточке подписки откройте «Дополнительно → Привязать Telegram». Клиент нажимает «Привязать подписку» в боте и отправляет код обычным сообщением. Код одноразовый, действует 15 минут.</p><p className="mt-3 text-sm leading-6 text-neutral-600">После привязки доступны срок, оба счётчика трафика, подключение и инструкции для Happ, v2rayNG и AmneziaWG. Ссылка и QR-код открываются на личной странице; файлы выдаются только по запросу. Старые команды продолжают работать, но запоминать их не нужно.</p><p className="mt-3 text-sm leading-6 text-neutral-600">Уведомления собираются в разделе «Система → События» и обновляют рабочий экран. Во время ввода или подтверждения доставка ждёт завершения действия. Редактирование сообщения не вызывает нового звукового уведомления Telegram. Старые сообщения, которые Telegram уже не разрешает удалить, могут остаться в переписке.</p></section>
    <TemplateSettings />
  </>;
  return <PageLayout embedded={embedded} title="Telegram-бот" description="Подписки, инлайн-меню, выход через ноду и уведомления" icon={Send}>{content}</PageLayout>;
}
