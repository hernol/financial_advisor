"""Momentum indicators: RSI, multi-period returns and relative strength."""
from __future__ import annotations

from collections.abc import Mapping, Sequence

# Approximate session counts, used to translate calendar windows into bars.
SESSIONS = {"1m": 21, "3m": 63, "6m": 126, "12m": 252}


def rsi(closes: Sequence[float], period: int = 14) -> float | None:
    """Wilder's Relative Strength Index over the closing series."""
    if period <= 0 or len(closes) <= period:
        return None
    gains = 0.0
    losses = 0.0
    for previous, current in zip(closes[: period + 1], closes[1 : period + 1]):
        delta = current - previous
        gains += max(delta, 0.0)
        losses += max(-delta, 0.0)
    avg_gain = gains / period
    avg_loss = losses / period
    for previous, current in zip(closes[period:-1], closes[period + 1 :]):
        delta = current - previous
        avg_gain = (avg_gain * (period - 1) + max(delta, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-delta, 0.0)) / period
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def return_pct(closes: Sequence[float], sessions: int) -> float | None:
    """Percentage change over the last ``sessions`` bars."""
    if sessions <= 0 or len(closes) <= sessions:
        return None
    start = closes[-(sessions + 1)]
    if not start:
        return None
    return (closes[-1] - start) / start * 100.0


def returns_by_window(closes: Sequence[float]) -> dict[str, float | None]:
    """Returns for the standard 1/3/6/12 month momentum windows."""
    return {label: return_pct(closes, count) for label, count in SESSIONS.items()}


def relative_strength(
    closes: Sequence[float], benchmark: Sequence[float], sessions: int
) -> float | None:
    """Excess return over the benchmark, in percentage points.

    Positive means the ticker beat the index over the window. ``None`` when
    either series is too short: an unanswerable comparison must not be faked.
    """
    own = return_pct(closes, sessions)
    index = return_pct(benchmark, sessions)
    if own is None or index is None:
        return None
    return own - index


def relative_strength_by_window(
    closes: Sequence[float], benchmark: Sequence[float]
) -> dict[str, float | None]:
    return {
        label: relative_strength(closes, benchmark, count) for label, count in SESSIONS.items()
    }


def extremes(values: Sequence[float], sessions: int) -> tuple[float, float] | None:
    """(low, high) over the last ``sessions`` bars, or the whole series if shorter."""
    window = [v for v in values[-sessions:] if v is not None]
    if not window:
        return None
    return (min(window), max(window))


def window_extremes(
    closes: Sequence[float],
    highs: Sequence[float | None],
    lows: Sequence[float | None],
    sessions: int = SESSIONS["12m"],
) -> Mapping[str, float] | None:
    """52-week style band, using real highs/lows when the provider supplies them."""
    high_values = [v for v in highs[-sessions:] if v is not None] or list(closes[-sessions:])
    low_values = [v for v in lows[-sessions:] if v is not None] or list(closes[-sessions:])
    if not high_values or not low_values:
        return None
    return {"high": max(high_values), "low": min(low_values)}
