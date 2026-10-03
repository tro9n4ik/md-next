import React, { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Check, AlertCircle, Loader2, ShieldCheck, ShieldOff, KeyRound, QrCode } from 'lucide-react';
import { QRCodeSVG } from 'qrcode.react';
import { apiFetch } from '../utils/api';

interface UserMe {
  id: number;
  username: string;
  totp_enabled: boolean;
}

export const Settings: React.FC = () => {
  const queryClient = useQueryClient();

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

  // Security states
  const [oldPassword, setOldPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [pwdMessage, setPwdMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

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
    </div>
  );
};

export default Settings;
