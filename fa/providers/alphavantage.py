"""Fallback provider: Alpha Vantage REST API (requires ALPHA_VANTAGE_API_KEY)."""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Mapping, Sequence

from fa.errors import ProviderError
from fa.models import CorporateEvent, Fundamentals, PricePoint, Quote
from fa.providers.http import get_json
from fa.providers.normalize import as_date, quarter_label, to_float, to_millions

NAME = "alphavantage"
BASE_URL = "https://www.alphavantage.co/query"


class AlphaVantageProvider:
    """Second choice when Yahoo is unreachable or rate limited."""

    name = NAME

    def __init__(self, api_key: str | None) -> None:
        self._api_key = api_key

    def available(self) -> bool:
        return bool(self._api_key)

    def _call(self, function: str, ticker: str, **extra: Any) -> Mapping[str, Any]:
        if not self._api_key:
            raise ProviderError("ALPHA_VANTAGE_API_KEY is not set")
        payload = get_json(
            BASE_URL, {"function": function, "symbol": ticker.upper(), "apikey": self._api_key, **extra}
        )
        if not isinstance(payload, dict):
            raise ProviderError("alphavantage returned an unexpected payload")
        for key in ("Note", "Information", "Error Message"):
            if key in payload:
                raise ProviderError(f"alphavantage: {payload[key]}")
        return payload

    def get_quote(self, ticker: str) -> Quote:
        payload = self._call("GLOBAL_QUOTE", ticker)
        quote = payload.get("Global Quote") or {}
        price = to_float(quote.get("05. price"))
        if price is None:
            raise ProviderError(f"alphavantage returned no price for {ticker}")
        return Quote(
            ticker=ticker.upper(),
            price=price,
            currency="USD",
            as_of=datetime.now(timezone.utc),
            source=NAME,
            previous_close=to_float(quote.get("08. previous close")),
        )

    def get_history(self, ticker: str, period: str = "2y") -> Sequence[PricePoint]:
        payload = self._call("TIME_SERIES_DAILY", ticker, outputsize="full")
        series = payload.get("Time Series (Daily)") or {}
        points: list[PricePoint] = []
        for day_text, row in series.items():
            day = as_date(day_text)
            close = to_float(row.get("4. close"))
            if day and close is not None:
                points.append(
                    PricePoint(
                        day=day,
                        close=close,
                        high=to_float(row.get("2. high")),
                        low=to_float(row.get("3. low")),
                        volume=to_float(row.get("5. volume")),
                    )
                )
        if not points:
            raise ProviderError(f"alphavantage returned no history for {ticker}")
        points.sort(key=lambda p: p.day)
        return points[-_period_to_sessions(period) :]

    def get_fundamentals(self, ticker: str) -> Fundamentals:
        balance = self._call("BALANCE_SHEET", ticker)
        cashflow = self._call("CASH_FLOW", ticker)
        annual = _merge(balance.get("annualReports", []), cashflow.get("annualReports", []), quarterly=False)
        quarterly = _merge(
            balance.get("quarterlyReports", []), cashflow.get("quarterlyReports", []), quarterly=True
        )
        if not annual and not quarterly:
            raise ProviderError(f"alphavantage returned no statements for {ticker}")
        shares = None
        try:
            overview = self._call("OVERVIEW", ticker)
            shares = to_millions(overview.get("SharesOutstanding"))
        except ProviderError:
            shares = None
        return Fundamentals(
            ticker=ticker.upper(), annual=annual, quarterly=quarterly, shares_outstanding=shares, source=NAME
        )

    def get_next_earnings(self, ticker: str) -> date | None:
        try:
            payload = self._call("EARNINGS", ticker)
        except ProviderError:
            return None
        today = date.today()
        upcoming = [
            day
            for day in (as_date(r.get("reportedDate")) for r in payload.get("quarterlyEarnings", []))
            if day and day >= today
        ]
        return min(upcoming) if upcoming else None

    def get_next_ex_dividend(self, ticker: str) -> date | None:
        try:
            overview = self._call("OVERVIEW", ticker)
        except ProviderError:
            return None
        day = as_date(overview.get("ExDividendDate"))
        return day if day and day >= date.today() else None

    def get_splits(self, ticker: str, since: date) -> Sequence[CorporateEvent]:
        try:
            payload = self._call("SPLITS", ticker)
        except ProviderError:
            return ()
        events: list[CorporateEvent] = []
        for row in payload.get("data", []) if isinstance(payload, dict) else []:
            day = as_date(row.get("effective_date"))
            ratio = to_float(row.get("split_factor"))
            if day and ratio and day >= since:
                events.append(CorporateEvent(ticker=ticker.upper(), kind="split", event_date=day, value=ratio))
        return tuple(events)


def _merge(
    balance_reports: Sequence[Mapping[str, Any]],
    cash_reports: Sequence[Mapping[str, Any]],
    *,
    quarterly: bool,
) -> list[dict[str, Any]]:
    by_date = {r.get("fiscalDateEnding"): r for r in cash_reports}
    rows: list[dict[str, Any]] = []
    for report in balance_reports:
        period_end = as_date(report.get("fiscalDateEnding"))
        if not period_end:
            continue
        cash = by_date.get(report.get("fiscalDateEnding"), {})
        capex = to_millions(cash.get("capitalExpenditures"))
        rows.append(
            {
                "label": quarter_label(period_end) if quarterly else str(period_end.year),
                "period_end": period_end,
                "total_assets": to_millions(report.get("totalAssets")),
                "total_liabilities": to_millions(report.get("totalLiabilities")),
                "operating_cash_flow": to_millions(cash.get("operatingCashflow")),
                "capex": abs(capex) if capex is not None else None,
            }
        )
    rows.sort(key=lambda r: r["period_end"])
    return rows[-6:]


def _period_to_sessions(period: str) -> int:
    """Rough trading-session count for a yfinance style period string."""
    units = {"d": 1, "mo": 21, "y": 252}
    text = period.strip().lower()
    for suffix, factor in (("mo", units["mo"]), ("y", units["y"]), ("d", units["d"])):
        if text.endswith(suffix):
            try:
                return max(int(text[: -len(suffix)]) * factor, 2)
            except ValueError:
                break
    return 504
