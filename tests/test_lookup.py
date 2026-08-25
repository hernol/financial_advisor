"""Reaching a ticker nobody is following.

The terminal analyses any symbol on the spot. The dashboard could only reach
what was already stored, so a ticker never touched before was unreachable —
which is the gap this closes.
"""
from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from fa.alerts import kinds
from fa.api import deps
from fa.api.app import create_app
from fa.models import Alert, Position, PricePoint
from fa.store import alerts as alerts_store
from fa.store import history as history_store
from fa.store import positions as positions_store


@pytest.fixture()
def client(conn):
    deps.set_database(conn)
    with TestClient(create_app(serve_web=False)) as test_client:
        yield test_client
    deps.set_database(None)


def bars(conn, ticker="NVDA"):
    history_store.save_bars(
        conn, ticker, [PricePoint(day=date(2026, 8, 20), close=100.0)], "test"
    )


# --- the lookup -------------------------------------------------------------


def test_an_unknown_ticker_is_queued(client):
    body = client.post("/api/tickers/NVDA/lookup").json()
    assert body["ticker"] == "NVDA"
    assert body["fetching"] is True


def test_a_ticker_already_stored_is_reported_ready(client, conn):
    """No point paying for a download of what is already on file."""
    bars(conn)
    body = client.post("/api/tickers/NVDA/lookup").json()
    assert body["status"] == "ready"
    assert body["fetching"] is False


def test_the_symbol_is_normalised(client):
    assert client.post("/api/tickers/nvda/lookup").json()["ticker"] == "NVDA"


def test_a_ticker_shaped_like_markup_is_refused(client):
    assert client.post("/api/tickers/<img src=x>/lookup").status_code in (404, 422)


def test_a_failed_lookup_is_not_an_error_for_the_caller(client, conn):
    """The test market refuses every call; the request still succeeds and the
    absence of data is what the detail endpoint reports afterwards."""
    assert client.post("/api/tickers/NVDA/lookup").status_code == 202
    assert client.get("/api/tickers/NVDA").status_code == 404


# --- following vs looking up ------------------------------------------------


def test_a_looked_up_ticker_is_not_followed(client, conn):
    """Looking is not following: the list stays what you chose to watch."""
    bars(conn)
    assert client.get("/api/tickers/NVDA").json()["followed"] is False
    assert client.get("/api/tickers").json() == []


def test_a_ticker_with_an_alert_is_followed(client, conn):
    bars(conn)
    alerts_store.add_alert(
        conn, Alert(ticker="NVDA", kind="rsi", params=kinds.normalize_params("rsi", {}))
    )
    assert client.get("/api/tickers/NVDA").json()["followed"] is True
    assert [row["ticker"] for row in client.get("/api/tickers").json()] == ["NVDA"]


def test_a_ticker_with_a_position_is_followed(client, conn):
    bars(conn)
    positions_store.add_position(
        conn, Position(ticker="NVDA", quantity=1, buy_price=1.0, buy_date=date(2026, 1, 1))
    )
    assert client.get("/api/tickers/NVDA").json()["followed"] is True


def test_creating_an_alert_is_how_a_lookup_becomes_followed(client, conn):
    bars(conn)
    assert client.get("/api/tickers/NVDA").json()["followed"] is False
    client.post("/api/tickers/NVDA/alerts", json={"kind": "rsi"})
    assert client.get("/api/tickers/NVDA").json()["followed"] is True
