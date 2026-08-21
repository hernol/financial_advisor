"""MarketService caching, snapshot persistence and degradation."""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from fa.errors import DataUnavailableError
from fa.market import MarketService
from fa.models import Fundamentals, PricePoint, Quote
from fa.store import events as events_store


class FakeChain:
    names = ("fake",)

    def __init__(self, *, history_fails: bool = False) -> None:
        self.quote_calls = 0
        self._history_fails = history_fails

    def get_quote(self, ticker):
        self.quote_calls += 1
        return Quote(
            ticker=ticker, price=141.5, currency="USD", as_of=datetime.now(timezone.utc), source="fake"
        )

    def get_history(self, ticker, period="2y"):
        if self._history_fails:
            raise DataUnavailableError("no history")
        return (PricePoint(day=date(2026, 8, 20), close=141.5),)

    def get_fundamentals(self, ticker):
        return Fundamentals(
            ticker=ticker,
            annual=[
                {
                    "label": "2025",
                    "period_end": date(2025, 12, 31),
                    "total_assets": 3100.0,
                    "total_liabilities": 1720.0,
                    "operating_cash_flow": 440.0,
                    "capex": 62.0,
                }
            ],
            quarterly=[],
            shares_outstanding=69.35,
            source="fake",
        )

    def get_next_earnings(self, ticker):
        return date(2026, 9, 3)

    def get_next_ex_dividend(self, ticker):
        return None

    def get_splits(self, ticker, since):
        return ()


def test_quote_is_snapshotted_in_the_database(conn):
    service = MarketService(FakeChain(), conn)
    service.quote("PODD")
    assert events_store.max_snapshot_since(conn, "PODD", "2000-01-01") == 141.5


def test_context_is_memoised_per_ticker(conn):
    chain = FakeChain()
    service = MarketService(chain, conn)
    service.context("PODD")
    service.context("podd")
    assert chain.quote_calls == 1


def test_context_survives_a_missing_history(conn):
    service = MarketService(FakeChain(history_fails=True), conn)
    context = service.context("PODD", since=date(2026, 1, 1))
    assert context.history == () and context.next_earnings == date(2026, 9, 3)


def test_analysis_tables_use_live_fundamentals(conn):
    annual, quarterly, fundamentals, context = MarketService(FakeChain(), conn).analysis_tables("PODD")
    assert fundamentals.source == "fake" and context.quote.price == 141.5
    assert annual.iloc[0]["FCF"] == pytest.approx(378.0)
    assert quarterly.empty


def test_providers_are_exposed(conn):
    assert MarketService(FakeChain(), conn).providers == ("fake",)


def test_context_cache_is_keyed_by_period(conn):
    chain = FakeChain()
    service = MarketService(chain, conn)
    service.context("PODD", period="2y")
    service.context("PODD", period="5y")
    assert chain.quote_calls == 2
