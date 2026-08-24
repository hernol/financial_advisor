"""Derived technical view of a ticker, computed once from a MarketContext.

Every field is optional on purpose: when the history is too short or the
provider only supplied closing prices, the value stays ``None`` instead of
being approximated. Callers render "n/a" and the AI prompt lists it as missing
data.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from fa import indicators
from fa.models import MarketContext

DEFAULT_BENCHMARK = "SPY"
VOLATILITY_WINDOW = 90
BOLLINGER_PERIOD = 20
ATR_PERIOD = 14
VOLUME_PERIOD = 20
FAST_SMA = 50
SLOW_SMA = 200


@dataclass(frozen=True)
class TechnicalSnapshot:
    """All the price-derived numbers for one ticker at one moment."""

    ticker: str
    price: float
    currency: str = "USD"
    sma_fast: float | None = None
    sma_slow: float | None = None
    vs_sma_slow_pct: float | None = None
    rsi: float | None = None
    macd_histogram: float | None = None
    macd_state: str | None = None
    percent_b: float | None = None
    volatility_pct: float | None = None
    atr: float | None = None
    atr_pct: float | None = None
    high_52w: float | None = None
    low_52w: float | None = None
    from_high_pct: float | None = None
    from_low_pct: float | None = None
    max_drawdown_pct: float | None = None
    volume_ratio: float | None = None
    returns: Mapping[str, float | None] = None  # type: ignore[assignment]
    relative_strength: Mapping[str, float | None] = None  # type: ignore[assignment]
    benchmark: str = ""
    sessions: int = 0

    @property
    def trend(self) -> str:
        """Coarse trend label driven by the price/SMA200 relationship."""
        if self.vs_sma_slow_pct is None:
            return "desconocida"
        return "alcista" if self.vs_sma_slow_pct >= 0 else "bajista"

    @property
    def beats_benchmark(self) -> bool | None:
        value = (self.relative_strength or {}).get("12m")
        return None if value is None else value > 0

    def missing(self) -> tuple[str, ...]:
        """Names of the indicators that could not be computed."""
        gaps: list[str] = []
        if self.atr is None:
            gaps.append("ATR (el proveedor no entregó high/low)")
        if self.volume_ratio is None:
            gaps.append("volumen relativo (el proveedor no entregó volumen)")
        if not any((self.relative_strength or {}).values()):
            gaps.append(f"fuerza relativa vs {self.benchmark or DEFAULT_BENCHMARK}")
        if self.sma_slow is None:
            gaps.append(f"SMA{SLOW_SMA} (historial corto)")
        return tuple(gaps)


def build_snapshot(ctx: MarketContext) -> TechnicalSnapshot:
    """Compute every indicator the context can support."""
    closes = ctx.closes
    price = ctx.quote.price
    highs, lows = ctx.highs, ctx.lows
    # Today's partial bar would understate the volume comparison.
    volumes = ctx.completed_volumes()

    band = indicators.window_extremes(closes, highs, lows)
    high_52w = band["high"] if band else None
    low_52w = band["low"] if band else None

    average_true_range = indicators.atr(highs, lows, closes, ATR_PERIOD)
    bands = indicators.bollinger(closes, BOLLINGER_PERIOD)
    macd_values = indicators.macd(closes)
    sma_slow = indicators.sma(closes, SLOW_SMA)

    return TechnicalSnapshot(
        ticker=ctx.ticker,
        price=price,
        currency=ctx.quote.currency,
        sma_fast=indicators.sma(closes, FAST_SMA),
        sma_slow=sma_slow,
        vs_sma_slow_pct=indicators.distance_pct(price, sma_slow),
        rsi=indicators.rsi(closes),
        macd_histogram=macd_values[2] if macd_values else None,
        macd_state=indicators.macd_cross(closes),
        percent_b=bands[3] if bands else None,
        volatility_pct=indicators.volatility_pct(closes, VOLATILITY_WINDOW),
        atr=average_true_range,
        atr_pct=None if average_true_range is None or not price else average_true_range / price * 100.0,
        high_52w=high_52w,
        low_52w=low_52w,
        from_high_pct=indicators.distance_pct(price, high_52w),
        from_low_pct=indicators.distance_pct(price, low_52w),
        max_drawdown_pct=indicators.max_drawdown_pct(closes),
        volume_ratio=indicators.volume_ratio(volumes, VOLUME_PERIOD),
        returns=indicators.returns_by_window(closes),
        relative_strength=indicators.relative_strength_by_window(closes, ctx.benchmark_closes),
        benchmark=ctx.benchmark_ticker or DEFAULT_BENCHMARK,
        sessions=len(closes),
    )


def to_payload(snapshot: TechnicalSnapshot) -> dict[str, Any]:
    """Flat machine-readable view, used by --json output and the AI data pack."""
    payload: dict[str, Any] = {
        "ticker": snapshot.ticker,
        "price": snapshot.price,
        "trend": snapshot.trend,
        "sessions": snapshot.sessions,
        "benchmark": snapshot.benchmark,
    }
    for name in (
        "sma_fast",
        "sma_slow",
        "vs_sma_slow_pct",
        "rsi",
        "macd_histogram",
        "macd_state",
        "percent_b",
        "volatility_pct",
        "atr",
        "atr_pct",
        "high_52w",
        "low_52w",
        "from_high_pct",
        "from_low_pct",
        "max_drawdown_pct",
        "volume_ratio",
    ):
        value = getattr(snapshot, name)
        payload[name] = round(value, 4) if isinstance(value, float) else value
    payload["returns"] = {
        k: (round(v, 4) if v is not None else None) for k, v in (snapshot.returns or {}).items()
    }
    payload["relative_strength"] = {
        k: (round(v, 4) if v is not None else None)
        for k, v in (snapshot.relative_strength or {}).items()
    }
    return payload
