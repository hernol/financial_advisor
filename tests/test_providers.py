"""Provider payload parsing, exercised with canned vendor responses."""
from __future__ import annotations

import time
from datetime import date, datetime

import pandas as pd
import pytest

from fa.errors import ProviderError
from fa.providers.alphavantage import AlphaVantageProvider, _merge, _period_to_sessions
from fa.providers.finnhub import FinnhubProvider, _concepts, _first, _period_to_days
from fa.providers.normalize import as_date, pick, quarter_label, to_float, to_millions
from fa.providers.yahoo import YahooProvider

# --- normalisation ----------------------------------------------------------


def test_to_millions_scales_absolute_amounts():
    assert to_millions(2_600_000_000) == pytest.approx(2600.0)


def test_to_float_rejects_nan_and_placeholders():
    assert to_float(float("nan")) is None
    assert to_float("None") is None
    assert to_float("") is None


def test_pick_returns_the_first_present_alias():
    assert pick({"Total Liab": 5}, ("Total Liabilities", "Total Liab")) == 5


def test_as_date_accepts_datetimes_strings_and_timestamps():
    assert as_date(datetime(2026, 3, 1, 10, 0)) == date(2026, 3, 1)
    assert as_date("2026-03-01T00:00:00") == date(2026, 3, 1)
    assert as_date("no es fecha") is None


def test_quarter_label_matches_the_calendar_quarter():
    assert quarter_label(date(2026, 6, 30)) == "26-Q2"


# --- Alpha Vantage ----------------------------------------------------------


def _refuses(*args, **kwargs):
    """Stand-in for a provider that rejects the key on every endpoint."""
    raise ProviderError("HTTP request failed: HTTP Error 401: Unauthorized")



BALANCE = [{"fiscalDateEnding": "2025-12-31", "totalAssets": "3100000000", "totalLiabilities": "1720000000"}]
CASH = [{"fiscalDateEnding": "2025-12-31", "operatingCashflow": "440000000", "capitalExpenditures": "-62000000"}]


def test_merge_joins_balance_and_cashflow_by_period():
    rows = _merge(BALANCE, CASH, quarterly=False)
    assert rows[0]["label"] == "2025"
    assert rows[0]["total_assets"] == pytest.approx(3100.0)
    assert rows[0]["capex"] == pytest.approx(62.0)  # sign normalised to positive


def test_merge_skips_reports_without_a_period():
    assert _merge([{"totalAssets": "1"}], [], quarterly=False) == []


def test_period_to_sessions_understands_years_and_months():
    assert _period_to_sessions("2y") == 504
    assert _period_to_sessions("6mo") == 126
    assert _period_to_sessions("raro") == 504


def test_alphavantage_is_unavailable_without_a_key():
    assert AlphaVantageProvider(None).available() is False


def test_alphavantage_surfaces_rate_limit_notes(monkeypatch):
    provider = AlphaVantageProvider("key")
    monkeypatch.setattr("fa.providers.alphavantage.get_json", lambda *a, **k: {"Note": "rate limited"})
    with pytest.raises(ProviderError, match="rate limited"):
        provider.get_quote("PODD")


def test_alphavantage_spaces_requests_for_the_one_per_second_limit(monkeypatch):
    """Fundamentals needs three calls; free keys reject them fired back to back."""
    provider = AlphaVantageProvider("key")
    slept: list[float] = []
    monkeypatch.setattr(time, "sleep", slept.append)
    monkeypatch.setattr(
        "fa.providers.alphavantage.get_json", lambda *a, **k: {"annualReports": BALANCE}
    )

    provider.get_fundamentals("PODD")

    # BALANCE_SHEET, CASH_FLOW and OVERVIEW: the first goes straight out, the
    # other two wait.
    assert len(slept) == 2
    assert all(pause >= 1.0 for pause in slept)


def test_alphavantage_reports_a_failed_earnings_lookup(monkeypatch):
    provider = AlphaVantageProvider("key")
    monkeypatch.setattr("fa.providers.alphavantage.get_json", _refuses)
    with pytest.raises(ProviderError):
        provider.get_next_earnings("PODD")


def test_alphavantage_reports_a_failed_dividend_lookup(monkeypatch):
    """A rejected key must not read as \"this ticker pays no dividend\"."""
    provider = AlphaVantageProvider("key")
    monkeypatch.setattr("fa.providers.alphavantage.get_json", _refuses)
    with pytest.raises(ProviderError):
        provider.get_next_ex_dividend("PODD")


def test_alphavantage_reports_a_failed_splits_lookup(monkeypatch):
    provider = AlphaVantageProvider("key")
    monkeypatch.setattr("fa.providers.alphavantage.get_json", _refuses)
    with pytest.raises(ProviderError):
        provider.get_splits("PODD", date(2026, 1, 1))


def test_alphavantage_parses_a_global_quote(monkeypatch):
    provider = AlphaVantageProvider("key")
    payload = {"Global Quote": {"05. price": "141.50", "08. previous close": "145.00"}}
    monkeypatch.setattr("fa.providers.alphavantage.get_json", lambda *a, **k: payload)
    quote = provider.get_quote("podd")
    assert quote.price == 141.5 and quote.ticker == "PODD" and quote.source == "alphavantage"


def test_alphavantage_rejects_an_empty_quote(monkeypatch):
    provider = AlphaVantageProvider("key")
    monkeypatch.setattr("fa.providers.alphavantage.get_json", lambda *a, **k: {"Global Quote": {}})
    with pytest.raises(ProviderError):
        provider.get_quote("PODD")


# --- Finnhub ----------------------------------------------------------------


def test_concepts_are_flattened_by_name():
    assert _concepts([{"concept": "Assets", "value": 10}]) == {"Assets": 10}


def test_first_returns_the_first_known_concept():
    assert _first({"CapitalExpenditures": 3}, ("PaymentsToAcquire", "CapitalExpenditures")) == 3


def test_period_to_days_converts_the_window():
    assert _period_to_days("1y") == 366
    assert _period_to_days("3mo") == 93


def test_finnhub_needs_a_key():
    assert FinnhubProvider(None).available() is False


def test_finnhub_reports_a_failed_dividend_lookup(monkeypatch):
    """A rejected key must not read as \"this ticker pays no dividend\"."""
    provider = FinnhubProvider("key")
    monkeypatch.setattr("fa.providers.finnhub.get_json", _refuses)
    with pytest.raises(ProviderError):
        provider.get_next_ex_dividend("PODD")


def test_finnhub_reports_a_failed_splits_lookup(monkeypatch):
    provider = FinnhubProvider("key")
    monkeypatch.setattr("fa.providers.finnhub.get_json", _refuses)
    with pytest.raises(ProviderError):
        provider.get_splits("PODD", date(2026, 1, 1))


def test_finnhub_parses_a_quote(monkeypatch):
    provider = FinnhubProvider("key")
    monkeypatch.setattr("fa.providers.finnhub.get_json", lambda *a, **k: {"c": 141.5, "pc": 145.0})
    quote = provider.get_quote("PODD")
    assert quote.price == 141.5 and quote.previous_close == 145.0


def test_finnhub_rejects_a_zero_price(monkeypatch):
    provider = FinnhubProvider("key")
    monkeypatch.setattr("fa.providers.finnhub.get_json", lambda *a, **k: {"c": 0})
    with pytest.raises(ProviderError):
        provider.get_quote("PODD")


# --- Yahoo ------------------------------------------------------------------


class FakeTicker:
    def __init__(self) -> None:
        index = pd.to_datetime(["2026-08-18", "2026-08-19", "2026-08-20"])
        self._history = pd.DataFrame({"Close": [150.0, 145.0, 141.5]}, index=index)
        columns = pd.to_datetime(["2025-12-31"])
        self.balance_sheet = pd.DataFrame({columns[0]: {"Total Assets": 3.1e9, "Total Liab": 1.72e9}})
        self.cashflow = pd.DataFrame(
            {columns[0]: {"Operating Cash Flow": 4.4e8, "Capital Expenditure": -6.2e7}}
        )
        self.quarterly_balance_sheet = self.balance_sheet
        self.quarterly_cashflow = self.cashflow
        self.fast_info = {"last_price": 141.5, "previous_close": 145.0, "currency": "USD", "shares": 69_350_000}
        self.calendar = {"Earnings Date": [date(2026, 9, 3)], "Ex-Dividend Date": [date(2026, 9, 10)]}
        self.splits = pd.Series([4.0], index=pd.to_datetime(["2026-05-01"]))

    def history(self, period="2y", auto_adjust=False):
        return self._history


@pytest.fixture()
def yahoo(monkeypatch) -> YahooProvider:
    provider = YahooProvider()
    monkeypatch.setattr(provider, "_ticker", lambda ticker: FakeTicker())
    monkeypatch.setattr(provider, "_module", lambda: object())
    return provider


def test_yahoo_quote_prefers_fast_info(yahoo):
    quote = yahoo.get_quote("podd")
    assert quote.price == 141.5 and quote.previous_close == 145.0 and quote.source == "yahoo"


def test_yahoo_history_is_sorted_price_points(yahoo):
    history = yahoo.get_history("PODD")
    assert history[0].day == date(2026, 8, 18) and history[-1].close == 141.5


def test_yahoo_fundamentals_normalise_to_millions(yahoo):
    fundamentals = yahoo.get_fundamentals("PODD")
    row = fundamentals.annual[0]
    assert row["total_assets"] == pytest.approx(3100.0)
    assert row["total_liabilities"] == pytest.approx(1720.0)
    assert row["capex"] == pytest.approx(62.0)
    assert fundamentals.shares_outstanding == pytest.approx(69.35)


def test_yahoo_reads_the_next_earnings_date(yahoo, monkeypatch):
    monkeypatch.setattr("fa.providers.yahoo.date", _FrozenDate)
    assert yahoo.get_next_earnings("PODD") == date(2026, 9, 3)


def test_yahoo_reads_the_next_ex_dividend(yahoo, monkeypatch):
    monkeypatch.setattr("fa.providers.yahoo.date", _FrozenDate)
    assert yahoo.get_next_ex_dividend("PODD") == date(2026, 9, 10)


def test_yahoo_returns_splits_after_the_purchase(yahoo):
    events = yahoo.get_splits("PODD", date(2026, 1, 1))
    assert len(events) == 1 and events[0].value == 4.0


def test_yahoo_ignores_splits_before_the_window(yahoo):
    assert yahoo.get_splits("PODD", date(2026, 7, 1)) == ()


class _FrozenDate(date):
    """date subclass with a fixed ``today`` so calendar tests stay stable."""

    @classmethod
    def today(cls) -> date:
        return date(2026, 8, 20)
