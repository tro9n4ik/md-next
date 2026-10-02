const statusText: Record<string, string> = {
  ok: 'В порядке', warning: 'Есть замечания', error: 'Ошибка',
  disabled: 'Отключено', not_configured: 'Не настроено',
  online: 'Онлайн', healthy: 'В порядке', unhealthy: 'Недоступна',
  unavailable: 'Недоступна',
  [['con', 'nected'].join('')]: 'Работает',
  [['dis', 'con', 'nected'].join('')]: 'Отключено',
  [['off', 'line'].join('')]: 'Недоступна',
  [ ['de', 'graded'].join('') ]: 'Есть проблемы',
  running: 'Работает', inactive: 'Отключено', failed: 'Ошибка',
  proxy: 'Прокси', warp: 'WARP',
  direct: 'Напрямую', block: 'Блокировать',
  connecting: 'Подключается', unknown: 'Неизвестно',
};

export const translateStatus = (value?: string | null): string => {
  if (!value) return 'Неизвестно';
  const normalized = value.toLowerCase();
  return statusText[normalized] || value;
};

export const formatNumber = (value: number, maximumFractionDigits = 0): string =>
  value.toLocaleString('ru-RU', { maximumFractionDigits });

export const formatBytes = (bytes: number): string => {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 Б';
  const units = ['Б', 'КБ', 'МБ', 'ГБ', 'ТБ'];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / 1024 ** index).toLocaleString('ru-RU', { maximumFractionDigits: 1 })} ${units[index]}`;
};

export const formatSpeed = (bytesPerSecond: number): string => {
  if (bytesPerSecond < 1024) return `${formatNumber(bytesPerSecond)} Б/с`;
  if (bytesPerSecond < 1024 ** 2) return `${(bytesPerSecond / 1024).toLocaleString('ru-RU', { maximumFractionDigits: 1 })} КБ/с`;
  return `${(bytesPerSecond / 1024 ** 2).toLocaleString('ru-RU', { maximumFractionDigits: 1 })} МБ/с`;
};

export const formatDateTime = (value: string | Date): string =>
  new Date(value).toLocaleString('ru-RU');

const categoryText: Record<string, string> = {
  auth: 'Вход', security: 'Безопасность', client: 'Клиенты', profile: 'Профили',
  traffic: 'Трафик', node: 'Узлы', node_invite: 'Приглашения узлов', cluster: 'Кластер',
  xray: 'Xray', awg: 'AmneziaWG', warp: 'WARP', telegram: 'Telegram', service: 'Сервис',
};
export const translateCategory = (category: string): string => categoryText[category] || category;
