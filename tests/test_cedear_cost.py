"""Converting a peso trade uses that day's own prices, or refuses.

Both legs are real closes from the trade date. Falling back to today's rate for
a purchase made months ago is exactly the invented number the project forbids.
"""
from __future__ import annotations

from datetime import date

import pytest

from fa.cedear_cost import derive_cost, implied_rate
from fa.cedears import Cedear
from fa.errors import DataUnavailableError
from fa.models import PricePoint
from fa.store import history as history_store

AAPL = Cedear(local="AAPL", yahoo="AAPL.BA", underlying="AAPL",
              cedears=20, shares=1, name="APPLE INC")


def _bars(conn, ticker, close):
    history_store.save_bars(
        conn, ticker, [PricePoint(day=date(2026, 6, 2), close=close)], "test"
    )


def test_the_implied_rate_comes_out_of_the_two_closes():
    """25340 pesos a CEDEAR, twenty to a share, against 316.85 dollars."""
    rate = implied_rate(AAPL, ars_close=25340.0, usd_close=316.85)
    assert rate == pytest.approx(1599.5, rel=1e-3)


def test_an_inverted_ratio_does_not_flip_the_rate():
    sid = Cedear(local="SID", yahoo="SID.BA", underlying="SID",
                 cedears=1, shares=8, name="CSN")
    assert implied_rate(sid, ars_close=1600.0, usd_close=2.0) == pytest.approx(100.0)


def test_the_cost_is_the_pesos_paid_over_that_days_rate(conn):
    _bars(conn, "AAPL.BA", 25340.0)
    _bars(conn, "AAPL", 316.85)
    usd_cost, rate = derive_cost(conn, AAPL, trade_date=date(2026, 6, 2),
                                 quantity=20, ars_price=25340.0)
    assert rate == pytest.approx(1599.5, rel=1e-3)
    assert usd_cost == pytest.approx(316.85, rel=1e-3)


def test_paying_above_the_close_costs_more(conn):
    """The price paid is what counts; the close only sets the rate."""
    _bars(conn, "AAPL.BA", 25340.0)
    _bars(conn, "AAPL", 316.85)
    cheap, _ = derive_cost(conn, AAPL, trade_date=date(2026, 6, 2),
                           quantity=20, ars_price=25340.0)
    dear, _ = derive_cost(conn, AAPL, trade_date=date(2026, 6, 2),
                          quantity=20, ars_price=26000.0)
    assert dear > cheap


def test_fees_are_part_of_the_cost(conn):
    _bars(conn, "AAPL.BA", 25340.0)
    _bars(conn, "AAPL", 316.85)
    plain, rate = derive_cost(conn, AAPL, trade_date=date(2026, 6, 2),
                              quantity=20, ars_price=25340.0)
    with_fee, _ = derive_cost(conn, AAPL, trade_date=date(2026, 6, 2), quantity=20,
                              ars_price=25340.0, fees=rate)
    assert with_fee == pytest.approx(plain + 1.0, rel=1e-3)


def test_a_missing_underlying_close_refuses(conn):
    """Seven of 250 sessions have no pair: BYMA trades, New York is shut."""
    _bars(conn, "AAPL.BA", 25340.0)
    with pytest.raises(DataUnavailableError, match="a mano"):
        derive_cost(conn, AAPL, trade_date=date(2026, 6, 2), quantity=20, ars_price=25340.0)


def test_a_missing_cedear_close_refuses(conn):
    _bars(conn, "AAPL", 316.85)
    with pytest.raises(DataUnavailableError, match="a mano"):
        derive_cost(conn, AAPL, trade_date=date(2026, 6, 2), quantity=20, ars_price=25340.0)


def test_it_never_falls_back_to_another_day(conn):
    """The rate of a nearby session is still not this trade's rate."""
    history_store.save_bars(
        conn, "AAPL.BA", [PricePoint(day=date(2026, 6, 1), close=25340.0)], "test"
    )
    history_store.save_bars(
        conn, "AAPL", [PricePoint(day=date(2026, 6, 1), close=316.85)], "test"
    )
    with pytest.raises(DataUnavailableError):
        derive_cost(conn, AAPL, trade_date=date(2026, 6, 2), quantity=20, ars_price=25340.0)
