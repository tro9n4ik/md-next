import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiFetch } from '../utils/api';
import { Check, Radio, Save, Smartphone } from 'lucide-react';

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
  const dirty = draft !== null && draft !== settings.data?.name;
  const save = useMutation({ mutationFn: async () => {
    const response = await apiFetch('/api/v1/settings/subscription', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: name.trim() }) }, 'subscription-settings-form');
    if (!response.ok) throw new Error('Не удалось сохранить название подписки');
    return response.json();
  }, onSuccess: data => { cache.setQueryData(['subscription-settings'], data); setDraft(null); setNotice('Сохранено. Обновите подписку в VPN-приложении.'); }, onError: (error: Error) => setNotice(error.message) });
  return <form id="subscription-settings-form" className="ui-card settings-subscription" onSubmit={event => { event.preventDefault(); if (dirty) save.mutate(); }}>
    <div className="settings-subscription-content"><div className="settings-subscription-editor">
      <span className="settings-section-label">ПОДПИСКИ</span><h3>Название в приложении</h3>
      <p>Это имя увидят клиенты после добавления подписки в VPN-приложение.</p>
      <label htmlFor="subscription-name">Название подписки</label>
      <input id="subscription-name" aria-describedby="subscription-name-hint" maxLength={25} required value={name} disabled={settings.isLoading || settings.isError || save.isPending} onChange={event => { setDraft(event.target.value); setNotice(''); }} />
      <div id="subscription-name-hint" className="settings-input-hint"><span>До 25 символов</span><span>{name.length}/25</span></div>
    </div><aside className="settings-subscription-preview" aria-label="Предпросмотр названия подписки"><span><Smartphone size={15} />Так увидят клиенты</span><div className="settings-preview-profile"><span className="settings-profile-icon"><Radio size={22} /></span><div><b>{settings.isLoading ? 'Загрузка…' : settings.isError ? 'Нет данных' : name.trim() || 'Название подписки'}</b><small>VPN-подписка</small></div></div><p>Новое имя появится после обновления подписки в приложении.</p></aside></div>
    <footer className="settings-save-bar"><span>{dirty ? 'Есть несохранённые изменения' : <><Check size={15} />{settings.isSuccess ? 'Настройки актуальны' : 'Настройки подписки'}</>}</span><button type="submit" className="ui-button ui-button-primary" disabled={settings.isLoading || settings.isError || save.isPending || !name.trim() || !dirty}><Save size={16} />{save.isPending ? 'Сохранение…' : 'Сохранить'}</button></footer>
    {settings.isError && <p role="alert" className="settings-form-notice">Не удалось загрузить настройки. <button type="button" className="underline" onClick={() => settings.refetch()}>Повторить</button></p>}
    {notice && <p role="status" className="settings-form-notice">{notice}</p>}
  </form>;
}
