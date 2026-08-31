"""Convert a CEDEAR trade made in pesos into dollars, using that day's prices.

The rate is never fetched. It falls out of two closes the project already
stores: what the CEDEAR was worth in pesos, and what the underlying was worth
in dollars, on the day of the trade. When either is missing the answer is a
refusal, not today's rate applied to an old purchase.
"""
from __future__ import annotations

from datetime import date

from fa.cedears import Cedear
from fa.errors import DataUnavailableError
from fa.store import history as history_store
from fa.store.database import Database


def implied_rate(cedear: Cedear, *, ars_close: float, usd_close: float) -> float:
    """Pesos per dollar, as the two listings of the same claim imply it.

    One CEDEAR is ``shares / cedears`` of a share, so its dollar value is that
    fraction of the underlying's price, and the peso price divided by it is the
    rate the market was actually charging that day. Measured across six papers
    on 2026-08-31 this agreed to 0.51%, and to 0.19% against the published CCL.
    """
    if usd_close <= 0 or ars_close <= 0:
        raise DataUnavailableError(
            "no se puede derivar el tipo de cambio de un precio en cero o negativo"
        )
    return ars_close / (cedear.shares_per_cedear * usd_close)


def _close_on(conn: Database, ticker: str, day: date) -> float | None:
    """That exact session's close, or nothing.

    Deliberately does not reach for a nearby day. A neighbouring session's rate
    is still not this trade's rate, and the difference is real: the implied rate
    moved 16.5% over the measured year.
    """
    for bar in history_store.load_bars(conn, ticker, since=day):
        if bar.day == day:
            return bar.close
    return None


def derive_cost(
    conn: Database,
    cedear: Cedear,
    *,
    trade_date: date,
    quantity: float,
    ars_price: float,
    fees: float = 0.0,
) -> tuple[float, float]:
    """``(cost in USD, rate used)`` for a purchase paid in pesos.

    Raises rather than guess. Seven of 250 sessions have no pair - BYMA traded
    and New York was shut - and a purchase older than the stored history has no
    legs at all. Filling those in with today's rate is the invented number the
    project forbids; asking the owner is saying so out loud.
    """
    ars_close = _close_on(conn, cedear.yahoo, trade_date)
    if ars_close is None:
        raise DataUnavailableError(
            f"no hay cierre de {cedear.yahoo} del {trade_date}, así que el costo en "
            f"dólares no se puede derivar. Cargalo a mano."
        )
    usd_close = _close_on(conn, cedear.underlying, trade_date)
    if usd_close is None:
        raise DataUnavailableError(
            f"no hay cierre de {cedear.underlying} del {trade_date} — puede que BYMA "
            f"haya operado y Nueva York no. El costo en dólares no se puede derivar; "
            f"cargalo a mano."
        )
    rate = implied_rate(cedear, ars_close=ars_close, usd_close=usd_close)
    return (quantity * ars_price + fees) / rate, rate
