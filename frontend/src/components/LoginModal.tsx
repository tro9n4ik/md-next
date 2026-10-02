import React, { useState } from 'react';
import { apiFetch } from '../utils/api';
import { Lock, ShieldCheck, User } from 'lucide-react';

interface LoginModalProps {
  onLoginSuccess: (token: string) => void;
}

export const LoginModal: React.FC<LoginModalProps> = ({ onLoginSuccess }) => {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [totpCode, setTotpCode] = useState('');
  const [isTotpRequired, setIsTotpRequired] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setLoading(true);

    try {
      const res = await apiFetch('/api/v1/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          username,
          password,
          totp_code: totpCode || null,
        }),
      });

      const data = await res.json();

      if (!res.ok) {
        throw new Error(data.detail || 'Ошибка авторизации');
      }

      if (data.totp_required) {
        setIsTotpRequired(true);
        setLoading(false);
        return;
      }

      if (data.access_token) {
        localStorage.setItem('token', data.access_token);
        onLoginSuccess(data.access_token);
      }
    } catch (err: any) {
      setError(err.message || 'Ошибка соединения с сервером');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-neutral-900/60 backdrop-blur-sm flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-2xl max-w-md w-full p-8 shadow-2xl border border-neutral-100">
        <div className="text-center mb-6">
          <div className="inline-flex p-3 bg-neutral-100 rounded-2xl mb-3">
            <Lock className="w-8 h-8 text-neutral-800" />
          </div>
          <h2 className="text-2xl font-bold text-neutral-800">Вход в панель MD-Next</h2>
          <p className="text-neutral-500 text-sm mt-1">
            {isTotpRequired ? 'Введите код двухфакторной аутентификации' : 'Введите учётные данные администратора'}
          </p>
        </div>

        {error && (
          <div className="mb-4 p-3 bg-red-50 text-red-600 rounded-xl text-sm text-center font-medium">
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          {!isTotpRequired ? (
            <>
              <div>
                <label className="block text-xs font-semibold text-neutral-500 uppercase mb-1">
                  Логин
                </label>
                <div className="relative">
                  <User className="w-5 h-5 absolute left-3 top-1/2 -translate-y-1/2 text-neutral-400" />
                  <input
                    type="text"
                    required
                    value={username}
                    onChange={(e) => setUsername(e.target.value)}
                    className="w-full pl-10 pr-4 py-2.5 bg-neutral-50 border border-neutral-200 rounded-xl text-neutral-800 focus:outline-none focus:ring-2 focus:ring-neutral-900"
                    placeholder="admin"
                  />
                </div>
              </div>

              <div>
                <label className="block text-xs font-semibold text-neutral-500 uppercase mb-1">
                  Пароль
                </label>
                <div className="relative">
                  <Lock className="w-5 h-5 absolute left-3 top-1/2 -translate-y-1/2 text-neutral-400" />
                  <input
                    type="password"
                    required
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    className="w-full pl-10 pr-4 py-2.5 bg-neutral-50 border border-neutral-200 rounded-xl text-neutral-800 focus:outline-none focus:ring-2 focus:ring-neutral-900"
                    placeholder="••••••••"
                  />
                </div>
              </div>
            </>
          ) : (
            <div>
              <label className="block text-xs font-semibold text-neutral-500 uppercase mb-1">
                Код 2FA (Authenticator)
              </label>
              <div className="relative">
                <ShieldCheck className="w-5 h-5 absolute left-3 top-1/2 -translate-y-1/2 text-neutral-400" />
                <input
                  type="text"
                  required
                  maxLength={6}
                  autoFocus
                  value={totpCode}
                  onChange={(e) => setTotpCode(e.target.value)}
                  className="w-full pl-10 pr-4 py-2.5 bg-neutral-50 border border-neutral-200 rounded-xl text-neutral-800 focus:outline-none focus:ring-2 focus:ring-neutral-900 tracking-widest font-mono text-center text-lg"
                  placeholder="123456"
                />
              </div>
            </div>
          )}

          <button
            type="submit"
            disabled={loading}
            className="w-full py-3 bg-neutral-900 text-white font-medium rounded-xl hover:bg-neutral-800 transition-colors shadow-sm disabled:opacity-50"
          >
            {loading ? 'Вход...' : isTotpRequired ? 'Подтвердить 2FA' : 'Войти'}
          </button>
        </form>
      </div>
    </div>
  );
};
