"""Отображение отдельной квоты БС в метаданных подписки Happ."""
import base64


def traffic_size(value: int) -> str:
    amount = max(0, value)
    for unit in ('Б', 'КБ', 'МБ', 'ГБ', 'ТБ'):
        if amount < 1024 or unit == 'ТБ':
            return f'{amount:.2f} {unit}' if unit != 'Б' else f'{amount} {unit}'
        amount /= 1024


def cdn_announcement(limits: dict, available: bool) -> str:
    if not available:
        text = 'Обход БС не подключён.'
    else:
        used = limits['cdn_monthly_traffic_used']
        total = limits['cdn_monthly_traffic_limit']
        reset = limits['traffic_period_end'].strftime('%d.%m.%Y')
        if total:
            text = (f"Обход БС: {traffic_size(used)} из {traffic_size(total)}. "
                    f"Осталось {traffic_size(max(0, total-used))}. "
                    f"Обновление {reset} (UTC).")
            if limits['cdn_quota_exhausted']:
                text += ' Лимит БС исчерпан.'
        else:
            text = f'Обход БС: без лимита. За месяц {traffic_size(used)}. Обновление {reset} (UTC).'
    # Документированный формат Happ для кириллицы в HTTP-заголовках.
    return 'base64:' + base64.b64encode(text[:200].encode('utf-8')).decode('ascii')
