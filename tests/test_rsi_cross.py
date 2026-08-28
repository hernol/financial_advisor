"""The RSI alert reports the crossing, not the state.

``value >= overbought`` is a condition, not an event. In a strong trend the RSI
holds above 70 for weeks, so the alert used to repeat the same sentence every
cooldown window until the reader stopped looking. Over five years of stored
bars that was 1295 firings across ten tickers where 353 crossings happened.
"""
from __future__ import annotations

import pytest

from fa.alerts import kinds
from fa.alerts.rules import rsi_bounds
from fa.errors import ValidationError
from fa.indicators import rsi
from fa.models import Alert


class _Quote:
    def __init__(self, price):
        self.price = price
        self.currency = "USD"


class _Ctx:
    def __init__(self, closes):
        self.ticker = "AAA"
        self.closes = list(closes)
        self.quote = _Quote(closes[-1])


def alert(**params):
    body = {"period": 14, "overbought": 70.0, "oversold": 30.0, **params}
    return Alert(id=1, ticker="AAA", kind=kinds.RSI, params=body)


def climbing(flat=30, rise=30):
    """Flat, then a run of up sessions: the RSI walks from ~50 through 70."""
    return [100.0] * flat + [100.0 + i * 2.0 for i in range(1, rise + 1)]


def first_crossing(closes, threshold=70.0):
    """The shortest prefix whose RSI is at or above the threshold."""
    for i in range(16, len(closes) + 1):
        value = rsi(closes[:i], 14)
        if value is not None and value >= threshold:
            return closes[:i]
    raise AssertionError("el fixture tiene que cruzar el umbral")


# --- the crossing ----------------------------------------------------------


def test_it_fires_on_the_session_the_threshold_is_crossed():
    window = first_crossing(climbing())
    signal = rsi_bounds(alert(), _Ctx(window), None)
    assert signal is not None
    assert "sobrecompra" in signal.title


def test_it_stays_quiet_while_the_reading_remains_above():
    """The session after the crossing is not news; the trend simply continues."""
    closes = climbing()
    window = first_crossing(closes)
    assert rsi_bounds(alert(), _Ctx(window), None) is not None

    longer = closes[: len(window) + 1]
    assert rsi(longer, 14) >= 70.0, "sigue en sobrecompra"
    assert rsi_bounds(alert(), _Ctx(longer), None) is None


def test_it_fires_again_after_leaving_and_re_entering():
    """Two crossings are two events, however long the gap between them."""
    closes = climbing()
    window = first_crossing(closes)
    assert rsi_bounds(alert(), _Ctx(window), None) is not None

    # Fall back under the threshold, then climb through it again.
    recovering = list(window)
    while rsi(recovering, 14) is None or rsi(recovering, 14) >= 70.0:
        recovering.append(recovering[-1] * 0.97)
    while rsi(recovering, 14) < 70.0:
        recovering.append(recovering[-1] * 1.06)
    assert rsi_bounds(alert(), _Ctx(recovering), None) is not None


def test_the_oversold_side_crosses_too():
    closes = [100.0] * 30 + [100.0 - i * 2.0 for i in range(1, 31)]
    window = None
    for i in range(16, len(closes) + 1):
        value = rsi(closes[:i], 14)
        if value is not None and value <= 30.0:
            window = closes[:i]
            break
    assert window is not None
    signal = rsi_bounds(alert(), _Ctx(window), None)
    assert signal is not None and "sobreventa" in signal.title


# --- the escape hatch and the old rows -------------------------------------


def test_level_mode_still_repeats():
    """Anyone who wants the standing reminder can still have it."""
    closes = climbing()
    window = first_crossing(closes)
    longer = closes[: len(window) + 1]
    assert rsi_bounds(alert(mode="level"), _Ctx(longer), None) is not None


def test_an_alert_saved_before_this_existed_gets_crossing_semantics():
    """Rows in the database have no mode key. They must pick up the new
    behaviour, which is the whole point of the change."""
    closes = climbing()
    window = first_crossing(closes)
    longer = closes[: len(window) + 1]
    legacy = Alert(id=1, ticker="AAA", kind=kinds.RSI,
                   params={"period": 14, "overbought": 70.0, "oversold": 30.0})
    assert rsi_bounds(legacy, _Ctx(longer), None) is None


def test_without_a_previous_reading_it_says_nothing():
    """A first evaluation has no crossing to observe, only a position. Firing
    would assert a transition nobody saw."""
    closes = climbing()
    window = first_crossing(closes)
    short = window[-15:]           # exactly period + 1: current computable, previous not
    assert rsi(short, 14) is not None
    assert rsi(short[:-1], 14) is None
    assert rsi_bounds(alert(), _Ctx(short), None) is None


# --- what the event carries ------------------------------------------------


def test_the_event_reports_where_it_came_from():
    window = first_crossing(climbing())
    signal = rsi_bounds(alert(), _Ctx(window), None)
    assert signal.payload["previous_rsi"] < 70.0
    assert signal.payload["rsi"] >= 70.0
    assert "venía de" in signal.message


# --- validation ------------------------------------------------------------


def test_an_unknown_mode_is_rejected():
    with pytest.raises(ValidationError):
        kinds.normalize_params(kinds.RSI, {"mode": "sometimes"})


def test_inverted_thresholds_are_rejected():
    with pytest.raises(ValidationError):
        kinds.normalize_params(kinds.RSI, {"overbought": 20.0, "oversold": 80.0})


def test_the_default_is_crossing():
    assert kinds.CATALOGUE[kinds.RSI].defaults["mode"] == "cross"
    assert kinds.normalize_params(kinds.RSI, {})["mode"] == "cross"
