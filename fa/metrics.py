"""Balance-sheet metrics computed from real fundamentals."""
from __future__ import annotations

from datetime import date
from typing import Any, Mapping, Sequence

import pandas as pd

from fa.models import Fundamentals, PricePoint

NET_DEBT_RATIO = 0.4  # proxy: net debt approximated as 40% of total liabilities

COLUMNS = [
    "Period",
    "Total_Assets",
    "Total_Liabilities",
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
]

SUMMARY_COLUMNS = ["Period", "Total_Assets", "Total_Liabilities", "FCF", "FCF_Yield", "EV_FCF_Yield"]


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
    for row in rows:
        period_end = row.get("period_end")
        price = close_on_or_before(history, period_end) if period_end else None
        if price is None:
            price = current_price
        ocf = row.get("operating_cash_flow")
        capex = row.get("capex")
        assets = row.get("total_assets")
        liabilities = row.get("total_liabilities")
        fcf = None if ocf is None else ocf - (capex or 0.0)
        equity = None if assets is None or liabilities is None else assets - liabilities
        market_cap = None if shares_outstanding is None else shares_outstanding * price
        net_debt = None if liabilities is None else liabilities * NET_DEBT_RATIO
        ev = None if market_cap is None or net_debt is None else market_cap + net_debt
        records.append(
            {
                "Period": row.get("label", ""),
                "Total_Assets": assets,
                "Total_Liabilities": liabilities,
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
            }
        )
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
    return (
        f"--- ANNUAL DATA (source: {source}, figures in millions) ---\n"
        f"{annual.to_string(index=False)}\n\n"
        f"--- QUARTERLY DATA (source: {source}, figures in millions) ---\n"
        f"{quarterly.to_string(index=False)}"
    )
