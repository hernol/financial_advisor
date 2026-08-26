"""Taking a ticker out of the portfolio.

For the case the per-entry delete handles badly: a holding loaded with the
wrong quantity, or a price from a different instrument, where what you want is
the ticker gone rather than a hunt through the ledger for its rows.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from fa import equity, ledger
from fa.api import deps
from fa.api.app import create_app
from fa.models import Position, PricePoint
from fa.store import history as history_store
from fa.store import positions as positions_store
from fa.store import transactions as transactions_store


@pytest.fixture()
def client(conn):
    deps.set_database(conn)
    with TestClient(create_app(serve_web=False)) as test_client:
        yield test_client
    deps.set_database(None)


def buy(conn, ticker="SPY", quantity=1856.0, price=12.84, day=date(2026, 8, 18)):
    return positions_store.add_position(
        conn, Position(ticker=ticker, quantity=quantity, buy_price=price, buy_date=day)
    )


def bars(conn, ticker, closes, end=date(2026, 8, 20)):
    history_store.save_bars(
        conn, ticker,
        [
            PricePoint(day=end - timedelta(days=len(closes) - 1 - i), close=c)
            for i, c in enumerate(closes)
        ],
        "test",
    )


# --- the removal ------------------------------------------------------------


def test_the_holding_leaves_the_portfolio(client, conn):
    buy(conn)
    bars(conn, "SPY", (12.0, 13.0))
    assert client.get("/api/portfolio").json()["count"] == 1

    body = client.delete("/api/portfolio/holdings/SPY").json()
    assert body["retired"] == 1
    assert client.get("/api/portfolio").json()["count"] == 0
    assert ledger.holdings(conn) == []


def test_every_entry_of_that_ticker_goes_at_once(client, conn):
    """One by one is the thing this exists to avoid."""
    buy(conn, quantity=100.0, price=10.0, day=date(2026, 8, 10))
    buy(conn, quantity=200.0, price=11.0, day=date(2026, 8, 12))
    buy(conn, quantity=300.0, price=12.0, day=date(2026, 8, 14))
    assert client.delete("/api/portfolio/holdings/SPY").json()["retired"] == 3
    assert transactions_store.list_transactions(conn, ticker="SPY") == []


def test_the_other_holdings_are_untouched(client, conn):
    buy(conn, ticker="SPY")
    buy(conn, ticker="LDOS", quantity=7.0, price=143.27)
    bars(conn, "LDOS", (140.0, 150.0))
    client.delete("/api/portfolio/holdings/SPY")
    assert [h.ticker for h in ledger.holdings(conn)] == ["LDOS"]


def test_the_rollup_follows(client, conn):
    buy(conn)
    client.delete("/api/portfolio/holdings/SPY")
    assert positions_store.get_position_for_ticker(conn, "SPY") is None
    assert positions_store.list_positions(conn) == []


def test_the_symbol_is_matched_regardless_of_case(client, conn):
    buy(conn)
    assert client.delete("/api/portfolio/holdings/spy").json()["ticker"] == "SPY"


def test_removing_something_that_is_not_there_is_a_404(client, conn):
    response = client.delete("/api/portfolio/holdings/NOPE")
    assert response.status_code == 404
    assert "no tiene movimientos" in response.json()["detail"]


def test_removing_twice_is_a_404_the_second_time(client, conn):
    buy(conn)
    assert client.delete("/api/portfolio/holdings/SPY").status_code == 200
    assert client.delete("/api/portfolio/holdings/SPY").status_code == 404


# --- what survives ----------------------------------------------------------


def test_the_entries_stay_in_the_history(client, conn):
    """Soft, like every other delete here."""
    buy(conn)
    client.delete("/api/portfolio/holdings/SPY")
    assert transactions_store.list_transactions(conn, ticker="SPY") == []
    kept = transactions_store.list_transactions(conn, ticker="SPY", include_deleted=True)
    assert len(kept) == 1
    assert kept[0].quantity == 1856.0


def test_the_curve_stops_counting_it(client, conn):
    """The curve is derived from the live entries, so the past drops it too —
    which is the point when the numbers were wrong all along."""
    buy(conn, ticker="SPY", quantity=1000.0, price=10.0, day=date(2026, 8, 18))
    buy(conn, ticker="LDOS", quantity=10.0, price=100.0, day=date(2026, 8, 18))
    bars(conn, "SPY", (10.0, 10.0, 10.0))
    bars(conn, "LDOS", (100.0, 100.0, 100.0))
    before = equity.curve(conn, today=date(2026, 8, 20))[-1]["market_value"]

    client.delete("/api/portfolio/holdings/SPY")
    after = equity.curve(conn, today=date(2026, 8, 20))[-1]["market_value"]
    assert before == 11000.0
    assert after == 1000.0


def test_the_market_data_is_kept(client, conn):
    """The bars belong to the ticker, not to the holding, and cost a download."""
    buy(conn)
    bars(conn, "SPY", (12.0, 13.0))
    client.delete("/api/portfolio/holdings/SPY")
    assert history_store.bar_coverage(conn, "SPY")["sessions"] == 2
