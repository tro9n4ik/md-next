"""Проверка регионального отказа Gemini через WARP, без входа в аккаунт Google.

Открытая страница не доказывает доступ к генерации. Только явный отказ по региону
или повторный сигнал RU от страницы входа Google разрешают переподключение.
Ошибки сети и неопределённый регион сами по себе его не разрешают.
"""
import asyncio
import json
import logging
import re
import time
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx

from app.db import database
from app.models.node import Node
from app.models.setting import Setting
from app.services.events import log_event
from app.services.google_region import probe_google_region, unknown_region
from app.services.warp import WarpService, _setup_lock
from app.services.warp_node import test_node_proxy

logger = logging.getLogger(__name__)
CHECK_INTERVAL = 300
ROTATE_COOLDOWN = 1800
ROTATE_WINDOW = 21600
MAX_ROTATIONS = 3
CONFIRMATIONS = 2
ENABLED_KEY = "warp.gemini.enabled"
STATE_PREFIX = "warp.gemini.state."
GEMINI_URL = "https://gemini.google.com/app?hl=en"


class VisibleText(HTMLParser):
    BLOCKS = {"body", "main", "div", "p", "h1", "h2", "h3", "h4", "h5", "h6", "br", "li"}
    def __init__(self):
        super().__init__()
        self.ignored = []
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript", "template"}:
            self.ignored.append(tag)
        if tag in self.BLOCKS and not self.ignored:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if self.ignored and tag == self.ignored[-1]:
            self.ignored.pop()
        if tag in self.BLOCKS and not self.ignored:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.ignored:
            self.parts.append(data)


def classify_page(url: str, status: int, body: str) -> dict:
    """Оценивает только ответ Gemini; регион страницы входа проверяется отдельно."""
    location = urlsplit(url)
    if location.hostname != "gemini.google.com":
        return {"status": "unknown", "message": "Google требует входа или согласия; регион не определён."}
    if status not in {200, 403}:
        return {"status": "unknown", "message": f"Gemini вернул HTTP {status}; регион не определён."}
    if location.path.rstrip("/").lower() == "/unsupportedcountry":
        return {"status": "region_blocked", "message": "Gemini явно сообщил, что регион выхода не поддерживается."}
    parser = VisibleText()
    parser.feed(body)
    blocks = [re.sub(r"\s+", " ", line).strip().replace("’", "'").lower()
              for line in "".join(parser.parts).splitlines()]
    # Не принимаем FAQ вида «If Gemini isn't available ...» за отказ сервиса.
    blocked = any(re.match(
        r"(?:sorry,? )?gemini (?:isn't|is not) (?:currently )?(?:available|supported) in your (?:country|region)(?:[.!]|$| yet\b)",
        text,
    ) for text in blocks)
    if blocked:
        return {"status": "region_blocked", "message": "Gemini явно сообщил, что регион выхода не поддерживается."}
    if status == 200 and location.path.rstrip("/") == "/app" and any("gemini" in text for text in blocks):
        return {"status": "page_loaded", "message": "Публичная страница Gemini открылась без явного регионального отказа. Доступ к генерации не подтверждён."}
    return {"status": "unknown", "message": "Явного ответа о регионе нет. Автосмена IP пропущена."}


async def probe_proxy(port: int, *, timeout: float) -> dict:
    # Проверяем WARP именно на этом временном прокси, без прямого fallback.
    observed = await WarpService.test_proxy(port, timeout=timeout)
    trace = {key: observed.get(key, "") for key in ("ip", "country", "warp")}
    if trace.get("warp") not in {"on", "plus"}:
        return {"status": "unknown", "message": "Проверочный выход работает без WARP.", **trace}
    async def page_probe():
        try:
            return await asyncio.wait_for(_probe_gemini_page(port, timeout=timeout, trace=trace), timeout=timeout)
        except (httpx.HTTPError, asyncio.TimeoutError):
            return {"status": "unknown", "message": "WARP ответил, но ответ Gemini получить не удалось. Доступ не подтверждён.", **trace}
    page, region = await asyncio.gather(page_probe(), probe_google_region(port, timeout=timeout))
    result = {**page, **region, "gemini_status": page["status"]}
    if page["status"] != "region_blocked" and region.get("google_country") == "RU":
        result.update(status="google_region_ru", message="Страница входа Google определила регион выхода как РФ (RU). Это отдельный сигнал о регионе, а не подтверждённый отказ Gemini.")
    return result


async def _probe_gemini_page(port: int, *, timeout: float, trace: dict) -> dict:
    async with httpx.AsyncClient(proxy=f"socks5://127.0.0.1:{port}", timeout=timeout,
                                 follow_redirects=False, trust_env=False) as client:
        url = GEMINI_URL
        for _ in range(6):
            async with client.stream("GET", url, headers={"Accept-Language": "en-US,en;q=0.9"}) as response:
                if response.is_redirect:
                    url = urljoin(url, response.headers.get("location", ""))
                    target = urlsplit(url)
                    if target.scheme != "https" or target.hostname != "gemini.google.com" or target.port not in {None, 443}:
                        return {"status": "unknown", "message": "Google перенаправил запрос за пределы Gemini; регион не определён.", **trace}
                    if target.path.rstrip("/").lower() == "/unsupportedcountry":
                        return {**classify_page(url, 200, ""), **trace}
                    continue
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > 2 * 1024 * 1024:
                        return {"status": "unknown", "message": "Ответ Gemini слишком большой; регион не определён.", **trace}
                return {**classify_page(url, response.status_code, body.decode("utf-8", errors="replace")), **trace}
    return {"status": "unknown", "message": "Слишком много перенаправлений Gemini.", **trace}


def result_fields(result: dict) -> dict:
    return {**{key: result.get(key, "") for key in ("status", "message", "ip", "country", "warp", "gemini_status")},
            **{key: result.get(key, value) for key, value in unknown_region("Регион Google не проверен.").items()}}


async def _load_state(db, node_id: int) -> dict:
    row = await db.get(Setting, STATE_PREFIX + str(node_id))
    return json.loads(row.value) if row else {}


async def _save_state(db, node_id: int, state: dict):
    await WarpService._save_setting(db, STATE_PREFIX + str(node_id), json.dumps(state, ensure_ascii=False))


async def monitor_status(db) -> dict:
    enabled = await db.get(Setting, ENABLED_KEY)
    target = await WarpService.target(db)
    return {"enabled": bool(enabled and enabled.value == "true"),
            "node_id": target["node_id"], "node_name": target["name"],
            "interval_s": CHECK_INTERVAL, "cooldown_s": ROTATE_COOLDOWN,
            "max_rotations": MAX_ROTATIONS, "window_s": ROTATE_WINDOW,
            "confirmations": CONFIRMATIONS,
            "result": await _load_state(db, target["node_id"]) if target["node_id"] else {}}


class GeminiMonitor:
    def __init__(self):
        self._lock = asyncio.Lock()

    async def check(self, db, *, automatic: bool = False) -> dict:
        # Один запуск на весь backend; ручные проверки не запускают переподключение.
        async with self._lock:
            async with _setup_lock:
                return await self._check(db, automatic=automatic)

    async def _check(self, db, *, automatic: bool) -> dict:
        enabled = await db.get(Setting, ENABLED_KEY, populate_existing=True)
        if automatic and not (enabled and enabled.value == "true"):
            return {"status": "disabled", "message": "Автопроверка выключена."}
        target = await WarpService.target(db)
        node = await db.get(Node, target["node_id"]) if target["node_id"] else None
        if not node or not node.is_enabled:
            return {"status": "skipped", "message": "Активная нода не выбрана или отключена."}
        usage = await db.get(Setting, "warp.usage", populate_existing=True)
        if not usage or usage.value == "off":
            return {"status": "skipped", "message": "Включите WARP для проверки Gemini."}
        now = time.time()
        state = await _load_state(db, node.id)
        if automatic and now < state.get("next_check_at", 0):
            return state
        previous = state.get("status")
        attempts = [stamp for stamp in state.get("rotation_attempts", []) if stamp > now - ROTATE_WINDOW]
        try:
            result = await asyncio.wait_for(test_node_proxy(node, target["port"], probe_proxy), 45)
        except Exception:
            result = {"status": "unknown", "message": "Не удалось проверить Gemini через WARP. Проверьте соединение ноды."}
        block_reason = result["status"] if result["status"] in {"region_blocked", "google_region_ru"} else ""
        previous_reason = state.get("block_reason", "region_blocked")
        blocked = (state.get("blocked_checks", 0) + 1 if previous_reason == block_reason else 1) if block_reason else 0
        state.update({**result_fields(result), "block_reason": block_reason,
                      "checked_at": now, "next_check_at": now + CHECK_INTERVAL,
                      "blocked_checks": blocked, "rotation_attempts": attempts})
        state.pop("rotation_note", None)
        await _save_state(db, node.id, state)
        if previous != result["status"]:
            log_event("warning" if blocked else "info", "warp", "Проверка региона Gemini", {
                "node_id": node.id, "status": result["status"], "automatic": automatic})
        if not automatic or blocked < CONFIRMATIONS:
            return state
        if len(attempts) >= MAX_ROTATIONS:
            state["rotation_note"] = "Лимит автопереподключений исчерпан: не более трёх за шесть часов."
        elif attempts and now < attempts[-1] + ROTATE_COOLDOWN:
            state["rotation_note"] = "Выдерживаю паузу после предыдущего автопереподключения."
        else:
            # Перечитываем выбор ноды и настройки после сетевого запроса. При failover
            # не перезапускаем WARP прежней ноды. Бюджет сохраняем ДО внешнего эффекта.
            node_id = node.id
            db.expire_all()
            current = await WarpService.target(db)
            enabled = await db.get(Setting, ENABLED_KEY)
            usage = await db.get(Setting, "warp.usage")
            node = await db.get(Node, node_id)
            if (current != target or not enabled or enabled.value != "true"
                    or not usage or usage.value == "off" or not node or not node.is_enabled):
                return state
            attempts.append(time.time())
            state["rotation_attempts"] = attempts
            state["blocked_checks"] = 0
            state["rotation_note"] = "Автопереподключение запрошено."
            await _save_state(db, node.id, state)
            try:
                rotated = await WarpService.control_node(node, "rotate", target["port"] == 40000)
                state["rotation_note"] = ("WARP переподключён, IP изменился." if rotated["changed"]
                                          else "WARP переподключён; Cloudflare сохранил прежний IP.")
                state["last_rotation_at"] = time.time()
                state["last_rotation_changed"] = rotated["changed"]
                # Повторная проверка идёт через тот же выход, а не локальный интернет панели.
                result = await asyncio.wait_for(test_node_proxy(node, target["port"], probe_proxy), 45)
                state.update({**result_fields(result),
                              "checked_at": time.time()})
                log_event("warning", "warp", "Автопереподключение WARP для Gemini", {
                    "node_id": node.id, "changed": rotated["changed"], "status": result["status"]})
            except Exception:
                state.update(result_fields({"status": "unknown", "message": "Переподключение WARP или повторная проверка не завершились."}))
                log_event("error", "warp", "Не удалось восстановить доступ Gemini через WARP", {"node_id": node.id})
        await _save_state(db, node.id, state)
        return state


gemini_monitor = GeminiMonitor()


async def gemini_monitor_loop():
    while True:
        try:
            async with database.AsyncSessionLocal() as db:
                await gemini_monitor.check(db, automatic=True)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Ошибка автопроверки Gemini")
        await asyncio.sleep(30)
