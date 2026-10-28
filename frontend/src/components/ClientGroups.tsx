import { useConfirm } from '../contexts/ConfirmationContext';
import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Check, Trash2 } from 'lucide-react';
import Drawer from './ui/Drawer';
import { groupRequest } from '../utils/clientGroups';

export type ClientGroup = { id: number; name: string; color: string; count: number };
export type GroupSummary = { groups: ClientGroup[]; total: number; ungrouped_count: number };
const groupColors = ['emerald', 'blue', 'violet', 'amber', 'rose', 'slate'] as const;
const colorNames = ['Зелёный', 'Синий', 'Фиолетовый', 'Жёлтый', 'Розовый', 'Серый'];

export function GroupEditor({ group, onClose }: { group: ClientGroup | null; onClose: () => void }) {
  const confirm = useConfirm();
  const queryClient = useQueryClient();
  const [name, setName] = useState(group?.name || '');
  const [color, setColor] = useState(group?.color || 'emerald');
  const [confirmDelete, setConfirmDelete] = useState(false);
  const dirty = name !== (group?.name || '') || color !== (group?.color || 'emerald');
  const close = async () => { if (!dirty || await confirm({ title: 'Закрыть без сохранения?', description: 'Изменения названия и цвета группы не будут сохранены.', confirmText: 'Закрыть без сохранения', cancelText: 'Продолжить редактирование', tone: 'warning' })) onClose(); };
  const save = useMutation({
    mutationFn: () => groupRequest('/api/v1/client-groups' + (group ? `/${group.id}` : ''), { method: group ? 'PUT' : 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: name.trim(), color }) }),
    onSuccess: () => { void queryClient.invalidateQueries({ queryKey: ['clientGroups'] }); void queryClient.invalidateQueries({ queryKey: ['clients'] }); onClose(); },
  });
  const remove = useMutation({
    mutationFn: () => groupRequest(`/api/v1/client-groups/${group!.id}`, { method: 'DELETE' }),
    onSuccess: () => { void queryClient.invalidateQueries({ queryKey: ['clientGroups'] }); void queryClient.invalidateQueries({ queryKey: ['clients'] }); onClose(); },
  });
  const busy = save.isPending || remove.isPending;
  return <Drawer title={group ? 'Настроить группу' : 'Новая группа'} onClose={close} dirty={dirty} busy={busy}>
    <form onSubmit={event => { event.preventDefault(); save.mutate(); }} className="client-editor-form">
      <p className="client-form-hint">Группы помогают упорядочить клиентов. Сроки подписок и доступ к VPN задаются отдельно.</p>
      <label>Название группы<input autoFocus required maxLength={48} value={name} onChange={event => setName(event.target.value)} placeholder="Например, Семья" disabled={busy} /></label>
      <fieldset><legend>Цвет группы</legend><div className="group-color-picker">{groupColors.map((item, index) => <button type="button" key={item} disabled={busy} className={`group-color group-${item}`} aria-label={colorNames[index]} aria-pressed={color === item} onClick={() => setColor(item)}>{color === item && <Check size={18} />}</button>)}</div></fieldset>
      {(save.error || remove.error) && <p role="alert" className="client-form-error">{(save.error || remove.error)?.message}</p>}
      <button disabled={busy || !name.trim()} className="ui-button ui-button-primary">{save.isPending ? 'Сохраняем…' : group ? 'Сохранить группу' : 'Создать группу'}</button>
      {group && <div className="group-delete-section">{confirmDelete ? <><p>Удалить группу «{group.name}»? Клиенты останутся в разделе «Без группы». Подписки и подключения сохранятся.</p><div className="flex gap-2"><button type="button" disabled={busy} onClick={() => remove.mutate()} className="ui-button ui-button-danger">Удалить группу</button><button type="button" disabled={busy} className="ui-button ui-button-secondary" onClick={() => setConfirmDelete(false)}>Отмена</button></div></> : <button type="button" disabled={busy} onClick={() => setConfirmDelete(true)} className="group-delete-button"><Trash2 size={16} />Удалить группу</button>}</div>}
    </form>
  </Drawer>;
}
