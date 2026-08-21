"""Console + rotating-free log channel. Always enabled."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fa.models import Signal
from fa.notify.base import format_plain

SEVERITY_PREFIX = {"info": "ℹ️ ", "warning": "⚠️ ", "critical": "🚨"}


class ConsoleChannel:
    """Prints to stdout and appends to the alert log file."""

    name = "console"

    def __init__(self, log_path: Path | str | None = None, *, echo: bool = True) -> None:
        self._log_path = Path(log_path) if log_path else None
        self._echo = echo

    def available(self) -> bool:
        return True

    def send(self, signal: Signal) -> bool:
        line = f"{SEVERITY_PREFIX.get(signal.severity, '')} {format_plain(signal)}"
        if self._echo:
            print(f"\n{line}")
        return self._append(signal)

    def _append(self, signal: Signal) -> bool:
        if self._log_path is None:
            return True
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        record = f"{stamp}\t{signal.severity}\t{signal.alert.ticker}\t{signal.alert.kind}\t{signal.message}\n"
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._log_path.open("a", encoding="utf-8") as handle:
                handle.write(record)
        except OSError:
            return False
        return True
