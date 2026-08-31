"""Facade that assembles the market context used by rules and reports."""
from __future__ import annotations

import logging
import time
from datetime import date, datetime, timezone
from typing import Sequence

import pandas as pd

from fa import cedears
from fa.cedears import Cedear
from fa.config import ANALYSIS_HISTORY_PERIOD, DEFAULT_BENCHMARK, DEFAULT_HISTORY_PERIOD
from fa.errors import DataUnavailableError
from fa.metrics import build_tables
from fa.models import Fundamentals, MarketContext, PricePoint, Quote
from fa.providers.chain import ProviderChain
from fa.store import events as events_store
from fa.store import history as history_store
from fa.store import runs as runs_store
from fa.store.database import Database

logger = logging.getLogger(__name__)


class MarketService:
    """Fetches live data once per ticker and caches it for the current run."""

    def __init__(
        self,
        chain: ProviderChain,
        conn: Database | None = None,
        benchmark: str = DEFAULT_BENCHMARK,
    ) -> None:
        self._chain = chain
        self._conn = conn
        self._benchmark = (benchmark or "").upper()
        self._contexts: dict[str, MarketContext] = {}
        self._benchmarks: dict[str, Sequence[PricePoint]] = {}

    @property
    def benchmark(self) -> str:
        return self._benchmark

    def benchmark_history(self, period: str) -> Sequence[PricePoint]:
        """Index history, memoised per period and shared by every ticker.

        A missing benchmark degrades to an empty series: relative strength then
        reports ``None`` rather than comparing against something invented.
        """
        if not self._benchmark:
            return ()
        if period not in self._benchmarks:
            try:
                self._benchmarks[period] = self._chain.get_history(self._benchmark, period)
            except DataUnavailableError:
                logger.warning("benchmark %s unavailable for period %s", self._benchmark, period)
                self._benchmarks[period] = ()
        return self._benchmarks[period]

    @property
    def providers(self) -> tuple[str, ...]:
        return self._chain.names

    def resolve_symbol(self, ticker: str) -> tuple[str, Cedear | None]:
        """The symbol to fetch, and the CEDEAR it stands for.

        A CEDEAR is contractually a fraction of the underlying share, so every
        question about it - price, history, statements, technicals - is really a
        question about that share, asked in dollars instead of pesos. Resolving
        here, at the one facade every consumer goes through, is what lets the
        rest of the application stay unaware that CEDEARs exist.

        The substitution is deliberate and stays visible: the Quote that comes
        back says AAPL, not AAPL.BA, and callers that care carry both.
        """
        symbol = (ticker or "").upper()
        cedear = cedears.resolve(symbol)
        if cedear is None:
            return symbol, None
        if not cedear.supported:
            raise DataUnavailableError(
                f"{symbol} es un CEDEAR sin subyacente utilizable: {cedear.reason}."
            )
        return cedear.underlying.upper(), cedear

    def quote(self, ticker: str) -> Quote:
        symbol, _ = self.resolve_symbol(ticker)
        started = time.monotonic()
        try:
            quote = self._chain.get_quote(symbol)
        except DataUnavailableError as exc:
            self._log_fetch(symbol, "quote", "", started, error=exc)
            raise
        self._log_fetch(symbol, "quote", quote.source, started)
        if self._conn is not None:
            events_store.save_snapshot(self._conn, quote)
        return quote

    def _log_fetch(
        self,
        ticker: str,
        kind: str,
        provider: str,
        started: float,
        *,
        error: Exception | None = None,
    ) -> None:
        """Record which provider answered, so a silent fallback stays visible.

        The chain tries Yahoo first and drops to Alpha Vantage or Finnhub when it
        fails. Without this the only evidence a fallback happened was a different
        ``source`` string on one quote, which nothing kept.
        """
        if self._conn is None:
            return
        try:
            runs_store.record_fetch(
                self._conn,
                ticker=ticker,
                kind=kind,
                provider=provider,
                ok=error is None,
                error=str(error) if error else "",
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        except Exception:  # noqa: BLE001 - provenance must never break a fetch
            logger.exception("could not record the %s fetch for %s", kind, ticker)

    def context(
        self, ticker: str, *, since: date | None = None, period: str = DEFAULT_HISTORY_PERIOD
    ) -> MarketContext:
        """Build (and memoise) everything the alert rules need for a ticker."""
        # Keyed on the resolved symbol, so AAPL and AAPL.BA share one fetch
        # instead of building the same context twice under two spellings.
        symbol, _ = self.resolve_symbol(ticker)
        key = f"{symbol}@{period}"
        if key in self._contexts:
            return self._contexts[key]
        quote = self.quote(symbol)
        started = time.monotonic()
        try:
            history = self._chain.get_history(symbol, period)
        except DataUnavailableError as exc:
            self._log_fetch(symbol, "history", "", started, error=exc)
            history = ()
        else:
            self._log_fetch(symbol, "history", quote.source, started)
            self._archive(symbol, history, quote.source)
        lookback = since or date.today().replace(year=date.today().year - 1)
        context = MarketContext(
            ticker=symbol,
            quote=quote,
            history=history,
            benchmark_ticker=self._benchmark,
            benchmark_history=() if symbol == self._benchmark else self.benchmark_history(period),
            next_earnings=self._chain.get_next_earnings(symbol),
            next_ex_dividend=self._chain.get_next_ex_dividend(symbol),
            recent_splits=self._chain.get_splits(symbol, lookback),
            evaluated_at=datetime.now(timezone.utc),
        )
        self._contexts[key] = context
        return context

    def _archive(self, ticker: str, history: Sequence[PricePoint], source: str) -> None:
        """Keep the bars the provider just sent, so the next run need not refetch."""
        if self._conn is None or not history:
            return
        try:
            history_store.save_bars(self._conn, ticker, history, source)
        except Exception:  # noqa: BLE001 - archiving must never break a fetch
            logger.exception("could not archive bars for %s", ticker)

    def fundamentals(self, ticker: str) -> Fundamentals:
        symbol, _ = self.resolve_symbol(ticker)
        return self._chain.get_fundamentals(symbol)

    def analysis_tables(
        self, ticker: str, *, since: date | None = None
    ) -> tuple[pd.DataFrame, pd.DataFrame, Fundamentals, MarketContext]:
        """Annual + quarterly metric tables plus the context they were built from.

        A five year history is used so each reporting period can be priced with
        its own historic close instead of today's price.
        """
        symbol, _ = self.resolve_symbol(ticker)
        context = self.context(symbol, since=since, period=ANALYSIS_HISTORY_PERIOD)
        fundamentals = self._chain.get_fundamentals(symbol)
        # The quote knows what money it is in; without handing that over, the
        # tables cannot tell a TWD statement from a USD one.
        annual, quarterly = build_tables(
            fundamentals,
            context.history,
            context.quote.price,
            quote_currency=context.quote.currency,
        )
        return annual, quarterly, fundamentals, context
