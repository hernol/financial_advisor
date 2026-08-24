"""Tests for the income-statement columns added to the metric tables."""
from __future__ import annotations

from datetime import date

import pytest

from fa.metrics import QUALITY_COLUMNS, build_frame, to_payload


def row(label: str, **overrides):
    base = {
        "label": label,
        "period_end": date(int(label), 12, 31),
        "total_assets": 1000.0,
        "total_liabilities": 600.0,
        "operating_cash_flow": 150.0,
        "capex": 50.0,
        "total_debt": 400.0,
        "cash": 100.0,
        "revenue": 900.0,
        "gross_profit": 450.0,
        "operating_income": 200.0,
        "net_income": 120.0,
        "interest_expense": -40.0,
    }
    return {**base, **overrides}


def test_net_debt_is_real_when_debt_and_cash_are_reported():
    frame = build_frame([row("2024")], [], 50.0, 60.0)
    assert frame.loc[0, "Net_Debt"] == pytest.approx(300.0)
    assert bool(frame.loc[0, "Net_Debt_Estimated"]) is False


def test_net_debt_falls_back_and_flags_the_estimate():
    frame = build_frame([row("2024", total_debt=None, cash=None)], [], 50.0, 60.0)
    assert frame.loc[0, "Net_Debt"] == pytest.approx(240.0)
    assert bool(frame.loc[0, "Net_Debt_Estimated"]) is True


def test_enterprise_value_uses_the_real_net_debt():
    frame = build_frame([row("2024")], [], 50.0, 60.0)
    # market cap = 50 shares * 60 = 3000; EV = 3000 + 300
    assert frame.loc[0, "EV"] == pytest.approx(3300.0)


def test_margins_are_percentages_of_revenue():
    frame = build_frame([row("2024")], [], 50.0, 60.0)
    assert frame.loc[0, "Gross_Margin"] == pytest.approx(50.0)
    assert frame.loc[0, "Operating_Margin"] == pytest.approx(200 / 900 * 100)
    assert frame.loc[0, "Net_Margin"] == pytest.approx(120 / 900 * 100)


def test_revenue_growth_is_blank_on_the_first_period():
    frame = build_frame([row("2024"), row("2025", revenue=990.0)], [], 50.0, 60.0)
    assert frame.loc[0, "Revenue_Growth"] != frame.loc[0, "Revenue_Growth"]  # NaN
    assert frame.loc[1, "Revenue_Growth"] == pytest.approx(10.0)


def test_revenue_growth_carries_the_last_known_base_over_a_gap():
    rows = [row("2023"), row("2024", revenue=None), row("2025", revenue=990.0)]
    frame = build_frame(rows, [], 50.0, 60.0)
    assert frame.loc[2, "Revenue_Growth"] == pytest.approx(10.0)


def test_quality_ratios_are_computed():
    frame = build_frame([row("2024")], [], 50.0, 60.0)
    assert frame.loc[0, "Interest_Coverage"] == pytest.approx(5.0)
    assert frame.loc[0, "FCF_Conversion"] == pytest.approx(100 / 120 * 100)
    assert frame.loc[0, "ROE"] == pytest.approx(120 / 400 * 100)
    assert frame.loc[0, "Net_Debt_to_FCF"] == pytest.approx(3.0)


def test_missing_income_lines_leave_the_ratios_empty_not_zero():
    bare = {
        "label": "2024",
        "period_end": date(2024, 12, 31),
        "total_assets": 1000.0,
        "total_liabilities": 600.0,
        "operating_cash_flow": 150.0,
        "capex": 50.0,
    }
    frame = build_frame([bare], [], 50.0, 60.0)
    # Net_Debt_to_FCF survives: it only needs the (estimated) net debt and FCF.
    income_driven = [
        c for c in QUALITY_COLUMNS if c not in {"Period", "Net_Debt_to_FCF"}
    ]
    for column in income_driven:
        value = frame.loc[0, column]
        # An all-empty column stays object dtype (None); a partially filled one is NaN.
        assert value is None or value != value
    assert frame.loc[0, "Net_Debt_to_FCF"] == pytest.approx(2.4)


def test_payload_warns_when_the_net_debt_was_estimated():
    estimated = build_frame([row("2024", total_debt=None, cash=None)], [], 50.0, 60.0)
    exact = build_frame([row("2024")], [], 50.0, 60.0)
    assert "rough estimate" in to_payload(estimated, exact, "test")
    assert "rough estimate" not in to_payload(exact, exact, "test")
