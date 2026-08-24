"""Balance-sheet metrics computed from real fundamentals."""
from __future__ import annotations

from datetime import date
from typing import Any, Mapping, Sequence

import pandas as pd

from fa import ratios
from fa.models import Fundamentals, PricePoint

# Kept for backwards compatibility; the real net debt is used whenever the
# vendor reports total debt and cash. See fa.ratios.net_debt.
NET_DEBT_RATIO = ratios.FALLBACK_NET_DEBT_RATIO

COLUMNS = [
    "Period",
    "Revenue",
    "Gross_Profit",
    "Operating_Income",
    "Net_Income",
    "Total_Assets",
    "Total_Liabilities",
    "Total_Debt",
    "Cash",
    "Net_Debt",
    "Net_Debt_Estimated",
    "Operating_Cash_Flow",
    "CapEx",
    "FCF",
    "Equity",
    "Shares_Outstanding",
    "Stock_Price",
    "Market_Cap",
    "EV",
    "FCF_Yield",
    "EV_FCF_Yield",
    "Gross_Margin",
    "Operating_Margin",
    "Net_Margin",
    "Revenue_Growth",
    "Interest_Coverage",
    "FCF_Conversion",
    "ROE",
    "Net_Debt_to_FCF",
]

SUMMARY_COLUMNS = [
    "Period",
    "Revenue",
    "FCF",
    "Net_Debt",
    "FCF_Yield",
    "EV_FCF_Yield",
]

QUALITY_COLUMNS = [
    "Period",
    "Gross_Margin",
    "Operating_Margin",
    "Net_Margin",
    "Revenue_Growth",
    "Interest_Coverage",
    "FCF_Conversion",
    "ROE",
    "Net_Debt_to_FCF",
]


def close_on_or_before(history: Sequence[PricePoint], day: date) -> float | None:
    """Last known close at or before ``day``."""
    candidates = [p.close for p in history if p.day <= day]
    return candidates[-1] if candidates else None


def build_frame(
    rows: Sequence[Mapping[str, Any]],
    history: Sequence[PricePoint],
    shares_outstanding: float | None,
    current_price: float,
) -> pd.DataFrame:
    """Turn provider rows into the derived-metrics table (values in millions)."""
    records: list[dict[str, Any]] = []
    previous_revenue: float | None = None
    for row in rows:
        period_end = row.get("period_end")
        price = close_on_or_before(history, period_end) if period_end else None
        if price is None:
            price = current_price
        ocf = row.get("operating_cash_flow")
        capex = row.get("capex")
        assets = row.get("total_assets")
        liabilities = row.get("total_liabilities")
        revenue = row.get("revenue")
        net_income = row.get("net_income")
        operating_income = row.get("operating_income")
        fcf = None if ocf is None else ocf - (capex or 0.0)
        equity = None if assets is None or liabilities is None else assets - liabilities
        market_cap = None if shares_outstanding is None else shares_outstanding * price
        debt_value, estimated = ratios.net_debt(row.get("total_debt"), row.get("cash"), liabilities)
        ev = None if market_cap is None or debt_value is None else market_cap + debt_value
        records.append(
            {
                "Period": row.get("label", ""),
                "Revenue": revenue,
                "Gross_Profit": row.get("gross_profit"),
                "Operating_Income": operating_income,
                "Net_Income": net_income,
                "Total_Assets": assets,
                "Total_Liabilities": liabilities,
                "Total_Debt": row.get("total_debt"),
                "Cash": row.get("cash"),
                "Net_Debt": debt_value,
                "Net_Debt_Estimated": estimated,
                "Operating_Cash_Flow": ocf,
                "CapEx": capex,
                "FCF": fcf,
                "Equity": equity,
                "Shares_Outstanding": shares_outstanding,
                "Stock_Price": price,
                "Market_Cap": market_cap,
                "EV": ev,
                "FCF_Yield": _yield(fcf, market_cap),
                "EV_FCF_Yield": _yield(fcf, ev),
                "Gross_Margin": ratios.margin(row.get("gross_profit"), revenue),
                "Operating_Margin": ratios.margin(operating_income, revenue),
                "Net_Margin": ratios.margin(net_income, revenue),
                "Revenue_Growth": ratios.growth_pct(revenue, previous_revenue),
                "Interest_Coverage": ratios.interest_coverage(
                    operating_income, row.get("interest_expense")
                ),
                "FCF_Conversion": ratios.fcf_conversion(fcf, net_income),
                "ROE": ratios.percent(net_income, equity),
                "Net_Debt_to_FCF": ratios.leverage(debt_value, fcf),
            }
        )
        previous_revenue = revenue if revenue is not None else previous_revenue
    frame = pd.DataFrame(records, columns=COLUMNS)
    return frame


def _yield(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or not denominator:
        return None
    return numerator / denominator * 100.0


def build_tables(
    fundamentals: Fundamentals, history: Sequence[PricePoint], current_price: float
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Annual and quarterly metric tables for a ticker."""
    shares = fundamentals.shares_outstanding
    annual = build_frame(fundamentals.annual, history, shares, current_price)
    quarterly = build_frame(fundamentals.quarterly, history, shares, current_price)
    return annual, quarterly


def to_payload(annual: pd.DataFrame, quarterly: pd.DataFrame, source: str) -> str:
    """Plain-text rendering handed to the AI analyst."""
    note = ""
    if _has_estimated_debt(annual) or _has_estimated_debt(quarterly):
        note = (
            "\nNOTE: the vendor did not report total debt and cash for some periods, so Net_Debt "
            f"there is a rough estimate ({int(NET_DEBT_RATIO * 100)}% of total liabilities) and "
            "every EV-based figure inherits that approximation.\n"
        )
    return (
        f"--- ANNUAL DATA (source: {source}, figures in millions) ---\n"
        f"{annual.to_string(index=False)}\n\n"
        f"--- QUARTERLY DATA (source: {source}, figures in millions) ---\n"
        f"{quarterly.to_string(index=False)}\n{note}"
    )


def _has_estimated_debt(frame: pd.DataFrame) -> bool:
    if "Net_Debt_Estimated" not in frame.columns:
        return False
    return bool(frame["Net_Debt_Estimated"].fillna(False).any())
