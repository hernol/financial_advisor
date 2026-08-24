"""The data pack must carry the indicators and admit what it could not compute."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pandas as pd

from fa.ai_context import build_data_pack
from fa.metrics import COLUMNS
from fa.models import MarketContext, PricePoint, Quote


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


def context(history, benchmark=()) -> MarketContext:
    return MarketContext(
        ticker="TEST",
        quote=Quote(
            ticker="TEST",
            price=history[-1].close,
            currency="USD",
            as_of=datetime(2024, 6, 3, tzinfo=timezone.utc),
            source="test",
        ),
        history=history,
        benchmark_ticker="SPY",
        benchmark_history=benchmark,
        evaluated_at=datetime(2024, 6, 3, tzinfo=timezone.utc),
    )


def empty_frame() -> pd.DataFrame:
    return pd.DataFrame([], columns=COLUMNS)


def build(history, benchmark=()):
    return build_data_pack(
        context(history, benchmark), empty_frame(), empty_frame(), "test", [], []
    )


def test_pack_includes_a_technicals_section():
    pack = build(bars(300), bars(300))
    assert "## TECHNICALS" in pack.text
    assert "RSI(14)" in pack.text
    assert "SMA200" in pack.text


def test_pack_reports_the_relative_strength_against_the_benchmark():
    pack = build(bars(300), bars(300))
    assert "Fuerza relativa vs SPY" in pack.text
    assert "SPY." in pack.text  # the explicit verdict line


def test_pack_lists_uncomputable_indicators_as_missing():
    pack = build(bars(300, ohlc=False, volume=None))
    assert "ATR" in " ".join(pack.missing)
    assert "fuerza relativa" in " ".join(pack.missing)
    assert "MISSING DATA" in pack.text


def test_missing_values_render_as_na_never_as_zero():
    pack = build(bars(300, ohlc=False, volume=None))
    technicals = pack.text.split("## TECHNICALS")[1].split("##")[0]
    assert "n/a" in technicals
    assert "ATR(14): n/a" in technicals
