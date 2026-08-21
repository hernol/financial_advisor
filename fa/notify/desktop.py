"""Linux desktop channel using notify-send."""
from __future__ import annotations

import logging
import shutil
import subprocess

from fa.models import Signal

logger = logging.getLogger(__name__)
URGENCY = {"info": "normal", "warning": "normal", "critical": "critical"}


class DesktopChannel:
    """Best-effort desktop popup; silently unavailable inside plain containers."""

    name = "desktop"

    def __init__(self, enabled: bool = True) -> None:
        self._enabled = enabled

    def available(self) -> bool:
        return self._enabled and shutil.which("notify-send") is not None

    def send(self, signal: Signal) -> bool:
        if not self.available():
            return False
        command = [
            "notify-send",
            "--app-name=financial-analyzer",
            f"--urgency={URGENCY.get(signal.severity, 'normal')}",
            signal.title,
            signal.message,
        ]
        try:
            subprocess.run(command, check=True, timeout=10, capture_output=True)  # noqa: S603 - fixed argv
        except (subprocess.SubprocessError, OSError) as exc:
            logger.warning("notify-send failed: %s", exc)
            return False
        return True
