import Switch from './ui/Switch';
import React, { useEffect, useRef, useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Send, Check, AlertCircle, Loader2 } from 'lucide-react';
import { apiFetch } from '../utils/api';

interface TelegramSettings {
  token_set: boolean;
  token_masked: string;
  admin_id: string;
  proxy_url: string;
  notify_node_down: boolean;
  notify_failover: boolean;
  notify_quota: boolean;
  bot_status: string;
  bot_error: string | null;
  use_node: boolean;
  node_id: number | null;
}

type TelegramMessage = { type: 'success' | 'error'; text: string } | null;

export const TelegramSettings: React.FC = () => {
  const [tgMessage, setTgMessage] = useState<TelegramMessage>(null);
  const { data, dataUpdatedAt, isLoading, error } = useQuery<TelegramSettings>({
    queryKey: ['telegramSettings'],
    refetchOnWindowFocus: false,
    queryFn: async () => {
      const res = await apiFetch('/api/v1/settings/telegram');
      if (!res.ok) throw new Error('Не удалось загрузить настройки Telegram');
      return res.json();
    },
  });
  if (isLoading) return <p className="text-sm text-neutral-500">Загрузка настроек...</p>;
  if (error || !data) return <p className="text-sm text-red-600">{error?.message || 'Настройки недоступны'}</p>;
  return <TelegramForm key={dataUpdatedAt} tgSettings={data} tgMessage={tgMessage} setTgMessage={setTgMessage} />;
};

const TelegramForm: React.FC<{ tgSettings: TelegramSettings; tgMessage: TelegramMessage; setTgMessage: React.Dispatch<React.SetStateAction<TelegramMessage>> }> = ({ tgSettings, tgMessage, setTgMessage }) => {
  const queryClient = useQueryClient();
  // Telegram states
  const [tgToken, setTgToken] = useState('');
  const [tgAdminId, setTgAdminId] = useState(tgSettings.admin_id);
  const [notifyNodeDown, setNotifyNodeDown] = useState(tgSettings.notify_node_down);
  const [notifyFailover, setNotifyFailover] = useState(tgSettings.notify_failover);
  const [notifyQuota, setNotifyQuota] = useState(tgSettings.notify_quota);
  const sectionRef = useRef<HTMLDivElement>(null);
  const dirty = !!tgToken.trim() || tgAdminId.trim() !== tgSettings.admin_id
    || notifyNodeDown !== tgSettings.notify_node_down || notifyFailover !== tgSettings.notify_failover || notifyQuota !== tgSettings.notify_quota;
  useEffect(() => {
    const scope = sectionRef.current;
    window.dispatchEvent(new CustomEvent('ui:unsaved-change', { detail: { scope, dirty } }));
    return () => { window.dispatchEvent(new CustomEvent('ui:unsaved-change', { detail: { scope, dirty: false } })); };
  }, [dirty]);

  const saveTgMutation = useMutation({
    mutationFn: async (payload: {
      token?: string;
      admin_id: string;
      notify_node_down: boolean;
      notify_failover: boolean;
      notify_quota: boolean;
    }) => {
      setTgMessage(null);
      const res = await apiFetch('/api/v1/settings/telegram', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.detail || 'Ошибка сохранения настроек Telegram');
      }
      return res.json();
    },
    onSuccess: (data: TelegramSettings) => {
      queryClient.invalidateQueries({ queryKey: ['telegramSettings'] });
      setTgMessage(data.bot_status === 'error'
        ? { type: 'error', text: data.bot_error || 'Настройки сохранены, но бот не смог подключиться.' }
        : { type: 'success', text: 'Настройки Telegram сохранены и применены.' });
      setTgToken('');
    },
    onError: (err: Error) => {
      setTgMessage({ type: 'error', text: err.message });
    }
  });

  const sendTestTgMutation = useMutation({
    mutationFn: async () => {
      setTgMessage(null);
      const res = await apiFetch('/api/v1/settings/telegram/test', { method: 'POST' });
      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.detail || 'Не удалось отправить тестовое сообщение');
      }
      return res.json();
    },
    onSuccess: () => {
      setTgMessage({ type: 'success', text: 'Тестовое сообщение успешно отправлено в Telegram!' });
    },
    onError: (err: Error) => {
      setTgMessage({ type: 'error', text: err.message });
    }
  });

  const handleTgSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    saveTgMutation.mutate({
      token: tgToken.trim() || undefined,
      admin_id: tgAdminId.trim(),
      notify_node_down: notifyNodeDown,
      notify_failover: notifyFailover,
      notify_quota: notifyQuota,
    });
  };

  return (
    <div ref={sectionRef} data-unsaved-controlled className="ui-card ui-panel">
        <div className="space-y-6">
          <div>
            <h3 className="text-base font-bold text-neutral-800 mb-1">Подключение бота</h3>
            <p className="text-xs text-neutral-500">Управление доступно указанному администратору. Клиенты получают личный кабинет после привязки по одноразовому коду. Сначала отправьте боту /start.</p>
            <p className="text-sm mt-3">Состояние: {tgSettings?.bot_status === 'running' ? 'Работает' : tgSettings?.bot_status === 'error' ? 'Ошибка подключения' : 'Выключен'}</p>
            {tgSettings?.bot_error && <p className="text-xs text-red-600 mt-1">{tgSettings.bot_error}</p>}
          </div>

          {tgMessage && (
            <div className={`p-3.5 rounded-xl text-xs flex items-center space-x-2 ${
              tgMessage.type === 'success' ? 'bg-emerald-50 text-emerald-700 border border-emerald-200' : 'bg-red-50 text-red-700 border border-red-200'
            }`}>
              {tgMessage.type === 'success' ? <Check className="w-4 h-4 shrink-0" /> : <AlertCircle className="w-4 h-4 shrink-0" />}
              <span>{tgMessage.text}</span>
            </div>
          )}

            <form onSubmit={handleTgSubmit} autoComplete="off" className="space-y-4">
              <p className="rounded-xl bg-neutral-50 p-4 text-sm text-neutral-600">Бот работает через активную ноду из раздела «Узлы». При переключении выхода подключение обновляется автоматически.</p>
              <div>
                <label className="block text-xs font-semibold text-neutral-500 uppercase mb-1">Токен Telegram Бота</label>
                <input
                  type="password"
                  name="telegram-bot-token"
                  autoComplete="new-password"
                  value={tgToken}
                  onChange={(e) => setTgToken(e.target.value)}
                  placeholder={tgSettings?.token_set ? `Задан (${tgSettings.token_masked})` : 'Введите токен от @BotFather'}
                  className="w-full px-3.5 py-2.5 bg-neutral-50 border border-neutral-200 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/20 focus:border-emerald-500 font-mono"
                />
                <p className="text-[11px] text-neutral-400 mt-1">Оставьте пустым, если не хотите менять действующий токен.</p>
              </div>

              <div>
                <label className="block text-xs font-semibold text-neutral-500 uppercase mb-1">Telegram ID Администратора</label>
                <input
                  type="text"
                  value={tgAdminId}
                  name="telegram-admin-id"
                  autoComplete="off"
                  inputMode="numeric"
                  pattern="[0-9]*"
                  onChange={(e) => setTgAdminId(e.target.value)}
                  placeholder="Например: 123456789"
                  className="w-full px-3.5 py-2.5 bg-neutral-50 border border-neutral-200 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/20 focus:border-emerald-500 font-mono"
                />
              </div>

              <div className="pt-2 border-t border-neutral-100 space-y-2">
                <label className="block text-xs font-semibold text-neutral-500 uppercase mb-1">Типы уведомлений</label>
                <label className="flex items-center space-x-2 text-xs text-neutral-700 cursor-pointer">
                  <Switch label="Нода недоступна" checked={notifyNodeDown} onChange={setNotifyNodeDown} />
                  <span>Нода недоступна</span>
                </label>
                <label className="flex items-center space-x-2 text-xs text-neutral-700 cursor-pointer">
                  <Switch label="Переключение на другую ноду" checked={notifyFailover} onChange={setNotifyFailover} />
                  <span>Переключение на другую ноду (Failover)</span>
                </label>
              </div>

              <label className="flex cursor-pointer items-center space-x-2 text-xs text-neutral-700">
                <Switch label="Сроки и лимиты подписок" checked={notifyQuota} onChange={setNotifyQuota} />
                <span>Сроки подписок и расход 80–100% лимита</span>
              </label>

              <div className="ui-actionbar mt-6">
                <button
                  type="submit"
                  disabled={saveTgMutation.isPending}
                  className="ui-button ui-button-primary"
                >
                  {saveTgMutation.isPending && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                  <span>Сохранить и применить</span>
                </button>

                <button
                  type="button"
                  onClick={() => sendTestTgMutation.mutate()}
                  disabled={sendTestTgMutation.isPending || saveTgMutation.isPending || tgSettings?.bot_status !== 'running'}
                  className="ui-button ui-button-secondary"
                >
                  <Send className="w-3.5 h-3.5 text-neutral-500" />
                  <span>Отправить тестовое сообщение</span>
                </button>
              </div>
            </form>
        </div>
    </div>
  );
};
