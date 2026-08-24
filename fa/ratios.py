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
