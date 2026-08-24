"""The providers must carry high/low/volume, not just closes."""
from __future__ import annotations

import pytest

from fa.providers.alphavantage import AlphaVantageProvider
from fa.providers.finnhub import FinnhubProvider
from fa.providers.normalize import BALANCE_ALIASES, INCOME_ALIASES, pick


def test_alphavantage_history_carries_the_full_bar(monkeypatch):
    payload = {
        "Time Series (Daily)": {
            "2024-06-03": {"2. high": "12.5", "3. low": "9.5", "4. close": "10.0", "5. volume": "1500"},
        }
    }
    monkeypatch.setattr("fa.providers.alphavantage.get_json", lambda *a, **k: payload)
    point = AlphaVantageProvider("key").get_history("TEST", "1mo")[-1]
    assert point.close == pytest.approx(10.0)
    assert point.high == pytest.approx(12.5)
    assert point.low == pytest.approx(9.5)
    assert point.volume == pytest.approx(1500.0)


def test_alphavantage_tolerates_a_bar_without_volume(monkeypatch):
    payload = {"Time Series (Daily)": {"2024-06-03": {"4. close": "10.0"}}}
    monkeypatch.setattr("fa.providers.alphavantage.get_json", lambda *a, **k: payload)
    point = AlphaVantageProvider("key").get_history("TEST", "1mo")[-1]
    assert point.close == pytest.approx(10.0)
    assert point.volume is None


def test_finnhub_candles_carry_the_full_bar(monkeypatch):
    payload = {"s": "ok", "t": [1717372800], "c": [10.0], "h": [12.0], "l": [9.0], "v": [2000]}
    monkeypatch.setattr("fa.providers.finnhub.get_json", lambda *a, **k: payload)
    point = FinnhubProvider("key").get_history("TEST", "1mo")[-1]
    assert (point.high, point.low, point.volume) == (12.0, 9.0, 2000.0)


def test_finnhub_survives_candles_without_the_optional_arrays(monkeypatch):
    payload = {"s": "ok", "t": [1717372800], "c": [10.0]}
    monkeypatch.setattr("fa.providers.finnhub.get_json", lambda *a, **k: payload)
    point = FinnhubProvider("key").get_history("TEST", "1mo")[-1]
    assert point.close == pytest.approx(10.0)
    assert (point.high, point.low, point.volume) == (None, None, None)


def test_balance_aliases_cover_debt_and_cash():
    assert pick({"Total Debt": 5}, BALANCE_ALIASES["total_debt"]) == 5
    assert pick({"totalDebt": 7}, BALANCE_ALIASES["total_debt"]) == 7
    assert pick({"Cash And Cash Equivalents": 3}, BALANCE_ALIASES["cash"]) == 3


def test_income_aliases_cover_the_lines_the_ratios_need():
    for key in ("revenue", "gross_profit", "operating_income", "net_income", "interest_expense"):
        assert INCOME_ALIASES[key], f"{key} has no alias"
    assert pick({"Total Revenue": 100}, INCOME_ALIASES["revenue"]) == 100
    assert pick({"totalRevenue": 100}, INCOME_ALIASES["revenue"]) == 100
