"""Shared pytest fixtures. No test touches the network.

The suite runs against SQLite by default. Setting ``FA_TEST_DATABASE_URL`` runs
it against Postgres as well, which is how the portability layer is proven rather
than assumed:

    docker compose --profile dev up -d postgres
    FA_TEST_DATABASE_URL=postgresql://fa:fa@localhost:55432/fa_test pytest

Each Postgres test gets a throwaway schema of its own, so the tests stay as
isolated as they are with a temporary SQLite file.
"""
from __future__ import annotations

import os
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fa.app import App  # noqa: E402
from fa.config import Settings  # noqa: E402
from fa.localai import LocalAIClient  # noqa: E402
from fa.models import MarketContext, Position, PricePoint, Quote  # noqa: E402
from fa.store.db import connect  # noqa: E402

TEST_DATABASE_URL = os.environ.get("FA_TEST_DATABASE_URL", "")


def postgres_enabled() -> bool:
    return bool(TEST_DATABASE_URL)


requires_postgres = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="FA_TEST_DATABASE_URL no está configurada"
)


@pytest.fixture()
def conn(tmp_path):
    """A migrated, empty database on whichever engine is under test."""
    if not TEST_DATABASE_URL:
        # threadsafe so the same connection can back a TestClient, which runs
        # the application on a worker thread.
        database = connect(tmp_path / "test.db", threadsafe=True)
        yield database
        database.close()
        return

    schema_name = f"t_{uuid.uuid4().hex[:16]}"
    database = _postgres_schema(schema_name)
    try:
        yield database
    finally:
        database.rollback()
        database.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        database.commit()
        database.close()


@pytest.fixture()
def pg_conn():
    """A migrated Postgres schema, for the tests that compare engines directly."""
    if not TEST_DATABASE_URL:
        pytest.skip("FA_TEST_DATABASE_URL no está configurada")
    schema_name = f"x_{uuid.uuid4().hex[:16]}"
    database = _postgres_schema(schema_name)
    try:
        yield database
    finally:
        database.rollback()
        database.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        database.commit()
        database.close()


def _postgres_schema(name: str):
    """Open a connection whose search_path is a schema created for this test."""
    from fa.store.database import PostgresDatabase
    from fa.store.db import migrate

    database = PostgresDatabase(TEST_DATABASE_URL)
    database.execute(f'CREATE SCHEMA IF NOT EXISTS "{name}"')
    database.execute(f'SET search_path TO "{name}"')
    database.commit()
    migrate(database)
    return database


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
        "benchmark": "SPY",
        "database_url": "",
        "api_token": "",
        "supabase_url": "",
        "supabase_anon_key": "",
        "supabase_jwt_secret": "",
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
