"""Portfolio valuation against live prices."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Sequence

from fa.errors import DataUnavailableError
from fa.market import MarketService
from fa.models import Position
from fa.store import positions as positions_store


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


def build_portfolio(conn: sqlite3.Connection, market: MarketService) -> Portfolio:
    """Value every open position; a failing ticker is reported, never faked."""
    holdings: list[Holding] = []
    for position in positions_store.list_positions(conn):
        try:
            quote = market.quote(position.ticker)
        except DataUnavailableError as exc:
            holdings.append(Holding(position=position, price=None, currency=position.currency, error=str(exc)))
            continue
        holdings.append(Holding(position=position, price=quote.price, currency=quote.currency))
    return Portfolio(holdings=tuple(holdings))
