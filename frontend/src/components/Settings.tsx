import React, { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Send, Check, AlertCircle, Loader2, ShieldCheck, ShieldOff, KeyRound, QrCode } from 'lucide-react';
import { QRCodeSVG } from 'qrcode.react';
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
}

interface UserMe {
  id: number;
  username: string;
  totp_enabled: boolean;
}

export const Settings: React.FC = () => {
  const queryClient = useQueryClient();
  const [activeTab, setActiveTab] = useState<'telegram' | 'security'>('telegram');

  // User 2FA state
  const { data: userMe } = useQuery<UserMe>({
    queryKey: ['me'],
    queryFn: async () => {
      const res = await apiFetch('/api/v1/auth/me');
      if (!res.ok) throw new Error('Не удалось загрузить данные пользователя');
      return res.json();
    }
  });

  // 2FA States
  const [totpSetupData, setTotpSetupData] = useState<{ secret: string; otpauth_url: string } | null>(null);
  const [totpCode, setTotpCode] = useState('');
  const [totpCurrentCode, setTotpCurrentCode] = useState('');
  const [totpMessage, setTotpMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);
  const [showDisable2FA, setShowDisable2FA] = useState(false);

  // Telegram states
  const [tgToken, setTgToken] = useState('');
  const [tgAdminId, setTgAdminId] = useState('');
  const [tgProxyUrl, setTgProxyUrl] = useState('');
  const [notifyNodeDown, setNotifyNodeDown] = useState(true);
  const [notifyFailover, setNotifyFailover] = useState(true);
  const [notifyQuota, setNotifyQuota] = useState(true);
  const [tgMessage, setTgMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  // Security states
  const [oldPassword, setOldPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [pwdMessage, setPwdMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  const { data: tgSettings, isLoading: tgLoading } = useQuery<TelegramSettings>({
    queryKey: ['telegramSettings'],
    queryFn: async () => {
      const res = await apiFetch('/api/v1/settings/telegram');
      if (!res.ok) throw new Error('Не удалось загрузить настройки Telegram');
      const data = await res.json();
      setTgAdminId(data.admin_id || '');
      setTgProxyUrl(data.proxy_url || '');
      setNotifyNodeDown(data.notify_node_down ?? true);
      setNotifyFailover(data.notify_failover ?? true);
      setNotifyQuota(data.notify_quota ?? true);
      return data;
    }
  });

  const saveTgMutation = useMutation({
    mutationFn: async (payload: {
      token?: string;
      admin_id: string;
      proxy_url: string;
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
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['telegramSettings'] });
      setTgMessage({ type: 'success', text: 'Настройки Telegram успешно сохранены и применены!' });
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

  const setup2FAMutation = useMutation({
    mutationFn: async (currentCode?: string) => {
      setTotpMessage(null);
      const res = await apiFetch('/api/v1/auth/2fa/setup', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(currentCode ? { current_code: currentCode } : {})
      });
      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.detail || 'Ошибка инициализации 2FA');
      }
      return res.json();
    },
    onSuccess: (data) => {
      setTotpSetupData(data);
      setTotpCode('');
      setTotpCurrentCode('');
      setTotpMessage({ type: 'success', text: 'Секрет 2FA сгенерирован. Сканируйте QR-код приложением и введите код подтверждения.' });
    },
    onError: (err: Error) => {
      setTotpMessage({ type: 'error', text: err.message });
    }
  });

  const verify2FAMutation = useMutation({
    mutationFn: async (code: string) => {
      setTotpMessage(null);
      const res = await apiFetch('/api/v1/auth/2fa/verify', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code })
      });
      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.detail || 'Ошибка подтверждения 2FA');
      }
      return res.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['me'] });
      setTotpSetupData(null);
      setTotpCode('');
      setTotpMessage({ type: 'success', text: 'Двухфакторная аутентификация (2FA) успешно активирована!' });
    },
    onError: (err: Error) => {
      setTotpMessage({ type: 'error', text: err.message });
    }
  });

  const disable2FAMutation = useMutation({
    mutationFn: async (currentCode: string) => {
      setTotpMessage(null);
      const res = await apiFetch('/api/v1/auth/2fa/disable', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ current_code: currentCode })
      });
      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.detail || 'Ошибка отключения 2FA');
      }
      return res.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['me'] });
      setTotpSetupData(null);
      setTotpCurrentCode('');
      setShowDisable2FA(false);
      setTotpMessage({ type: 'success', text: 'Двухфакторная аутентификация успешно отключена.' });
    },
    onError: (err: Error) => {
      setTotpMessage({ type: 'error', text: err.message });
    }
  });

  const changePwdMutation = useMutation({
    mutationFn: async (payload: { old_password: string; new_password: string }) => {
      setPwdMessage(null);
      const res = await apiFetch('/api/v1/auth/password', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.detail || 'Ошибка смены пароля');
      }
      return res.json();
    },
    onSuccess: () => {
      setPwdMessage({ type: 'success', text: 'Пароль администратора успешно изменён!' });
      setOldPassword('');
      setNewPassword('');
      setConfirmPassword('');
    },
    onError: (err: Error) => {
      setPwdMessage({ type: 'error', text: err.message });
    }
  });

  const handleTgSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    saveTgMutation.mutate({
      token: tgToken.trim() || undefined,
      admin_id: tgAdminId.trim(),
      proxy_url: tgProxyUrl.trim(),
      notify_node_down: notifyNodeDown,
      notify_failover: notifyFailover,
      notify_quota: notifyQuota
    });
  };

  const handlePwdSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (newPassword !== confirmPassword) {
      setPwdMessage({ type: 'error', text: 'Новые пароли не совпадают' });
      return;
    }
    if (newPassword.length < 10) {
      setPwdMessage({ type: 'error', text: 'Новый пароль должен содержать минимум 10 символов' });
      return;
    }
    changePwdMutation.mutate({
      old_password: oldPassword,
      new_password: newPassword
    });
  };

  return (
    <div className="bg-white rounded-xl border border-neutral-200/60 shadow-sm p-6 max-w-3xl">
      <div className="flex space-x-2 border-b border-neutral-100 pb-3 mb-6">
        <button
          onClick={() => setActiveTab('telegram')}
          className={`px-4 py-2 text-sm font-semibold rounded-xl transition-all ${
            activeTab === 'telegram' ? 'bg-emerald-600 text-white shadow-sm' : 'text-neutral-500 hover:text-neutral-800 hover:bg-neutral-50'
          }`}
        >
          Telegram-бот
        </button>
        <button
          onClick={() => setActiveTab('security')}
          className={`px-4 py-2 text-sm font-semibold rounded-xl transition-all ${
            activeTab === 'security' ? 'bg-emerald-600 text-white shadow-sm' : 'text-neutral-500 hover:text-neutral-800 hover:bg-neutral-50'
          }`}
        >
          Безопасность
        </button>
      </div>

      {activeTab === 'telegram' ? (
        <div className="space-y-6">
          <div>
            <h3 className="text-base font-bold text-neutral-800 mb-1">Уведомления в Telegram</h3>
            <p className="text-xs text-neutral-500">Настройка бота для получения алертов о состоянии кластера и событиях</p>
          </div>

          {tgMessage && (
            <div className={`p-3.5 rounded-xl text-xs flex items-center space-x-2 ${
              tgMessage.type === 'success' ? 'bg-emerald-50 text-emerald-700 border border-emerald-200' : 'bg-red-50 text-red-700 border border-red-200'
            }`}>
              {tgMessage.type === 'success' ? <Check className="w-4 h-4 shrink-0" /> : <AlertCircle className="w-4 h-4 shrink-0" />}
              <span>{tgMessage.text}</span>
            </div>
          )}

          {tgLoading ? (
            <div className="text-sm text-neutral-500">Загрузка настроек...</div>
          ) : (
            <form onSubmit={handleTgSubmit} className="space-y-4">
              <div>
                <label className="block text-xs font-semibold text-neutral-500 uppercase mb-1">Токен Telegram Бота</label>
                <input
                  type="password"
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
                  onChange={(e) => setTgAdminId(e.target.value)}
                  placeholder="Например: 123456789"
                  className="w-full px-3.5 py-2.5 bg-neutral-50 border border-neutral-200 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/20 focus:border-emerald-500 font-mono"
                />
              </div>

              <div>
                <label className="block text-xs font-semibold text-neutral-500 uppercase mb-1">SOCKS5 Прокси (Опционально)</label>
                <input
                  type="text"
                  value={tgProxyUrl}
                  onChange={(e) => setTgProxyUrl(e.target.value)}
                  placeholder="socks5://127.0.0.1:10808"
                  className="w-full px-3.5 py-2.5 bg-neutral-50 border border-neutral-200 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/20 focus:border-emerald-500 font-mono"
                />
              </div>

              <div className="pt-2 border-t border-neutral-100 space-y-2">
                <label className="block text-xs font-semibold text-neutral-500 uppercase mb-1">Типы уведомлений</label>
                <label className="flex items-center space-x-2 text-xs text-neutral-700 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={notifyNodeDown}
                    onChange={(e) => setNotifyNodeDown(e.target.checked)}
                    className="rounded border-neutral-300 text-emerald-600 focus:ring-emerald-500"
                  />
                  <span>Нода недоступна</span>
                </label>
                <label className="flex items-center space-x-2 text-xs text-neutral-700 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={notifyFailover}
                    onChange={(e) => setNotifyFailover(e.target.checked)}
                    className="rounded border-neutral-300 text-emerald-600 focus:ring-emerald-500"
                  />
                  <span>Переключение на другую ноду (Failover)</span>
                </label>
              </div>

              <label className="flex cursor-pointer items-center space-x-2 text-xs text-neutral-700">
                <input type="checkbox" checked={notifyQuota} onChange={(e) => setNotifyQuota(e.target.checked)} className="rounded border-neutral-300 text-emerald-600 focus:ring-emerald-500" />
                <span>Отключение клиента при превышении лимита трафика</span>
              </label>

              <div className="flex items-center space-x-3 pt-2">
                <button
                  type="submit"
                  disabled={saveTgMutation.isPending}
                  className="px-5 py-2.5 bg-emerald-600 hover:bg-emerald-700 text-white font-medium rounded-xl text-xs shadow-sm transition-colors disabled:opacity-50 flex items-center space-x-2"
                >
                  {saveTgMutation.isPending && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                  <span>Сохранить и применить</span>
                </button>

                <button
                  type="button"
                  onClick={() => sendTestTgMutation.mutate()}
                  disabled={sendTestTgMutation.isPending || !tgSettings?.token_set}
                  className="px-4 py-2.5 bg-neutral-100 hover:bg-neutral-200 text-neutral-700 font-medium rounded-xl text-xs transition-colors disabled:opacity-50 flex items-center space-x-1.5"
                >
                  <Send className="w-3.5 h-3.5 text-neutral-500" />
                  <span>Отправить тестовое сообщение</span>
                </button>
              </div>
            </form>
          )}
        </div>
      ) : activeTab === 'security' ? (
        <div className="space-y-6">
          <div>
            <h3 className="text-base font-bold text-neutral-800 mb-1">Смена пароля администратора</h3>
            <p className="text-xs text-neutral-500">Пароль должен состоять минимум из 10 символов</p>
          </div>

          {pwdMessage && (
            <div className={`p-3.5 rounded-xl text-xs flex items-center space-x-2 ${
              pwdMessage.type === 'success' ? 'bg-emerald-50 text-emerald-700 border border-emerald-200' : 'bg-red-50 text-red-700 border border-red-200'
            }`}>
              {pwdMessage.type === 'success' ? <Check className="w-4 h-4 shrink-0" /> : <AlertCircle className="w-4 h-4 shrink-0" />}
              <span>{pwdMessage.text}</span>
            </div>
          )}

          <form onSubmit={handlePwdSubmit} className="space-y-4">
            <div>
              <label className="block text-xs font-semibold text-neutral-500 uppercase mb-1">Текущий пароль</label>
              <input
                type="password"
                required
                value={oldPassword}
                onChange={(e) => setOldPassword(e.target.value)}
                className="w-full px-3.5 py-2.5 bg-neutral-50 border border-neutral-200 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/20 focus:border-emerald-500"
              />
            </div>

            <div>
              <label className="block text-xs font-semibold text-neutral-500 uppercase mb-1">Новый пароль (мин. 10 символов)</label>
              <input
                type="password"
                required
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                className="w-full px-3.5 py-2.5 bg-neutral-50 border border-neutral-200 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/20 focus:border-emerald-500"
              />
            </div>

            <div>
              <label className="block text-xs font-semibold text-neutral-500 uppercase mb-1">Подтвердите новый пароль</label>
              <input
                type="password"
                required
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                className="w-full px-3.5 py-2.5 bg-neutral-50 border border-neutral-200 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/20 focus:border-emerald-500"
              />
            </div>

            <button
              type="submit"
              disabled={changePwdMutation.isPending}
              className="px-5 py-2.5 bg-emerald-600 hover:bg-emerald-700 text-white font-medium rounded-xl text-xs shadow-sm transition-colors disabled:opacity-50 flex items-center space-x-2"
            >
              {changePwdMutation.isPending && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
              <span>Изменить пароль</span>
            </button>
          </form>

          {/* Двухфакторная аутентификация (2FA) */}
          <div className="pt-6 border-t border-neutral-100 space-y-4">
            <div>
              <h3 className="text-base font-bold text-neutral-800 mb-1">Двухфакторная аутентификация (2FA)</h3>
              <p className="text-xs text-neutral-500">Защита аккаунта одноразовыми кодами TOTP (Google Authenticator / YubiKey)</p>
            </div>

            {totpMessage && (
              <div className={`p-3.5 rounded-xl text-xs flex items-center space-x-2 ${
                totpMessage.type === 'success' ? 'bg-emerald-50 text-emerald-700 border border-emerald-200' : 'bg-red-50 text-red-700 border border-red-200'
              }`}>
                {totpMessage.type === 'success' ? <Check className="w-4 h-4 shrink-0" /> : <AlertCircle className="w-4 h-4 shrink-0" />}
                <span>{totpMessage.text}</span>
              </div>
            )}

            {userMe?.totp_enabled ? (
              <div className="p-4 bg-emerald-50/60 rounded-xl border border-emerald-200/80 space-y-3">
                <div className="flex items-center space-x-2.5 text-emerald-800">
                  <ShieldCheck className="w-5 h-5 text-emerald-600 shrink-0" />
                  <span className="text-xs font-bold">Двухфакторная аутентификация ВКЛЮЧЕНА</span>
                </div>
                <p className="text-xs text-neutral-600">Ваш аккаунт защищён TOTP. При каждом входе требуется ввести код из аутентификатора.</p>

                <div className="flex items-center space-x-3 pt-2">
                  <button
                    type="button"
                    onClick={() => {
                      if (!totpCurrentCode) {
                        setTotpMessage({ type: 'error', text: 'Введите текущий код 2FA перед сменой секрета.' });
                        return;
                      }
                      setup2FAMutation.mutate(totpCurrentCode);
                    }}
                    className="px-4 py-2 bg-white border border-neutral-200 hover:bg-neutral-50 text-neutral-700 font-medium rounded-xl text-xs transition-colors flex items-center space-x-1.5"
                  >
                    <KeyRound className="w-3.5 h-3.5 text-neutral-500" />
                    <span>Сменить 2FA</span>
                  </button>

                  <button
                    type="button"
                    onClick={() => setShowDisable2FA(!showDisable2FA)}
                    className="px-4 py-2 bg-red-50 hover:bg-red-100 text-red-700 border border-red-200 font-medium rounded-xl text-xs transition-colors flex items-center space-x-1.5"
                  >
                    <ShieldOff className="w-3.5 h-3.5 text-red-500" />
                    <span>Отключить 2FA</span>
                  </button>
                </div>

                <div className="pt-2">
                  <label className="block text-xs font-semibold text-neutral-500 uppercase mb-1">Текущий код 2FA (для смены или отключения)</label>
                  <input
                    type="text"
                    maxLength={6}
                    value={totpCurrentCode}
                    onChange={(e) => setTotpCurrentCode(e.target.value.trim())}
                    placeholder="123456"
                    className="w-48 px-3.5 py-2 bg-white border border-neutral-200 rounded-xl text-sm font-mono focus:outline-none focus:ring-2 focus:ring-emerald-500/20"
                  />
                </div>

                {showDisable2FA && (
                  <div className="pt-3 border-t border-emerald-200/60">
                    <button
                      type="button"
                      disabled={disable2FAMutation.isPending || !totpCurrentCode}
                      onClick={() => disable2FAMutation.mutate(totpCurrentCode)}
                      className="px-4 py-2 bg-red-600 hover:bg-red-700 text-white font-medium rounded-xl text-xs shadow-sm transition-colors disabled:opacity-50 flex items-center space-x-1.5"
                    >
                      {disable2FAMutation.isPending && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                      <span>Подтвердить отключение 2FA</span>
                    </button>
                  </div>
                )}
              </div>
            ) : (
              <div className="p-4 bg-neutral-50 rounded-xl border border-neutral-200 space-y-3">
                <div className="flex items-center space-x-2.5 text-neutral-700">
                  <ShieldOff className="w-5 h-5 text-neutral-400 shrink-0" />
                  <span className="text-xs font-bold">2FA отключена</span>
                </div>
                <p className="text-xs text-neutral-500">Включите двухфакторную аутентификацию для максимальной защиты панели управления.</p>

                {!totpSetupData ? (
                  <button
                    type="button"
                    disabled={setup2FAMutation.isPending}
                    onClick={() => setup2FAMutation.mutate(undefined)}
                    className="px-4 py-2.5 bg-emerald-600 hover:bg-emerald-700 text-white font-medium rounded-xl text-xs shadow-sm transition-colors disabled:opacity-50 flex items-center space-x-2"
                  >
                    {setup2FAMutation.isPending && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                    <QrCode className="w-4 h-4" />
                    <span>Настроить 2FA</span>
                  </button>
                ) : (
                  <div className="mt-4 p-4 bg-white rounded-xl border border-neutral-200 space-y-4">
                    <div className="flex flex-col sm:flex-row items-center space-y-3 sm:space-y-0 sm:space-x-6">
                      <div className="p-2 bg-white border border-neutral-200 rounded-xl">
                        <QRCodeSVG value={totpSetupData.otpauth_url} size={150} />
                      </div>
                      <div className="space-y-2">
                        <p className="text-xs font-semibold text-neutral-700">Отсканируйте QR-код в Google Authenticator или1Password</p>
                        <p className="text-[11px] text-neutral-400">Или введите секрет вручную:</p>
                        <code className="block p-2 bg-neutral-100 rounded-lg text-xs font-mono font-bold text-neutral-800 select-all">
                          {totpSetupData.secret}
                        </code>
                      </div>
                    </div>

                    <div className="pt-3 border-t border-neutral-100 space-y-3">
                      <label className="block text-xs font-semibold text-neutral-600">Код подтверждения из приложения</label>
                      <div className="flex items-center space-x-3">
                        <input
                          type="text"
                          maxLength={6}
                          value={totpCode}
                          onChange={(e) => setTotpCode(e.target.value.trim())}
                          placeholder="123456"
                          className="w-40 px-3.5 py-2 bg-neutral-50 border border-neutral-200 rounded-xl text-sm font-mono focus:outline-none focus:ring-2 focus:ring-emerald-500/20"
                        />
                        <button
                          type="button"
                          disabled={verify2FAMutation.isPending || totpCode.length < 6}
                          onClick={() => verify2FAMutation.mutate(totpCode)}
                          className="px-4 py-2 bg-emerald-600 hover:bg-emerald-700 text-white font-medium rounded-xl text-xs shadow-sm transition-colors disabled:opacity-50 flex items-center space-x-1.5"
                        >
                          {verify2FAMutation.isPending && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                          <span>Подтвердить и активировать</span>
                        </button>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      ) : null}
    </div>
  );
};

export default Settings;
