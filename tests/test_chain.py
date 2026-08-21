"""The provider chain falls through failures and never invents data."""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from fa.errors import DataUnavailableError, ProviderError
from fa.models import Quote
from fa.providers.chain import ProviderChain, build_chain
from tests.conftest import make_settings


class StubProvider:
    def __init__(self, name: str, *, price: float | None = None, usable: bool = True) -> None:
        self.name = name
        self._price = price
        self._usable = usable
        self.quote_calls = 0

    def available(self) -> bool:
        return self._usable

    def get_quote(self, ticker: str) -> Quote:
        self.quote_calls += 1
        if self._price is None:
            raise ProviderError(f"{self.name} has no price")
        return Quote(
            ticker=ticker,
            price=self._price,
            currency="USD",
            as_of=datetime.now(timezone.utc),
            source=self.name,
        )

    def get_next_earnings(self, ticker: str):
        raise ProviderError("no calendar")

    def get_splits(self, ticker: str, since: date):
        raise ProviderError("no splits")


def test_first_healthy_provider_wins():
    primary = StubProvider("yahoo", price=100.0)
    backup = StubProvider("alphavantage", price=200.0)
    quote = ProviderChain([primary, backup]).get_quote("PODD")
    assert quote.price == 100.0 and backup.quote_calls == 0


def test_failure_falls_through_to_the_backup():
    primary = StubProvider("yahoo")
    backup = StubProvider("alphavantage", price=200.0)
    quote = ProviderChain([primary, backup]).get_quote("PODD")
    assert quote.source == "alphavantage"


def test_unavailable_providers_are_dropped():
    chain = ProviderChain([StubProvider("yahoo", price=1.0, usable=False), StubProvider("finnhub", price=2.0)])
    assert chain.names == ("finnhub",)


def test_total_failure_raises_instead_of_simulating():
    chain = ProviderChain([StubProvider("yahoo"), StubProvider("finnhub")])
    with pytest.raises(DataUnavailableError) as excinfo:
        chain.get_quote("PODD")
    assert "yahoo" in str(excinfo.value) and "finnhub" in str(excinfo.value)


def test_empty_chain_raises_with_a_hint():
    with pytest.raises(DataUnavailableError):
        ProviderChain([]).get_quote("PODD")


def test_optional_data_degrades_to_none():
    chain = ProviderChain([StubProvider("yahoo", price=1.0)])
    assert chain.get_next_earnings("PODD") is None
    assert chain.get_splits("PODD", date(2026, 1, 1)) == ()




def test_build_chain_prefers_yahoo_then_the_keyed_fallbacks(tmp_path):
    chain = build_chain(make_settings(tmp_path, alpha_vantage_key="k1", finnhub_key="k2"))
    assert chain.names[-2:] == ("alphavantage", "finnhub")


def test_build_chain_skips_fallbacks_without_keys(tmp_path):
    chain = build_chain(make_settings(tmp_path))
    assert "alphavantage" not in chain.names and "finnhub" not in chain.names
