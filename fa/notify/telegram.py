"""Telegram bot channel (TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID)."""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from fa.models import Signal
from fa.notify.base import format_plain

logger = logging.getLogger(__name__)
API_TEMPLATE = "https://api.telegram.org/bot{token}/sendMessage"
TIMEOUT = 15


class TelegramChannel:
    """Push notifications to a Telegram chat."""

    name = "telegram"

    def __init__(self, token: str | None, chat_id: str | None) -> None:
        self._token = token
        self._chat_id = chat_id

    def available(self) -> bool:
        return bool(self._token and self._chat_id)

    def send(self, signal: Signal) -> bool:
        if not self.available():
            return False
        payload = urllib.parse.urlencode(
            {
                "chat_id": self._chat_id,
                "text": format_plain(signal),
                "disable_web_page_preview": "true",
            }
        ).encode("utf-8")
        request = urllib.request.Request(API_TEMPLATE.format(token=self._token), data=payload)
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310 - fixed https host
                body = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            logger.warning("telegram delivery failed: %s", exc)
            return False
        if not body.get("ok"):
            logger.warning("telegram rejected the message: %s", body.get("description"))
            return False
        return True
