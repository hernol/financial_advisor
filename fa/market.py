"""Facade that assembles the market context used by rules and reports."""
from __future__ import annotations

import logging
import sqlite3
from datetime import date, datetime, timezone
from typing import Sequence

import pandas as pd

from fa.config import ANALYSIS_HISTORY_PERIOD, DEFAULT_BENCHMARK, DEFAULT_HISTORY_PERIOD
from fa.errors import DataUnavailableError
from fa.metrics import build_tables
from fa.models import Fundamentals, MarketContext, PricePoint, Quote
from fa.providers.chain import ProviderChain
from fa.store import events as events_store

logger = logging.getLogger(__name__)


class MarketService:
    """Fetches live data once per ticker and caches it for the current run."""

    def __init__(
        self,
        chain: ProviderChain,
        conn: sqlite3.Connection | None = None,
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

    def quote(self, ticker: str) -> Quote:
        quote = self._chain.get_quote(ticker)
        if self._conn is not None:
            events_store.save_snapshot(self._conn, quote)
        return quote

    def context(
        self, ticker: str, *, since: date | None = None, period: str = DEFAULT_HISTORY_PERIOD
    ) -> MarketContext:
        """Build (and memoise) everything the alert rules need for a ticker."""
        key = f"{ticker.upper()}@{period}"
        if key in self._contexts:
            return self._contexts[key]
        symbol = ticker.upper()
        quote = self.quote(symbol)
        try:
            history = self._chain.get_history(symbol, period)
        except DataUnavailableError:
            history = ()
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

    def fundamentals(self, ticker: str) -> Fundamentals:
        return self._chain.get_fundamentals(ticker)

    def analysis_tables(
        self, ticker: str, *, since: date | None = None
    ) -> tuple[pd.DataFrame, pd.DataFrame, Fundamentals, MarketContext]:
        """Annual + quarterly metric tables plus the context they were built from.

        A five year history is used so each reporting period can be priced with
        its own historic close instead of today's price.
        """
        context = self.context(ticker, since=since, period=ANALYSIS_HISTORY_PERIOD)
        fundamentals = self._chain.get_fundamentals(ticker)
        annual, quarterly = build_tables(fundamentals, context.history, context.quote.price)
        return annual, quarterly, fundamentals, context
