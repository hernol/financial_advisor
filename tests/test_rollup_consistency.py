"""The rollup and the ledger say the same thing, whoever wrote the entry.

Before this, only the CLI's add_position maintained the positions table, so a
trade loaded from the dashboard existed in the ledger and nowhere else: the
terminal could not see it and the alerts that need a position ignored it.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from fa import ledger, models
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


def post_trade(client, ticker="CDE", kind="buy", **fields):
    body = {"ticker": ticker, "kind": kind, "trade_date": "2026-06-12", **fields}
    response = client.post("/api/portfolio/transactions", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def agrees(conn, ticker) -> bool:
    """The rollup matches a fresh replay of the ledger."""
    rollup = positions_store.get_position_for_ticker(conn, ticker)
    holding = ledger.holding(conn, ticker)
    if rollup is None:
        return not holding.is_open
    return (
        rollup.quantity == pytest.approx(holding.quantity)
        and rollup.buy_price == pytest.approx(holding.average_cost)
    )


# --- both doors ------------------------------------------------------------


def test_a_trade_from_the_api_reaches_the_cli(client, conn):
    """The bug that started this: loaded on the phone, invisible in the terminal."""
    post_trade(client, quantity=292, price=16.99)
    assert [p.ticker for p in positions_store.list_positions(conn)] == ["CDE"]
    assert positions_store.tracked_tickers(conn) == ["CDE"]


def test_both_doors_produce_the_same_rollup(client, conn):
    post_trade(client, ticker="AAA", quantity=10, price=100.0)
    positions_store.add_position(
        conn, Position(ticker="BBB", quantity=10, buy_price=100.0, buy_date=date(2026, 6, 12))
    )
    a = positions_store.get_position_for_ticker(conn, "AAA")
    b = positions_store.get_position_for_ticker(conn, "BBB")
    assert (a.quantity, a.buy_price) == (b.quantity, b.buy_price)


def test_the_same_ticker_twice_is_one_position_at_the_average(conn):
    """Two rows for one ticker was the old behaviour and it double counted."""
    positions_store.add_position(
        conn, Position(ticker="RH", quantity=40, buy_price=168.0, buy_date=date(2026, 2, 10))
    )
    positions_store.add_position(
        conn, Position(ticker="RH", quantity=20, buy_price=132.0, buy_date=date(2026, 6, 3))
    )
    rows = positions_store.list_positions(conn)
    assert len(rows) == 1
    assert rows[0].quantity == 60.0
    assert rows[0].buy_price == pytest.approx(156.0)


def test_the_buy_date_is_the_first_purchase(conn):
    for day, price in ((date(2026, 6, 3), 132.0), (date(2026, 2, 10), 168.0)):
        transactions_store.record(
            conn,
            Transaction(ticker="RH", kind=models.BUY, trade_date=day, quantity=10, price=price),
        )
        positions_store.sync_from_ledger(conn, "RH")
    assert positions_store.get_position_for_ticker(conn, "RH").buy_date == date(2026, 2, 10)


# --- every mutation keeps them in step -------------------------------------


def test_a_sale_closes_the_rollup(client, conn):
    post_trade(client, ticker="AAA", quantity=10, price=100.0)
    post_trade(client, ticker="AAA", kind="sell", quantity=10, price=150.0)
    assert positions_store.list_positions(conn) == []
    closed = positions_store.get_position_for_ticker(conn, "AAA")
    assert closed.closed_at is not None
    assert closed.realized_pnl == pytest.approx(500.0)
    assert agrees(conn, "AAA")


def test_a_partial_sale_leaves_the_rest(client, conn):
    post_trade(client, ticker="AAA", quantity=10, price=100.0)
    post_trade(client, ticker="AAA", kind="sell", quantity=4, price=150.0)
    rollup = positions_store.get_position_for_ticker(conn, "AAA")
    assert rollup.quantity == 6.0
    assert rollup.closed_at is None
    assert agrees(conn, "AAA")


def test_a_split_rescales_the_rollup(client, conn):
    post_trade(client, ticker="AAA", quantity=10, price=400.0)
    post_trade(client, ticker="AAA", kind="split", ratio=4)
    rollup = positions_store.get_position_for_ticker(conn, "AAA")
    assert rollup.quantity == 40.0
    assert rollup.buy_price == pytest.approx(100.0)
    # The purchase a split used to overwrite is still on file.
    original = transactions_store.list_transactions(conn, ticker="AAA", kind=models.BUY)[0]
    assert original.price == 400.0


def test_removing_an_entry_moves_the_rollup_back(client, conn):
    first = post_trade(client, ticker="AAA", quantity=10, price=100.0)
    post_trade(client, ticker="AAA", quantity=10, price=200.0)
    assert positions_store.get_position_for_ticker(conn, "AAA").quantity == 20.0

    assert client.delete(f"/api/portfolio/transactions/{first['id']}").status_code == 204
    rollup = positions_store.get_position_for_ticker(conn, "AAA")
    assert rollup.quantity == 10.0
    assert rollup.buy_price == pytest.approx(200.0)
    assert agrees(conn, "AAA")


def test_removing_the_only_entry_retires_the_rollup(client, conn):
    entry = post_trade(client, ticker="AAA", quantity=10, price=100.0)
    client.delete(f"/api/portfolio/transactions/{entry['id']}")
    assert positions_store.list_positions(conn) == []
    assert positions_store.get_position_for_ticker(conn, "AAA") is None


def test_a_position_archived_by_hand_stays_archived(conn):
    """No sale to point at, so nothing should reopen it."""
    position = positions_store.add_position(
        conn, Position(ticker="AAA", quantity=10, buy_price=100.0, buy_date=date(2026, 1, 1))
    )
    positions_store.close_position(conn, position.id)
    positions_store.sync_from_ledger(conn, "AAA")
    assert positions_store.get_position_for_ticker(conn, "AAA").closed_at is not None
    assert positions_store.list_positions(conn) == []


def test_the_entry_is_linked_back_to_its_rollup(client, conn):
    post_trade(client, ticker="AAA", quantity=10, price=100.0)
    rollup = positions_store.get_position_for_ticker(conn, "AAA")
    entry = transactions_store.list_transactions(conn, ticker="AAA")[0]
    assert entry.position_id == rollup.id


# --- fetching the first prices ---------------------------------------------


def test_a_new_ticker_reports_that_prices_are_coming(client, conn):
    """Not "go run check-alerts": the program fetches what it needs."""
    body = post_trade(client, ticker="NEW", quantity=1, price=10.0)
    assert body["fetching_prices"] is True


def test_a_known_ticker_does_not_refetch(client, conn):
    history_store.save_bars(
        conn, "AAA", [PricePoint(day=date(2026, 8, 20), close=100.0)], "test"
    )
    body = post_trade(client, ticker="AAA", quantity=1, price=10.0)
    assert body["fetching_prices"] is False


def test_a_failed_fetch_does_not_undo_the_trade(client, conn):
    """The test market always refuses; the entry must survive anyway."""
    post_trade(client, ticker="NEW", quantity=1, price=10.0)
    assert transactions_store.list_transactions(conn, ticker="NEW")
    assert positions_store.get_position_for_ticker(conn, "NEW") is not None


def test_reads_still_never_fetch(client, conn):
    """The narrower rule: only the write that introduces a ticker may fetch."""
    post_trade(client, ticker="AAA", quantity=1, price=10.0)
    history_store.save_bars(
        conn, "AAA",
        [PricePoint(day=date(2026, 8, 20) - timedelta(days=i), close=100.0 + i) for i in range(3)],
        "test",
    )
    for path in (
        "/api/portfolio",
        "/api/portfolio/history",
        "/api/tickers",
        "/api/tickers/AAA",
        "/api/tickers/AAA/bars",
        "/api/health",
    ):
        assert client.get(path).status_code == 200, path
