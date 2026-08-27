"""A MACD cross has to have a body before it counts as news.

The histogram is zero *at* the crossing by definition, so its size on the day
says how steeply the lines parted. A nearly flat one is two lines grazing, and
it can reverse the next session without anything having happened — but it was
delivered with the same wording as a decisive cross.
"""
from __future__ import annotations

import math

import pytest

from fa.alerts import kinds
from fa.alerts.rules_technical import DEFAULT_MACD_STRENGTH, _cross_strength, macd_cross
from fa.models import Alert


class _Quote:
    def __init__(self, price):
        self.price = price
        self.currency = "USD"


class _Ctx:
    def __init__(self, closes):
        self.ticker = "AAA"
        self.closes = closes
        self.quote = _Quote(closes[-1])


def series(n=250, base=100.0, wobble=0.0):
    """A gently rising series with optional noise, long enough for MACD."""
    return [base + i * 0.05 + (wobble * math.sin(i / 3.0)) for i in range(n)]


def alert(**params):
    body = {"fast": 12, "slow": 26, "signal": 9, "direction": "any", **params}
    return Alert(id=1, ticker="AAA", kind=kinds.MACD_CROSS, params=body)


# --- the measure -----------------------------------------------------------


def test_strength_is_the_histogram_over_the_daily_move():
    closes = series()
    # A flat-ish riser has a tiny daily move, so even a small histogram is big
    # relative to it. What matters is that the ratio is what gets computed.
    value = _cross_strength(closes, 1.0)
    assert value is not None and value > 0


def test_strength_scales_with_the_histogram():
    closes = series(wobble=2.0)
    small = _cross_strength(closes, 0.01)
    large = _cross_strength(closes, 1.0)
    assert large > small


def test_a_flat_series_has_no_measurable_daily_move():
    """Nothing to normalise against, so the answer is 'unknown', not zero."""
    assert _cross_strength([100.0] * 120, 0.5) is None


def test_strength_is_unsigned():
    """A bearish cross is as decisive as a bullish one of the same size."""
    closes = series(wobble=2.0)
    assert _cross_strength(closes, -0.5) == _cross_strength(closes, 0.5)


# --- what the rule does with it --------------------------------------------


def test_the_default_threshold_is_not_zero():
    """Zero would leave the old behaviour in place and fix nothing."""
    assert DEFAULT_MACD_STRENGTH > 0
    assert kinds.CATALOGUE[kinds.MACD_CROSS].defaults["min_strength"] == DEFAULT_MACD_STRENGTH


def test_min_strength_zero_restores_the_old_behaviour():
    """The escape hatch has to actually work: somebody who wants every cross
    should be able to say so."""
    closes = series(wobble=3.0)
    fired_any = False
    for i in range(120, len(closes)):
        if macd_cross(alert(min_strength=0), _Ctx(closes[:i]), None):
            fired_any = True
            break
    assert fired_any


def test_a_graze_is_silent_and_a_decisive_cross_is_not():
    """The same series, the same cross — only the floor changes."""
    from fa import indicators

    closes = series(wobble=3.0)
    crossing = None
    for i in range(120, len(closes)):
        window = closes[:i]
        if indicators.macd_cross(window, 12, 26, 9):
            crossing = window
            break
    assert crossing is not None, "el fixture tiene que producir un cruce"

    histogram = indicators.macd(crossing, 12, 26, 9)[2]
    strength = _cross_strength(crossing, histogram)
    ctx = _Ctx(crossing)
    # A floor just under the measured strength lets it through; just over
    # silences it. Nothing else differs.
    assert macd_cross(alert(min_strength=strength * 0.5), ctx, None) is not None
    assert macd_cross(alert(min_strength=strength * 2.0), ctx, None) is None


def test_an_alert_saved_before_this_existed_gets_the_new_floor():
    """Existing rows have no min_strength in their params. They must pick up
    the default rather than silently keeping the old firing-on-anything."""
    from fa import indicators

    closes = series(wobble=3.0)
    for i in range(120, len(closes)):
        window = closes[:i]
        if not indicators.macd_cross(window, 12, 26, 9):
            continue
        histogram = indicators.macd(window, 12, 26, 9)[2]
        strength = _cross_strength(window, histogram)
        if strength is not None and strength < DEFAULT_MACD_STRENGTH:
            legacy = Alert(id=1, ticker="AAA", kind=kinds.MACD_CROSS,
                           params={"fast": 12, "slow": 26, "signal": 9, "direction": "any"})
            assert macd_cross(legacy, _Ctx(window), None) is None
            return
    pytest.skip("el fixture no produjo un cruce por debajo del umbral")


def test_the_signal_reports_the_strength_it_measured():
    """Whoever reads the event should be able to see why it passed."""
    closes = series(wobble=3.0)
    for i in range(120, len(closes)):
        signal = macd_cross(alert(min_strength=0), _Ctx(closes[:i]), None)
        if signal:
            assert "strength" in signal.payload
            return
    pytest.fail("el fixture tiene que producir un cruce")
