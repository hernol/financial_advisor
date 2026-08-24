"""Tests for the pure financial ratios."""
from __future__ import annotations

import pytest

from fa import ratios


def test_net_debt_uses_the_reported_debt_and_cash():
    value, estimated = ratios.net_debt(1000.0, 250.0, 5000.0)
    assert value == pytest.approx(750.0)
    assert estimated is False


def test_net_debt_treats_missing_cash_as_zero():
    value, estimated = ratios.net_debt(1000.0, None, None)
    assert (value, estimated) == (1000.0, False)


def test_net_debt_falls_back_to_a_flagged_estimate():
    value, estimated = ratios.net_debt(None, None, 2000.0)
    assert value == pytest.approx(2000.0 * ratios.FALLBACK_NET_DEBT_RATIO)
    assert estimated is True


def test_net_debt_gives_up_when_nothing_is_reported():
    assert ratios.net_debt(None, None, None) == (None, False)


def test_safe_div_and_percent_refuse_a_zero_denominator():
    assert ratios.safe_div(1.0, 0.0) is None
    assert ratios.percent(None, 10.0) is None
    assert ratios.percent(25.0, 200.0) == pytest.approx(12.5)


def test_growth_is_undefined_on_a_non_positive_base():
    assert ratios.growth_pct(110.0, 0.0) is None
    assert ratios.growth_pct(110.0, -10.0) is None
    assert ratios.growth_pct(110.0, 100.0) == pytest.approx(10.0)


def test_interest_coverage_uses_the_absolute_interest_bill():
    # Vendors report the expense as a negative number.
    assert ratios.interest_coverage(500.0, -100.0) == pytest.approx(5.0)
    assert ratios.interest_coverage(500.0, 0.0) is None


def test_fcf_conversion_needs_a_positive_profit():
    assert ratios.fcf_conversion(80.0, 100.0) == pytest.approx(80.0)
    assert ratios.fcf_conversion(80.0, -100.0) is None


def test_leverage_needs_positive_free_cash_flow():
    assert ratios.leverage(700.0, 100.0) == pytest.approx(7.0)
    assert ratios.leverage(700.0, -50.0) is None


def test_share_count_change_detects_buybacks_and_dilution():
    buyback = [{"shares_outstanding": 100.0}, {"shares_outstanding": 90.0}]
    assert ratios.share_count_change_pct(buyback) == pytest.approx(-10.0)
    assert ratios.share_count_change_pct([{"shares_outstanding": 100.0}]) is None
