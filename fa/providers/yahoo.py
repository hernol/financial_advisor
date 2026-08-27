"""Primary provider: Yahoo Finance through the ``yfinance`` package."""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Sequence

from fa.errors import ProviderError
from fa.models import CorporateEvent, Fundamentals, PricePoint, Quote
from fa.providers.normalize import (
    BALANCE_ALIASES,
    CASHFLOW_ALIASES,
    INCOME_ALIASES,
    as_date,
    pick,
    quarter_label,
    to_float,
    to_millions,
)

NAME = "yahoo"


class YahooProvider:
    """Free, key-less market data. Preferred source."""

    name = NAME

    def __init__(self) -> None:
        self._yf: Any = None

    def _module(self) -> Any:
        if self._yf is None:
            try:
                import yfinance  # noqa: PLC0415 - optional dependency, imported lazily
            except ImportError as exc:  # pragma: no cover - depends on environment
                raise ProviderError("yfinance is not installed") from exc
            self._yf = yfinance
        return self._yf

    def available(self) -> bool:
        try:
            self._module()
        except ProviderError:
            return False
        return True

    def _ticker(self, ticker: str) -> Any:
        return self._module().Ticker(ticker.upper())

    def get_quote(self, ticker: str) -> Quote:
        handle = self._ticker(ticker)
        price = previous = None
        currency = "USD"
        try:
            fast = handle.fast_info
            price = to_float(fast.get("last_price") if hasattr(fast, "get") else fast["last_price"])
            previous = to_float(fast.get("previous_close") if hasattr(fast, "get") else None)
            currency = (fast.get("currency") if hasattr(fast, "get") else None) or "USD"
        except Exception:  # noqa: BLE001 - vendor raises many unrelated errors
            price = None
        if price is None:
            price, previous, currency = self._quote_from_history(handle, currency)
        if price is None:
            raise ProviderError(f"yahoo returned no price for {ticker}")
        return Quote(
            ticker=ticker.upper(),
            price=price,
            currency=currency,
            as_of=datetime.now(timezone.utc),
            source=NAME,
            previous_close=previous,
        )

    def _quote_from_history(self, handle: Any, currency: str) -> tuple[float | None, float | None, str]:
        try:
            frame = handle.history(period="5d")
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(f"yahoo history failed: {exc}") from exc
        if frame is None or frame.empty:
            return None, None, currency
        closes = [to_float(v) for v in frame["Close"].tolist() if to_float(v) is not None]
        if not closes:
            return None, None, currency
        previous = closes[-2] if len(closes) > 1 else None
        return closes[-1], previous, currency

    def get_history(self, ticker: str, period: str = "2y") -> Sequence[PricePoint]:
        try:
            frame = self._ticker(ticker).history(period=period, auto_adjust=False)
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(f"yahoo history failed for {ticker}: {exc}") from exc
        if frame is None or frame.empty:
            raise ProviderError(f"yahoo returned empty history for {ticker}")
        # yfinance already downloaded the whole OHLCV bar; keeping high/low/volume
        # is what makes ATR and the volume indicators possible.
        highs = _series(frame, "High")
        lows = _series(frame, "Low")
        volumes = _series(frame, "Volume")
        points: list[PricePoint] = []
        for index, (stamp, close) in enumerate(zip(frame.index, frame["Close"].tolist())):
            day = as_date(stamp)
            value = to_float(close)
            if day and value is not None:
                points.append(
                    PricePoint(
                        day=day,
                        close=value,
                        high=highs[index] if index < len(highs) else None,
                        low=lows[index] if index < len(lows) else None,
                        volume=volumes[index] if index < len(volumes) else None,
                    )
                )
        if not points:
            raise ProviderError(f"yahoo history for {ticker} had no usable closes")
        return points

    def get_fundamentals(self, ticker: str) -> Fundamentals:
        handle = self._ticker(ticker)
        annual = self._statement_rows(handle, quarterly=False)
        quarterly = self._statement_rows(handle, quarterly=True)
        if not annual and not quarterly:
            raise ProviderError(f"yahoo returned no financial statements for {ticker}")
        return Fundamentals(
            ticker=ticker.upper(),
            annual=annual,
            quarterly=quarterly,
            shares_outstanding=self._shares(handle),
            source=NAME,
            currency=self._statement_currency(handle),
        )

    def _statement_rows(self, handle: Any, *, quarterly: bool) -> list[dict[str, Any]]:
        balance = _frame(handle, "quarterly_balance_sheet" if quarterly else "balance_sheet")
        cashflow = _frame(handle, "quarterly_cashflow" if quarterly else "cashflow")
        income = _frame(handle, "quarterly_income_stmt" if quarterly else "income_stmt")
        frames = (balance, cashflow, income)
        if all(f is None for f in frames):
            return []
        periods = sorted({as_date(c) for f in frames if f is not None for c in f.columns} - {None})
        rows: list[dict[str, Any]] = []
        for period_end in periods:
            balance_col = _column(balance, period_end)
            cash_col = _column(cashflow, period_end)
            income_col = _column(income, period_end)
            capex = to_millions(pick(cash_col, CASHFLOW_ALIASES["capex"]))
            row = {
                "label": quarter_label(period_end) if quarterly else str(period_end.year),
                "period_end": period_end,
                "total_assets": to_millions(pick(balance_col, BALANCE_ALIASES["total_assets"])),
                "total_liabilities": to_millions(pick(balance_col, BALANCE_ALIASES["total_liabilities"])),
                "operating_cash_flow": to_millions(
                    pick(cash_col, CASHFLOW_ALIASES["operating_cash_flow"])
                ),
                # Vendors report CapEx as a negative outflow; the model wants it positive.
                "capex": abs(capex) if capex is not None else None,
            }
            for key in ("total_debt", "cash"):
                row[key] = to_millions(pick(balance_col, BALANCE_ALIASES[key]))
            for key, aliases in INCOME_ALIASES.items():
                row[key] = to_millions(pick(income_col, aliases))
            rows.append(row)
        return rows[-6:]

    def _statement_currency(self, handle: Any) -> str:
        """What the financial statements are denominated in.

        Distinct from the quote currency. Yahoo reports both and they differ for
        every foreign company with a US listing; without this the price and the
        statements get divided by each other as if they were the same money.
        """
        try:
            value = handle.info.get("financialCurrency")
        except Exception:  # noqa: BLE001 - a missing blob means unknown, not broken
            return ""
        return str(value).upper() if value else ""

    def _shares(self, handle: Any) -> float | None:
        for attribute in ("fast_info", "info"):
            try:
                blob = getattr(handle, attribute)
                value = blob.get("shares") if attribute == "fast_info" else blob.get("sharesOutstanding")
                shares = to_float(value)
                if shares:
                    return shares / 1_000_000.0
            except Exception:  # noqa: BLE001
                continue
        return None

    def get_next_earnings(self, ticker: str) -> date | None:
        handle = self._ticker(ticker)
        today = date.today()
        try:
            calendar = handle.calendar
        except Exception:  # noqa: BLE001
            calendar = None
        for value in _calendar_values(calendar, "Earnings Date"):
            day = as_date(value)
            if day and day >= today:
                return day
        try:
            frame = handle.earnings_dates
        except Exception:  # noqa: BLE001
            return None
        if frame is None or getattr(frame, "empty", True):
            return None
        upcoming = sorted({d for d in (as_date(i) for i in frame.index) if d and d >= today})
        return upcoming[0] if upcoming else None

    def get_next_ex_dividend(self, ticker: str) -> date | None:
        try:
            calendar = self._ticker(ticker).calendar
        except Exception:  # noqa: BLE001
            return None
        today = date.today()
        for value in _calendar_values(calendar, "Ex-Dividend Date"):
            day = as_date(value)
            if day and day >= today:
                return day
        return None

    def get_splits(self, ticker: str, since: date) -> Sequence[CorporateEvent]:
        try:
            series = self._ticker(ticker).splits
        except Exception:  # noqa: BLE001
            return ()
        if series is None or getattr(series, "empty", True):
            return ()
        events: list[CorporateEvent] = []
        for stamp, ratio in series.items():
            day = as_date(stamp)
            value = to_float(ratio)
            if day and value and day >= since:
                events.append(
                    CorporateEvent(ticker=ticker.upper(), kind="split", event_date=day, value=value)
                )
        return tuple(events)


def _frame(handle: Any, attribute: str) -> Any:
    try:
        frame = getattr(handle, attribute)
    except Exception:  # noqa: BLE001
        return None
    if frame is None or getattr(frame, "empty", True):
        return None
    return frame


def _column(frame: Any, period_end: date) -> dict[str, Any]:
    if frame is None:
        return {}
    for column in frame.columns:
        if as_date(column) == period_end:
            series = frame[column]
            return {str(k): v for k, v in series.items()}
    return {}


def _calendar_values(calendar: Any, key: str) -> list[Any]:
    if not calendar:
        return []
    if isinstance(calendar, dict):
        value = calendar.get(key)
    else:  # older yfinance returns a DataFrame
        try:
            value = calendar.loc[key].tolist()
        except Exception:  # noqa: BLE001
            return []
    if value is None:
        return []
    return list(value) if isinstance(value, (list, tuple)) else [value]


def _series(frame: Any, column: str) -> list[float | None]:
    """Optional OHLCV column as a plain list; absent columns yield nothing."""
    if column not in getattr(frame, "columns", []):
        return []
    return [to_float(v) for v in frame[column].tolist()]
