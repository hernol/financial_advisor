"""Pure technical indicators computed over a closing-price series."""
from __future__ import annotations

from typing import Sequence


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


def drawdown_pct(current: float, peak: float) -> float:
    """Percentage drop from ``peak`` down to ``current`` (0 when above peak)."""
    if peak <= 0:
        return 0.0
    return max((peak - current) / peak * 100.0, 0.0)
