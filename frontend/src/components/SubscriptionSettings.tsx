import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiFetch } from '../utils/api';

export default function SubscriptionSettings() {
  const cache = useQueryClient();
  const [draft, setDraft] = useState<string | null>(null);
  const [notice, setNotice] = useState('');
  const settings = useQuery<{ name: string }>({ queryKey: ['subscription-settings'], queryFn: async () => {
    const response = await apiFetch('/api/v1/settings/subscription');
    if (!response.ok) throw new Error('Не удалось загрузить название подписки');
    return response.json();
  } });
  const name = draft ?? settings.data?.name ?? 'MD-NEXT';
  const save = useMutation({ mutationFn: async () => {
    const response = await apiFetch('/api/v1/settings/subscription', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: name.trim() }) }, 'subscription-settings-form');
    if (!response.ok) throw new Error('Не удалось сохранить название подписки');
    return response.json();
  }, onSuccess: data => { cache.setQueryData(['subscription-settings'], data); setDraft(null); setNotice('Сохранено. Обновите подписку в VPN-приложении.'); }, onError: (error: Error) => setNotice(error.message) });
  return <form id="subscription-settings-form" className="ui-card ui-panel" onSubmit={event => { event.preventDefault(); save.mutate(); }}>
    <h2 className="ui-card-title">Название подписки</h2>
    <p className="mt-2 text-sm text-neutral-500">Имя подписки в VPN-приложениях.</p>
    <div className="mt-4 flex flex-col gap-3 sm:flex-row sm:items-end"><label className="flex-1 text-sm font-medium" htmlFor="subscription-name">Название (до 25 символов)
    <input id="subscription-name" className="mt-2 block w-full rounded-xl border border-neutral-300 px-3 py-2" maxLength={25} required value={name} disabled={settings.isLoading || settings.isError || save.isPending} onChange={event => setDraft(event.target.value)} /></label><button type="submit" className="ui-button ui-button-primary" disabled={settings.isLoading || settings.isError || save.isPending || !name.trim()}>{save.isPending ? 'Сохранение…' : 'Сохранить'}</button></div>
    {settings.isError && <p role="alert" className="mt-3 text-sm text-red-700">Не удалось загрузить настройки. Обновите страницу.</p>}
    {notice && <p role="status" className="mt-3 text-sm">{notice}</p>}
  </form>;
}
