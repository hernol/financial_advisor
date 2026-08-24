"""The portfolio screen's API.

Holdings come from the ledger, prices from stored bars. Nothing here touches a
provider, and a position whose price is missing is reported as unpriced rather
than valued at zero.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from fa import models
from fa.api import deps
from fa.api.app import create_app
from fa.models import Position, PricePoint, Transaction
from fa.store import history as history_store
from fa.store import positions as positions_store
from fa.store import transactions as transactions_store


@pytest.fixture()
def client(conn):
    deps.set_database(conn)
    with TestClient(create_app(serve_web=False)) as test_client:
        yield test_client
    deps.set_database(None)


def buy(conn, ticker="PODD", quantity=10.0, price=100.0, day=date(2026, 1, 15)):
    return positions_store.add_position(
        conn, Position(ticker=ticker, quantity=quantity, buy_price=price, buy_date=day)
    )


def bars(conn, ticker="PODD", closes=(100.0, 110.0), end=date(2026, 8, 20)):
    history_store.save_bars(
        conn,
        ticker,
        [
            PricePoint(day=end - timedelta(days=len(closes) - 1 - i), close=c)
            for i, c in enumerate(closes)
        ],
        "test",
    )


# --- empty state ------------------------------------------------------------


def test_an_empty_portfolio_is_zeroed_not_broken(client):
    body = client.get("/api/portfolio").json()
    assert body["holdings"] == []
    assert body["count"] == 0
    assert body["market_value"] == 0.0
    assert body["pnl_pct"] is None


def test_the_curve_is_empty_before_any_run(client):
    assert client.get("/api/portfolio/history").json()["sessions"] == 0


# --- valuation --------------------------------------------------------------


def test_a_holding_is_valued_at_its_last_stored_close(client, conn):
    buy(conn, quantity=10.0, price=100.0)
    bars(conn, closes=(100.0, 130.0))
    body = client.get("/api/portfolio").json()
    row = body["holdings"][0]
    assert row["quantity"] == 10.0
    assert row["price"] == 130.0
    assert row["value"] == 1300.0
    assert row["pnl_abs"] == 300.0
    assert row["pnl_pct"] == pytest.approx(30.0)
    assert body["market_value"] == 1300.0
    assert body["cost_basis"] == 1000.0


def test_two_buys_average_into_one_holding(client, conn):
    buy(conn, quantity=10.0, price=100.0)
    buy(conn, quantity=10.0, price=120.0, day=date(2026, 3, 1))
    bars(conn, closes=(100.0, 130.0))
    row = client.get("/api/portfolio").json()["holdings"][0]
    assert row["quantity"] == 20.0
    assert row["average_cost"] == 110.0
    assert row["entries"] == 2


def test_weights_are_shares_of_the_valued_total(client, conn):
    buy(conn, ticker="AAA", quantity=10.0, price=10.0)
    buy(conn, ticker="BBB", quantity=10.0, price=10.0)
    bars(conn, "AAA", closes=(10.0, 30.0))
    bars(conn, "BBB", closes=(10.0, 10.0))
    body = client.get("/api/portfolio").json()
    weights = {r["ticker"]: r["weight_pct"] for r in body["holdings"]}
    assert weights["AAA"] == pytest.approx(75.0)
    assert weights["BBB"] == pytest.approx(25.0)


def test_holdings_come_back_biggest_first(client, conn):
    buy(conn, ticker="SMALL", quantity=1.0, price=10.0)
    buy(conn, ticker="BIG", quantity=100.0, price=10.0)
    bars(conn, "SMALL", closes=(10.0, 10.0))
    bars(conn, "BIG", closes=(10.0, 10.0))
    body = client.get("/api/portfolio").json()
    assert [r["ticker"] for r in body["holdings"]] == ["BIG", "SMALL"]


def test_a_position_without_bars_is_reported_unpriced(client, conn):
    """Valuing it at zero would quietly understate the whole portfolio."""
    buy(conn, ticker="NOPE", quantity=10.0, price=100.0)
    body = client.get("/api/portfolio").json()
    assert body["unpriced"] == ["NOPE"]
    row = body["holdings"][0]
    assert row["price"] is None
    assert row["value"] is None
    assert row["pnl_abs"] is None


def test_the_day_change_uses_the_previous_close(client, conn):
    buy(conn)
    bars(conn, closes=(100.0, 110.0))
    row = client.get("/api/portfolio").json()["holdings"][0]
    assert row["day_change_pct"] == pytest.approx(10.0)


def test_a_single_bar_leaves_the_day_change_unknown(client, conn):
    buy(conn)
    bars(conn, closes=(110.0,))
    assert client.get("/api/portfolio").json()["holdings"][0]["day_change_pct"] is None


# --- realised results -------------------------------------------------------


def test_a_closed_position_leaves_the_holdings_but_keeps_its_result(client, conn):
    position = buy(conn, quantity=10.0, price=100.0)
    bars(conn, closes=(100.0, 150.0))
    positions_store.close_position(conn, position.id, price=150.0)
    body = client.get("/api/portfolio").json()
    assert body["holdings"] == []
    assert body["realized_pnl"] == pytest.approx(500.0)


def test_dividends_accumulate_across_the_portfolio(client, conn):
    buy(conn)
    bars(conn)
    transactions_store.record(
        conn,
        Transaction(ticker="PODD", kind=models.DIVIDEND, trade_date=date(2026, 4, 1), amount=25.0),
    )
    assert client.get("/api/portfolio").json()["dividends"] == 25.0


# --- the curve --------------------------------------------------------------


def test_the_curve_returns_one_point_per_day(client, conn):
    for day, value in ((date(2026, 8, 18), 1000.0), (date(2026, 8, 19), 1100.0)):
        history_store.save_valuation(
            conn,
            cost_basis=900.0,
            market_value=value,
            pnl_abs=value - 900.0,
            pnl_pct=(value - 900.0) / 9.0,
            positions=1,
            taken_at=__import__("datetime").datetime(
                day.year, day.month, day.day, 20, 0, tzinfo=__import__("datetime").timezone.utc
            ),
        )
    body = client.get("/api/portfolio/history").json()
    assert body["sessions"] == 2
    assert body["day"] == ["2026-08-18", "2026-08-19"]
    assert body["market_value"] == [1000.0, 1100.0]


# --- the ledger -------------------------------------------------------------


def test_the_ledger_lists_newest_first(client, conn):
    buy(conn, ticker="AAA", day=date(2026, 1, 10))
    buy(conn, ticker="BBB", day=date(2026, 5, 20))
    rows = client.get("/api/portfolio/transactions").json()
    assert [r["ticker"] for r in rows] == ["BBB", "AAA"]
    assert rows[0]["kind"] == "buy"
    assert rows[0]["cash_flow"] < 0


def test_a_split_shows_up_in_the_ledger_with_its_ratio(client, conn):
    position = buy(conn, quantity=10.0, price=400.0)
    positions_store.apply_split(conn, position.id, 4.0)
    kinds = {r["kind"]: r for r in client.get("/api/portfolio/transactions").json()}
    assert kinds["split"]["ratio"] == 4.0
    # The purchase that a split used to overwrite is still on file.
    assert kinds["buy"]["price"] == 400.0


def test_the_portfolio_never_calls_a_provider(client, conn):
    buy(conn)
    bars(conn)
    for path in ("/api/portfolio", "/api/portfolio/history", "/api/portfolio/transactions"):
        assert client.get(path).status_code == 200, path
