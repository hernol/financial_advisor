"""Fan-out of fired signals to every configured channel."""
from __future__ import annotations

import logging
from typing import Sequence

from fa.config import Settings
from fa.models import Signal
from fa.notify.console import ConsoleChannel
from fa.notify.desktop import DesktopChannel
from fa.notify.telegram import TelegramChannel

logger = logging.getLogger(__name__)


class Dispatcher:
    """Sends each signal to all available channels, collecting the successes."""

    def __init__(self, channels: Sequence[object]) -> None:
        self._channels = tuple(c for c in channels if c.available())

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(c.name for c in self._channels)

    def send(self, signal: Signal) -> list[str]:
        """Return the names of the channels that accepted the message."""
        delivered: list[str] = []
        for channel in self._channels:
            try:
                if channel.send(signal):
                    delivered.append(channel.name)
            except Exception:  # noqa: BLE001 - a broken channel must not stop the others
                logger.exception("channel %s crashed", channel.name)
        return delivered


def build_dispatcher(settings: Settings, *, echo: bool = True) -> Dispatcher:
    """Console+log is always on; Telegram and desktop join when configured."""
    return Dispatcher(
        [
            ConsoleChannel(settings.log_path, echo=echo),
            TelegramChannel(settings.telegram_bot_token, settings.telegram_chat_id),
            DesktopChannel(settings.desktop_notifications),
        ]
    )
