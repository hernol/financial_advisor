"""The write endpoints.

Two properties matter beyond the happy path: validation is the same one the CLI
uses, and creating an alert never reaches for a provider.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from fa.alerts import kinds
from fa.api import deps
from fa.api.app import create_app
from fa.models import Alert, Position, PricePoint
from fa.store import alerts as alerts_store
from fa.store import events as events_store
from fa.store import history as history_store
from fa.store import positions as positions_store
from fa.store import transactions as transactions_store


@pytest.fixture()
def client(conn):
    deps.set_database(conn)
    with TestClient(create_app(serve_web=False)) as test_client:
        yield test_client
    deps.set_database(None)


def bars(conn, ticker="PODD", closes=(100.0, 120.0)):
    history_store.save_bars(
        conn,
        ticker,
        [
            PricePoint(day=date(2026, 8, 20) - timedelta(days=len(closes) - 1 - i), close=c)
            for i, c in enumerate(closes)
        ],
        "test",
    )


def buy(conn, ticker="PODD"):
    return positions_store.add_position(
        conn, Position(ticker=ticker, quantity=10, buy_price=100.0, buy_date=date(2026, 1, 1))
    )


# --- the catalogue ----------------------------------------------------------


def test_the_catalogue_is_served_for_building_the_form(client):
    rows = client.get("/api/alert-kinds").json()
    assert len(rows) == len(kinds.CATALOGUE)
    rsi = next(r for r in rows if r["key"] == "rsi")
    assert rsi["defaults"]["period"] == 14
    assert rsi["requires_position"] is False


def test_closed_sets_come_back_as_choices(client):
    """A param with fixed options should be a picker, not a free text box."""
    rows = {r["key"]: r for r in client.get("/api/alert-kinds").json()}
    assert rows["rel_strength"]["choices"]["window"] == list(kinds.WINDOWS)
    assert rows["sma_break"]["choices"]["direction"] == ["above", "below"]
    assert rows["pct_down"]["choices"]["reference"] == ["buy", "baseline"]


# --- creating alerts --------------------------------------------------------


def test_an_alert_is_created_with_the_catalogue_defaults(client, conn):
    response = client.post("/api/tickers/PODD/alerts", json={"kind": "rsi"})
    assert response.status_code == 201
    body = response.json()
    assert body["params"] == {"period": 14, "overbought": 70.0, "oversold": 30.0}
    assert body["ticker"] == "PODD"
    assert alerts_store.list_alerts(conn, ticker="PODD")


def test_params_are_validated_by_the_same_rules_as_the_cli(client):
    response = client.post(
        "/api/tickers/PODD/alerts", json={"kind": "sma_cross", "params": {"fast": 200, "slow": 50}}
    )
    assert response.status_code == 422
    assert "menor que la lenta" in response.json()["detail"]


def test_an_unknown_kind_is_refused_by_name(client):
    response = client.post("/api/tickers/PODD/alerts", json={"kind": "telepathy"})
    assert response.status_code == 422
    assert "telepathy" in response.json()["detail"]


def test_an_alert_needing_a_position_says_so(client):
    response = client.post("/api/tickers/PODD/alerts", json={"kind": "trailing_stop"})
    assert response.status_code == 422
    assert "posición" in response.json()["detail"]


def test_a_baseline_alert_anchors_to_the_last_stored_close(client, conn):
    """Never to a live quote: creating an alert must not trigger a download."""
    bars(conn, closes=(100.0, 137.5))
    response = client.post(
        "/api/tickers/PODD/alerts",
        json={"kind": "pct_down", "params": {"pct": 8, "reference": "baseline"}},
    )
    assert response.status_code == 201
    assert response.json()["params"]["baseline_price"] == 137.5


def test_a_baseline_alert_without_stored_prices_explains_the_gap(client):
    response = client.post(
        "/api/tickers/NOPE/alerts",
        json={"kind": "pct_down", "params": {"pct": 8, "reference": "baseline"}},
    )
    assert response.status_code == 422
    assert "precio" in response.json()["detail"]


def test_a_new_alert_attaches_to_the_open_position(client, conn):
    position = buy(conn)
    body = client.post("/api/tickers/PODD/alerts", json={"kind": "trailing_stop"}).json()
    stored = alerts_store.get_alert(conn, body["id"])
    assert stored.position_id == position.id


def test_the_cooldown_can_be_overridden(client):
    body = client.post(
        "/api/tickers/PODD/alerts", json={"kind": "rsi", "cooldown_hours": 168}
    ).json()
    assert body["cooldown_hours"] == 168


def test_a_negative_cooldown_is_rejected_before_it_reaches_the_store(client):
    response = client.post(
        "/api/tickers/PODD/alerts", json={"kind": "rsi", "cooldown_hours": -5}
    )
    assert response.status_code == 422


# --- silencing and deleting -------------------------------------------------


def test_an_alert_can_be_silenced_and_brought_back(client, conn):
    alert_id = client.post("/api/tickers/PODD/alerts", json={"kind": "rsi"}).json()["id"]
    assert client.patch(f"/api/alerts/{alert_id}", json={"active": False}).json()["active"] is False
    assert alerts_store.list_alerts(conn, only_active=True) == []
    assert client.patch(f"/api/alerts/{alert_id}", json={"active": True}).json()["active"] is True
    assert len(alerts_store.list_alerts(conn, only_active=True)) == 1


def test_deleting_an_alert_is_soft(client, conn):
    alert_id = client.post("/api/tickers/PODD/alerts", json={"kind": "rsi"}).json()["id"]
    assert client.delete(f"/api/alerts/{alert_id}").status_code == 204
    assert alerts_store.list_alerts(conn) == []
    assert alerts_store.get_alert(conn, alert_id) is not None


def test_touching_a_missing_alert_is_a_404(client):
    assert client.patch("/api/alerts/999", json={"active": False}).status_code == 404
    assert client.delete("/api/alerts/999").status_code == 404


# --- acknowledging ----------------------------------------------------------


def test_one_event_can_be_acknowledged(client, conn):
    from fa.alerts.engine import run_checks
    from tests.conftest import make_history
    from tests.test_engine import FakeMarket, RecordingDispatcher

    buy(conn)
    alerts_store.add_alert(
        conn,
        Alert(ticker="PODD", kind="pct_up", params=kinds.normalize_params("pct_up", {"pct": 10})),
    )
    run_checks(conn, FakeMarket(price=130.0, history=make_history([100.0, 130.0])),
               RecordingDispatcher())
    event = events_store.recent_events(conn)[0]
    assert client.post(f"/api/events/{event['id']}/ack").status_code == 200
    assert events_store.recent_events(conn, unacknowledged_only=True) == []
    # Acknowledging twice is not an error the user caused, but it is not a
    # silent success either.
    assert client.post(f"/api/events/{event['id']}/ack").status_code == 404


# --- the ledger -------------------------------------------------------------


def test_a_buy_can_be_recorded(client, conn):
    response = client.post(
        "/api/portfolio/transactions",
        json={"ticker": "podd", "kind": "buy", "trade_date": "2026-02-10",
              "quantity": 10, "price": 100.0},
    )
    assert response.status_code == 201
    assert response.json()["cash_flow"] == -1000.0
    entries = transactions_store.list_transactions(conn, ticker="PODD")
    assert len(entries) == 1
    assert entries[0].ticker == "PODD"


def test_a_buy_without_a_price_is_refused_by_name(client):
    response = client.post(
        "/api/portfolio/transactions",
        json={"ticker": "PODD", "kind": "buy", "trade_date": "2026-02-10", "quantity": 10},
    )
    assert response.status_code == 422
    assert "price" in response.json()["detail"]


def test_a_future_trade_is_refused(client):
    ahead = (date.today() + timedelta(days=3)).isoformat()
    response = client.post(
        "/api/portfolio/transactions",
        json={"ticker": "PODD", "kind": "buy", "trade_date": ahead,
              "quantity": 1, "price": 1.0},
    )
    assert response.status_code == 422
    assert "futuro" in response.json()["detail"]


def test_a_cash_dividend_needs_an_amount(client):
    response = client.post(
        "/api/portfolio/transactions",
        json={"ticker": "PODD", "kind": "dividend", "trade_date": "2026-02-10"},
    )
    assert response.status_code == 422


def test_an_unknown_kind_lists_the_valid_ones(client):
    response = client.post(
        "/api/portfolio/transactions",
        json={"ticker": "PODD", "kind": "gift", "trade_date": "2026-02-10"},
    )
    assert response.status_code == 422
    assert "buy" in response.json()["detail"]


def test_a_recorded_sale_shows_up_in_the_portfolio(client, conn):
    client.post("/api/portfolio/transactions",
                json={"ticker": "PODD", "kind": "buy", "trade_date": "2026-01-10",
                      "quantity": 10, "price": 100.0})
    client.post("/api/portfolio/transactions",
                json={"ticker": "PODD", "kind": "sell", "trade_date": "2026-06-10",
                      "quantity": 10, "price": 150.0})
    body = client.get("/api/portfolio").json()
    assert body["holdings"] == []
    assert body["realized_pnl"] == pytest.approx(500.0)


def test_a_deleted_entry_leaves_the_rollup_but_stays_on_file(client, conn):
    created = client.post(
        "/api/portfolio/transactions",
        json={"ticker": "PODD", "kind": "buy", "trade_date": "2026-01-10",
              "quantity": 10, "price": 100.0},
    ).json()
    assert client.delete(f"/api/portfolio/transactions/{created['id']}").status_code == 204
    assert client.get("/api/portfolio").json()["count"] == 0
    assert len(transactions_store.list_transactions(conn, include_deleted=True)) == 1


def test_removing_a_missing_entry_is_a_404(client):
    assert client.delete("/api/portfolio/transactions/999").status_code == 404


# --- refreshing prices on demand -------------------------------------------


def test_refreshing_a_ticker_is_accepted(client, conn):
    bars(conn)
    assert client.post("/api/tickers/PODD/refresh").json() == {
        "ticker": "PODD", "status": "running"
    }


def test_refreshing_reaches_the_provider_even_with_prices_on_file(client, conn, monkeypatch):
    """A refresh must force. Without it the warm helper short-circuits on the
    bars already stored and the button would do nothing at all."""
    bars(conn)
    seen = {}

    def fake_warm(db, market, ticker, *, force=False):
        seen["ticker"] = ticker
        seen["force"] = force
        return True

    monkeypatch.setattr("fa.warm.warm", fake_warm)
    client.post("/api/tickers/podd/refresh")
    assert seen == {"ticker": "PODD", "force": True}


def test_refreshing_does_not_evaluate_alerts(client, conn, monkeypatch):
    """Distinct from /check on purpose: asking for a fresher number must not be
    able to fire an alert and send a notification."""
    bars(conn)
    position = buy(conn)
    alerts_store.add_alert(
        conn,
        Alert(ticker="PODD", kind=kinds.PCT_UP, params={"pct": 1, "reference": "buy"},
              position_id=position.id),
    )
    monkeypatch.setattr("fa.warm.warm", lambda *a, **k: True)

    def explode(*args, **kwargs):
        raise AssertionError("un refresh no debe correr el motor de alertas")

    monkeypatch.setattr("fa.alerts.engine.run_checks", explode)
    assert client.post("/api/tickers/PODD/refresh").status_code == 202
    assert events_store.recent_events(conn, limit=5) == []


def test_a_refresh_that_cannot_reach_a_provider_does_not_kill_the_worker(client, conn):
    """The autouse market refuses every call; the endpoint still answers."""
    bars(conn)
    assert client.post("/api/tickers/PODD/refresh").status_code == 202
