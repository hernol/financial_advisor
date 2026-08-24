"""Trend indicators: moving averages, crosses, MACD and Bollinger bands."""
from __future__ import annotations

from collections.abc import Sequence


def sma(closes: Sequence[float], period: int) -> float | None:
    """Simple moving average of the last ``period`` closes."""
    if period <= 0 or len(closes) < period:
        return None
    window = closes[-period:]
    return sum(window) / period


def sma_series(closes: Sequence[float], period: int) -> list[float]:
    """Full SMA series; shorter than ``closes`` by ``period - 1`` items."""
    if period <= 0 or len(closes) < period:
        return []
    return [sum(closes[i - period : i]) / period for i in range(period, len(closes) + 1)]


def detect_cross(closes: Sequence[float], fast_period: int, slow_period: int) -> str | None:
    """Return ``"golden"``, ``"death"`` or ``None`` for the latest session.

    A cross is only reported when it happened on the most recent close, so the
    same event is not re-announced on every run.
    """
    if fast_period >= slow_period:
        return None
    fast = sma_series(closes, fast_period)
    slow = sma_series(closes, slow_period)
    if len(fast) < 2 or len(slow) < 2:
        return None
    # Align the tails: the slow series is the shorter one.
    fast = fast[-len(slow) :]
    previous_diff = fast[-2] - slow[-2]
    current_diff = fast[-1] - slow[-1]
    if previous_diff <= 0 < current_diff:
        return "golden"
    if previous_diff >= 0 > current_diff:
        return "death"
    return None


def ema_series(closes: Sequence[float], period: int) -> list[float]:
    """Exponential moving average series, seeded with the first SMA window."""
    if period <= 0 or len(closes) < period:
        return []
    multiplier = 2.0 / (period + 1)
    value = sum(closes[:period]) / period
    series = [value]
    for close in closes[period:]:
        value = (close - value) * multiplier + value
        series.append(value)
    return series


def ema(closes: Sequence[float], period: int) -> float | None:
    series = ema_series(closes, period)
    return series[-1] if series else None


def macd_series(
    closes: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[list[float], list[float]]:
    """Return the (macd, signal) series aligned on their common tail."""
    if fast >= slow:
        return ([], [])
    fast_series = ema_series(closes, fast)
    slow_series = ema_series(closes, slow)
    if not fast_series or not slow_series:
        return ([], [])
    # The fast EMA starts earlier; trim it so both line up on the same sessions.
    fast_series = fast_series[-len(slow_series) :]
    line = [f - s for f, s in zip(fast_series, slow_series)]
    signal_series = ema_series(line, signal)
    if not signal_series:
        return (line, [])
    return (line[-len(signal_series) :], signal_series)


def macd(
    closes: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[float, float, float] | None:
    """Latest (macd, signal, histogram); ``None`` when the series is too short."""
    line, signal_series = macd_series(closes, fast, slow, signal)
    if not line or not signal_series:
        return None
    return (line[-1], signal_series[-1], line[-1] - signal_series[-1])


def macd_cross(
    closes: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9
) -> str | None:
    """``"bullish"``/``"bearish"`` only when the cross happened on the last close."""
    line, signal_series = macd_series(closes, fast, slow, signal)
    if len(line) < 2 or len(signal_series) < 2:
        return None
    previous = line[-2] - signal_series[-2]
    current = line[-1] - signal_series[-1]
    if previous <= 0 < current:
        return "bullish"
    if previous >= 0 > current:
        return "bearish"
    return None


def bollinger(
    closes: Sequence[float], period: int = 20, deviations: float = 2.0
) -> tuple[float, float, float, float] | None:
    """Return (lower, middle, upper, %B).

    %B places the price inside the band: 0 sits on the lower band, 1 on the
    upper one, and values outside [0, 1] mean the price broke out.
    """
    middle = sma(closes, period)
    if middle is None:
        return None
    window = closes[-period:]
    variance = sum((c - middle) ** 2 for c in window) / period
    spread = variance**0.5 * deviations
    lower, upper = middle - spread, middle + spread
    width = upper - lower
    percent_b = 0.5 if width == 0 else (closes[-1] - lower) / width
    return (lower, middle, upper, percent_b)


def distance_pct(price: float, reference: float | None) -> float | None:
    """Signed percentage distance from ``reference`` to ``price``."""
    if not reference:
        return None
    return (price - reference) / reference * 100.0
