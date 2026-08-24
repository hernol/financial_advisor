"""The technical view must show gaps instead of hiding them behind zeros."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from fa.analytics import build_snapshot
from fa.models import MarketContext, PricePoint, Quote
from fa.ui.technicals import render


def bars(count: int, *, ohlc: bool = True, volume=1000.0):
    first = date(2023, 1, 2)
    out = []
    for i in range(count):
        close = 100.0 + 0.5 * i
        out.append(
            PricePoint(
                day=first + timedelta(days=i),
                close=close,
                high=close + 1 if ohlc else None,
                low=close - 1 if ohlc else None,
                volume=volume,
            )
        )
    return out


def snapshot(history, benchmark=()):
    ctx = MarketContext(
        ticker="TEST",
        quote=Quote(
            ticker="TEST", price=history[-1].close, currency="USD",
            as_of=datetime(2024, 6, 3, tzinfo=timezone.utc), source="test",
        ),
        history=history,
        benchmark_ticker="SPY",
        benchmark_history=benchmark,
        evaluated_at=datetime(2024, 6, 3, tzinfo=timezone.utc),
    )
    return build_snapshot(ctx)


def test_render_prints_the_headline_indicators(capsys):
    render(snapshot(bars(300), bars(300)))
    out = capsys.readouterr().out
    assert "TÉCNICOS — TEST" in out
    assert "RSI(14)" in out
    assert "Rango 52s" in out
    assert "vs SPY" in out


def test_render_shows_na_for_indicators_it_could_not_compute(capsys):
    render(snapshot(bars(300, ohlc=False, volume=None)))
    out = capsys.readouterr().out
    assert "n/a" in out
    assert "Sin datos para" in out


def test_render_states_the_benchmark_verdict(capsys):
    flat_index = [PricePoint(day=b.day, close=100.0) for b in bars(300)]
    render(snapshot(bars(300), flat_index))
    assert "le gana a SPY" in capsys.readouterr().out


def test_render_omits_the_verdict_without_a_benchmark(capsys):
    render(snapshot(bars(300)))
    out = capsys.readouterr().out
    assert "le gana" not in out and "pierde contra" not in out
