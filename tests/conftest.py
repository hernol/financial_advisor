"""Shared pytest fixtures. No test touches the network."""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fa.app import App  # noqa: E402
from fa.config import Settings  # noqa: E402
from fa.localai import LocalAIClient  # noqa: E402
from fa.models import MarketContext, Position, PricePoint, Quote  # noqa: E402
from fa.store.db import connect  # noqa: E402


@pytest.fixture()
def conn(tmp_path):
    connection = connect(tmp_path / "test.db")
    yield connection
    connection.close()


@pytest.fixture()
def now() -> datetime:
    return datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)


def make_quote(price: float, ticker: str = "PODD", previous: float | None = None) -> Quote:
    return Quote(
        ticker=ticker,
        price=price,
        currency="USD",
        as_of=datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc),
        source="test",
        previous_close=previous,
    )


def make_history(closes, end: date = date(2026, 8, 20)) -> tuple[PricePoint, ...]:
    return tuple(
        PricePoint(day=end - timedelta(days=len(closes) - 1 - i), close=value)
        for i, value in enumerate(closes)
    )


def make_context(price: float, **kwargs) -> MarketContext:
    return MarketContext(
        ticker=kwargs.pop("ticker", "PODD"),
        quote=make_quote(price),
        history=kwargs.pop("history", ()),
        next_earnings=kwargs.pop("next_earnings", None),
        next_ex_dividend=kwargs.pop("next_ex_dividend", None),
        recent_splits=kwargs.pop("recent_splits", ()),
        evaluated_at=kwargs.pop("evaluated_at", datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)),
    )


def make_position(buy_price: float = 100.0, buy_date: date = date(2026, 1, 15), **kwargs) -> Position:
    return Position(
        id=kwargs.pop("id", 1),
        ticker=kwargs.pop("ticker", "PODD"),
        quantity=kwargs.pop("quantity", 10.0),
        buy_price=buy_price,
        buy_date=buy_date,
    )


def make_settings(tmp_path, **overrides) -> Settings:
    """Settings with every field defaulted, so tests only state what matters."""
    base = {
        "gemini_api_key": None,
        "gemini_model": "test-model",
        "alpha_vantage_key": None,
        "finnhub_key": None,
        "telegram_bot_token": None,
        "telegram_chat_id": None,
        "desktop_notifications": False,
        "default_cooldown_hours": 24,
        "earnings_warning_days": 7,
        "local_ai_url": "http://localhost:1234/v1",
        "local_ai_model": None,
        "local_ai_api_key": "not-needed",
        "local_ai_timeout": 5,
        "local_ai_max_tokens": 2000,
        "db_path": tmp_path / "t.db",
        "log_path": tmp_path / "t.log",
    }
    return Settings(**{**base, **overrides})


def make_app(conn, tmp_path, *, market=None, dispatcher=None, **setting_overrides) -> App:
    settings = make_settings(tmp_path, **setting_overrides)
    return App(
        settings=settings,
        conn=conn,
        market=market,
        dispatcher=dispatcher,
        local_ai=LocalAIClient.from_settings(settings),
    )
