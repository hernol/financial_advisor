"""Portfolio valuation against live prices."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Sequence

from fa import cedears
from fa.cedears import Cedear
from fa.config import BASE_CURRENCY
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
    # A CEDEAR is a fraction of a share, so the quantity held is not a number of
    # shares. One for an ordinary holding, which leaves every existing
    # calculation exactly as it was.
    shares_per_unit: float = 1.0
    cedear: Cedear | None = None

    @property
    def market_value(self) -> float | None:
        """What the holding is worth, in the currency of ``price``.

        For a CEDEAR the price is the underlying's, in dollars, and the ratio
        converts the receipts into the shares they represent. No exchange rate
        takes part: both factors are a real quote and a published constant.
        """
        if self.price is None:
            return None
        return self.price * self.position.quantity * self.shares_per_unit

    @property
    def is_base_currency(self) -> bool:
        return self.currency.upper() == BASE_CURRENCY

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

    @property
    def excluded(self) -> Sequence[Holding]:
        """Holdings left out of the total, each carrying the reason why."""
        return tuple(h for h in self.holdings if h.price is None)


def build_portfolio(
    conn: Database, market: MarketService, *, record: bool = True
) -> Portfolio:
    """Value every open position; a failing ticker is reported, never faked."""
    holdings: list[Holding] = []
    for position in positions_store.list_positions(conn):
        cedear = cedears.resolve(position.ticker)
        if cedear is not None and not cedear.supported:
            # Refused rather than valued through a euro price, and reported the
            # same way a genuinely foreign listing already is.
            holdings.append(
                Holding(
                    position=position,
                    price=None,
                    currency=position.currency,
                    cedear=cedear,
                    error=f"{position.ticker}: {cedear.reason}. Queda fuera del total.",
                )
            )
            continue
        try:
            quote = market.quote(position.ticker)
        except DataUnavailableError as exc:
            holdings.append(Holding(position=position, price=None, currency=position.currency, error=str(exc)))
            continue
        if quote.currency.upper() != BASE_CURRENCY:
            # Yahoo reports the listing's own currency. Adding a euro price into
            # a dollar total would look like a number and be a lie.
            holdings.append(
                Holding(
                    position=position,
                    price=None,
                    currency=quote.currency,
                    error=(
                        f"{position.ticker} cotiza en {quote.currency} y la cartera se "
                        f"valúa en {BASE_CURRENCY}: queda fuera del total."
                    ),
                )
            )
            continue
        holdings.append(
            Holding(
                position=position,
                price=quote.price,
                currency=quote.currency,
                shares_per_unit=cedear.shares_per_cedear if cedear else 1.0,
                cedear=cedear,
            )
        )
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
