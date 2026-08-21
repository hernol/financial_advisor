"""Portfolio digest: gathers the facts, the local model writes the prose."""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone
from typing import Sequence

from fa.alerts import kinds
from fa.errors import DataUnavailableError
from fa.market import MarketService
from fa.models import Alert, Position
from fa.portfolio import build_portfolio
from fa.store import alerts as alerts_store
from fa.store import events as events_store
from fa.store import positions as positions_store


def _distance_to_trigger(alert: Alert, price: float, position: Position | None) -> str | None:
    """How far the current price is from firing this alert, when computable."""
    if alert.kind in {kinds.PRICE_ABOVE, kinds.PRICE_BELOW}:
        target = float(alert.params.get("price", 0) or 0)
        if not target:
            return None
        gap = (target - price) / price * 100.0
        return f"objetivo {target:,.2f} ({gap:+.2f}% desde {price:,.2f})"
    if alert.kind in {kinds.PCT_UP, kinds.PCT_DOWN}:
        reference = position.buy_price if position else alert.params.get("baseline_price")
        if not reference:
            return None
        change = (price - float(reference)) / float(reference) * 100.0
        threshold = float(alert.params.get("pct", 0) or 0)
        target = threshold if alert.kind == kinds.PCT_UP else -threshold
        return f"variación {change:+.2f}% contra umbral {target:+.2f}%"
    return None


def collect_facts(
    conn: sqlite3.Connection, market: MarketService, *, today: date | None = None
) -> str:
    """Plain-text fact sheet. Only real data; gaps are stated as such."""
    moment = today or datetime.now(timezone.utc).date()
    lines = [f"FECHA: {moment.isoformat()}", "", "POSICIONES:"]
    portfolio = build_portfolio(conn, market)
    prices: dict[str, float] = {}
    if not portfolio.holdings:
        lines.append("- (ninguna)")
    for holding in portfolio.holdings:
        position = holding.position
        if holding.price is None:
            lines.append(f"- {position.ticker}: precio NO DISPONIBLE ({holding.error})")
            continue
        prices[position.ticker] = holding.price
        absolute, percentage = holding.pnl
        lines.append(
            f"- {position.ticker}: {position.quantity:g} acciones, costo {position.buy_price:,.2f}, "
            f"precio {holding.price:,.2f}, P&L {absolute:+,.2f} ({percentage:+.2f}%), "
            f"comprada el {position.buy_date}"
        )
    if portfolio.holdings:
        absolute, percentage = portfolio.total_pnl
        lines.append(
            f"- TOTAL: costo {portfolio.cost_basis:,.2f}, valor {portfolio.market_value:,.2f}, "
            f"P&L {absolute:+,.2f} ({percentage:+.2f}%)"
        )

    lines.extend(["", "ALERTAS ACTIVAS:"])
    active = alerts_store.list_alerts(conn, only_active=True)
    if not active:
        lines.append("- (ninguna)")
    for alert in active:
        price = prices.get(alert.ticker)
        position = next(iter(positions_store.positions_for_ticker(conn, alert.ticker)), None)
        distance = _distance_to_trigger(alert, price, position) if price else None
        params = ", ".join(f"{k}={v}" for k, v in alert.params.items())
        lines.append(f"- {alert.ticker} {alert.kind} ({params})" + (f" — {distance}" if distance else ""))

    lines.extend(["", "CALENDARIO (earnings próximos):"])
    calendar = _calendar(market, sorted({h.position.ticker for h in portfolio.holdings}))
    lines.extend(calendar or ["- (sin fechas disponibles)"])

    lines.extend(["", "ÚLTIMAS ALERTAS DISPARADAS:"])
    events = events_store.recent_events(conn, limit=8)
    if not events:
        lines.append("- (ninguna)")
    for event in events:
        lines.append(f"- [{str(event['fired_at'])[:16]}] {event['title']}")
    return "\n".join(lines)


def _calendar(market: MarketService, tickers: Sequence[str]) -> list[str]:
    entries: list[str] = []
    for ticker in tickers:
        try:
            context = market.context(ticker)
        except DataUnavailableError:
            continue
        if context.next_earnings:
            entries.append(f"- {ticker}: earnings {context.next_earnings}")
    return entries
