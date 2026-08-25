"""Rendering helpers shared by the CLI and the interactive menu."""
from __future__ import annotations

from typing import Mapping, Sequence

from fa.alerts.engine import CheckReport
from fa.models import Alert, Position, Suggestion
from fa.portfolio import Portfolio
from fa.store import positions as positions_store
from fa.store.database import Database

SEPARATOR = "=" * 70


def render_positions(positions: Sequence[Position]) -> None:
    if not positions:
        print("\n(No hay posiciones cargadas)")
        return
    print(f"\n{'ID':<4} {'TICKER':<8} {'CANT':>10} {'COMPRA':>12} {'FECHA':<12} NOTAS")
    print("-" * 70)
    for p in positions:
        print(f"{p.id:<4} {p.ticker:<8} {p.quantity:>10,.4f} {p.buy_price:>12,.2f} {p.buy_date!s:<12} {p.notes}")


def render_portfolio(portfolio: Portfolio) -> None:
    if not portfolio.holdings:
        print("\n(No hay posiciones cargadas)")
        return
    print(f"\n{'TICKER':<8} {'CANT':>10} {'COMPRA':>10} {'ACTUAL':>10} {'VALOR':>14} {'P&L':>14} {'P&L %':>9}")
    print("-" * 80)
    for holding in portfolio.holdings:
        position = holding.position
        if holding.price is None:
            print(f"{position.ticker:<8} {position.quantity:>10,.4f} {position.buy_price:>10,.2f}   ⚠️  {holding.error}")
            continue
        pnl_abs, pnl_pct = holding.pnl
        print(
            f"{position.ticker:<8} {position.quantity:>10,.4f} {position.buy_price:>10,.2f} "
            f"{holding.price:>10,.2f} {holding.market_value:>14,.2f} {pnl_abs:>+14,.2f} {pnl_pct:>+8,.2f}%"
        )
    total_abs, total_pct = portfolio.total_pnl
    print("-" * 80)
    print(
        f"{'TOTAL':<8} {'':>10} {portfolio.cost_basis:>21,.2f} {portfolio.market_value:>14,.2f} "
        f"{total_abs:>+14,.2f} {total_pct:>+8,.2f}%"
    )


def render_alerts(conn: Database, alerts: Sequence[Alert]) -> None:
    if not alerts:
        print("\n(No hay alertas configuradas)")
        return
    print(f"\n{'ID':<4} {'TICKER':<8} {'TIPO':<16} {'ON':<3} {'ÚLTIMA':<20} PARÁMETROS")
    print("-" * 90)
    for alert in alerts:
        last = alert.last_fired_at.strftime("%Y-%m-%d %H:%M") if alert.last_fired_at else "-"
        state = "sí" if alert.active else "no"
        params = ", ".join(f"{k}={v}" for k, v in alert.params.items())
        print(f"{alert.id:<4} {alert.ticker:<8} {alert.kind:<16} {state:<3} {last:<20} {params}")


def render_events(events: Sequence[Mapping[str, object]]) -> None:
    if not events:
        print("\n(Sin alertas disparadas todavía)")
        return
    print("\n--- 🔔 ÚLTIMAS ALERTAS DISPARADAS ---")
    for event in events:
        print(f"[{str(event['fired_at'])[:19]}] {event['title']}")
        print(f"    {event['message']}")
        delivered = ", ".join(event["delivered"]) or "sin entregar"
        print(f"    canales: {delivered}")


def render_check_report(report: CheckReport) -> None:
    print(f"\nAlertas evaluadas: {report.checked} | disparadas: {len(report.fired)} "
          f"| en cooldown: {report.skipped_cooldown} | expiradas: {report.expired}")
    if report.errors:
        print("⚠️  Errores de datos:")
        for error in report.errors:
            print(f"   - {error}")
    if not report.fired:
        print("✅ Nada que reportar.")


def portfolio_summary_for_ai(conn: Database, ticker: str) -> str:
    """Compact text description of the user's exposure, fed to the AI prompt."""
    from fa.store import (
        alerts as alerts_store,  # noqa: PLC0415 - avoids a circular import
    )

    positions = positions_store.positions_for_ticker(conn, ticker)
    alerts = alerts_store.list_alerts(conn, ticker=ticker, only_active=True)
    lines: list[str] = []
    for position in positions:
        lines.append(
            f"- Position: {position.quantity:g} shares @ {position.buy_price:.2f} "
            f"{position.currency} bought on {position.buy_date}"
        )
    for alert in alerts:
        params = ", ".join(f"{k}={v}" for k, v in alert.params.items())
        lines.append(f"- Active alert: {alert.kind} ({params})")
    return "\n".join(lines)


SUGGESTION_MARK = {"high": "🔴", "medium": "🟡", "low": "🟢"}


def render_suggestions(suggestions: Sequence[Suggestion]) -> None:
    """Table of AI suggestions with their state."""
    if not suggestions:
        print("\n(No hay sugerencias)")
        return
    print(f"\n{'ID':<4} {'TICKER':<8} {'TIPO':<8} {'ESTADO':<9} PROPUESTA")
    print("-" * 90)
    for item in suggestions:
        mark = SUGGESTION_MARK.get(item.priority, " ")
        print(f"{item.id:<4} {item.ticker:<8} {item.category:<8} {item.status:<9} {mark} {item.headline}")
        if item.rationale:
            print(f"     ↳ {item.rationale}")
