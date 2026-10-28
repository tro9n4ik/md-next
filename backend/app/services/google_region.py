"""Anonymous Google sign-in region hint, inspired by vernette/ipregion.

Source: https://github.com/vernette/ipregion (lookup_google).
The hidden region field is undocumented; it is not the account country or proof
that Gemini supports an account. No credentials or cookies are persisted.
"""

import asyncio
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx

GOOGLE_REGION_URL = (
    "https://accounts.google.com/v3/signin/identifier?flowName=GlifSetupAndroid"
)
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0"
SOURCE = "google_signin_region"
MAX_BODY = 2 * 1024 * 1024


class RegionInputs(HTMLParser):
    def __init__(self):
        super().__init__()
        self.values = []
        self.ignored = []
        self.challenge = False

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "template", "noscript"}:
            self.ignored.append(tag)
        if self.ignored:
            return
        attributes = dict(attrs)
        if (
            attributes.get("id") in {"captcha", "recaptcha"}
            or "g-recaptcha" in (attributes.get("class") or "").split()
        ):
            self.challenge = True
        if (
            tag == "input"
            and attributes.get("name") == "region"
            and attributes.get("type", "").lower() == "hidden"
        ):
            self.values.append((attributes.get("value") or "").strip().upper())

    def handle_endtag(self, tag):
        if self.ignored and self.ignored[-1] == tag:
            self.ignored.pop()


def parse_google_country(body: str) -> str:
    parser = RegionInputs()
    parser.feed(body)
    values = set(parser.values)
    if parser.challenge or len(values) != 1:
        return ""
    value = values.pop()
    return (
        value
        if re.fullmatch(r"[A-Z]{2}", value) and value not in {"XX", "ZZ", "EU", "UN"}
        else ""
    )


def unknown_region(message: str) -> dict:
    return {
        "google_country": "",
        "google_country_source": SOURCE,
        "google_region_status": "unknown",
        "google_region_message": message,
    }


async def probe_google_region(port: int, *, timeout: float) -> dict:
    async def probe():
        # Separate anonymous session, same node's SOCKS exit, never direct fallback.
        async with httpx.AsyncClient(
            proxy=f"socks5://127.0.0.1:{port}",
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            url = GOOGLE_REGION_URL
            for _ in range(4):
                target = urlsplit(url)
                if (
                    target.scheme != "https"
                    or target.hostname != "accounts.google.com"
                    or target.port not in {None, 443}
                    or target.username
                    or target.password
                    or target.path
                    not in {"/v3/signin/identifier", "/signin/v2/identifier"}
                ):
                    return unknown_region(
                        "Страница входа Google перенаправила проверку; регион не определён."
                    )
                async with client.stream(
                    "GET",
                    url,
                    headers={
                        "User-Agent": USER_AGENT,
                        "Accept-Language": "en-US,en;q=0.9",
                    },
                ) as response:
                    if response.is_redirect:
                        url = urljoin(url, response.headers.get("location", ""))
                        continue
                    if response.status_code != 200:
                        return unknown_region(
                            f"Страница входа Google вернула HTTP {response.status_code}; регион не определён."
                        )
                    if response.headers.get("content-type", "").split(";", 1)[
                        0
                    ].strip().lower() not in {"text/html", "application/xhtml+xml"}:
                        return unknown_region(
                            "Google вернул ответ не в формате HTML; регион не определён."
                        )
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > MAX_BODY:
                            return unknown_region(
                                "Ответ Google слишком большой; регион не определён."
                            )
                    country = parse_google_country(
                        body.decode("utf-8", errors="replace")
                    )
                    if not country:
                        return unknown_region(
                            "Поле региона Google отсутствует, неоднозначно или показана CAPTCHA."
                        )
                    return {
                        "google_country": country,
                        "google_country_source": SOURCE,
                        "google_region_status": "detected",
                        "google_region_message": "Регион по скрытому полю страницы входа Google; это не страна аккаунта и не гарантия доступа к Gemini.",
                    }
            return unknown_region("Слишком много перенаправлений Google.")

    try:
        return await asyncio.wait_for(probe(), timeout=timeout)
    except (httpx.HTTPError, asyncio.TimeoutError, ValueError):
        return unknown_region("Не удалось получить регион Google через WARP.")
