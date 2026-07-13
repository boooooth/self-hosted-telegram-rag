"""Plain HTTP call to the Telegram Bot API's sendMessage endpoint.

Used by the worker, which has no aiogram Bot instance of its own (it's a
separate long-running process from the bot container) and just needs to
notify a user once ingestion finishes or fails.
"""
import httpx

from app.config import settings

_API_BASE = "https://api.telegram.org"


def send_message(chat_id: int, text: str) -> None:
    """`text` must already be safe Telegram HTML -- callers are responsible
    for escaping any dynamic content (see app.telegram_format.escape_html),
    same convention as app.bot's aiogram-side handlers."""
    url = f"{_API_BASE}/bot{settings.telegram_bot_token}/sendMessage"
    response = httpx.post(
        url, json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"}, timeout=10
    )
    response.raise_for_status()
