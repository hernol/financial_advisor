"""Statements reported in another currency, converted at each period's own rate.

TSMC reports in TWD and its ADR trades in USD, so every ratio that divides a
price by a statement line was left blank. The rate of the period's own close
unlocks them without inventing anything: it is a real market price, fetched
like any other, and it is emphatically not today's rate applied to an old
balance sheet.
"""
from __future__ import annotations

from datetime import date

import pytest

from fa import fx
from fa.metrics import build_frame
from fa.models import PricePoint

# 31.7 TWD per USD at the 2024 close, 30.7 at the 2023 one - the real shape.
FX_BARS = (
    PricePoint(day=date(2023, 12, 29), close=30.70),
    PricePoint(day=date(2024, 12, 31), close=32.76),
)
PRICE_BARS = (
    PricePoint(day=date(2023, 12, 29), close=100.0),
    PricePoint(day=date(2024, 12, 31), close=200.0),
)
ROWS = (
    {"label": "2023", "period_end": date(2023, 12, 31),
     "revenue": 2_161_735.8, "net_income": 851_740.0,
     "total_assets": 5_532_196.6, "total_liabilities": 2_078_330.1,
     "operating_cash_flow": 1_241_967.3, "capex": 955_398.4},
    {"label": "2024", "period_end": date(2024, 12, 31),
     "revenue": 2_894_307.7, "net_income": 1_173_512.1,
     "total_assets": 6_691_765.0, "total_liabilities": 2_412_887.0,
     "operating_cash_flow": 1_826_365.0, "capex": 1_180_000.0},
)
SHARES = 5_186.47


def _frame(**kwargs):
    return build_frame(
        ROWS, PRICE_BARS, SHARES, 200.0,
        statement_currency="TWD", quote_currency="USD", **kwargs
    )


# --- the pair symbol --------------------------------------------------------


def test_the_pair_symbol_is_units_of_the_currency_per_dollar():
    assert fx.pair_symbol("TWD") == "TWD=X"
    assert fx.pair_symbol("jpy") == "JPY=X"


def test_a_currency_that_is_not_a_code_is_refused():
    """Guessing a symbol would send us fetching some unrelated instrument."""
    for junk in ("", "US DOLLAR", "U$D", "TW"):
        with pytest.raises(ValueError):
            fx.pair_symbol(junk)


def test_converting_divides_because_the_rate_is_units_per_dollar():
    """The direction is the whole risk here: inverting it is a 1000x error."""
    assert fx.to_usd(3_170.0, 31.70) == pytest.approx(100.0)


# --- what the conversion unlocks -------------------------------------------


def test_without_a_rate_the_crossing_ratios_stay_blank(conn=None):
    """Unchanged behaviour: this is what the codebase does today."""
    frame = _frame()
    assert frame["Currency_Mismatch"].all()
    assert frame["PE"].isna().all()
    assert frame["EPS"].isna().all()


def test_with_a_rate_the_pe_is_computed_in_dollars():
    frame = _frame(fx_history=FX_BARS)
    row = frame[frame["Period"] == "2024"].iloc[0]
    eps_usd = (1_173_512.1 / 32.76) / SHARES
    assert row["EPS"] == pytest.approx(eps_usd, rel=1e-6)
    assert row["PE"] == pytest.approx(200.0 / eps_usd, rel=1e-6)


def test_each_period_uses_its_own_rate_not_the_latest():
    """Today's rate on an old balance sheet is the thing this must never do."""
    frame = _frame(fx_history=FX_BARS)
    row = frame[frame["Period"] == "2023"].iloc[0]
    assert row["FX_Rate"] == pytest.approx(30.70)
    assert row["EPS"] == pytest.approx((851_740.0 / 30.70) / SHARES, rel=1e-6)


def test_the_conversion_is_flagged_so_nothing_reads_as_reported():
    frame = _frame(fx_history=FX_BARS)
    assert frame["Currency_Converted"].all()


def test_a_period_with_no_rate_before_it_stays_blank():
    """Refusing is the same path as before, not a new one."""
    frame = _frame(fx_history=(PricePoint(day=date(2024, 12, 31), close=32.76),))
    row = frame[frame["Period"] == "2023"].iloc[0]
    assert row["Currency_Converted"] is False or not row["Currency_Converted"]
    assert pd_isna(row["PE"])


def test_growth_is_measured_in_the_reported_currency():
    """The trap. Each period converts at its own rate, so growth computed on
    the converted figures would carry the currency's movement inside it.

    TWD went 30.70 -> 32.76, about 6.7%. Revenue really grew 33.9%. In dollars
    that reads about 25%, which is a true number about a different question and
    a false one about the business.
    """
    frame = _frame(fx_history=FX_BARS)
    row = frame[frame["Period"] == "2024"].iloc[0]
    reported = (2_894_307.7 / 2_161_735.8 - 1) * 100.0
    assert row["Revenue_Growth"] == pytest.approx(reported, rel=1e-6)


def test_margins_are_unaffected_by_the_conversion():
    """Both sides of a margin sit in one period, so the rate cancels."""
    plain = _frame().iloc[1]["Net_Margin"]
    converted = _frame(fx_history=FX_BARS).iloc[1]["Net_Margin"]
    assert converted == pytest.approx(plain)


def test_a_matching_currency_is_never_converted():
    frame = build_frame(ROWS, PRICE_BARS, SHARES, 200.0,
                        statement_currency="USD", quote_currency="USD",
                        fx_history=FX_BARS)
    assert not frame["Currency_Converted"].any()
    assert frame["Revenue"].iloc[1] == pytest.approx(2_894_307.7)


def pd_isna(value):
    import pandas as pd

    return pd.isna(value)


# --- what the caveats say ---------------------------------------------------


def test_the_ai_note_stops_claiming_the_ratios_are_blank():
    """Once converted they are not blank, and telling the analyst otherwise
    would have it discount numbers that are there and correct."""
    from fa.metrics import to_payload

    converted = _frame(fx_history=FX_BARS)
    payload = to_payload(converted, converted, "test")
    assert "is blank" not in payload
    assert "convert" in payload.lower()
    assert "cierre" in payload or "close" in payload.lower()


def test_the_ai_note_still_warns_when_nothing_could_be_converted():
    from fa.metrics import to_payload

    payload = to_payload(_frame(), _frame(), "test")
    assert "blank" in payload


# --- what reaches the dashboard --------------------------------------------


def test_the_api_says_the_figures_were_converted(conn):
    """A converted figure must never render as a reported one, so the flag has
    to travel all the way to the client."""
    import pandas as pd
    from fastapi.testclient import TestClient

    from fa.api import deps
    from fa.api.app import create_app
    from fa.store import fundamentals as fundamentals_store

    frame = pd.DataFrame([{
        "Period": "2024", "Currency_Mismatch": True, "Currency_Converted": True,
        "FX_Rate": 32.76, "PE": 29.0,
    }])
    fundamentals_store.save(conn, "TSM", fundamentals_store.ANNUAL, frame, source="yahoo")

    deps.set_database(conn)
    try:
        with TestClient(create_app(serve_web=False)) as client:
            body = client.get("/api/tickers/TSM/fundamentals").json()
    finally:
        deps.set_database(None)

    assert body["converted_currency"] is True
    # Not "mixed" any more: the ratios are there, so warning about blanks would
    # point at numbers that exist.
    assert body["currency_mismatch"] is False
