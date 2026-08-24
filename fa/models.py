"""Immutable domain models shared across the application."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class Quote:
    """A point-in-time price for a ticker."""

    ticker: str
    price: float
    currency: str
    as_of: datetime
    source: str
    previous_close: float | None = None

    @property
    def change_pct(self) -> float | None:
        if not self.previous_close:
            return None
        return (self.price - self.previous_close) / self.previous_close * 100.0


@dataclass(frozen=True)
class PricePoint:
    """One daily bar. Only ``close`` is guaranteed: the fallback providers do
    not always carry the full OHLCV, so range/volume indicators must degrade to
    ``None`` instead of guessing."""

    day: date
    close: float
    high: float | None = None
    low: float | None = None
    volume: float | None = None


@dataclass(frozen=True)
class Fundamentals:
    """Balance-sheet and cash-flow lines, already normalised to millions."""

    ticker: str
    annual: Sequence[Mapping[str, Any]]
    quarterly: Sequence[Mapping[str, Any]]
    shares_outstanding: float | None
    source: str


@dataclass(frozen=True)
class CorporateEvent:
    """Split or dividend event reported by a provider."""

    ticker: str
    kind: str  # "split" | "dividend"
    event_date: date
    value: float  # split ratio or dividend amount per share


@dataclass(frozen=True)
class Position:
    """A stock position the user owns (or watches with quantity 0)."""

    ticker: str
    quantity: float
    buy_price: float
    buy_date: date
    currency: str = "USD"
    notes: str = ""
    id: int | None = None
    closed_at: datetime | None = None
    created_at: datetime | None = None

    @property
    def cost_basis(self) -> float:
        return self.quantity * self.buy_price

    def unrealized(self, price: float) -> tuple[float, float]:
        """Return (absolute P&L, percentage P&L) against ``price``."""
        pct = (price - self.buy_price) / self.buy_price * 100.0 if self.buy_price else 0.0
        return (price - self.buy_price) * self.quantity, pct

    def with_id(self, position_id: int) -> "Position":
        return replace(self, id=position_id)


@dataclass(frozen=True)
class Alert:
    """A persisted alert rule."""

    ticker: str
    kind: str
    params: Mapping[str, Any] = field(default_factory=dict)
    position_id: int | None = None
    active: bool = True
    one_shot: bool = False
    cooldown_hours: int = 24
    last_fired_at: datetime | None = None
    expires_at: date | None = None
    note: str = ""
    id: int | None = None
    created_at: datetime | None = None

    def with_id(self, alert_id: int) -> "Alert":
        return replace(self, id=alert_id)


@dataclass(frozen=True)
class Signal:
    """The result of an alert rule that fired."""

    alert: Alert
    title: str
    message: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    severity: str = "info"  # info | warning | critical


@dataclass(frozen=True)
class Suggestion:
    """An alert or action proposed by the AI report, pending the user's call."""

    ticker: str
    category: str  # "alert" | "action"
    kind: str = ""
    params: Mapping[str, Any] = field(default_factory=dict)
    rationale: str = ""
    priority: str = "medium"  # low | medium | high
    status: str = "pending"
    alert_id: int | None = None
    model: str = ""
    analysis_id: int | None = None
    id: int | None = None
    created_at: datetime | None = None
    decided_at: datetime | None = None

    @property
    def headline(self) -> str:
        if self.category == "alert":
            params = ", ".join(f"{k}={v}" for k, v in self.params.items())
            return f"{self.kind}({params})"
        return self.kind or "acción sugerida"

    def with_id(self, suggestion_id: int) -> "Suggestion":
        return replace(self, id=suggestion_id)


@dataclass(frozen=True)
class AIReport:
    """The narrative report plus the machine-readable suggestions it carries."""

    ticker: str
    text: str
    suggestions: Sequence[Suggestion] = ()
    model: str = ""
    provenance: str = ""
    analysis_id: int | None = None


@dataclass(frozen=True)
class MarketContext:
    """Everything the rules need to evaluate a ticker, fetched once per run."""

    ticker: str
    quote: Quote
    history: Sequence[PricePoint] = ()
    next_earnings: date | None = None
    next_ex_dividend: date | None = None
    recent_splits: Sequence[CorporateEvent] = ()
    evaluated_at: datetime | None = None
    benchmark_ticker: str = ""
    benchmark_history: Sequence[PricePoint] = ()

    @property
    def closes(self) -> list[float]:
        return [p.close for p in self.history]

    @property
    def highs(self) -> list[float | None]:
        return [p.high for p in self.history]

    @property
    def lows(self) -> list[float | None]:
        return [p.low for p in self.history]

    @property
    def volumes(self) -> list[float | None]:
        return [p.volume for p in self.history]

    @property
    def benchmark_closes(self) -> list[float]:
        return [p.close for p in self.benchmark_history]

    def completed_bars(self, today: date | None = None) -> Sequence[PricePoint]:
        """History without the session currently in progress.

        Intraday the provider returns a partial bar for today: its volume is a
        fraction of the real one, which would make every volume comparison
        meaningless. Price rules keep using the live quote instead.
        """
        reference = today or (self.evaluated_at.date() if self.evaluated_at else date.today())
        bars = list(self.history)
        if bars and bars[-1].day >= reference:
            return bars[:-1]
        return bars

    def completed_volumes(self, today: date | None = None) -> list[float | None]:
        return [p.volume for p in self.completed_bars(today)]

    @property
    def has_ohlc(self) -> bool:
        """True when the provider supplied high/low bars, not just closes."""
        return any(p.high is not None and p.low is not None for p in self.history)

    def max_close_since(self, since: date) -> float | None:
        values = [p.close for p in self.history if p.day >= since]
        return max(values) if values else None
