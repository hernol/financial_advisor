"""Persistence round-trips against a temporary SQLite file."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from fa.models import Alert, Position, Signal
from fa.store import alerts as alerts_store
from fa.store import events as events_store
from fa.store import positions as positions_store
from tests.conftest import make_quote


def test_position_round_trip(conn):
    stored = positions_store.add_position(
        conn, Position(ticker="podd", quantity=10, buy_price=141.5, buy_date=date(2026, 3, 1))
    )
    assert stored.id is not None
    loaded = positions_store.get_position(conn, stored.id)
    assert loaded.ticker == "PODD"
    assert loaded.buy_date == date(2026, 3, 1)
    assert loaded.cost_basis == 1415.0


def test_closed_positions_are_hidden_by_default(conn):
    stored = positions_store.add_position(
        conn, Position(ticker="PODD", quantity=1, buy_price=100, buy_date=date(2026, 1, 1))
    )
    positions_store.close_position(conn, stored.id)
    assert positions_store.list_positions(conn) == []
    assert len(positions_store.list_positions(conn, include_closed=True)) == 1


def test_alert_round_trip_preserves_params(conn):
    stored = alerts_store.add_alert(conn, Alert(ticker="PODD", kind="pct_up", params={"pct": 10.0}))
    loaded = alerts_store.get_alert(conn, stored.id)
    assert loaded.params == {"pct": 10.0}
    assert loaded.active is True


def test_mark_fired_deactivates_one_shot_alerts(conn):
    stored = alerts_store.add_alert(
        conn, Alert(ticker="PODD", kind="price_above", params={"price": 200}, one_shot=True)
    )
    alerts_store.mark_fired(conn, stored)
    reloaded = alerts_store.get_alert(conn, stored.id)
    assert reloaded.active is False
    assert reloaded.last_fired_at is not None


def test_mark_fired_keeps_recurring_alerts_active(conn):
    stored = alerts_store.add_alert(conn, Alert(ticker="PODD", kind="pct_up", params={"pct": 5}))
    alerts_store.mark_fired(conn, stored)
    assert alerts_store.get_alert(conn, stored.id).active is True


def test_expire_stale_deactivates_past_alerts(conn):
    alerts_store.add_alert(
        conn, Alert(ticker="PODD", kind="pct_up", params={"pct": 5}, expires_at=date(2020, 1, 1))
    )
    assert alerts_store.expire_stale(conn, datetime(2026, 8, 20, tzinfo=timezone.utc)) == 1
    assert alerts_store.list_alerts(conn, only_active=True) == []


def test_tracked_tickers_merges_positions_and_alerts(conn):
    positions_store.add_position(
        conn, Position(ticker="PODD", quantity=1, buy_price=100, buy_date=date(2026, 1, 1))
    )
    alerts_store.add_alert(conn, Alert(ticker="AAPL", kind="pct_up", params={"pct": 5}))
    assert positions_store.tracked_tickers(conn) == ["AAPL", "PODD"]


def test_events_are_recorded_and_acknowledged(conn):
    stored = alerts_store.add_alert(conn, Alert(ticker="PODD", kind="pct_up", params={"pct": 5}))
    signal = Signal(alert=stored, title="t", message="m", payload={"price": 1.0})
    events_store.record_event(conn, signal, ["console"])
    pending = events_store.recent_events(conn, unacknowledged_only=True)
    assert len(pending) == 1 and pending[0]["delivered"] == ["console"]
    assert events_store.acknowledge_all(conn) == 1
    assert events_store.recent_events(conn, unacknowledged_only=True) == []


def test_snapshot_peak_since_a_timestamp(conn):
    base = datetime(2026, 8, 1, tzinfo=timezone.utc)
    for offset, price in enumerate([100.0, 150.0, 120.0]):
        quote = make_quote(price)
        events_store.save_snapshot(
            conn, type(quote)(**{**quote.__dict__, "as_of": base + timedelta(days=offset)})
        )
    assert events_store.max_snapshot_since(conn, "PODD", base.isoformat()) == 150.0


def test_split_adjustment_rewrites_the_cost_basis(conn):
    stored = positions_store.add_position(
        conn, Position(ticker="PODD", quantity=10, buy_price=200, buy_date=date(2026, 1, 1))
    )
    positions_store.update_buy_price(conn, stored.id, 50.0, 40.0)
    updated = positions_store.get_position(conn, stored.id)
    assert (updated.buy_price, updated.quantity) == (50.0, 40.0)
    assert updated.cost_basis == stored.cost_basis
