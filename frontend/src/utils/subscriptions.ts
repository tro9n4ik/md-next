export type SubscriptionValues = { period: string; date: string; quotaGB: string; cdnQuotaGB: string };
export type ClientLimits = {
  expires_at: string | null; monthly_traffic_limit: number; monthly_traffic_used: number;
  traffic_period_end: string; access_allowed: boolean; blocked_reason: string | null;
  cdn_monthly_traffic_limit: number; cdn_monthly_traffic_used: number; cdn_quota_exhausted: boolean;
};

export function subscriptionPayload(values: SubscriptionValues) {
  const bytes = values.quotaGB === '' ? 0 : Math.round(Number(values.quotaGB) * 1024 ** 3);
  if (!Number.isSafeInteger(bytes) || (values.quotaGB !== '' && bytes <= 0)) throw new Error('Укажите положительный месячный лимит трафика');
  const cdnBytes = values.cdnQuotaGB === '' ? 0 : Math.round(Number(values.cdnQuotaGB) * 1024 ** 3);
  if (!Number.isSafeInteger(cdnBytes) || (values.cdnQuotaGB !== '' && cdnBytes <= 0)) throw new Error('Укажите положительный месячный лимит обхода БС');
  const payload: { monthly_traffic_limit: number; cdn_monthly_traffic_limit: number; subscription_period?: string; expires_at?: string } = { monthly_traffic_limit: bytes, cdn_monthly_traffic_limit: cdnBytes };
  if (values.period !== 'keep') payload.subscription_period = values.period;
  if (values.period === 'custom') {
    const date = new Date(values.date);
    if (!Number.isFinite(date.getTime()) || date.getTime() <= Date.now()) throw new Error('Выберите дату окончания подписки в будущем');
    payload.expires_at = date.toISOString();
  }
  return payload;
}

export function formatSubscriptionDate(value: string | null) {
  return value ? new Date(value).toLocaleString('ru-RU', { dateStyle: 'medium', timeStyle: 'short' }) : 'Бессрочно';
}

export function clientStatus(reason: string | null) {
  return ({ disabled: 'Отключён', expired: 'Срок истёк', monthly_quota: 'Лимит исчерпан' } as Record<string, string>)[reason || ''] || 'Работает';
}

