"""A CEDEAR is fetched as its underlying, and says so rather than pretending."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from fa.errors import DataUnavailableError
from fa.market import MarketService
from fa.models import Quote


class StubChain:
    names = ("stub",)

    def __init__(self):
        self.asked: list[str] = []

    def get_quote(self, ticker):
        self.asked.append(ticker)
        return Quote(ticker=ticker, price=316.85, currency="USD",
                     as_of=datetime(2026, 8, 31, tzinfo=timezone.utc), source="stub")


def test_a_cedear_is_fetched_as_its_underlying():
    chain = StubChain()
    quote = MarketService(chain).quote("AAPL.BA")
    assert chain.asked == ["AAPL"]
    assert quote.ticker == "AAPL"
    assert quote.currency == "USD"


def test_an_ordinary_ticker_is_untouched():
    chain = StubChain()
    MarketService(chain).quote("PODD")
    assert chain.asked == ["PODD"]


def test_an_unsupported_cedear_refuses_with_the_reason(monkeypatch):
    """Refusing beats valuing it through a euro price."""
    from fa import cedears

    monkeypatch.setattr(cedears, "_TABLE", {
        "BAS.BA": cedears.Cedear(local="BAS", yahoo="BAS.BA", underlying="BAS.DE",
                                 cedears=2, shares=1, name="BASF SE",
                                 supported=False, reason="cotiza en EUR"),
    })
    with pytest.raises(DataUnavailableError, match="EUR"):
        MarketService(StubChain()).quote("BAS.BA")


def test_an_unknown_ba_ticker_is_passed_through_untouched():
    """Not every .BA symbol is a CEDEAR; Argentine shares live there too."""
    chain = StubChain()
    MarketService(chain).quote("YPFD.BA")
    assert chain.asked == ["YPFD.BA"]


def test_the_cedear_and_the_share_resolve_to_one_symbol():
    """Both spellings reach the provider as the same underlying."""
    chain = StubChain()
    service = MarketService(chain)
    service.quote("AAPL.BA")
    service.quote("AAPL")
    assert chain.asked == ["AAPL", "AAPL"]
