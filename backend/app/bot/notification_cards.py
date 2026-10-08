"""Small, escaped Telegram cards shared by automatic and test notifications."""
from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


@dataclass(frozen=True)
class Notice:
    text: str
    action: str = "md:status"
    button: str = "Состояние системы"
    silent: bool = False

    def markup(self):
        return InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=self.button, callback_data=self.action)
        ]])


def safe(value, limit=180):
    return escape(str(value).replace("\n", " ").replace("\r", " ")[:limit])


def card(icon, title, lines, *, hint="", action="md:status", button="Состояние системы", silent=False, now=None):
    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%d.%m · %H:%M UTC")
    body = f"{icon} <b>{escape(title)}</b>\n\n" + "\n".join(lines)
    if hint:
        body += "\n\n" + escape(hint)
    return Notice(body + f"\n\n<i>MD-Next · {stamp}</i>", action, button, silent)


def node_down(node, *, reason, ping_ms, threshold, checks, now=None):
    causes = {"port": "Нет соединения с нодой", "egress": "Нода отвечает, но выход в интернет не проходит", "latency": f"Задержка {ping_ms} мс выше порога {threshold} мс"}
    return card("🔴", "Нода недоступна", [f"<b>{safe(node.name)}</b>", safe(causes[reason]), f"Неудачных проверок подряд: {checks}."],
                hint="Текущий маршрут и доступные резервные ноды — в состоянии системы.", now=now)


def node_recovered(node, seconds, checks, now=None):
    duration = f"{seconds // 3600} ч {(seconds % 3600) // 60} мин" if seconds >= 3600 else f"{seconds // 60} мин {seconds % 60} сек"
    return card("🟢", "Нода восстановлена", [f"<b>{safe(node.name)}</b>", f"Сбой длился {duration}.", f"Успешных проверок подряд: {checks}."],
                hint="Переключение выхода, если оно выполнено, придёт отдельным сообщением.", silent=True, now=now)


def route_changed(previous, node, reason, now=None):
    titles = {"failback": "Возврат на основную ноду", "manual": "Выход изменён вручную", "route_unset": "Выход через ноду включён"}
    causes = {"node_unhealthy": "Прежняя нода недоступна", "all_nodes_unhealthy": "Все ноды недоступны", "failback": "Основная нода снова стабильно доступна", "manual": "По команде администратора", "route_unset": "Выбрана доступная нода"}
    label = lambda n: safe(n.name) if n else "Прямой выход сервера"
    return card("🟠" if node is None else "🔄", titles.get(reason, "Выход переключён"),
                [f"Было: <b>{label(previous)}</b>", f"Стало: <b>{label(node)}</b>", safe(causes.get(reason, "Маршрут обновлён"))],
                hint="Профили и ссылки подписок менять не требуется.", silent=reason in {"manual", "failback", "route_unset"}, now=now)


def test_notice():
    return card("🟢", "Уведомления подключены", ["Бот получил тестовое сообщение.", "Сбои, переключения выхода и напоминания будут приходить сюда."],
                hint="Типы уведомлений можно изменить в настройках панели.", action="md:home", button="Открыть меню", silent=True)
