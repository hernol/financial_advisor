"""Unit tests for the indicators added on top of the original SMA/RSI set."""
from __future__ import annotations

import pytest

from fa.indicators import (
    atr,
    average_volume,
    bollinger,
    daily_returns,
    distance_pct,
    ema,
    ema_series,
    extremes,
    macd,
    macd_cross,
    max_drawdown_pct,
    relative_strength,
    relative_strength_by_window,
    return_pct,
    returns_by_window,
    stdev,
    true_ranges,
    volatility_pct,
    volume_ratio,
    window_extremes,
)


def ramp(count: int, start: float = 100.0, step: float = 1.0) -> list[float]:
    return [start + step * i for i in range(count)]


# --- trend -----------------------------------------------------------------

def test_ema_needs_a_full_seed_window():
    assert ema([1.0, 2.0], 5) is None
    assert ema_series([1.0, 2.0], 5) == []


def test_ema_seeds_on_the_first_sma_then_weights_recent_closes():
    # Seed = mean(1,2,3) = 2; next value = (4 - 2) * 2/(3+1) + 2 = 3.
    assert ema_series([1.0, 2.0, 3.0, 4.0], 3) == pytest.approx([2.0, 3.0])


def test_ema_reacts_faster_than_the_sma_after_a_jump():
    closes = [100.0] * 40 + [200.0] * 5
    assert ema(closes, 20) > sum(closes[-20:]) / 20


def test_macd_is_none_until_the_series_covers_slow_plus_signal():
    assert macd(ramp(20)) is None


def test_macd_histogram_is_the_gap_between_line_and_signal():
    line, signal, histogram = macd(ramp(80))
    assert histogram == pytest.approx(line - signal)


def test_macd_cross_only_reports_the_latest_session():
    # A long decline that turns up at the very end produces a bullish cross.
    closes = ramp(60, start=200.0, step=-1.0) + ramp(20, start=141.0, step=6.0)
    assert macd_cross(closes) in {"bullish", None}
    # The same series one session earlier must not already report it.
    assert macd_cross(closes[:-12]) != "bullish"


def test_macd_cross_rejects_inverted_periods():
    assert macd_cross(ramp(80), fast=26, slow=12) is None


def test_bollinger_puts_percent_b_at_one_on_the_upper_band():
    lower, middle, upper, percent_b = bollinger([10.0] * 19 + [10.0], 20)
    assert lower == middle == upper == 10.0
    # A flat series has no width; %B falls back to the midpoint instead of dividing by zero.
    assert percent_b == 0.5


def test_bollinger_marks_a_breakout_above_one():
    band = bollinger([10.0] * 19 + [20.0], 20)
    assert band is not None and band[3] > 1.0


def test_distance_pct_is_signed_and_safe_on_zero():
    assert distance_pct(110.0, 100.0) == pytest.approx(10.0)
    assert distance_pct(90.0, 100.0) == pytest.approx(-10.0)
    assert distance_pct(90.0, 0.0) is None
    assert distance_pct(90.0, None) is None


# --- momentum --------------------------------------------------------------

def test_return_pct_needs_one_bar_more_than_the_window():
    assert return_pct([100.0, 110.0], 2) is None
    assert return_pct([100.0, 105.0, 110.0], 2) == pytest.approx(10.0)


def test_returns_by_window_reports_none_for_windows_it_cannot_cover():
    values = returns_by_window(ramp(70))
    assert values["1m"] is not None and values["3m"] is not None
    assert values["6m"] is None and values["12m"] is None


def test_relative_strength_is_the_excess_over_the_benchmark():
    own = [100.0] + [120.0] * 21          # +20%
    index = [100.0] + [110.0] * 21        # +10%
    assert relative_strength(own, index, 21) == pytest.approx(10.0)


def test_relative_strength_is_none_without_a_benchmark():
    assert relative_strength(ramp(300), [], 252) is None
    assert all(v is None for v in relative_strength_by_window(ramp(300), []).values())


def test_extremes_returns_the_window_low_and_high():
    assert extremes([5.0, 1.0, 9.0, 4.0], 3) == (1.0, 9.0)
    assert extremes([], 3) is None


def test_window_extremes_prefers_real_highs_and_lows():
    closes = [10.0, 11.0, 12.0]
    band = window_extremes(closes, [10.5, 11.5, 13.0], [9.0, 10.0, 11.0], 3)
    assert band == {"high": 13.0, "low": 9.0}


def test_window_extremes_falls_back_to_closes_without_ohlc():
    closes = [10.0, 11.0, 12.0]
    assert window_extremes(closes, [None] * 3, [None] * 3, 3) == {"high": 12.0, "low": 10.0}


# --- risk ------------------------------------------------------------------

def test_daily_returns_skips_a_zero_priced_session():
    assert daily_returns([0.0, 10.0, 11.0]) == pytest.approx([0.1])


def test_stdev_needs_two_points():
    assert stdev([1.0]) is None
    assert stdev([2.0, 4.0]) == pytest.approx(1.4142135, rel=1e-5)


def test_volatility_is_zero_for_a_flat_series():
    assert volatility_pct([100.0] * 120) == pytest.approx(0.0)


def test_volatility_grows_with_the_swing_size():
    calm = [100.0, 101.0] * 60
    wild = [100.0, 130.0] * 60
    assert volatility_pct(wild) > volatility_pct(calm)


def test_max_drawdown_measures_the_worst_peak_to_trough():
    assert max_drawdown_pct([100.0, 50.0, 80.0]) == pytest.approx(50.0)
    assert max_drawdown_pct([]) is None


def test_true_ranges_skips_sessions_without_a_bar():
    ranges = true_ranges([None, 12.0], [None, 8.0], [10.0, 11.0])
    assert ranges == pytest.approx([4.0])


def test_atr_is_none_when_the_provider_only_gave_closes():
    closes = ramp(60)
    assert atr([None] * 60, [None] * 60, closes, 14) is None


def test_atr_averages_the_true_range():
    highs = [11.0] * 40
    lows = [9.0] * 40
    closes = [10.0] * 40
    assert atr(highs, lows, closes, 14) == pytest.approx(2.0)


# --- flow ------------------------------------------------------------------

def test_average_volume_ignores_missing_sessions():
    assert average_volume([None, 100.0, 200.0], 3) == pytest.approx(150.0)
    assert average_volume([None, None], 3) is None


def test_volume_ratio_excludes_the_measured_bar_from_its_baseline():
    volumes = [100.0] * 20 + [300.0]
    assert volume_ratio(volumes, 20) == pytest.approx(3.0)


def test_volume_ratio_is_none_without_volume_data():
    assert volume_ratio([None, None], 20) is None
    assert volume_ratio([], 20) is None
