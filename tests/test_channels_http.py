"""Telegram, desktop, HTTP helper and the AI guard: no real network involved."""
from __future__ import annotations

import io
import json
import subprocess
import urllib.error

import pytest

from fa.config import load_settings
from fa.errors import ConfigError, ProviderError
from fa.models import Alert, Signal
from fa.notify.desktop import DesktopChannel
from fa.notify.dispatcher import build_dispatcher
from fa.notify.telegram import TelegramChannel
from fa.providers.http import get_json
from tests.conftest import make_settings

SIGNAL = Signal(alert=Alert(id=1, ticker="PODD", kind="pct_up"), title="t", message="m", severity="critical")


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
        return False


def test_get_json_parses_a_payload(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda *a, **k: FakeResponse(json.dumps({"ok": 1}).encode())
    )
    assert get_json("https://example.test", {"a": 1}) == {"ok": 1}


def test_get_json_wraps_network_errors(monkeypatch):
    def boom(*a, **k):
        raise urllib.error.URLError("down")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    with pytest.raises(ProviderError):
        get_json("https://example.test", {})


def test_get_json_rejects_non_json(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: FakeResponse(b"<html>"))
    with pytest.raises(ProviderError):
        get_json("https://example.test", {})


def test_telegram_needs_token_and_chat():
    assert TelegramChannel(None, "1").available() is False
    assert TelegramChannel("t", "1").available() is True


def test_telegram_reports_success(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda *a, **k: FakeResponse(json.dumps({"ok": True}).encode())
    )
    assert TelegramChannel("t", "1").send(SIGNAL) is True


def test_telegram_reports_a_rejected_message(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *a, **k: FakeResponse(json.dumps({"ok": False, "description": "chat not found"}).encode()),
    )
    assert TelegramChannel("t", "1").send(SIGNAL) is False


def test_telegram_survives_a_network_error(monkeypatch):
    def boom(*a, **k):
        raise urllib.error.URLError("down")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    assert TelegramChannel("t", "1").send(SIGNAL) is False


def test_desktop_channel_disabled_by_configuration():
    assert DesktopChannel(enabled=False).available() is False


def test_desktop_channel_requires_notify_send(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _: None)
    assert DesktopChannel(enabled=True).available() is False
    assert DesktopChannel(enabled=True).send(SIGNAL) is False


def test_desktop_channel_invokes_notify_send(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/notify-send")
    monkeypatch.setattr("subprocess.run", lambda cmd, **kw: calls.append(cmd))
    assert DesktopChannel(enabled=True).send(SIGNAL) is True
    assert calls[0][0] == "notify-send" and "--urgency=critical" in calls[0]


def test_desktop_channel_survives_a_failing_binary(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/notify-send")

    def boom(*a, **k):
        raise subprocess.SubprocessError("nope")

    monkeypatch.setattr("subprocess.run", boom)
    assert DesktopChannel(enabled=True).send(SIGNAL) is False




def test_console_channel_is_always_present(tmp_path):
    assert build_dispatcher(make_settings(tmp_path)).names == ("console",)


def test_telegram_joins_when_configured(tmp_path):
    settings = make_settings(tmp_path, telegram_bot_token="t", telegram_chat_id="1")
    assert settings.telegram_enabled is True
    assert "telegram" in build_dispatcher(settings).names


def test_ai_refuses_to_run_without_an_api_key(tmp_path):
    from fa import ai
    from fa.ai_context import DataPack

    with pytest.raises(ConfigError):
        ai.analyze(make_settings(tmp_path), "PODD", DataPack(text="x", provenance="y"), "")


def test_load_settings_reads_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("FA_DB_PATH", str(tmp_path / "db.sqlite"))
    monkeypatch.setenv("FA_LOG_PATH", str(tmp_path / "a.log"))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    monkeypatch.setenv("FA_COOLDOWN_HOURS", "6")
    monkeypatch.setenv("FA_DESKTOP_NOTIFICATIONS", "false")
    settings = load_settings()
    assert settings.telegram_enabled is True
    assert settings.default_cooldown_hours == 6
    assert settings.desktop_notifications is False


def test_invalid_numeric_environment_falls_back_to_the_default(monkeypatch, tmp_path):
    monkeypatch.setenv("FA_DB_PATH", str(tmp_path / "db.sqlite"))
    monkeypatch.setenv("FA_LOG_PATH", str(tmp_path / "a.log"))
    monkeypatch.setenv("FA_COOLDOWN_HOURS", "muchas")
    assert load_settings().default_cooldown_hours == 24


def test_unwritable_database_explains_the_fix(monkeypatch, tmp_path):
    """A root-owned file left by Docker must not surface as a raw traceback."""
    import sqlite3

    from fa import app as app_module
    from fa.errors import ConfigError

    monkeypatch.setenv("FA_DB_PATH", str(tmp_path / "db.sqlite"))
    monkeypatch.setenv("FA_LOG_PATH", str(tmp_path / "a.log"))

    def readonly(*args, **kwargs):
        raise sqlite3.OperationalError("attempt to write a readonly database")

    monkeypatch.setattr(app_module, "connect", readonly)
    with pytest.raises(ConfigError, match="chown"):
        with app_module.build_app():
            pass
