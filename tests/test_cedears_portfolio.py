"""A CEDEAR holding is worth the shares it represents, at the USD price.

No exchange rate takes part: the ratio is a published contractual constant and
the price is a real dollar quote. That is the entire point of the design.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from fa.models import Position, Quote
from fa.portfolio import build_portfolio
from fa.store import positions as positions_store


class StubMarket:
    """Answers with the underlying's price, the way MarketService now does."""

    def __init__(self, price: float = 316.85, currency: str = "USD") -> None:
        self._price, self._currency = price, currency

    def quote(self, ticker: str) -> Quote:
        from fa import cedears

        cedear = cedears.resolve(ticker)
        symbol = cedear.underlying if cedear else ticker
        return Quote(ticker=symbol, price=self._price, currency=self._currency,
                     as_of=datetime(2026, 8, 31, tzinfo=timezone.utc), source="stub")


def hold(conn, ticker, quantity, currency="ARS"):
    return positions_store.add_position(
        conn,
        Position(ticker=ticker, quantity=quantity, buy_price=25000.0,
                 buy_date=date(2026, 6, 2), currency=currency),
    )


def test_twenty_cedears_are_worth_one_share(conn):
    hold(conn, "AAPL.BA", 20)
    portfolio = build_portfolio(conn, StubMarket(price=316.85), record=False)
    assert portfolio.market_value == pytest.approx(316.85)


def test_an_inverted_ratio_multiplies_instead_of_dividing(conn):
    """SID is 1:8. Getting the direction wrong here is a 64x error."""
    hold(conn, "SID.BA", 10)
    portfolio = build_portfolio(conn, StubMarket(price=2.0), record=False)
    assert portfolio.market_value == pytest.approx(160.0)


def test_an_ordinary_share_is_unchanged(conn):
    hold(conn, "PODD", 10, currency="USD")
    portfolio = build_portfolio(conn, StubMarket(price=100.0), record=False)
    assert portfolio.market_value == pytest.approx(1000.0)


def test_an_unsupported_cedear_is_excluded_with_its_reason(conn, monkeypatch):
    """Spec section 9: excluded, with the reason, without breaking the total."""
    from fa import cedears

    monkeypatch.setattr(cedears, "_TABLE", {
        "BAS.BA": cedears.Cedear(local="BAS", yahoo="BAS.BA", underlying="BAS.DE",
                                 cedears=2, shares=1, name="BASF SE",
                                 supported=False, reason="cotiza en EUR"),
        "AAPL.BA": cedears.Cedear(local="AAPL", yahoo="AAPL.BA", underlying="AAPL",
                                  cedears=20, shares=1, name="APPLE INC"),
    })
    hold(conn, "BAS.BA", 10)
    hold(conn, "AAPL.BA", 20)
    portfolio = build_portfolio(conn, StubMarket(price=316.85), record=False)
    assert portfolio.market_value == pytest.approx(316.85)   # only the good one
    assert len(portfolio.excluded) == 1
    assert "EUR" in portfolio.excluded[0].error


def test_the_holding_says_it_is_a_cedear(conn):
    hold(conn, "AAPL.BA", 20)
    holding = build_portfolio(conn, StubMarket(), record=False).holdings[0]
    assert holding.position.ticker == "AAPL.BA"
    assert holding.cedear.underlying == "AAPL"
    assert holding.shares_per_unit == pytest.approx(1 / 20)
