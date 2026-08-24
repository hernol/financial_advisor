"""Portfolio endpoints: what you hold, what it is worth, and how it got there.

Holdings are replayed from the transaction ledger rather than read off the
``positions`` rollup. That is where average cost, realised P&L and dividends
actually live, and it is the reason the ledger exists.

Prices come from stored bars, never from a provider. The screen therefore says
"as of the last stored close" and is honest about it, instead of appearing live
while quietly depending on Yahoo answering.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from fa import ledger
from fa.api.deps import get_db
from fa.store import history as history_store
from fa.store.database import Database

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])


def _last_two_closes(db: Database, ticker: str) -> tuple[float | None, float | None]:
    bars = history_store.load_bars(db, ticker, limit=2)
    if not bars:
        return (None, None)
    if len(bars) == 1:
        return (bars[0].close, None)
    return (bars[-1].close, bars[-2].close)


@router.get("")
def portfolio(db: Database = Depends(get_db)) -> dict[str, Any]:
    """Every open holding valued at its last stored close."""
    rows: list[dict[str, Any]] = []
    market_value = 0.0
    cost_basis = 0.0
    realized = 0.0
    dividends = 0.0
    fees = 0.0
    currency = "USD"
    missing: list[str] = []

    for holding in ledger.holdings(db, open_only=False):
        realized += holding.realized_pnl
        dividends += holding.dividends
        fees += holding.fees
        currency = holding.currency or currency
        if not holding.is_open:
            continue

        price, previous = _last_two_closes(db, holding.ticker)
        if price is None:
            # No stored bar means no honest valuation. Say so rather than
            # silently valuing the position at zero or at its cost.
            missing.append(holding.ticker)
        value = holding.market_value(price) if price is not None else None
        pnl = holding.unrealized(price) if price is not None else (None, None)

        cost_basis += holding.cost_basis
        market_value += value or 0.0
        rows.append(
            {
                "ticker": holding.ticker,
                "quantity": holding.quantity,
                "average_cost": holding.average_cost,
                "cost_basis": holding.cost_basis,
                "price": price,
                "value": value,
                "pnl_abs": pnl[0],
                "pnl_pct": pnl[1],
                "day_change_pct": (
                    (price - previous) / previous * 100.0
                    if price is not None and previous
                    else None
                ),
                "currency": holding.currency,
                "entries": holding.entries,
            }
        )

    total = market_value or 0.0
    for row in rows:
        row["weight_pct"] = (row["value"] / total * 100.0) if row["value"] and total else None
    rows.sort(key=lambda r: r["value"] or 0.0, reverse=True)

    unrealized = market_value - cost_basis if rows else 0.0
    return {
        "holdings": rows,
        "count": len(rows),
        "currency": currency,
        "cost_basis": cost_basis,
        "market_value": market_value,
        "pnl_abs": unrealized,
        "pnl_pct": (unrealized / cost_basis * 100.0) if cost_basis else None,
        "realized_pnl": realized,
        "dividends": dividends,
        "fees": fees,
        "unpriced": missing,
    }


@router.get("/history")
def history(
    days: int = Query(365, ge=5, le=3650),
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    """The equity curve, one point per day.

    Written by the scheduled check rather than on demand, so the curve has a
    point for every day the timer ran — not only the days somebody opened this
    screen.
    """
    points = history_store.equity_curve(db, days=days)
    return {
        "sessions": len(points),
        "day": [p["day"] for p in points],
        "market_value": [p["market_value"] for p in points],
        "cost_basis": [p["cost_basis"] for p in points],
        "pnl_pct": [p["pnl_pct"] for p in points],
    }


@router.get("/transactions")
def transactions(
    limit: int = Query(50, ge=1, le=500),
    db: Database = Depends(get_db),
) -> list[dict[str, Any]]:
    """The ledger, newest first: what was bought, sold, split and collected."""
    from fa.store import transactions as transactions_store

    entries = transactions_store.list_transactions(db)
    entries.reverse()
    return [
        {
            "id": e.id,
            "ticker": e.ticker,
            "kind": e.kind,
            "trade_date": e.trade_date.isoformat() if e.trade_date else None,
            "quantity": e.quantity,
            "price": e.price,
            "amount": e.amount,
            "ratio": e.ratio,
            "fees": e.fees,
            "currency": e.currency,
            "cash_flow": e.cash_flow,
            "note": e.note,
            "source": e.source,
        }
        for e in entries[:limit]
    ]
