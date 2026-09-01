"""Balance-sheet metrics computed from real fundamentals."""
from __future__ import annotations

from datetime import date
from typing import Any, Mapping, Sequence

import pandas as pd

from fa import fx, ratios
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
    "Currency_Mismatch",
    "Currency_Converted",
    "FX_Rate",
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
    "EPS",
    "PE",
    "Earnings_Growth",
    "PEG",
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
    "PE",
    "Earnings_Growth",
    "PEG",
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
    *,
    statement_currency: str = "",
    quote_currency: str = "",
    fx_history: Sequence[PricePoint] = (),
) -> pd.DataFrame:
    """Turn provider rows into the derived-metrics table (values in millions).

    When the statements and the quote are in different currencies, every ratio
    that divides one by the other is left blank. TSMC reports in TWD and its ADR
    trades in USD: the arithmetic gave a P/E of 0.93 against a real ~28, and an
    FCF yield of 63% against a real ~2%. Those are not approximations, they are
    the exchange rate wearing a metric's name.

    Given ``fx_history`` they are computed instead, each period converted at the
    rate of its own close. That is a real market price rather than a guess, and
    it is not the thing rejected here before: today's rate applied to a 2023
    balance sheet invents a precision nobody has. The rate moved 15% across the
    five years measured, which is exactly why the period's own one is needed.
    Without a rate at or before a period's end that period stays blank, because
    blank says "unknown", which is true.
    """
    # Unknown on either side is not a mismatch: nothing can be concluded, and
    # blanking on a guess would hide figures that are probably fine.
    mixed = bool(
        statement_currency
        and quote_currency
        and statement_currency.upper() != quote_currency.upper()
    )
    records: list[dict[str, Any]] = []
    previous_revenue: float | None = None
    previous_net_income: float | None = None
    for row in rows:
        period_end = row.get("period_end")
        price = close_on_or_before(history, period_end) if period_end else None
        if price is None:
            price = current_price
        # Growth spans two periods that convert at two different rates, so it is
        # measured on the figures as reported. In dollars TSMC's 2024 revenue
        # growth reads about 25% against a real 33.9%, the gap being the
        # currency's own move - a true answer to a different question.
        reported_revenue = row.get("revenue")
        reported_net_income = row.get("net_income")
        rate = close_on_or_before(fx_history, period_end) if mixed and period_end else None
        converted = bool(mixed and rate and rate > 0)
        if converted:
            row = fx.row_to_usd(row, rate)
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
        # PEG is built here rather than in the frame afterwards because both of
        # its halves are period-local: the P/E uses the price as at that period
        # end, not today's.
        earnings_per_share = ratios.eps(net_income, shares_outstanding)
        pe = ratios.price_earnings(price, earnings_per_share)
        earnings_growth = ratios.growth_pct(reported_net_income, previous_net_income)
        if mixed and not converted:
            # Everything downstream of a price divided by a statement line.
            # Growth and the margins survive: both sides of those are in the
            # same money, so they were never affected.
            market_cap = ev = earnings_per_share = pe = None
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
                "Currency_Mismatch": mixed,
                "Currency_Converted": converted,
                "FX_Rate": rate if converted else None,
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
                "EPS": earnings_per_share,
                "PE": pe,
                "Earnings_Growth": earnings_growth,
                "PEG": ratios.peg(pe, earnings_growth),
                "Gross_Margin": ratios.margin(row.get("gross_profit"), revenue),
                "Operating_Margin": ratios.margin(operating_income, revenue),
                "Net_Margin": ratios.margin(net_income, revenue),
                "Revenue_Growth": ratios.growth_pct(reported_revenue, previous_revenue),
                "Interest_Coverage": ratios.interest_coverage(
                    operating_income, row.get("interest_expense")
                ),
                "FCF_Conversion": ratios.fcf_conversion(fcf, net_income),
                "ROE": ratios.percent(net_income, equity),
                "Net_Debt_to_FCF": ratios.leverage(debt_value, fcf),
            }
        )
        # Carried in the reported currency, to keep growth free of the rate.
        previous_revenue = (
            reported_revenue if reported_revenue is not None else previous_revenue
        )
        previous_net_income = (
            reported_net_income if reported_net_income is not None else previous_net_income
        )
    frame = pd.DataFrame(records, columns=COLUMNS)
    return frame


def _yield(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or not denominator:
        return None
    return numerator / denominator * 100.0


def build_tables(
    fundamentals: Fundamentals,
    history: Sequence[PricePoint],
    current_price: float,
    *,
    quote_currency: str = "",
    fx_history: Sequence[PricePoint] = (),
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Annual and quarterly metric tables for a ticker."""
    shares = fundamentals.shares_outstanding
    kwargs = {
        "statement_currency": fundamentals.currency,
        "quote_currency": quote_currency,
        "fx_history": fx_history,
    }
    annual = build_frame(fundamentals.annual, history, shares, current_price, **kwargs)
    quarterly = build_frame(fundamentals.quarterly, history, shares, current_price, **kwargs)
    return annual, quarterly


def to_payload(annual: pd.DataFrame, quarterly: pd.DataFrame, source: str) -> str:
    """Plain-text rendering handed to the AI analyst."""
    note = ""
    if _has_currency_conversion(annual) or _has_currency_conversion(quarterly):
        # Saying "blank" here once the figures exist would have the analyst
        # discount numbers that are present and right.
        note += (
            "\nNOTE: the statements are reported in a different currency than the share "
            "trades in. Every money figure has been converted to the quote's currency at "
            "the exchange rate of each period's own close (see FX_Rate), so the "
            "price-derived ratios are computed and comparable. Two caveats: the closing "
            "rate is used for the income statement as well as the balance sheet, where "
            "strictly an average rate belongs; and Revenue_Growth and Earnings_Growth are "
            "measured on the figures as reported, so they carry no currency movement.\n"
        )
    elif _has_currency_mismatch(annual) or _has_currency_mismatch(quarterly):
        note += (
            "\nNOTE: the statements are reported in a different currency than the share "
            "trades in, and no exchange rate was available, so every price-derived ratio "
            "(P/E, PEG, EPS, FCF yield, EV) is blank rather than wrong. Do not compare the "
            "absolute figures to the share price.\n"
        )
    if _has_estimated_debt(annual) or _has_estimated_debt(quarterly):
        # Appended, not assigned: both caveats can apply to the same ticker and
        # each explains a different set of numbers.
        note += (
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


def _has_currency_mismatch(frame: pd.DataFrame) -> bool:
    return "Currency_Mismatch" in frame.columns and bool(frame["Currency_Mismatch"].any())


def _has_currency_conversion(frame: pd.DataFrame) -> bool:
    return "Currency_Converted" in frame.columns and bool(frame["Currency_Converted"].any())


def _has_estimated_debt(frame: pd.DataFrame) -> bool:
    if "Net_Debt_Estimated" not in frame.columns:
        return False
    return bool(frame["Net_Debt_Estimated"].fillna(False).any())
