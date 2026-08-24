"""MarketService must fetch the benchmark once and degrade without it."""
from __future__ import annotations

from datetime import date, datetime, timezone

from fa.errors import DataUnavailableError
from fa.market import MarketService
from fa.models import Fundamentals, PricePoint, Quote


class FakeChain:
    """Counts history calls so benchmark caching can be asserted."""

    names = ("fake",)

    def __init__(self, *, benchmark_fails: bool = False) -> None:
        self.history_calls: list[str] = []
        self._benchmark_fails = benchmark_fails

    def get_quote(self, ticker: str) -> Quote:
        return Quote(
            ticker=ticker.upper(),
            price=10.0,
            currency="USD",
            as_of=datetime(2024, 6, 3, tzinfo=timezone.utc),
            source="fake",
        )

    def get_history(self, ticker: str, period: str = "2y"):
        self.history_calls.append(ticker)
        if ticker == "SPY" and self._benchmark_fails:
            raise DataUnavailableError("no benchmark")
        return [PricePoint(day=date(2024, 1, 2), close=100.0)]

    def get_fundamentals(self, ticker: str) -> Fundamentals:
        return Fundamentals(ticker=ticker, annual=[], quarterly=[], shares_outstanding=None, source="fake")

    def get_next_earnings(self, ticker: str):
        return None

    def get_next_ex_dividend(self, ticker: str):
        return None

    def get_splits(self, ticker: str, since: date):
        return ()


def test_context_carries_the_benchmark_series():
    chain = FakeChain()
    context = MarketService(chain, benchmark="SPY").context("TEST")
    assert context.benchmark_ticker == "SPY"
    assert context.benchmark_closes == [100.0]


def test_benchmark_history_is_fetched_once_per_period():
    chain = FakeChain()
    market = MarketService(chain, benchmark="SPY")
    market.context("AAA")
    market.context("BBB")
    assert chain.history_calls.count("SPY") == 1


def test_the_benchmark_does_not_compare_against_itself():
    chain = FakeChain()
    context = MarketService(chain, benchmark="SPY").context("SPY")
    assert context.benchmark_closes == []


def test_a_missing_benchmark_degrades_to_an_empty_series():
    chain = FakeChain(benchmark_fails=True)
    context = MarketService(chain, benchmark="SPY").context("TEST")
    assert context.benchmark_closes == []
    assert context.closes == [100.0]


def test_no_benchmark_configured_skips_the_extra_fetch():
    chain = FakeChain()
    context = MarketService(chain, benchmark="").context("TEST")
    assert context.benchmark_closes == []
    assert "SPY" not in chain.history_calls
