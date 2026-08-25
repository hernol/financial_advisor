"""Correcting a ledger entry.

Editing in place would make a fixed typo and a number quietly changed months
later look identical afterwards, which is the one thing a ledger exists to
prevent. A correction is a new entry that retires the old one and points at it.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from fa import ledger
from fa.api import deps
from fa.api.app import create_app
from fa.store import positions as positions_store
from fa.store import transactions as transactions_store


@pytest.fixture()
def client(conn):
    deps.set_database(conn)
    with TestClient(create_app(serve_web=False)) as test_client:
        yield test_client
    deps.set_database(None)


def buy(client, ticker="CDE", **fields):
    body = {"ticker": ticker, "kind": "buy", "trade_date": "2026-06-12",
            "quantity": 292, "price": 16.99, **fields}
    response = client.post("/api/portfolio/transactions", json=body)
    assert response.status_code == 201, response.text
    return response.json()


# --- the forgotten fee ------------------------------------------------------


def test_a_forgotten_fee_can_be_added(client, conn):
    entry = buy(client)
    corrected = client.patch(
        f"/api/portfolio/transactions/{entry['id']}", json={"fees": 12.5}
    ).json()
    assert corrected["fees"] == 12.5
    assert corrected["quantity"] == 292          # untouched fields survive
    assert corrected["price"] == 16.99
    assert corrected["cash_flow"] == pytest.approx(-(292 * 16.99) - 12.5)


def test_the_correction_replaces_the_original(client, conn):
    entry = buy(client)
    corrected = client.patch(
        f"/api/portfolio/transactions/{entry['id']}", json={"fees": 12.5}
    ).json()
    assert corrected["replaces_id"] == entry["id"]
    assert corrected["id"] != entry["id"]


def test_only_the_correction_counts(client, conn):
    """The retired entry must not be double counted in the holding."""
    entry = buy(client, quantity=10, price=100.0)
    client.patch(f"/api/portfolio/transactions/{entry['id']}", json={"price": 110.0})
    holding = ledger.holding(conn, "CDE")
    assert holding.quantity == 10.0
    assert holding.average_cost == pytest.approx(110.0)


def test_the_original_is_retired_not_erased(client, conn):
    entry = buy(client)
    client.patch(f"/api/portfolio/transactions/{entry['id']}", json={"fees": 12.5})
    live = transactions_store.list_transactions(conn, ticker="CDE")
    everything = transactions_store.list_transactions(conn, ticker="CDE", include_deleted=True)
    assert len(live) == 1
    assert len(everything) == 2


def test_the_chain_of_versions_can_be_read_back(client, conn):
    entry = buy(client, price=16.99)
    first = client.patch(
        f"/api/portfolio/transactions/{entry['id']}", json={"price": 17.5}
    ).json()
    second = client.patch(
        f"/api/portfolio/transactions/{first['id']}", json={"fees": 3.0}
    ).json()
    chain = transactions_store.history_of(conn, second["id"])
    assert [t.price for t in chain] == [17.5, 17.5, 16.99]
    assert [t.fees for t in chain] == [3.0, 0.0, 0.0]


# --- the rollup follows -----------------------------------------------------


def test_correcting_the_quantity_moves_the_rollup(client, conn):
    entry = buy(client, quantity=100, price=10.0)
    client.patch(f"/api/portfolio/transactions/{entry['id']}", json={"quantity": 120})
    rollup = positions_store.get_position_for_ticker(conn, "CDE")
    assert rollup.quantity == 120.0


def test_correcting_the_ticker_rebuilds_both(client, conn):
    """The old ticker loses its only entry and should stop being a holding."""
    entry = buy(client, ticker="AAA", quantity=10, price=10.0)
    client.patch(f"/api/portfolio/transactions/{entry['id']}", json={"ticker": "bbb"})
    assert positions_store.get_position_for_ticker(conn, "AAA") is None
    assert positions_store.get_position_for_ticker(conn, "BBB").quantity == 10.0
    assert [h.ticker for h in ledger.holdings(conn)] == ["BBB"]


def test_the_portfolio_reflects_the_correction(client, conn):
    from fa.models import PricePoint
    from fa.store import history as history_store

    entry = buy(client, ticker="AAA", quantity=10, price=100.0)
    history_store.save_bars(
        conn, "AAA", [PricePoint(day=date(2026, 8, 20), close=150.0)], "test"
    )
    client.patch(f"/api/portfolio/transactions/{entry['id']}", json={"quantity": 20})
    body = client.get("/api/portfolio").json()
    assert body["holdings"][0]["quantity"] == 20.0
    assert body["market_value"] == 3000.0


# --- what a correction may not do -------------------------------------------


def test_an_empty_patch_is_refused(client):
    entry = buy(client)
    response = client.patch(f"/api/portfolio/transactions/{entry['id']}", json={})
    assert response.status_code == 422


def test_a_future_date_is_refused(client):
    entry = buy(client)
    ahead = (date.today() + timedelta(days=2)).isoformat()
    response = client.patch(
        f"/api/portfolio/transactions/{entry['id']}", json={"trade_date": ahead}
    )
    assert response.status_code == 422
    assert "futuro" in response.json()["detail"]


def test_an_unknown_kind_is_refused(client):
    entry = buy(client)
    response = client.patch(
        f"/api/portfolio/transactions/{entry['id']}", json={"kind": "regalo"}
    )
    assert response.status_code == 422


def test_a_negative_fee_is_refused(client):
    entry = buy(client)
    assert client.patch(
        f"/api/portfolio/transactions/{entry['id']}", json={"fees": -1}
    ).status_code == 422


def test_correcting_something_that_does_not_exist_is_a_404(client):
    assert client.patch(
        "/api/portfolio/transactions/999", json={"fees": 1.0}
    ).status_code == 404


def test_the_store_refuses_to_edit_a_field_that_is_not_data(conn):
    """account_id, source and the links are not the user's to rewrite."""
    from datetime import date as d

    from fa.models import Transaction

    entry = transactions_store.record(
        conn, Transaction(ticker="AAA", kind="buy", trade_date=d(2026, 1, 1),
                          quantity=1, price=1.0)
    )
    with pytest.raises(ValueError, match="account_id"):
        transactions_store.amend(conn, entry.id, {"account_id": 99})
