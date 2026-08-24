"""Portfolio endpoints: what you hold, what it is worth, and how it got there.

Holdings are replayed from the transaction ledger rather than read off the
``positions`` rollup. That is where average cost, realised P&L and dividends
actually live, and it is the reason the ledger exists.

Prices come from stored bars, never from a provider. The screen therefore says
"as of the last stored close" and is honest about it, instead of appearing live
while quietly depending on Yahoo answering.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from fa import ledger, models
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


class TransactionRequest(BaseModel):
    """One ledger entry. Which fields matter depends on the kind."""

    ticker: str = Field(min_length=1, max_length=12)
    kind: str
    trade_date: date
    quantity: float | None = Field(default=None, gt=0)
    price: float | None = Field(default=None, gt=0)
    amount: float | None = None
    ratio: float | None = Field(default=None, gt=0)
    fees: float = Field(default=0.0, ge=0)
    currency: str = Field(default="USD", max_length=8)
    note: str = Field(default="", max_length=500)


# What each kind actually needs, stated once so the message can name it.
REQUIRED: dict[str, tuple[str, ...]] = {
    models.BUY: ("quantity", "price"),
    models.SELL: ("quantity", "price"),
    models.SPLIT: ("ratio",),
    models.DIVIDEND: (),
    models.FEE: (),
}


@router.post("/transactions", status_code=201)
def add_transaction(
    body: TransactionRequest, db: Database = Depends(get_db)
) -> dict[str, Any]:
    """Append one entry to the ledger.

    Nothing is edited in place and nothing is recalculated on the positions
    table: the holding is derived from the entries, so adding one here is the
    whole operation.
    """
    from fa.store import transactions as transactions_store

    if body.kind not in models.TRANSACTION_KINDS:
        raise HTTPException(
            status_code=422,
            detail=f"Tipo '{body.kind}' desconocido. Válidos: "
            f"{', '.join(models.TRANSACTION_KINDS)}.",
        )
    missing = [f for f in REQUIRED[body.kind] if getattr(body, f) is None]
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Una entrada de tipo '{body.kind}' necesita: {', '.join(missing)}.",
        )
    if body.kind == models.DIVIDEND and body.amount is None and body.quantity is None:
        raise HTTPException(
            status_code=422,
            detail="Un dividendo necesita un monto total o cantidad y precio por acción.",
        )
    if body.trade_date > date.today():
        raise HTTPException(status_code=422, detail="La fecha no puede estar en el futuro.")

    entry = transactions_store.record(
        db,
        models.Transaction(
            ticker=body.ticker,
            kind=body.kind,
            trade_date=body.trade_date,
            quantity=body.quantity,
            price=body.price,
            amount=body.amount,
            ratio=body.ratio,
            fees=body.fees,
            currency=body.currency,
            note=body.note,
        ),
    )
    return {
        "id": entry.id,
        "ticker": entry.ticker,
        "kind": entry.kind,
        "trade_date": entry.trade_date.isoformat(),
        "cash_flow": entry.cash_flow,
    }


@router.delete("/transactions/{transaction_id}", status_code=204)
def remove_transaction(transaction_id: int, db: Database = Depends(get_db)) -> None:
    """Retire an entry from the rollup without erasing it from the history."""
    from fa.store import transactions as transactions_store

    if not transactions_store.soft_delete(db, transaction_id):
        raise HTTPException(
            status_code=404, detail=f"No existe el movimiento {transaction_id}."
        )
