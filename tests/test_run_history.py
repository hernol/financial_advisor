"""Everything a run now leaves behind, fired or not."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from fa.alerts import kinds
from fa.alerts.engine import run_checks
from fa.models import Alert, Position, PricePoint
from fa.store import alerts as alerts_store
from fa.store import events as events_store
from fa.store import history as history_store
from fa.store import positions as positions_store
from fa.store import runs as runs_store
from tests.conftest import make_history
from tests.test_engine import FakeMarket, RecordingDispatcher

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)


class FailingDispatcher:
    """A dispatcher whose only channel refuses the message."""

    names = ("telegram",)

    def dispatch(self, signal):
        from fa.notify.dispatcher import DeliveryResult

        return [DeliveryResult("telegram", False, "HTTP 429")]

    def send(self, signal):
        return []


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


# --- the run log ------------------------------------------------------------


def test_a_quiet_run_is_still_recorded(conn):
    """The whole point: a silent scheduler and a calm market look different."""
    seed(conn)
    report = run_checks(conn, FakeMarket(price=101.0), RecordingDispatcher(), now=NOW, trigger="timer")
    assert report.fired == ()
    run = runs_store.last_run(conn)
    assert run is not None
    assert run["trigger"] == "timer"
    assert run["checked"] == 1
    assert run["fired"] == 0
    assert run["ok"] == 1
    assert run["finished_at"] is not None


def test_an_alert_that_did_not_fire_leaves_an_evaluation(conn):
    alert = seed(conn)
    run_checks(conn, FakeMarket(price=101.0), RecordingDispatcher(), now=NOW)
    evaluations = runs_store.evaluations_for(conn, alert.id)
    assert len(evaluations) == 1
    assert evaluations[0]["outcome"] == runs_store.QUIET
    assert evaluations[0]["price"] == 101.0


def test_a_firing_alert_is_recorded_as_fired(conn):
    alert = seed(conn)
    run_checks(conn, FakeMarket(price=130.0), RecordingDispatcher(), now=NOW)
    evaluations = runs_store.evaluations_for(conn, alert.id)
    assert [e["outcome"] for e in evaluations] == [runs_store.FIRED]
    assert evaluations[0]["detail"]["event_id"] is not None


def test_a_cooled_down_alert_says_so(conn):
    alert = seed(conn, cooldown_hours=24)
    alerts_store.mark_fired(conn, alert, NOW - timedelta(hours=1))
    run_checks(conn, FakeMarket(price=130.0), RecordingDispatcher(), now=NOW)
    evaluations = runs_store.evaluations_for(conn, alert.id)
    assert evaluations[-1]["outcome"] == runs_store.COOLDOWN


def test_an_unreachable_provider_is_recorded_per_alert(conn):
    alert = seed(conn)
    report = run_checks(conn, FakeMarket(fail=True), RecordingDispatcher(), now=NOW)
    assert report.errors
    evaluations = runs_store.evaluations_for(conn, alert.id)
    assert evaluations[0]["outcome"] == runs_store.SKIPPED
    assert runs_store.last_run(conn)["ok"] == 0


def test_health_summarises_the_last_run(conn):
    seed(conn)
    run_checks(conn, FakeMarket(price=101.0), RecordingDispatcher(), now=NOW)
    health = runs_store.health(conn)
    assert health["last_run_at"] is not None
    assert health["last_run_ok"] is True


# --- delivery ---------------------------------------------------------------


def test_every_delivery_attempt_is_recorded(conn):
    seed(conn)
    run_checks(conn, FakeMarket(price=130.0), RecordingDispatcher(), now=NOW)
    rows = conn.execute("SELECT * FROM delivery_attempts").fetchall()
    assert len(rows) == 1
    assert rows[0]["channel"] == "test"
    assert rows[0]["status"] == runs_store.SENT


def test_a_failed_delivery_keeps_its_error(conn):
    """A refused send used to vanish; push needs to know it failed and why."""
    seed(conn)
    run_checks(conn, FakeMarket(price=130.0), FailingDispatcher(), now=NOW)
    row = conn.execute("SELECT * FROM delivery_attempts").fetchone()
    assert row["status"] == runs_store.FAILED
    assert row["error"] == "HTTP 429"
    event = events_store.recent_events(conn)[0]
    assert event["delivered"] == []


# --- market history ---------------------------------------------------------


def test_a_run_archives_the_bars_it_downloaded(conn):
    seed(conn)
    history = make_history([100.0, 101.0, 102.0])
    run_checks(
        conn, FakeMarket(price=102.0, history=history), RecordingDispatcher(), now=NOW
    )
    bars = history_store.load_bars(conn, "PODD")
    assert len(bars) == 3
    assert [b.close for b in bars] == [100.0, 101.0, 102.0]


def test_a_run_archives_the_indicators_it_computed(conn):
    seed(conn)
    run_checks(
        conn,
        FakeMarket(price=102.0, history=make_history([float(90 + i) for i in range(60)])),
        RecordingDispatcher(),
        now=NOW,
    )
    latest = history_store.latest_indicators(conn, "PODD")
    assert latest is not None
    assert latest["price"] == 102.0
    assert latest["rsi"] is not None
    assert latest["payload"]["ticker"] == "PODD"


def test_indicator_series_comes_back_oldest_first(conn):
    seed(conn)
    market = FakeMarket(price=102.0, history=make_history([float(90 + i) for i in range(60)]))
    for offset in range(3):
        run_checks(conn, market, RecordingDispatcher(), now=NOW + timedelta(hours=offset))
    series = history_store.indicator_series(conn, "PODD", "rsi")
    assert len(series) == 3
    assert [row[0] for row in series] == sorted(row[0] for row in series)


def test_an_unknown_indicator_column_is_refused(conn):
    """The column name goes into the SQL, so it is whitelisted, not interpolated."""
    with pytest.raises(ValueError):
        history_store.indicator_series(conn, "PODD", "1; DROP TABLE daily_bars")


# --- bars -------------------------------------------------------------------


def test_saving_bars_twice_updates_instead_of_duplicating(conn):
    day = date(2026, 8, 20)
    history_store.save_bars(conn, "PODD", [PricePoint(day=day, close=100.0)])
    history_store.save_bars(conn, "PODD", [PricePoint(day=day, close=105.0, volume=900.0)])
    bars = history_store.load_bars(conn, "PODD")
    assert len(bars) == 1
    assert bars[0].close == 105.0
    assert bars[0].volume == 900.0


def test_a_close_only_provider_does_not_erase_known_highs(conn):
    """The fallback providers carry no OHLC; they must not wipe what Yahoo gave."""
    day = date(2026, 8, 20)
    history_store.save_bars(conn, "PODD", [PricePoint(day=day, close=100.0, high=101.0, low=99.0)])
    history_store.save_bars(conn, "PODD", [PricePoint(day=day, close=100.5)])
    bar = history_store.load_bars(conn, "PODD")[0]
    assert bar.high == 101.0
    assert bar.low == 99.0
    assert bar.close == 100.5


def test_bar_coverage_reports_what_is_held(conn):
    history_store.save_bars(
        conn,
        "PODD",
        [PricePoint(day=date(2026, 8, 18), close=100.0), PricePoint(day=date(2026, 8, 20), close=102.0)],
    )
    coverage = history_store.bar_coverage(conn, "PODD")
    assert coverage["sessions"] == 2
    assert coverage["first_day"] == "2026-08-18"
    assert coverage["last_day"] == "2026-08-20"


# --- the equity curve -------------------------------------------------------


def test_a_valuation_becomes_a_point_on_the_curve(conn):
    history_store.save_valuation(
        conn, cost_basis=1000.0, market_value=1200.0, pnl_abs=200.0, pnl_pct=20.0, positions=1
    )
    curve = history_store.equity_curve(conn)
    assert len(curve) == 1
    assert curve[0]["market_value"] == 1200.0


def test_the_curve_keeps_the_last_valuation_of_each_day(conn):
    moment = datetime(2026, 8, 20, 10, 0, tzinfo=timezone.utc)
    for hour, value in ((10, 1100.0), (14, 1250.0)):
        history_store.save_valuation(
            conn,
            cost_basis=1000.0,
            market_value=value,
            pnl_abs=value - 1000.0,
            pnl_pct=(value - 1000.0) / 10,
            positions=1,
            taken_at=moment.replace(hour=hour),
        )
    curve = history_store.equity_curve(conn)
    assert len(curve) == 1
    assert curve[0]["market_value"] == 1250.0


# --- events -----------------------------------------------------------------


def test_a_single_event_can_be_acknowledged(conn):
    seed(conn)
    run_checks(conn, FakeMarket(price=130.0), RecordingDispatcher(), now=NOW)
    event = events_store.recent_events(conn)[0]
    assert events_store.acknowledge(conn, event["id"])
    assert events_store.recent_events(conn, unacknowledged_only=True) == []


def test_an_events_history_survives_its_alert_being_deleted(conn):
    alert = seed(conn)
    run_checks(conn, FakeMarket(price=130.0), RecordingDispatcher(), now=NOW)
    alerts_store.delete_alert(conn, alert.id)
    assert alerts_store.list_alerts(conn) == []
    assert len(events_store.events_for_alert(conn, alert.id)) == 1
