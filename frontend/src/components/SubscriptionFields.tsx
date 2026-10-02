import React from 'react';
import type { SubscriptionValues } from '../utils/subscriptions';

const SubscriptionFields: React.FC<{ value: SubscriptionValues; onChange: (value: SubscriptionValues) => void; editing?: boolean }> = ({ value, onChange, editing }) => {
  const change = (field: keyof SubscriptionValues, text: string) => onChange({ ...value, [field]: text });
  const input = 'mt-1 w-full rounded-lg border border-neutral-300 bg-white p-2.5 text-sm';
  return <div className="space-y-3">
    <label className="block text-sm font-medium text-neutral-700">{editing ? 'Продление подписки' : 'Срок подписки'}
      <select value={value.period} onChange={event => change('period', event.target.value)} className={input}>
        {editing && <option value="keep">Оставить текущий срок</option>}
        <option value="week">{editing ? 'Продлить на неделю' : 'Неделя'}</option>
        <option value="month">{editing ? 'Продлить на месяц' : 'Месяц'}</option>
        <option value="year">{editing ? 'Продлить на год' : 'Год'}</option>
        <option value="custom">Произвольная дата</option>
        <option value="unlimited">Бессрочно</option>
      </select>
    </label>
    {value.period === 'custom' && <label className="block text-sm font-medium text-neutral-700">Дата окончания
      <input required type="datetime-local" value={value.date} onChange={event => change('date', event.target.value)} className={input} />
    </label>}
    <label className="block text-sm font-medium text-neutral-700">Трафик на месяц
      <select value={value.quotaGB === '' ? 'unlimited' : 'limited'} onChange={event => change('quotaGB', event.target.value === 'unlimited' ? '' : '100')} className={input}>
        <option value="unlimited">Без ограничений</option><option value="limited">Установить лимит</option>
      </select>
    </label>
    {value.quotaGB !== '' && <label className="block text-sm font-medium text-neutral-700">Лимит, ГБ
      <input required type="number" min="0.001" step="any" max="8388607" value={value.quotaGB} onChange={event => change('quotaGB', event.target.value || '0')} className={input} />
    </label>}
    <p className="text-xs text-neutral-500">Лимит обновляется каждый месяц от даты создания клиента. Продление подписки не обнуляет трафик.</p>
  </div>;
};

export default SubscriptionFields;
