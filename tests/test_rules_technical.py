"""Tests for the indicator-driven alert rules."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from fa.alerts import kinds
from fa.alerts.rules import evaluate
from fa.models import Alert, MarketContext, Position, PricePoint, Quote

TODAY = datetime(2024, 6, 3, tzinfo=timezone.utc)


def bars(count: int, *, start: float = 100.0, step: float = 0.5, ohlc: bool = True, volume=1000.0):
    first = date(2022, 1, 3)
    out = []
    for i in range(count):
        close = start + step * i
        out.append(
            PricePoint(
                day=first + timedelta(days=i),
                close=close,
                high=close + 1.0 if ohlc else None,
                low=close - 1.0 if ohlc else None,
                volume=volume,
            )
        )
    return out


def ctx(history, *, price=None, benchmark=(), ticker="TEST") -> MarketContext:
    last = price if price is not None else history[-1].close
    return MarketContext(
        ticker=ticker,
        quote=Quote(ticker=ticker, price=last, currency="USD", as_of=TODAY, source="test"),
        history=history,
        benchmark_ticker="SPY",
        benchmark_history=benchmark,
        evaluated_at=TODAY,
    )


def alert(kind: str, **params) -> Alert:
    return Alert(ticker="TEST", kind=kind, params=kinds.normalize_params(kind, params), id=1)


def position(buy_price: float = 100.0, buy_date: date = date(2022, 1, 3)) -> Position:
    return Position(ticker="TEST", quantity=10, buy_price=buy_price, buy_date=buy_date, id=1)


# --- rel_strength ----------------------------------------------------------

def test_rel_strength_fires_when_the_ticker_lags_the_index():
    own = bars(300, step=0.01)          # nearly flat
    index = bars(300, start=100.0, step=1.0)  # strong index
    signal = evaluate(alert(kinds.REL_STRENGTH, pct=10, window="3m"), ctx(own, benchmark=index), None)
    assert signal is not None
    assert signal.payload["excess_pct"] < -10
    assert "SPY" in signal.message


def test_rel_strength_stays_quiet_when_the_ticker_wins():
    own = bars(300, step=1.0)
    index = bars(300, start=100.0, step=0.01)
    assert evaluate(alert(kinds.REL_STRENGTH, pct=10), ctx(own, benchmark=index), None) is None


def test_rel_strength_needs_a_benchmark():
    assert evaluate(alert(kinds.REL_STRENGTH, pct=1), ctx(bars(300)), None) is None


def test_rel_strength_rejects_an_unknown_window():
    rogue = Alert(ticker="TEST", kind=kinds.REL_STRENGTH, params={"pct": 5.0, "window": "9m"}, id=1)
    assert evaluate(rogue, ctx(bars(300), benchmark=bars(300)), None) is None


# --- atr_stop --------------------------------------------------------------

def test_atr_stop_fires_below_the_volatility_adjusted_level():
    history = bars(60, start=100.0, step=1.0)   # peak 159, ATR = 2
    signal = evaluate(alert(kinds.ATR_STOP, multiple=3), ctx(history, price=150.0), position())
    assert signal is not None
    assert signal.severity == "critical"
    assert signal.payload["stop"] == pytest.approx(159.0 - 3 * 2.0)


def test_atr_stop_holds_while_the_price_is_above_the_stop():
    history = bars(60, start=100.0, step=1.0)
    assert evaluate(alert(kinds.ATR_STOP, multiple=3), ctx(history, price=158.0), position()) is None


def test_atr_stop_is_silent_without_ohlc_data():
    history = bars(60, start=100.0, step=1.0, ohlc=False)
    assert evaluate(alert(kinds.ATR_STOP, multiple=3), ctx(history, price=10.0), position()) is None


def test_atr_stop_lookback_ignores_an_ancient_peak():
    # A spike far in the past, then a calm stretch just below it.
    old_peak = [PricePoint(day=date(2022, 1, 3), close=500.0, high=501.0, low=499.0, volume=1.0)]
    recent = bars(60, start=100.0, step=1.0)
    history = old_peak + recent
    unbounded = evaluate(alert(kinds.ATR_STOP, multiple=3), ctx(history, price=150.0), position())
    windowed = evaluate(
        alert(kinds.ATR_STOP, multiple=3, lookback_days=30),
        ctx(history, price=150.0),
        position(),
    )
    # Without a window the stop hangs off the 500 spike; with one it tracks the recent peak.
    assert unbounded is not None
    assert unbounded.payload["peak"] == pytest.approx(500.0)
    assert windowed is None or windowed.payload["peak"] < 500.0


def test_atr_stop_records_the_window_it_used():
    history = bars(60, start=100.0, step=1.0)
    signal = evaluate(alert(kinds.ATR_STOP, multiple=3), ctx(history, price=150.0), position())
    assert signal.payload["peak_since"] == position().buy_date.isoformat()


def test_atr_stop_requires_a_position():
    assert evaluate(alert(kinds.ATR_STOP, multiple=3), ctx(bars(60)), None) is None


# --- volume_spike ----------------------------------------------------------

def test_volume_spike_fires_on_an_unusual_session():
    history = bars(40, volume=1000.0)
    spike = PricePoint(day=date(2024, 6, 2), close=history[-1].close, volume=5000.0)
    signal = evaluate(alert(kinds.VOLUME_SPIKE, ratio=2.5), ctx(list(history) + [spike]), None)
    assert signal is not None and signal.payload["volume_ratio"] == pytest.approx(5.0)


def test_volume_spike_ignores_the_session_still_running():
    history = bars(40, volume=1000.0)
    running = PricePoint(day=TODAY.date(), close=history[-1].close, volume=50_000.0)
    assert evaluate(alert(kinds.VOLUME_SPIKE, ratio=2.5), ctx(list(history) + [running]), None) is None


def test_volume_spike_is_silent_without_volume():
    assert evaluate(alert(kinds.VOLUME_SPIKE, ratio=2.0), ctx(bars(40, volume=None)), None) is None


# --- 52 week extremes ------------------------------------------------------

def test_new_high_fires_above_the_yearly_peak():
    history = bars(300, step=0.1)
    signal = evaluate(alert(kinds.NEW_52W_HIGH), ctx(history, price=10_000.0), None)
    assert signal is not None and "máximo" in signal.title


def test_new_low_fires_below_the_yearly_trough():
    history = bars(300, step=0.1)
    signal = evaluate(alert(kinds.NEW_52W_LOW), ctx(history, price=1.0), None)
    assert signal is not None and signal.severity == "critical"


def test_extremes_do_not_fire_inside_the_range():
    history = bars(300, step=0.1)
    middle = (history[0].close + history[-1].close) / 2
    assert evaluate(alert(kinds.NEW_52W_HIGH), ctx(history, price=middle), None) is None
    assert evaluate(alert(kinds.NEW_52W_LOW), ctx(history, price=middle), None) is None


def test_tolerance_lets_a_near_miss_fire():
    history = bars(300, step=0.1, ohlc=False)
    peak = max(p.close for p in history[:-1])
    assert evaluate(alert(kinds.NEW_52W_HIGH), ctx(history, price=peak * 0.99), None) is None
    signal = evaluate(alert(kinds.NEW_52W_HIGH, tolerance_pct=2), ctx(history, price=peak * 0.99), None)
    assert signal is not None


# --- sma_break -------------------------------------------------------------

def test_sma_break_fires_only_on_the_crossing_session():
    history = bars(260, start=100.0, step=1.0)
    below = evaluate(alert(kinds.SMA_BREAK, period=200, direction="below"), ctx(history, price=1.0), None)
    assert below is not None and below.severity == "warning"


def test_sma_break_upward_needs_the_upward_direction():
    history = bars(260, start=400.0, step=-1.0)   # falling, price under its SMA200
    up = evaluate(alert(kinds.SMA_BREAK, period=200, direction="above"), ctx(history, price=10_000.0), None)
    assert up is not None and "recuperó" in up.message


def test_sma_break_is_silent_when_the_price_stays_on_the_same_side():
    history = bars(260, start=100.0, step=1.0)
    assert evaluate(alert(kinds.SMA_BREAK, direction="below"), ctx(history), None) is None


def test_sma_break_needs_enough_history():
    assert evaluate(alert(kinds.SMA_BREAK, period=200), ctx(bars(50)), None) is None


# --- macd_cross ------------------------------------------------------------

def bullish_reversal():
    """A long decline whose single up session flips the MACD above its signal."""
    return bars(60, start=200.0, step=-1.0) + bars(1, start=147.0, step=1.0)


def test_macd_cross_reports_the_bullish_turn():
    signal = evaluate(alert(kinds.MACD_CROSS), ctx(bullish_reversal()), None)
    assert signal is not None
    assert signal.payload["cross"] == "bullish"
    assert "alcista" in signal.title


def test_macd_cross_filtered_to_the_other_direction_stays_quiet():
    assert evaluate(alert(kinds.MACD_CROSS, direction="below"), ctx(bullish_reversal()), None) is None


def test_macd_cross_matching_the_wanted_direction_fires():
    signal = evaluate(alert(kinds.MACD_CROSS, direction="above"), ctx(bullish_reversal()), None)
    assert signal is not None and signal.payload["cross"] == "bullish"


def test_macd_cross_needs_history():
    assert evaluate(alert(kinds.MACD_CROSS), ctx(bars(20)), None) is None
