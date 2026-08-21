"""Notification channel protocol."""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from fa.models import Signal


@runtime_checkable
class Channel(Protocol):
    """A delivery target for fired alerts."""

    name: str

    def available(self) -> bool:
        """True when the channel is configured and usable."""

    def send(self, signal: Signal) -> bool:
        """Deliver the signal; return True on success. Must not raise."""


def format_plain(signal: Signal) -> str:
    """Single-message rendering shared by the text based channels."""
    return f"{signal.title}\n{signal.message}"
