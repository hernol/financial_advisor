"""Risk indicators: drawdown, realised volatility and average true range."""
from __future__ import annotations

from collections.abc import Sequence

TRADING_DAYS = 252


def drawdown_pct(current: float, peak: float) -> float:
    """Percentage drop from ``peak`` down to ``current`` (0 when above peak)."""
    if peak <= 0:
        return 0.0
    return max((peak - current) / peak * 100.0, 0.0)


def daily_returns(closes: Sequence[float]) -> list[float]:
    """Simple period-over-period returns; zero-priced sessions are skipped."""
    return [
        (current - previous) / previous
        for previous, current in zip(closes, closes[1:])
        if previous
    ]


def stdev(values: Sequence[float]) -> float | None:
    """Sample standard deviation (n-1), the convention for return series."""
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return variance**0.5


def volatility_pct(closes: Sequence[float], window: int = 90) -> float | None:
    """Annualised realised volatility over the last ``window`` sessions, in %."""
    if window <= 1:
        return None
    returns = daily_returns(closes[-(window + 1) :])
    deviation = stdev(returns)
    if deviation is None:
        return None
    return deviation * (TRADING_DAYS**0.5) * 100.0


def max_drawdown_pct(closes: Sequence[float]) -> float | None:
    """Worst peak-to-trough decline of the whole series, in %."""
    if not closes:
        return None
    peak = closes[0]
    worst = 0.0
    for close in closes:
        peak = max(peak, close)
        worst = max(worst, drawdown_pct(close, peak))
    return worst


def true_ranges(
    highs: Sequence[float | None], lows: Sequence[float | None], closes: Sequence[float]
) -> list[float]:
    """True range per session; sessions without a high/low bar are dropped."""
    ranges: list[float] = []
    for index in range(1, min(len(highs), len(lows), len(closes))):
        high, low = highs[index], lows[index]
        if high is None or low is None:
            continue
        previous_close = closes[index - 1]
        ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
    return ranges


def atr(
    highs: Sequence[float | None],
    lows: Sequence[float | None],
    closes: Sequence[float],
    period: int = 14,
) -> float | None:
    """Wilder's Average True Range.

    Returns ``None`` when the provider gave closes only, which is the honest
    answer: ATR cannot be derived from closing prices alone.
    """
    if period <= 0:
        return None
    ranges = true_ranges(highs, lows, closes)
    if len(ranges) < period:
        return None
    value = sum(ranges[:period]) / period
    for current in ranges[period:]:
        value = (value * (period - 1) + current) / period
    return value
