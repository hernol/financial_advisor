"""Pesos are allowed through the door for a CEDEAR, and for nothing else."""
from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from fa.api import deps
from fa.api.app import create_app
from fa.models import PricePoint
from fa.store import history as history_store


@pytest.fixture()
def client(conn):
    deps.set_database(conn)
    with TestClient(create_app(serve_web=False)) as test_client:
        yield test_client
    deps.set_database(None)


def _bars(conn, ticker, close, day=date(2026, 6, 2)):
    history_store.save_bars(conn, ticker, [PricePoint(day=day, close=close)], "test")


def _buy(client, **overrides):
    body = {"ticker": "AAPL.BA", "kind": "buy", "trade_date": "2026-06-02",
            "quantity": 20, "price": 25340.0, "currency": "ARS"}
    body.update(overrides)
    return client.post("/api/portfolio/transactions", json=body)


def test_a_peso_trade_on_a_cedear_is_accepted(client, conn):
    _bars(conn, "AAPL.BA", 25340.0)
    _bars(conn, "AAPL", 316.85)
    assert _buy(client).status_code == 201


def test_a_peso_trade_on_an_ordinary_share_is_still_refused(client):
    """The gate opens for CEDEARs, not for pesos in general."""
    response = _buy(client, ticker="PODD", price=1000.0)
    assert response.status_code == 422


def test_a_euro_trade_is_refused_as_before(client):
    response = _buy(client, ticker="AIR.PA", currency="EUR", price=100.0)
    assert response.status_code == 422


def test_a_peso_trade_without_the_days_closes_is_refused_with_why(client, conn):
    """No pair, no derivation. The message has to say to load it by hand."""
    _bars(conn, "AAPL.BA", 25340.0)
    response = _buy(client)
    assert response.status_code == 422
    assert "a mano" in response.json()["detail"]


def test_the_trade_keeps_the_rate_it_was_converted_at(client, conn):
    _bars(conn, "AAPL.BA", 25340.0)
    _bars(conn, "AAPL", 316.85)
    _buy(client)
    row = conn.execute(
        "SELECT fx_rate, usd_price FROM transactions WHERE ticker = 'AAPL.BA'"
    ).fetchone()
    assert row["fx_rate"] == pytest.approx(1599.5, rel=1e-3)
    assert row["usd_price"] == pytest.approx(316.85 / 20, rel=1e-3)


def test_the_summary_values_a_cedear_through_the_underlying(client, conn):
    _bars(conn, "AAPL.BA", 25340.0)
    _bars(conn, "AAPL", 316.85)
    _buy(client)
    body = client.get("/api/portfolio").json()
    assert body["market_value"] == pytest.approx(316.85, rel=1e-3)
    assert body["foreign_currency"] == []


def test_the_summary_costs_a_cedear_in_dollars_too(client, conn):
    """A peso cost basis against a dollar value would be a meaningless P&L."""
    _bars(conn, "AAPL.BA", 25340.0)
    _bars(conn, "AAPL", 316.85)
    _buy(client)
    body = client.get("/api/portfolio").json()
    assert body["cost_basis"] == pytest.approx(316.85, rel=1e-2)
