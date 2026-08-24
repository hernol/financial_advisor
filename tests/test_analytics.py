"""Tests for the technical snapshot assembled from a MarketContext."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from fa.analytics import build_snapshot, to_payload
from fa.models import MarketContext, PricePoint, Quote


def series(count: int, *, start: float = 100.0, step: float = 0.5, ohlc: bool = True, volume: float | None = 1000.0):
    first = date(2023, 1, 2)
    points = []
    for i in range(count):
        close = start + step * i
        points.append(
            PricePoint(
                day=first + timedelta(days=i),
                close=close,
                high=close + 1.0 if ohlc else None,
                low=close - 1.0 if ohlc else None,
                volume=volume,
            )
        )
    return points


def context(history, benchmark=(), *, price=None, evaluated=None) -> MarketContext:
    last = price if price is not None else history[-1].close
    quote = Quote(
        ticker="TEST",
        price=last,
        currency="USD",
        as_of=datetime(2024, 6, 1, tzinfo=timezone.utc),
        source="test",
    )
    return MarketContext(
        ticker="TEST",
        quote=quote,
        history=history,
        benchmark_ticker="SPY",
        benchmark_history=benchmark,
        evaluated_at=evaluated or datetime(2024, 6, 1, tzinfo=timezone.utc),
    )


def test_snapshot_computes_the_full_set_when_data_is_complete():
    snapshot = build_snapshot(context(series(300), series(300, start=400.0, step=0.2)))
    assert snapshot.sma_slow is not None
    assert snapshot.atr is not None
    assert snapshot.volume_ratio is not None
    assert snapshot.relative_strength["12m"] is not None
    assert snapshot.missing() == ()


def test_trend_follows_the_sma200_relationship():
    rising = build_snapshot(context(series(300)))
    assert rising.trend == "alcista"
    falling = build_snapshot(context(series(300, start=250.0, step=-0.5)))
    assert falling.trend == "bajista"


def test_trend_is_unknown_when_the_history_is_too_short():
    assert build_snapshot(context(series(30))).trend == "desconocida"


def test_atr_and_volume_are_missing_when_the_provider_gave_closes_only():
    snapshot = build_snapshot(context(series(300, ohlc=False, volume=None)))
    assert snapshot.atr is None and snapshot.atr_pct is None
    assert snapshot.volume_ratio is None
    gaps = " ".join(snapshot.missing())
    assert "ATR" in gaps and "volumen" in gaps


def test_relative_strength_is_missing_without_a_benchmark():
    snapshot = build_snapshot(context(series(300)))
    assert snapshot.beats_benchmark is None
    assert any("fuerza relativa" in gap for gap in snapshot.missing())


def test_beats_benchmark_compares_the_twelve_month_window():
    winner = build_snapshot(context(series(300, step=1.0), series(300, start=400.0, step=0.1)))
    assert winner.beats_benchmark is True
    loser = build_snapshot(context(series(300, step=0.01), series(300, start=100.0, step=1.0)))
    assert loser.beats_benchmark is False


def test_volume_ratio_ignores_the_session_in_progress():
    history = series(60, volume=1000.0)
    # Today's partial bar reports a fraction of the real volume.
    partial = PricePoint(day=date(2024, 6, 1), close=history[-1].close, high=None, low=None, volume=5.0)
    snapshot = build_snapshot(context(list(history) + [partial], evaluated=datetime(2024, 6, 1, tzinfo=timezone.utc)))
    assert snapshot.volume_ratio == pytest.approx(1.0)


def test_from_high_is_negative_below_the_yearly_peak():
    snapshot = build_snapshot(context(series(300), price=50.0))
    assert snapshot.from_high_pct < 0
    assert snapshot.from_low_pct < 0


def test_payload_rounds_floats_and_keeps_none():
    snapshot = build_snapshot(context(series(300, ohlc=False, volume=None)))
    payload = to_payload(snapshot)
    assert payload["atr"] is None
    assert payload["ticker"] == "TEST"
    assert isinstance(payload["returns"], dict)
    assert payload["rsi"] == pytest.approx(round(snapshot.rsi, 4))
