"""Portfolio valuation, including tickers whose data is unavailable."""
from __future__ import annotations

from datetime import date

import pytest

from fa.errors import DataUnavailableError
from fa.models import Position
from fa.portfolio import build_portfolio
from fa.store import positions as positions_store
from tests.conftest import make_quote


class StubMarket:
    def __init__(self, prices: dict[str, float]) -> None:
        self._prices = prices

    def quote(self, ticker: str):
        if ticker not in self._prices:
            raise DataUnavailableError(f"no data for {ticker}")
        return make_quote(self._prices[ticker], ticker=ticker)


def seed(conn, ticker: str, quantity: float, price: float) -> None:
    positions_store.add_position(
        conn, Position(ticker=ticker, quantity=quantity, buy_price=price, buy_date=date(2026, 1, 1))
    )


def test_empty_portfolio(conn):
    assert build_portfolio(conn, StubMarket({})).holdings == ()


def test_portfolio_totals_cost_and_market_value(conn):
    seed(conn, "PODD", 10, 100.0)
    seed(conn, "AAPL", 5, 200.0)
    portfolio = build_portfolio(conn, StubMarket({"PODD": 150.0, "AAPL": 180.0}))
    assert portfolio.cost_basis == pytest.approx(2000.0)
    assert portfolio.market_value == pytest.approx(2400.0)
    absolute, percentage = portfolio.total_pnl
    assert absolute == pytest.approx(400.0) and percentage == pytest.approx(20.0)


def test_unavailable_price_is_reported_not_invented(conn):
    seed(conn, "PODD", 10, 100.0)
    holding = build_portfolio(conn, StubMarket({})).holdings[0]
    assert holding.price is None and holding.market_value is None
    assert "no data" in holding.error


def test_per_holding_pnl(conn):
    seed(conn, "PODD", 10, 100.0)
    holding = build_portfolio(conn, StubMarket({"PODD": 120.0})).holdings[0]
    absolute, percentage = holding.pnl
    assert absolute == pytest.approx(200.0) and percentage == pytest.approx(20.0)
