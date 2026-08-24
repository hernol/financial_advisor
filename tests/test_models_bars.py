"""Tests for the OHLCV fields and the in-progress session handling."""
from __future__ import annotations

from datetime import date, datetime, timezone

from fa.models import MarketContext, PricePoint, Quote


def context(history, evaluated=None) -> MarketContext:
    return MarketContext(
        ticker="TEST",
        quote=Quote(
            ticker="TEST",
            price=1.0,
            currency="USD",
            as_of=datetime(2024, 6, 3, tzinfo=timezone.utc),
            source="test",
        ),
        history=history,
        evaluated_at=evaluated,
    )


def test_price_point_defaults_keep_backwards_compatibility():
    point = PricePoint(day=date(2024, 1, 2), close=10.0)
    assert (point.high, point.low, point.volume) == (None, None, None)


def test_has_ohlc_detects_a_closes_only_provider():
    plain = [PricePoint(day=date(2024, 1, 2), close=10.0)]
    rich = [PricePoint(day=date(2024, 1, 2), close=10.0, high=11.0, low=9.0)]
    assert context(plain).has_ohlc is False
    assert context(rich).has_ohlc is True


def test_completed_bars_drops_the_session_in_progress():
    history = [
        PricePoint(day=date(2024, 6, 1), close=10.0, volume=1000.0),
        PricePoint(day=date(2024, 6, 3), close=11.0, volume=5.0),
    ]
    ctx = context(history, evaluated=datetime(2024, 6, 3, tzinfo=timezone.utc))
    assert len(ctx.completed_bars()) == 1
    assert ctx.completed_volumes() == [1000.0]


def test_completed_bars_keeps_everything_when_the_last_bar_is_old():
    history = [PricePoint(day=date(2024, 6, 1), close=10.0, volume=1000.0)]
    ctx = context(history, evaluated=datetime(2024, 6, 3, tzinfo=timezone.utc))
    assert len(ctx.completed_bars()) == 1


def test_benchmark_helpers_expose_the_index_series():
    ctx = MarketContext(
        ticker="TEST",
        quote=Quote(
            ticker="TEST", price=1.0, currency="USD",
            as_of=datetime(2024, 6, 3, tzinfo=timezone.utc), source="test",
        ),
        benchmark_ticker="SPY",
        benchmark_history=[PricePoint(day=date(2024, 1, 2), close=400.0)],
    )
    assert ctx.benchmark_closes == [400.0]
