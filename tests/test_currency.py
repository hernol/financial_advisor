"""The portfolio is valued in one currency, and says so when it cannot.

Mixing currencies without a conversion table gives a total that looks right and
is not, which is the failure this module exists to prevent. The rule is the
same everywhere: exclude, report, never sum.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from fa.api import deps
from fa.api.app import create_app
from fa.config import BASE_CURRENCY
from fa.models import Position, PricePoint, Quote, Transaction
from fa.portfolio import build_portfolio
from fa.store import history as history_store
from fa.store import positions as positions_store
from fa.store import transactions as transactions_store


@pytest.fixture()
def client(conn):
    deps.set_database(conn)
    with TestClient(create_app(serve_web=False)) as test_client:
        yield test_client
    deps.set_database(None)


class FixedMarket:
    """Quotes a fixed price, in whatever currency it was told to."""

    def __init__(self, price: float = 100.0, currency: str = "USD") -> None:
        self._price = price
        self._currency = currency

    def quote(self, ticker: str) -> Quote:
        return Quote(
            ticker=ticker,
            price=self._price,
            currency=self._currency,
            as_of=datetime(2026, 8, 20, tzinfo=timezone.utc),
            source="test",
        )


def buy(conn, ticker="PODD", currency="USD"):
    return positions_store.add_position(
        conn,
        Position(
            ticker=ticker, quantity=10, buy_price=90.0,
            buy_date=date(2026, 1, 15), currency=currency,
        ),
    )


def bars(conn, ticker="PODD", closes=(100.0, 130.0)):
    history_store.save_bars(
        conn, ticker,
        [
            PricePoint(day=date(2026, 8, 20) - timedelta(days=len(closes) - 1 - i), close=c)
            for i, c in enumerate(closes)
        ],
        "test",
    )


# --- live valuation ---------------------------------------------------------


def test_a_dollar_quote_is_valued_normally(conn):
    buy(conn)
    portfolio = build_portfolio(conn, FixedMarket(price=130.0), record=False)
    assert portfolio.market_value == 1300.0
    assert portfolio.excluded == ()


def test_a_foreign_quote_is_kept_out_of_the_total(conn):
    """The provider reports the listing's own currency; Yahoo really does."""
    buy(conn)
    portfolio = build_portfolio(conn, FixedMarket(price=130.0, currency="EUR"), record=False)
    assert portfolio.market_value == 0.0
    assert len(portfolio.excluded) == 1
    assert "EUR" in portfolio.excluded[0].error


def test_the_exclusion_says_which_ticker_and_why(conn):
    buy(conn, ticker="AIR.PA")
    portfolio = build_portfolio(conn, FixedMarket(currency="EUR"), record=False)
    message = portfolio.excluded[0].error
    assert "AIR.PA" in message
    assert BASE_CURRENCY in message


def test_a_foreign_holding_does_not_reach_the_equity_curve(conn):
    """A valuation with nothing priced is not a zero, it is an absence."""
    buy(conn)
    build_portfolio(conn, FixedMarket(currency="EUR"))
    assert conn.execute("SELECT COUNT(*) c FROM portfolio_valuations").fetchone()["c"] == 0


# --- the API ----------------------------------------------------------------


def test_the_api_reports_a_foreign_holding_separately(client, conn):
    positions_store.add_position(
        conn,
        Position(ticker="AAA", quantity=10, buy_price=90.0,
                 buy_date=date(2026, 1, 15), currency="USD"),
    )
    transactions_store.record(
        conn,
        Transaction(ticker="BBB", kind="buy", trade_date=date(2026, 1, 15),
                    quantity=5, price=50.0, currency="EUR"),
    )
    bars(conn, "AAA", closes=(90.0, 100.0))
    bars(conn, "BBB", closes=(50.0, 60.0))

    body = client.get("/api/portfolio").json()
    assert body["base_currency"] == BASE_CURRENCY
    assert body["foreign_currency"] == [{"ticker": "BBB", "currency": "EUR"}]
    assert body["market_value"] == 1000.0  # only AAA


def test_the_cost_basis_excludes_what_the_value_excludes(client, conn):
    """Otherwise the P&L divides a partial value by a full basis."""
    transactions_store.record(
        conn,
        Transaction(ticker="BBB", kind="buy", trade_date=date(2026, 1, 15),
                    quantity=5, price=50.0, currency="EUR"),
    )
    bars(conn, "BBB", closes=(50.0, 60.0))
    body = client.get("/api/portfolio").json()
    assert body["cost_basis"] == 0.0
    assert body["market_value"] == 0.0
    assert body["pnl_pct"] is None


def test_a_foreign_trade_is_refused_at_the_door(client):
    response = client.post(
        "/api/portfolio/transactions",
        json={"ticker": "AIR.PA", "kind": "buy", "trade_date": "2026-02-10",
              "quantity": 10, "price": 100.0, "currency": "EUR"},
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "EUR" in detail and BASE_CURRENCY in detail


def test_the_base_currency_is_accepted_in_any_case(client, conn):
    response = client.post(
        "/api/portfolio/transactions",
        json={"ticker": "PODD", "kind": "buy", "trade_date": "2026-02-10",
              "quantity": 10, "price": 100.0, "currency": "usd"},
    )
    assert response.status_code == 201
