"""Deposits and withdrawals, so the cash figure is a balance and not a net.

Without them, cash starts at zero and reads negative for as long as money is in
shares. With them it is what is actually sitting in the account, and the total
is holdings plus that.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from fa import equity, models
from fa.api import deps
from fa.api.app import create_app
from fa.models import Position, PricePoint, Transaction
from fa.store import history as history_store
from fa.store import positions as positions_store
from fa.store import transactions as transactions_store

END = date(2026, 8, 20)


@pytest.fixture()
def client(conn):
    deps.set_database(conn)
    with TestClient(create_app(serve_web=False)) as test_client:
        yield test_client
    deps.set_database(None)


def deposit(client, amount=10000.0, day="2026-08-17", **extra):
    body = {"kind": "deposit", "trade_date": day, "amount": amount, **extra}
    return client.post("/api/portfolio/transactions", json=body)


def bars(conn, ticker, closes, end=END):
    history_store.save_bars(
        conn, ticker,
        [
            PricePoint(day=end - timedelta(days=len(closes) - 1 - i), close=c)
            for i, c in enumerate(closes)
        ],
        "test",
    )


# --- the arithmetic ---------------------------------------------------------


def test_a_deposit_adds_to_cash(client, conn):
    deposit(client, 10000.0)
    body = client.get("/api/portfolio").json()
    assert body["cash"] == 10000.0
    assert body["net_worth"] == 10000.0
    # Putting money in is not earning it.
    assert body["total_result"] == 0.0


def test_a_withdrawal_takes_it_out(client):
    deposit(client, 10000.0)
    client.post("/api/portfolio/transactions",
                json={"kind": "withdraw", "trade_date": "2026-08-18", "amount": 2500.0})
    assert client.get("/api/portfolio").json()["cash"] == 7500.0


def test_a_withdrawal_of_a_positive_amount_moves_the_balance_down(client):
    """The amount says how much, never which way — a sign here read +1200."""
    deposit(client, 5000.0)
    client.post("/api/portfolio/transactions",
                json={"kind": "withdraw", "trade_date": "2026-08-18",
                      "amount": 1200.0, "fees": 15.0})
    assert client.get("/api/portfolio").json()["cash"] == pytest.approx(3785.0)


def test_buying_moves_money_out_of_cash_into_shares(client, conn):
    deposit(client, 10000.0)
    positions_store.add_position(
        conn, Position(ticker="AAA", quantity=10, buy_price=100.0, buy_date=date(2026, 8, 18))
    )
    bars(conn, "AAA", (100.0, 100.0, 100.0))
    body = client.get("/api/portfolio").json()
    assert body["cash"] == 9000.0
    assert body["market_value"] == 1000.0
    assert body["net_worth"] == 10000.0
    assert body["total_result"] == 0.0   # nothing gained or lost yet


def test_the_total_is_holdings_plus_cash(client, conn):
    deposit(client, 10000.0)
    positions_store.add_position(
        conn, Position(ticker="AAA", quantity=10, buy_price=100.0, buy_date=date(2026, 8, 18))
    )
    bars(conn, "AAA", (100.0, 100.0, 130.0))
    body = client.get("/api/portfolio").json()
    assert body["market_value"] == 1300.0
    assert body["net_worth"] == 10300.0
    assert body["total_result"] == 300.0


def test_selling_returns_the_money_to_cash(client, conn):
    deposit(client, 10000.0)
    position = positions_store.add_position(
        conn, Position(ticker="AAA", quantity=10, buy_price=100.0, buy_date=date(2026, 8, 18))
    )
    bars(conn, "AAA", (100.0, 100.0, 130.0))
    positions_store.close_position(conn, position.id, price=130.0)
    body = client.get("/api/portfolio").json()
    assert body["cash"] == 10300.0
    assert body["market_value"] == 0.0
    # Selling did not change what the account is worth, only where it sits.
    assert body["net_worth"] == 10300.0
    assert body["total_result"] == 300.0


# --- the curve --------------------------------------------------------------


def test_the_curve_starts_at_the_deposit(conn):
    """The account existed from the day money went in, not from the first buy."""
    transactions_store.record(
        conn,
        Transaction(ticker=None, kind=models.DEPOSIT, trade_date=date(2026, 8, 18),
                    amount=10000.0),
    )
    points = equity.curve(conn, today=END)
    assert points[0]["day"] == "2026-08-18"
    assert points[0]["total"] == 10000.0


def test_the_curve_holds_flat_through_a_purchase(conn):
    transactions_store.record(
        conn,
        Transaction(ticker=None, kind=models.DEPOSIT, trade_date=date(2026, 8, 18),
                    amount=10000.0),
    )
    positions_store.add_position(
        conn, Position(ticker="AAA", quantity=10, buy_price=100.0, buy_date=date(2026, 8, 19))
    )
    bars(conn, "AAA", (100.0, 100.0, 100.0))
    totals = [p["total"] for p in equity.curve(conn, today=END)]
    assert totals == [10000.0, 10000.0, 10000.0]


# --- what a cash movement is not --------------------------------------------


def test_a_deposit_needs_an_amount(client):
    response = client.post("/api/portfolio/transactions",
                           json={"kind": "deposit", "trade_date": "2026-08-18"})
    assert response.status_code == 422
    assert "amount" in response.json()["detail"]


def test_a_deposit_may_not_carry_a_ticker(client):
    """Attaching one would invent a relationship that does not exist."""
    response = deposit(client, 1000.0, ticker="AAA")
    assert response.status_code == 422
    assert "no de un papel" in response.json()["detail"]


def test_a_purchase_still_needs_a_ticker(client):
    response = client.post(
        "/api/portfolio/transactions",
        json={"kind": "buy", "trade_date": "2026-08-18", "quantity": 1, "price": 1.0},
    )
    assert response.status_code == 422
    assert "ticker" in response.json()["detail"]


def test_a_deposit_creates_no_holding(client, conn):
    deposit(client, 10000.0)
    assert client.get("/api/portfolio").json()["holdings"] == []
    assert positions_store.list_positions(conn) == []
    assert transactions_store.tickers(conn) == []


def test_a_deposit_does_not_send_anyone_looking_for_a_price(client, conn):
    """The test market refuses every call; a fetch here would surface as one."""
    assert deposit(client, 10000.0).json()["fetching_prices"] is False


def test_a_correction_cannot_give_a_deposit_a_ticker(client):
    created = deposit(client, 10000.0).json()
    response = client.patch(
        f"/api/portfolio/transactions/{created['id']}",
        json={"kind": "deposit", "ticker": "AAA"},
    )
    assert response.status_code == 422


def test_a_deposit_can_be_corrected(client):
    created = deposit(client, 10000.0).json()
    client.patch(f"/api/portfolio/transactions/{created['id']}", json={"amount": 12000.0})
    assert client.get("/api/portfolio").json()["cash"] == 12000.0


def test_a_deposit_can_be_removed(client):
    created = deposit(client, 10000.0).json()
    assert client.delete(f"/api/portfolio/transactions/{created['id']}").status_code == 204
    assert client.get("/api/portfolio").json()["cash"] == 0.0


# --- what was earned versus what was put in ---------------------------------


def test_the_result_ignores_a_deposit(client, conn):
    """The whole point of recording deposits: a bigger balance is not a gain."""
    deposit(client, 10000.0)
    before = client.get("/api/portfolio").json()["total_result"]
    deposit(client, 50000.0, day="2026-08-18")
    after = client.get("/api/portfolio").json()
    assert after["total_result"] == before
    assert after["contributed"] == 60000.0
    assert after["net_worth"] == 60000.0


def test_a_withdrawal_fee_is_a_loss_and_the_amount_is_not(client):
    """Taking money out returns capital; the fee to do it is gone for good."""
    deposit(client, 10000.0)
    client.post("/api/portfolio/transactions",
                json={"kind": "withdraw", "trade_date": "2026-08-18",
                      "amount": 2000.0, "fees": 30.0})
    body = client.get("/api/portfolio").json()
    assert body["contributed"] == 8000.0
    assert body["total_result"] == pytest.approx(-30.0)


def test_the_result_still_reads_the_same_without_any_deposit(client, conn):
    """Nobody has to record deposits: with none, contributed is zero and the
    result is what it always was — holdings plus the cash the ledger made."""
    bars(conn, "AAA", [100.0] * 5)
    positions_store.add_position(
        conn, Position(ticker="AAA", quantity=10, buy_price=90.0, buy_date=date(2026, 8, 17))
    )
    body = client.get("/api/portfolio").json()
    assert body["contributed"] == 0.0
    assert body["total_result"] == pytest.approx(body["market_value"] + body["cash"])


def test_the_curve_separates_worth_from_result(client, conn):
    """A deposit lifts what the account is worth and leaves the result flat."""
    deposit(client, 10000.0, day="2026-08-17")
    points = equity.curve(conn, days=10, today=END)
    day = points[-1]
    assert day["contributed"] == 10000.0
    assert day["total"] == 10000.0
    assert day["result"] == 0.0


def test_a_gain_shows_up_in_the_result_and_a_deposit_does_not(client, conn):
    bars(conn, "AAA", [100.0] * 5)
    deposit(client, 10000.0, day="2026-08-17")
    positions_store.add_position(
        conn, Position(ticker="AAA", quantity=10, buy_price=90.0, buy_date=date(2026, 8, 17))
    )
    body = client.get("/api/portfolio").json()
    # Bought 10 at 90, worth 100: the 100 of gain is the result, and the 10,000
    # that paid for it is not.
    assert body["total_result"] == pytest.approx(100.0)
    assert body["contributed"] == 10000.0


def test_contributed_counts_only_money_crossing_the_boundary(conn):
    """A sale moves money inside the account; it is not a contribution."""
    entries = [
        Transaction(ticker=None, kind=models.DEPOSIT,
                    trade_date=date(2026, 8, 17), amount=5000.0),
        Transaction(ticker="AAA", kind=models.SELL, trade_date=date(2026, 8, 18),
                    quantity=10, price=50.0),
        Transaction(ticker=None, kind=models.WITHDRAW,
                    trade_date=date(2026, 8, 19), amount=1000.0, fees=10.0),
    ]
    assert models.contributed(entries) == 4000.0
