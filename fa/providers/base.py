"""Provider protocol. Every market data source implements this interface."""
from __future__ import annotations

from datetime import date
from typing import Protocol, Sequence, runtime_checkable

from fa.models import CorporateEvent, Fundamentals, PricePoint, Quote

# Keys every provider must produce for a fundamentals row, values in millions.
FUNDAMENTAL_KEYS = (
    "label",
    "period_end",
    "total_assets",
    "total_liabilities",
    "operating_cash_flow",
    "capex",
)


@runtime_checkable
class MarketDataProvider(Protocol):
    """Read-only market data source.

    Every method raises :class:`fa.errors.ProviderError` when it cannot answer,
    so the chain can move on to the next provider. Returning simulated or
    placeholder numbers is forbidden.
    """

    name: str

    def available(self) -> bool:
        """True when the provider is usable (library installed, key present)."""

    def get_quote(self, ticker: str) -> Quote: ...

    def get_history(self, ticker: str, period: str = "2y") -> Sequence[PricePoint]: ...

    def get_fundamentals(self, ticker: str) -> Fundamentals: ...

    def get_next_earnings(self, ticker: str) -> date | None: ...

    def get_next_ex_dividend(self, ticker: str) -> date | None: ...

    def get_splits(self, ticker: str, since: date) -> Sequence[CorporateEvent]: ...
