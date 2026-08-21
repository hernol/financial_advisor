"""Engine level tests with fake market data and a recording dispatcher."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from fa.alerts import kinds
from fa.alerts.engine import in_cooldown, run_checks
from fa.errors import DataUnavailableError
from fa.models import Alert, Position, Signal
from fa.store import alerts as alerts_store
from fa.store import events as events_store
from fa.store import positions as positions_store
from tests.conftest import make_context

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)


class FakeMarket:
    """Stands in for MarketService; returns a canned context or raises."""

    def __init__(self, price: float = 130.0, fail: bool = False, **context_kwargs) -> None:
        self._price = price
        self._fail = fail
        self._kwargs = context_kwargs
        self.calls: list[str] = []

    def context(self, ticker, since=None):
        self.calls.append(ticker)
        if self._fail:
            raise DataUnavailableError("no provider answered")
        return make_context(self._price, ticker=ticker, **self._kwargs)


class RecordingDispatcher:
    names = ("test",)

    def __init__(self) -> None:
        self.sent: list[Signal] = []

    def send(self, signal: Signal) -> list[str]:
        self.sent.append(signal)
        return ["test"]


def seed(conn, kind=kinds.PCT_UP, params=None, **alert_kwargs) -> Alert:
    position = positions_store.add_position(
        conn, Position(ticker="PODD", quantity=10, buy_price=100.0, buy_date=date(2026, 1, 1))
    )
    return alerts_store.add_alert(
        conn,
        Alert(
            ticker="PODD",
            kind=kind,
            params=kinds.normalize_params(kind, params or {"pct": 10}),
            position_id=position.id,
            **alert_kwargs,
        ),
    )


def test_cooldown_blocks_a_recent_alert():
    alert = Alert(ticker="PODD", kind="pct_up", cooldown_hours=24, last_fired_at=NOW - timedelta(hours=2))
    assert in_cooldown(alert, NOW) is True


def test_cooldown_expires():
    alert = Alert(ticker="PODD", kind="pct_up", cooldown_hours=1, last_fired_at=NOW - timedelta(hours=2))
    assert in_cooldown(alert, NOW) is False


def test_zero_cooldown_never_blocks():
    alert = Alert(ticker="PODD", kind="pct_up", cooldown_hours=0, last_fired_at=NOW)
    assert in_cooldown(alert, NOW) is False


def test_run_checks_fires_notifies_and_persists(conn):
    seed(conn)
    dispatcher = RecordingDispatcher()
    report = run_checks(conn, FakeMarket(price=130.0), dispatcher, now=NOW)
    assert len(report.fired) == 1
    assert len(dispatcher.sent) == 1
    assert len(events_store.recent_events(conn)) == 1
    assert alerts_store.list_alerts(conn)[0].last_fired_at is not None


def test_run_checks_is_quiet_when_nothing_triggers(conn):
    seed(conn)
    dispatcher = RecordingDispatcher()
    report = run_checks(conn, FakeMarket(price=101.0), dispatcher, now=NOW)
    assert report.fired == () and dispatcher.sent == []


def test_alerts_in_cooldown_are_skipped(conn):
    alert = seed(conn)
    alerts_store.mark_fired(conn, alert, NOW - timedelta(hours=1))
    report = run_checks(conn, FakeMarket(price=130.0), RecordingDispatcher(), now=NOW)
    assert report.fired == () and report.skipped_cooldown == 1


def test_provider_failure_is_reported_not_faked(conn):
    seed(conn)
    report = run_checks(conn, FakeMarket(fail=True), RecordingDispatcher(), now=NOW)
    assert report.fired == ()
    assert report.errors and "no provider answered" in report.errors[0]
    assert report.ok is False


def test_inactive_alerts_are_ignored(conn):
    alert = seed(conn)
    alerts_store.set_active(conn, alert.id, active=False)
    report = run_checks(conn, FakeMarket(price=200.0), RecordingDispatcher(), now=NOW)
    assert report.checked == 0 and report.fired == ()


def test_market_is_queried_once_per_ticker(conn):
    seed(conn, kinds.PCT_UP, {"pct": 10})
    seed(conn, kinds.PCT_DOWN, {"pct": 90})
    market = FakeMarket(price=130.0)
    run_checks(conn, market, RecordingDispatcher(), now=NOW)
    assert market.calls == ["PODD"]


def test_expired_alerts_are_deactivated_before_evaluation(conn):
    seed(conn, expires_at=date(2020, 1, 1))
    report = run_checks(conn, FakeMarket(price=200.0), RecordingDispatcher(), now=NOW)
    assert report.expired == 1 and report.checked == 0
