"""Provider chain: try each source in order, never fall back to fake data."""
from __future__ import annotations

import logging
from datetime import date
from typing import Any, Callable, Sequence, TypeVar

from fa.config import Settings
from fa.errors import DataUnavailableError, ProviderError
from fa.models import CorporateEvent, Fundamentals, PricePoint, Quote
from fa.providers.alphavantage import AlphaVantageProvider
from fa.providers.finnhub import FinnhubProvider
from fa.providers.yahoo import YahooProvider

logger = logging.getLogger(__name__)

T = TypeVar("T")

NO_KEYS_HINT = (
    "No market data provider answered. yfinance is the primary source; set "
    "ALPHA_VANTAGE_API_KEY or FINNHUB_API_KEY to enable the fallbacks."
)


class ProviderChain:
    """Ordered set of providers queried until one answers.

    A failure of every provider raises :class:`DataUnavailableError`. Simulated
    values are never produced.
    """

    def __init__(self, providers: Sequence[Any]) -> None:
        self._providers = tuple(p for p in providers if p.available())

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(p.name for p in self._providers)

    def _try(self, operation: str, call: Callable[[Any], T]) -> T:
        if not self._providers:
            raise DataUnavailableError(NO_KEYS_HINT)
        failures: list[str] = []
        for provider in self._providers:
            try:
                return call(provider)
            except ProviderError as exc:
                failures.append(f"{provider.name}: {exc}")
                logger.debug("%s failed on %s: %s", provider.name, operation, exc)
            except Exception as exc:  # noqa: BLE001 - vendor libraries raise anything
                failures.append(f"{provider.name}: unexpected {type(exc).__name__}: {exc}")
                logger.debug("%s crashed on %s", provider.name, operation, exc_info=True)
        raise DataUnavailableError(f"{operation} failed on every provider -> " + " | ".join(failures))

    def _try_optional(self, operation: str, call: Callable[[Any], T], default: T) -> T:
        """Same as :meth:`_try` but a total failure degrades to ``default``.

        Used for genuinely optional data (dividend dates, splits) where the
        absence of the datum is a valid answer, not a missing price.
        """
        try:
            return self._try(operation, call)
        except DataUnavailableError:
            logger.info("optional data %s unavailable on every provider", operation)
            return default

    def get_quote(self, ticker: str) -> Quote:
        return self._try(f"quote({ticker})", lambda p: p.get_quote(ticker))

    def get_history(self, ticker: str, period: str = "2y") -> Sequence[PricePoint]:
        return self._try(f"history({ticker})", lambda p: p.get_history(ticker, period))

    def get_fundamentals(self, ticker: str) -> Fundamentals:
        return self._try(f"fundamentals({ticker})", lambda p: p.get_fundamentals(ticker))

    def get_next_earnings(self, ticker: str) -> date | None:
        return self._try_optional(f"earnings({ticker})", lambda p: p.get_next_earnings(ticker), None)

    def get_next_ex_dividend(self, ticker: str) -> date | None:
        return self._try_optional(f"dividend({ticker})", lambda p: p.get_next_ex_dividend(ticker), None)

    def get_splits(self, ticker: str, since: date) -> Sequence[CorporateEvent]:
        return self._try_optional(f"splits({ticker})", lambda p: p.get_splits(ticker, since), ())


def build_chain(settings: Settings) -> ProviderChain:
    """yfinance first, then the REST fallbacks that have an API key."""
    chain = ProviderChain(
        [
            YahooProvider(),
            AlphaVantageProvider(settings.alpha_vantage_key),
            FinnhubProvider(settings.finnhub_key),
        ]
    )
    if not chain.names:
        raise DataUnavailableError(NO_KEYS_HINT)
    return chain
