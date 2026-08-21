"""Metric table construction from provider rows."""
from __future__ import annotations

from datetime import date

import pytest

from fa.metrics import build_frame, build_tables, close_on_or_before, to_payload
from fa.models import Fundamentals
from tests.conftest import make_history

ROWS = [
    {
        "label": "2025",
        "period_end": date(2025, 12, 31),
        "total_assets": 3100.0,
        "total_liabilities": 1720.0,
        "operating_cash_flow": 440.0,
        "capex": 62.0,
    }
]


def test_close_on_or_before_picks_the_latest_prior_session():
    history = make_history([10.0, 11.0, 12.0], end=date(2026, 1, 3))
    assert close_on_or_before(history, date(2026, 1, 2)) == 11.0


def test_close_on_or_before_returns_none_when_history_starts_later():
    history = make_history([10.0], end=date(2026, 1, 3))
    assert close_on_or_before(history, date(2025, 1, 1)) is None


def test_frame_derives_fcf_equity_and_yields():
    frame = build_frame(ROWS, (), shares_outstanding=69.35, current_price=141.5)
    row = frame.iloc[0]
    assert row["FCF"] == pytest.approx(378.0)
    assert row["Equity"] == pytest.approx(1380.0)
    market_cap = 69.35 * 141.5
    assert row["Market_Cap"] == pytest.approx(market_cap)
    assert row["FCF_Yield"] == pytest.approx(378.0 / market_cap * 100)
    assert row["EV"] == pytest.approx(market_cap + 1720.0 * 0.4)


def test_missing_lines_produce_none_instead_of_invented_numbers():
    rows = [{"label": "2025", "period_end": date(2025, 12, 31), "total_assets": None,
             "total_liabilities": None, "operating_cash_flow": None, "capex": None}]
    row = build_frame(rows, (), shares_outstanding=None, current_price=100.0).iloc[0]
    assert row["FCF"] is None and row["Equity"] is None and row["FCF_Yield"] is None


def test_frame_uses_the_historic_close_of_the_period():
    history = make_history([100.0, 200.0], end=date(2025, 12, 31))
    frame = build_frame(ROWS, history, shares_outstanding=10.0, current_price=999.0)
    assert frame.iloc[0]["Stock_Price"] == pytest.approx(200.0)


def test_build_tables_returns_annual_and_quarterly():
    fundamentals = Fundamentals(ticker="PODD", annual=ROWS, quarterly=[], shares_outstanding=10.0, source="test")
    annual, quarterly = build_tables(fundamentals, (), 100.0)
    assert len(annual) == 1 and quarterly.empty


def test_payload_mentions_the_data_source():
    fundamentals = Fundamentals(ticker="PODD", annual=ROWS, quarterly=[], shares_outstanding=10.0, source="yahoo")
    annual, quarterly = build_tables(fundamentals, (), 100.0)
    assert "yahoo" in to_payload(annual, quarterly, "yahoo")
