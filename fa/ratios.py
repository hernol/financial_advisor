"""Pure financial ratios.

Each helper returns ``None`` when an input is missing so a gap in the vendor
data never turns into a fabricated ratio.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

# Used only when the vendor does not report debt and cash separately. It is a
# crude stand-in, so anything derived from it is flagged as estimated.
FALLBACK_NET_DEBT_RATIO = 0.4


def safe_div(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or not denominator:
        return None
    return numerator / denominator


def percent(numerator: float | None, denominator: float | None) -> float | None:
    ratio = safe_div(numerator, denominator)
    return None if ratio is None else ratio * 100.0


def net_debt(
    total_debt: float | None, cash: float | None, total_liabilities: float | None
) -> tuple[float | None, bool]:
    """Return (net debt, estimated?).

    Real net debt is total debt minus cash. When the vendor did not report
    those lines we fall back to a fraction of total liabilities and say so, so
    the caller can label the derived EV as approximate.
    """
    if total_debt is not None:
        return (total_debt - (cash or 0.0), False)
    if total_liabilities is not None:
        return (total_liabilities * FALLBACK_NET_DEBT_RATIO, True)
    return (None, False)


def margin(line: float | None, revenue: float | None) -> float | None:
    """Any income-statement line as a percentage of revenue."""
    return percent(line, revenue)


def growth_pct(current: float | None, previous: float | None) -> float | None:
    """Period-over-period growth; undefined when the base is zero or negative."""
    if current is None or previous is None or previous <= 0:
        return None
    return (current - previous) / previous * 100.0


def eps(net_income: float | None, shares: float | None) -> float | None:
    """Earnings per share. Negative when the company lost money, which is real."""
    if net_income is None or not shares:
        return None
    return net_income / shares


def price_earnings(price: float | None, earnings_per_share: float | None) -> float | None:
    """P/E, only where it means something.

    A company that lost money has no P/E: the arithmetic yields a negative
    number that sorts as "cheap" next to real ones, which is worse than a blank.
    """
    if price is None or earnings_per_share is None or earnings_per_share <= 0:
        return None
    return price / earnings_per_share


def peg(price_earnings_ratio: float | None, growth: float | None) -> float | None:
    """P/E divided by the earnings growth rate, in percentage points.

    Undefined unless growth is positive. Dividing by a contraction produces a
    negative PEG, and a negative PEG reads as the cheapest thing on the screen
    while describing a company whose earnings are shrinking — the exact opposite
    of what the number is for. Blank is the honest answer.

    Note this is a *trailing* PEG: it divides by growth that already happened,
    because that is what the statements report. The textbook PEG uses forecast
    growth, which needs analyst estimates no provider here supplies. The two are
    not interchangeable and the UI has to say which one it is showing.
    """
    if price_earnings_ratio is None or growth is None or growth <= 0:
        return None
    return price_earnings_ratio / growth


def interest_coverage(operating_income: float | None, interest_expense: float | None) -> float | None:
    """Times the operating profit covers the interest bill."""
    if operating_income is None or not interest_expense:
        return None
    return operating_income / abs(interest_expense)


def fcf_conversion(fcf: float | None, net_income: float | None) -> float | None:
    """FCF as a percentage of reported earnings.

    Well below 100% over several years suggests the profit is not turning into
    cash; well above can mean heavy depreciation or shrinking working capital.
    """
    if fcf is None or not net_income or net_income <= 0:
        return None
    return fcf / net_income * 100.0


def leverage(net_debt_value: float | None, fcf: float | None) -> float | None:
    """Years of free cash flow needed to repay the net debt."""
    if net_debt_value is None or not fcf or fcf <= 0:
        return None
    return net_debt_value / fcf


def share_count_change_pct(rows: Sequence[Mapping[str, Any]], key: str = "shares_outstanding") -> float | None:
    """Change in share count between the oldest and newest period, in %.

    Negative means buybacks, positive means dilution.
    """
    values = [row.get(key) for row in rows]
    present = [v for v in values if v]
    if len(present) < 2:
        return None
    return growth_pct(present[-1], present[0])
