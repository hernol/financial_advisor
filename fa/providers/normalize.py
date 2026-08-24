"""Helpers shared by providers to normalise heterogeneous vendor payloads."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Iterable, Mapping

MILLION = 1_000_000.0

# Vendor line-item aliases, first match wins.
BALANCE_ALIASES: Mapping[str, tuple[str, ...]] = {
    "total_assets": ("Total Assets", "totalAssets"),
    "total_liabilities": (
        "Total Liabilities Net Minority Interest",
        "Total Liab",
        "totalLiabilities",
    ),
    "total_debt": ("Total Debt", "totalDebt", "shortLongTermDebtTotal"),
    "cash": (
        "Cash And Cash Equivalents",
        "Cash Cash Equivalents And Short Term Investments",
        "CashAndCashEquivalentsAtCarryingValue",
        "cashAndCashEquivalentsAtCarryingValue",
    ),
}
INCOME_ALIASES: Mapping[str, tuple[str, ...]] = {
    "revenue": ("Total Revenue", "Operating Revenue", "totalRevenue", "Revenues"),
    "gross_profit": ("Gross Profit", "grossProfit"),
    "operating_income": ("Operating Income", "EBIT", "operatingIncome", "OperatingIncomeLoss"),
    "net_income": (
        "Net Income",
        "Net Income Common Stockholders",
        "netIncome",
        "NetIncomeLoss",
    ),
    "interest_expense": ("Interest Expense", "interestExpense", "InterestExpense"),
}
CASHFLOW_ALIASES: Mapping[str, tuple[str, ...]] = {
    "operating_cash_flow": (
        "Operating Cash Flow",
        "Total Cash From Operating Activities",
        "operatingCashflow",
    ),
    "capex": ("Capital Expenditure", "Capital Expenditures", "capitalExpenditures"),
}


def to_millions(value: Any) -> float | None:
    """Convert an absolute currency amount to millions."""
    number = to_float(value)
    if number is None:
        return None
    return number / MILLION


def to_float(value: Any) -> float | None:
    if value is None or value == "None" or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    return number


def pick(source: Mapping[str, Any], aliases: Iterable[str]) -> Any:
    for alias in aliases:
        if alias in source and source[alias] is not None:
            return source[alias]
    return None


def as_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value)[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def quarter_label(period_end: date) -> str:
    return f"{str(period_end.year)[2:]}-Q{(period_end.month - 1) // 3 + 1}"
