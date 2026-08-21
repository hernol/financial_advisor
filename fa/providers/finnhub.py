"""Last-resort provider: Finnhub REST API (requires FINNHUB_API_KEY)."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from fa.errors import ProviderError
from fa.models import CorporateEvent, Fundamentals, PricePoint, Quote
from fa.providers.http import get_json
from fa.providers.normalize import as_date, quarter_label, to_float, to_millions

NAME = "finnhub"
BASE_URL = "https://finnhub.io/api/v1"

# Finnhub "financials-reported" concept keys, first match wins.
CONCEPTS = {
    "total_assets": ("Assets", "TotalAssets"),
    "total_liabilities": ("Liabilities", "TotalLiabilities"),
    "operating_cash_flow": ("NetCashProvidedByUsedInOperatingActivities",),
    "capex": ("PaymentsToAcquirePropertyPlantAndEquipment", "CapitalExpenditures"),
}


class FinnhubProvider:
    """Used when both Yahoo and Alpha Vantage failed."""

    name = NAME

    def __init__(self, api_key: str | None) -> None:
        self._api_key = api_key

    def available(self) -> bool:
        return bool(self._api_key)

    def _call(self, path: str, **params: Any) -> Any:
        if not self._api_key:
            raise ProviderError("FINNHUB_API_KEY is not set")
        payload = get_json(f"{BASE_URL}/{path}", {**params, "token": self._api_key})
        if isinstance(payload, dict) and payload.get("error"):
            raise ProviderError(f"finnhub: {payload['error']}")
        return payload

    def get_quote(self, ticker: str) -> Quote:
        payload = self._call("quote", symbol=ticker.upper())
        price = to_float(payload.get("c")) if isinstance(payload, dict) else None
        if not price:
            raise ProviderError(f"finnhub returned no price for {ticker}")
        return Quote(
            ticker=ticker.upper(),
            price=price,
            currency="USD",
            as_of=datetime.now(timezone.utc),
            source=NAME,
            previous_close=to_float(payload.get("pc")),
        )

    def get_history(self, ticker: str, period: str = "2y") -> Sequence[PricePoint]:
        days = _period_to_days(period)
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days)
        payload = self._call(
            "stock/candle",
            symbol=ticker.upper(),
            resolution="D",
            **{"from": int(start.timestamp()), "to": int(end.timestamp())},
        )
        if not isinstance(payload, dict) or payload.get("s") != "ok":
            raise ProviderError(f"finnhub returned no candles for {ticker}")
        points = [
            PricePoint(day=datetime.fromtimestamp(ts, tz=timezone.utc).date(), close=float(close))
            for ts, close in zip(payload.get("t", []), payload.get("c", []))
        ]
        if not points:
            raise ProviderError(f"finnhub candles for {ticker} were empty")
        return points

    def get_fundamentals(self, ticker: str) -> Fundamentals:
        annual = self._reports(ticker, freq="annual")
        quarterly = self._reports(ticker, freq="quarterly")
        if not annual and not quarterly:
            raise ProviderError(f"finnhub returned no statements for {ticker}")
        shares = None
        try:
            metrics = self._call("stock/metric", symbol=ticker.upper(), metric="all")
            shares = to_float((metrics or {}).get("metric", {}).get("sharesOutstanding"))
        except ProviderError:
            shares = None
        return Fundamentals(
            ticker=ticker.upper(), annual=annual, quarterly=quarterly, shares_outstanding=shares, source=NAME
        )

    def _reports(self, ticker: str, *, freq: str) -> list[dict[str, Any]]:
        payload = self._call("stock/financials-reported", symbol=ticker.upper(), freq=freq)
        rows: list[dict[str, Any]] = []
        for entry in (payload or {}).get("data", [])[:6]:
            period_end = as_date(entry.get("endDate"))
            if not period_end:
                continue
            report = entry.get("report", {})
            balance = _concepts(report.get("bs", []))
            cash = _concepts(report.get("cf", []))
            capex = to_millions(_first(cash, CONCEPTS["capex"]))
            rows.append(
                {
                    "label": quarter_label(period_end) if freq == "quarterly" else str(period_end.year),
                    "period_end": period_end,
                    "total_assets": to_millions(_first(balance, CONCEPTS["total_assets"])),
                    "total_liabilities": to_millions(_first(balance, CONCEPTS["total_liabilities"])),
                    "operating_cash_flow": to_millions(_first(cash, CONCEPTS["operating_cash_flow"])),
                    "capex": abs(capex) if capex is not None else None,
                }
            )
        rows.sort(key=lambda r: r["period_end"])
        return rows

    def get_next_earnings(self, ticker: str) -> date | None:
        today = date.today()
        payload = self._call(
            "calendar/earnings",
            symbol=ticker.upper(),
            **{"from": today.isoformat(), "to": (today + timedelta(days=180)).isoformat()},
        )
        days = [
            day
            for day in (as_date(item.get("date")) for item in (payload or {}).get("earningsCalendar", []))
            if day and day >= today
        ]
        return min(days) if days else None

    def get_next_ex_dividend(self, ticker: str) -> date | None:
        today = date.today()
        try:
            payload = self._call(
                "stock/dividend",
                symbol=ticker.upper(),
                **{"from": today.isoformat(), "to": (today + timedelta(days=180)).isoformat()},
            )
        except ProviderError:
            return None
        days = [d for d in (as_date(item.get("date")) for item in payload or []) if d and d >= today]
        return min(days) if days else None

    def get_splits(self, ticker: str, since: date) -> Sequence[CorporateEvent]:
        try:
            payload = self._call(
                "stock/split",
                symbol=ticker.upper(),
                **{"from": since.isoformat(), "to": date.today().isoformat()},
            )
        except ProviderError:
            return ()
        events = []
        for item in payload or []:
            day = as_date(item.get("date"))
            ratio = to_float(item.get("toFactor")) or 0.0
            from_factor = to_float(item.get("fromFactor")) or 1.0
            if day and ratio and from_factor:
                events.append(
                    CorporateEvent(
                        ticker=ticker.upper(), kind="split", event_date=day, value=ratio / from_factor
                    )
                )
        return tuple(events)


def _concepts(items: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {str(item.get("concept")): item.get("value") for item in items if item.get("concept")}


def _first(source: Mapping[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        if source.get(key) is not None:
            return source[key]
    return None


def _period_to_days(period: str) -> int:
    text = period.strip().lower()
    for suffix, factor in (("mo", 31), ("y", 366), ("d", 1)):
        if text.endswith(suffix):
            try:
                return max(int(text[: -len(suffix)]) * factor, 7)
            except ValueError:
                break
    return 732
