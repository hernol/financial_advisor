"""Fan-out of fired signals to every configured channel."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Sequence

from fa.config import Settings
from fa.models import Signal
from fa.notify.console import ConsoleChannel
from fa.notify.desktop import DesktopChannel
from fa.notify.telegram import TelegramChannel

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeliveryResult:
    """What happened on one channel for one signal."""

    channel: str
    ok: bool
    error: str = ""


class Dispatcher:
    """Sends each signal to all available channels, collecting the successes."""

    def __init__(self, channels: Sequence[object]) -> None:
        self._channels = tuple(c for c in channels if c.available())

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(c.name for c in self._channels)

    def dispatch(self, signal: Signal) -> list[DeliveryResult]:
        """Try every channel and report each outcome, failures included.

        A channel that refuses or crashes used to leave no trace at all: the
        event simply recorded a shorter ``delivered`` list. Push notifications
        need the difference between "not configured" and "Telegram returned
        429", so both are returned here and persisted by the caller.
        """
        results: list[DeliveryResult] = []
        for channel in self._channels:
            try:
                ok = bool(channel.send(signal))
                results.append(
                    DeliveryResult(channel.name, ok, "" if ok else "el canal rechazó el envío")
                )
            except Exception as exc:  # noqa: BLE001 - a broken channel must not stop the others
                logger.exception("channel %s crashed", channel.name)
                results.append(DeliveryResult(channel.name, False, f"{type(exc).__name__}: {exc}"))
        return results

    def send(self, signal: Signal) -> list[str]:
        """Return the names of the channels that accepted the message."""
        return [result.channel for result in self.dispatch(signal) if result.ok]


def build_dispatcher(settings: Settings, *, echo: bool = True) -> Dispatcher:
    """Console+log is always on; Telegram and desktop join when configured."""
    return Dispatcher(
        [
            ConsoleChannel(settings.log_path, echo=echo),
            TelegramChannel(settings.telegram_bot_token, settings.telegram_chat_id),
            DesktopChannel(settings.desktop_notifications),
        ]
    )


def deliver(dispatcher: object, signal: Signal) -> tuple[DeliveryResult, ...]:
    """Per-channel outcome from any dispatcher.

    Test doubles only implement ``send``; for them a returned name means success
    and nothing is known about the channels that stayed silent.
    """
    detailed = getattr(dispatcher, "dispatch", None)
    if callable(detailed):
        return tuple(detailed(signal))
    return tuple(DeliveryResult(name, True) for name in dispatcher.send(signal))
