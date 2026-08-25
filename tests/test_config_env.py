"""Settings read from the environment, including the empty-string case.

A container passes VAR= for anything the operator left blank, and
os.environ.get(name, default) only falls back when the variable is absent — so
an unset value arrived as "" and beat the default. That is exactly how the
dashboard came to ask Gemini for an empty model name.
"""
from __future__ import annotations

import pytest

from fa.config import DEFAULT_BENCHMARK, DEFAULT_GEMINI_MODEL, DEFAULT_LOCAL_AI_URL, load_settings


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in (
        "GEMINI_API_KEY", "FA_GEMINI_MODEL", "ALPHA_VANTAGE_API_KEY", "FINNHUB_API_KEY",
        "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "FA_LOCAL_AI_URL", "FA_LOCAL_AI_MODEL",
        "FA_BENCHMARK", "DATABASE_URL", "FA_API_TOKEN", "SUPABASE_URL",
        "SUPABASE_ANON_KEY", "SUPABASE_JWT_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)


def test_an_absent_variable_uses_the_default():
    settings = load_settings()
    assert settings.gemini_model == DEFAULT_GEMINI_MODEL
    assert settings.benchmark == DEFAULT_BENCHMARK
    assert settings.local_ai_url == DEFAULT_LOCAL_AI_URL.rstrip("/")


def test_an_empty_variable_also_uses_the_default(monkeypatch):
    """The bug: compose writes VAR= and the default was skipped."""
    monkeypatch.setenv("FA_GEMINI_MODEL", "")
    monkeypatch.setenv("FA_BENCHMARK", "")
    monkeypatch.setenv("FA_LOCAL_AI_URL", "")
    settings = load_settings()
    assert settings.gemini_model == DEFAULT_GEMINI_MODEL
    assert settings.benchmark == DEFAULT_BENCHMARK
    assert settings.local_ai_url == DEFAULT_LOCAL_AI_URL.rstrip("/")


def test_whitespace_counts_as_empty(monkeypatch):
    monkeypatch.setenv("FA_GEMINI_MODEL", "   ")
    assert load_settings().gemini_model == DEFAULT_GEMINI_MODEL


def test_a_real_value_still_wins(monkeypatch):
    monkeypatch.setenv("FA_GEMINI_MODEL", "gemini-otro")
    monkeypatch.setenv("FA_BENCHMARK", "qqq")
    settings = load_settings()
    assert settings.gemini_model == "gemini-otro"
    assert settings.benchmark == "QQQ"


@pytest.mark.parametrize(
    "name,attribute",
    [
        ("GEMINI_API_KEY", "gemini_api_key"),
        ("ALPHA_VANTAGE_API_KEY", "alpha_vantage_key"),
        ("FINNHUB_API_KEY", "finnhub_key"),
        ("TELEGRAM_BOT_TOKEN", "telegram_bot_token"),
        ("FA_LOCAL_AI_MODEL", "local_ai_model"),
    ],
)
def test_an_empty_key_reads_as_absent(monkeypatch, name, attribute):
    """"" is not a key. Treating it as one produces an auth error instead of
    the message that says the key is missing."""
    monkeypatch.setenv(name, "")
    assert getattr(load_settings(), attribute) is None


def test_an_empty_database_url_means_sqlite(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "")
    settings = load_settings()
    assert settings.database_url == ""
    assert settings.database_target == settings.db_path


def test_an_empty_token_leaves_the_api_open(monkeypatch):
    from fa.api.auth import OPEN, mode_for

    monkeypatch.setenv("FA_API_TOKEN", "")
    assert mode_for(load_settings()) == OPEN
