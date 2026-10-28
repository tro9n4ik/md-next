export function lastOnlineLabel(value: string | null | undefined): string {
  if (!value) return 'Активность ещё не зафиксирована';
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return 'Время активности недоступно';
  return `Был онлайн ${date.toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', year: 'numeric' })} в ${date.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })}`;
}
