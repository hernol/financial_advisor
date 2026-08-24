"""Derive what you actually hold from the transaction ledger.

Cost basis uses the **average cost** method: every buy folds into a single
running average, and a sale realises the difference against that average. It is
the convention most brokers show by default and the only one that survives a
split cleanly, since a split rescales quantity and average price together
without touching the total invested.

Nothing in here reads the ``positions`` table. That is deliberate: positions are
a cached rollup that a split used to overwrite, while these numbers are
recomputed from entries that never change.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from fa import models
from fa.models import Transaction
from fa.store import transactions as transactions_store
from fa.store.database import Database


@dataclass(frozen=True)
class Holding:
    """The state of one ticker after replaying its whole ledger."""

    ticker: str
    quantity: float
    average_cost: float
    cost_basis: float
    realized_pnl: float
    dividends: float
    fees: float
    cash_flow: float
    entries: int
    currency: str = "USD"

    @property
    def is_open(self) -> bool:
        # Floating point residue from splits should not resurrect a sold-out
        # position, so anything under a thousandth of a share counts as zero.
        return self.quantity > 1e-3

    def unrealized(self, price: float) -> tuple[float, float]:
        """(absolute, percentage) against the average cost."""
        if not self.is_open or not self.average_cost:
            return (0.0, 0.0)
        absolute = (price - self.average_cost) * self.quantity
        return absolute, (price - self.average_cost) / self.average_cost * 100.0

    def market_value(self, price: float) -> float:
        return self.quantity * price


def replay(ticker: str, entries: Sequence[Transaction]) -> Holding:
    """Fold the ledger into a single holding. Entries must be chronological."""
    quantity = 0.0
    invested = 0.0  # total cost of the shares still held
    realized = 0.0
    dividends = 0.0
    fees = 0.0
    cash = 0.0
    currency = "USD"

    for entry in entries:
        currency = entry.currency or currency
        fees += entry.fees
        cash += entry.cash_flow
        if entry.kind == models.BUY:
            quantity += entry.quantity or 0.0
            invested += (entry.quantity or 0.0) * (entry.price or 0.0) + entry.fees
        elif entry.kind == models.SELL:
            sold = min(entry.quantity or 0.0, quantity)
            average = invested / quantity if quantity else 0.0
            realized += sold * ((entry.price or 0.0) - average) - entry.fees
            invested -= average * sold
            quantity -= sold
        elif entry.kind == models.SPLIT and entry.ratio:
            # Same money, more shares: the average price falls out of the ratio.
            quantity *= entry.ratio
        elif entry.kind == models.DIVIDEND:
            dividends += entry.amount if entry.amount is not None else (
                (entry.quantity or 0.0) * (entry.price or 0.0)
            )

    if quantity <= 1e-3:
        quantity, invested = 0.0, 0.0

    return Holding(
        ticker=ticker.upper(),
        quantity=quantity,
        average_cost=invested / quantity if quantity else 0.0,
        cost_basis=invested,
        realized_pnl=realized,
        dividends=dividends,
        fees=fees,
        cash_flow=cash,
        entries=len(entries),
        currency=currency,
    )


def holding(conn: Database, ticker: str) -> Holding:
    return replay(ticker, transactions_store.list_transactions(conn, ticker=ticker))


def holdings(conn: Database, *, open_only: bool = True) -> list[Holding]:
    """Every ticker the ledger knows about, replayed."""
    result = [holding(conn, ticker) for ticker in transactions_store.tickers(conn)]
    if open_only:
        result = [h for h in result if h.is_open]
    return sorted(result, key=lambda h: h.ticker)
