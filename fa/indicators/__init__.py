"""Pure technical indicators.

Every function is side-effect free and returns ``None`` when the input series
is too short or lacks the required fields, so callers never receive an
invented number.
"""
from __future__ import annotations

from fa.indicators.flow import average_volume, volume_ratio
from fa.indicators.momentum import (
    SESSIONS,
    extremes,
    relative_strength,
    relative_strength_by_window,
    return_pct,
    returns_by_window,
    rsi,
    window_extremes,
)
from fa.indicators.risk import (
    atr,
    daily_returns,
    drawdown_pct,
    max_drawdown_pct,
    stdev,
    true_ranges,
    volatility_pct,
)
from fa.indicators.trend import (
    bollinger,
    detect_cross,
    distance_pct,
    ema,
    ema_series,
    macd,
    macd_cross,
    macd_series,
    sma,
    sma_series,
)

__all__ = [
    "SESSIONS",
    "atr",
    "average_volume",
    "bollinger",
    "daily_returns",
    "detect_cross",
    "distance_pct",
    "drawdown_pct",
    "ema",
    "ema_series",
    "extremes",
    "macd",
    "macd_cross",
    "macd_series",
    "max_drawdown_pct",
    "relative_strength",
    "relative_strength_by_window",
    "return_pct",
    "returns_by_window",
    "rsi",
    "sma",
    "sma_series",
    "stdev",
    "true_ranges",
    "volatility_pct",
    "volume_ratio",
    "window_extremes",
]
