"""Portfolio valuation against live prices."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Sequence

from fa.errors import DataUnavailableError
from fa.market import MarketService
from fa.models import Position
from fa.store import history as history_store
from fa.store import positions as positions_store
from fa.store.database import Database

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Holding:
    """A position valued at the current market price."""

    position: Position
    price: float | None
    currency: str
    error: str | None = None

    @property
    def market_value(self) -> float | None:
        return None if self.price is None else self.price * self.position.quantity

    @property
    def pnl(self) -> tuple[float, float] | None:
        return None if self.price is None else self.position.unrealized(self.price)


@dataclass(frozen=True)
class Portfolio:
    holdings: Sequence[Holding]

    @property
    def cost_basis(self) -> float:
        return sum(h.position.cost_basis for h in self.holdings)

    @property
    def market_value(self) -> float:
        return sum(h.market_value or 0.0 for h in self.holdings)

    @property
    def total_pnl(self) -> tuple[float, float]:
        cost = self.cost_basis
        absolute = self.market_value - cost
        return absolute, (absolute / cost * 100.0 if cost else 0.0)

    @property
    def priced(self) -> Sequence[Holding]:
        """Holdings that got a live price; the rest are reported, never guessed."""
        return tuple(h for h in self.holdings if h.price is not None)


def build_portfolio(
    conn: Database, market: MarketService, *, record: bool = True
) -> Portfolio:
    """Value every open position; a failing ticker is reported, never faked."""
    holdings: list[Holding] = []
    for position in positions_store.list_positions(conn):
        try:
            quote = market.quote(position.ticker)
        except DataUnavailableError as exc:
            holdings.append(Holding(position=position, price=None, currency=position.currency, error=str(exc)))
            continue
        holdings.append(Holding(position=position, price=quote.price, currency=quote.currency))
    portfolio = Portfolio(holdings=tuple(holdings))
    if record:
        save_valuation(conn, portfolio)
    return portfolio


def save_valuation(conn: Database, portfolio: Portfolio) -> int | None:
    """Add one point to the equity curve.

    A valuation with no priced holding says nothing, so it is not written: the
    curve should have gaps where the data failed rather than a false zero.
    """
    priced = portfolio.priced
    if not priced:
        return None
    absolute, percentage = portfolio.total_pnl
    try:
        return history_store.save_valuation(
            conn,
            cost_basis=portfolio.cost_basis,
            market_value=portfolio.market_value,
            pnl_abs=absolute,
            pnl_pct=percentage,
            positions=len(priced),
            currency=priced[0].currency,
            holdings=[
                {
                    "ticker": h.position.ticker,
                    "quantity": h.position.quantity,
                    "price": h.price,
                    "value": h.market_value,
                    "cost_basis": h.position.cost_basis,
                }
                for h in priced
            ],
        )
    except Exception:  # noqa: BLE001 - the curve must never break the valuation
        logger.exception("could not record the portfolio valuation")
        return None
