"""Behaviour of every alert rule, without network or database."""
from __future__ import annotations

from datetime import date

import pytest

from fa.alerts import kinds, rules
from fa.models import Alert, CorporateEvent
from tests.conftest import make_context, make_history, make_position


def alert(kind: str, **params) -> Alert:
    return Alert(id=1, ticker="PODD", kind=kind, params=kinds.normalize_params(kind, params))


def test_pct_up_fires_above_the_threshold():
    signal = rules.evaluate(alert(kinds.PCT_UP, pct=10), make_context(115.0), make_position(100.0))
    assert signal is not None
    assert signal.payload["change_pct"] == pytest.approx(15.0)


def test_pct_up_stays_quiet_below_the_threshold():
    assert rules.evaluate(alert(kinds.PCT_UP, pct=10), make_context(105.0), make_position(100.0)) is None


def test_pct_down_fires_on_a_drop():
    signal = rules.evaluate(alert(kinds.PCT_DOWN, pct=10), make_context(85.0), make_position(100.0))
    assert signal is not None and signal.severity == "warning"


def test_pct_uses_the_frozen_baseline_when_there_is_no_position():
    rule = Alert(id=1, ticker="PODD", kind=kinds.PCT_UP, params={"pct": 10, "reference": "baseline", "baseline_price": 50.0})
    assert rules.evaluate(rule, make_context(60.0), None) is not None


def test_pct_without_reference_price_never_fires():
    rule = Alert(id=1, ticker="PODD", kind=kinds.PCT_UP, params={"pct": 10, "reference": "baseline"})
    assert rules.evaluate(rule, make_context(999.0), None) is None


def test_price_above_target():
    signal = rules.evaluate(alert(kinds.PRICE_ABOVE, price=200), make_context(201.0), None)
    assert signal is not None and signal.payload["target"] == 200.0


def test_price_above_target_not_reached():
    assert rules.evaluate(alert(kinds.PRICE_ABOVE, price=200), make_context(199.0), None) is None


def test_price_below_target():
    assert rules.evaluate(alert(kinds.PRICE_BELOW, price=100), make_context(99.0), None) is not None


def test_period_elapsed_fires_once_the_horizon_is_reached():
    position = make_position(buy_date=date(2026, 1, 1))
    signal = rules.evaluate(alert(kinds.PERIOD_ELAPSED, months=6), make_context(110.0), position)
    assert signal is not None and signal.payload["pnl_pct"] == pytest.approx(10.0)


def test_period_elapsed_waits_until_the_due_date():
    position = make_position(buy_date=date(2026, 7, 1))
    assert rules.evaluate(alert(kinds.PERIOD_ELAPSED, months=6), make_context(110.0), position) is None


def test_period_elapsed_needs_a_position():
    assert rules.evaluate(alert(kinds.PERIOD_ELAPSED, months=6), make_context(110.0), None) is None


def test_earnings_near_fires_inside_the_window():
    ctx = make_context(100.0, next_earnings=date(2026, 8, 25))
    signal = rules.evaluate(alert(kinds.EARNINGS_NEAR, days=7), ctx, None)
    assert signal is not None and signal.payload["days_ahead"] == 5


def test_earnings_outside_the_window_stays_quiet():
    ctx = make_context(100.0, next_earnings=date(2026, 10, 1))
    assert rules.evaluate(alert(kinds.EARNINGS_NEAR, days=7), ctx, None) is None


def test_earnings_in_the_past_stays_quiet():
    ctx = make_context(100.0, next_earnings=date(2026, 8, 1))
    assert rules.evaluate(alert(kinds.EARNINGS_NEAR, days=7), ctx, None) is None


def test_trailing_stop_fires_after_a_drop_from_the_peak():
    history = make_history([100.0, 150.0, 120.0])
    ctx = make_context(120.0, history=history)
    signal = rules.evaluate(alert(kinds.TRAILING_STOP, pct=15), ctx, make_position(100.0, date(2026, 1, 1)))
    assert signal is not None and signal.payload["peak"] == pytest.approx(150.0)


def test_trailing_stop_quiet_near_the_peak():
    ctx = make_context(145.0, history=make_history([100.0, 150.0, 145.0]))
    assert rules.evaluate(alert(kinds.TRAILING_STOP, pct=15), ctx, make_position(100.0, date(2026, 1, 1))) is None


def test_sma_cross_reports_the_direction_asked_for():
    ctx = make_context(20.0, history=make_history([10.0] * 10 + [9.0, 9.0, 20.0]))
    signal = rules.evaluate(alert(kinds.SMA_CROSS, fast=2, slow=5, direction="golden"), ctx, None)
    assert signal is not None and signal.payload["cross"] == "golden"


def test_sma_cross_ignores_the_opposite_direction():
    ctx = make_context(20.0, history=make_history([10.0] * 10 + [9.0, 9.0, 20.0]))
    assert rules.evaluate(alert(kinds.SMA_CROSS, fast=2, slow=5, direction="death"), ctx, None) is None


def test_rsi_fires_on_overbought():
    ctx = make_context(20.0, history=make_history([float(i) for i in range(1, 25)]))
    signal = rules.evaluate(alert(kinds.RSI, period=14), ctx, None)
    assert signal is not None and signal.payload["state"] == "sobrecompra"


def test_rsi_quiet_in_the_neutral_zone():
    closes = [10.0, 11.0, 10.0, 11.0] * 6
    ctx = make_context(11.0, history=make_history(closes))
    assert rules.evaluate(alert(kinds.RSI, period=14), ctx, None) is None


def test_dividend_alert_fires_inside_the_window():
    ctx = make_context(100.0, next_ex_dividend=date(2026, 8, 22))
    assert rules.evaluate(alert(kinds.DIVIDEND_EX_NEAR, days=7), ctx, None) is not None


def test_split_after_the_purchase_suggests_the_adjusted_cost():
    split = CorporateEvent(ticker="PODD", kind="split", event_date=date(2026, 5, 1), value=4.0)
    ctx = make_context(50.0, recent_splits=(split,))
    signal = rules.evaluate(alert(kinds.SPLIT_DETECTED), ctx, make_position(200.0, date(2026, 1, 1)))
    assert signal is not None
    assert signal.payload["adjusted_buy_price"] == pytest.approx(50.0)
    assert signal.payload["adjusted_quantity"] == pytest.approx(40.0)


def test_split_before_the_purchase_is_ignored():
    split = CorporateEvent(ticker="PODD", kind="split", event_date=date(2025, 5, 1), value=4.0)
    ctx = make_context(50.0, recent_splits=(split,))
    assert rules.evaluate(alert(kinds.SPLIT_DETECTED), ctx, make_position(200.0, date(2026, 1, 1))) is None


def test_unknown_kind_never_fires():
    rogue = Alert(id=1, ticker="PODD", kind="moon_phase", params={})
    assert rules.evaluate(rogue, make_context(100.0), None) is None
