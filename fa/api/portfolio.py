"""Portfolio endpoints: what you hold, what it is worth, and how it got there.

Holdings are replayed from the transaction ledger rather than read off the
``positions`` rollup. That is where average cost, realised P&L and dividends
actually live, and it is the reason the ledger exists.

Prices come from stored bars, never from a provider. The screen therefore says
"as of the last stored close" and is honest about it, instead of appearing live
while quietly depending on Yahoo answering.
"""
from __future__ import annotations

import logging
import threading
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from fa import cedears, equity, ledger, models
from fa.cedear_cost import derive_cost
from fa.api.auth import account_id
from fa.api.deps import build_market, get_db, in_background
from fa.config import BASE_CURRENCY
from fa.errors import DataUnavailableError
from fa.store import history as history_store
from fa.store.database import Database

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])


def _last_two_closes(db: Database, ticker: str) -> tuple[float | None, float | None]:
    bars = history_store.load_bars(db, ticker, limit=2)
    if not bars:
        return (None, None)
    if len(bars) == 1:
        return (bars[0].close, None)
    return (bars[-1].close, bars[-2].close)


@router.get("")
def portfolio(
    db: Database = Depends(get_db), account: int = Depends(account_id)
) -> dict[str, Any]:
    """Every open holding valued at its last stored close."""
    from fa.store import positions as positions_store
    from fa.store import transactions as transactions_store

    rows: list[dict[str, Any]] = []
    market_value = 0.0
    cost_basis = 0.0
    realized = 0.0
    dividends = 0.0
    fees = 0.0
    currency = BASE_CURRENCY
    missing: list[str] = []
    foreign: list[dict[str, str]] = []

    for holding in ledger.holdings(db, open_only=False, account_id=account):
        realized += holding.realized_pnl
        dividends += holding.dividends
        fees += holding.fees
        currency = holding.currency or currency
        if not holding.is_open:
            continue

        # A CEDEAR is priced through the share it represents: the bars stored
        # under the underlying, times the published ratio. No exchange rate
        # takes part, which is why a peso holding can join a dollar total here
        # while a genuinely foreign one still cannot.
        cedear = cedears.resolve(holding.ticker)
        is_cedear = cedear is not None and cedear.supported
        shares_per_unit = cedear.shares_per_cedear if is_cedear else 1.0
        symbol = cedear.underlying if is_cedear else holding.ticker

        price, previous = _last_two_closes(db, symbol)
        if not is_cedear and holding.currency.upper() != BASE_CURRENCY:
            # One currency, no conversion table. Adding these into the total
            # would produce a number that looks right and is not.
            foreign.append({"ticker": holding.ticker, "currency": holding.currency})
            price = None
        elif price is None:
            # No stored bar means no honest valuation. Say so rather than
            # silently valuing the position at zero or at its cost.
            missing.append(holding.ticker)
        value = holding.market_value(price) * shares_per_unit if price is not None else None
        # The ledger's cost is in whatever the trades were paid in, so for a
        # CEDEAR it is pesos and cannot meet a dollar value. The position keeps
        # the dollar cost frozen at each trade's own rate; use that instead, and
        # value the holding at nothing rather than mix the two.
        basis = holding.cost_basis
        if is_cedear:
            stored = positions_store.get_position_for_ticker(
                db, holding.ticker, account_id=account
            )
            basis = stored.cost_basis_usd if stored else None
            if basis is None:
                missing.append(holding.ticker)
                price, value = None, None
        pnl = (
            (value - basis, (value - basis) / basis * 100.0)
            if price is not None and basis
            else (None, None)
        )

        if price is not None:
            # A holding kept out of the total has to stay out of the cost too,
            # or the P&L compares a partial value against a full basis.
            cost_basis += basis or 0.0
            market_value += value or 0.0
        rows.append(
            {
                "ticker": holding.ticker,
                "quantity": holding.quantity,
                "average_cost": holding.average_cost,
                "cost_basis": basis,
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
    # Everything the ledger did to the cash side: negative while the money is
    # in shares, rising as sales and dividends bring it back.
    entries = transactions_store.list_transactions(db, account_id=account)
    cash = sum(e.cash_flow for e in entries)
    # Once deposits are on file, holdings plus cash is what the account is
    # worth, not what it earned — a deposit would otherwise read as a gain.
    # Subtracting what was put in separates the two.
    put_in = models.contributed(entries)
    return {
        "holdings": rows,
        "count": len(rows),
        "currency": currency,
        "cost_basis": cost_basis,
        "market_value": market_value,
        "pnl_abs": unrealized,
        "pnl_pct": (unrealized / cost_basis * 100.0) if cost_basis else None,
        "realized_pnl": realized,
        "cash": round(cash, 4),
        "contributed": round(put_in, 4),
        "net_worth": round(market_value + cash, 4),
        "total_result": round(market_value + cash - put_in, 4),
        "dividends": dividends,
        "fees": fees,
        "unpriced": missing,
        "foreign_currency": foreign,
        "base_currency": BASE_CURRENCY,
    }


# A bulk refresh is one job per account, not one per ticker: two of them at
# once would double every provider call for no extra information. Kept in
# memory on purpose — it describes work in flight, and work in flight does not
# survive a restart anyway.
_refresh_jobs: dict[int, dict[str, Any]] = {}
_refresh_lock = threading.Lock()

# A worker killed mid-run would otherwise leave the job "running" forever and
# the button disabled for good.
REFRESH_STALE_AFTER = timedelta(minutes=15)


def refresh_state(account: int) -> dict[str, Any]:
    with _refresh_lock:
        job = dict(_refresh_jobs.get(account, {}))
    if job.get("status") == "running":
        started = job.get("at")
        stale = True
        if started:
            try:
                when = datetime.fromisoformat(started)
                if when.tzinfo is None:
                    when = when.replace(tzinfo=timezone.utc)
                stale = datetime.now(timezone.utc) - when > REFRESH_STALE_AFTER
            except ValueError:
                stale = True
        if stale:
            job["status"] = "error"
            job["detail"] = "La actualización quedó sin terminar."
    return job


def reset_refresh_jobs() -> None:
    """Forget every bulk refresh. For the tests; safe at any time."""
    with _refresh_lock:
        _refresh_jobs.clear()


def _set_refresh(account: int, **fields: Any) -> None:
    with _refresh_lock:
        job = _refresh_jobs.setdefault(account, {})
        job.update(fields)
        job["at"] = datetime.now(timezone.utc).isoformat()


@router.post("/refresh", status_code=202)
def refresh_all(
    background: BackgroundTasks,
    db: Database = Depends(get_db),
    account: int = Depends(account_id),
) -> dict[str, Any]:
    """Re-fetch prices for every ticker this account follows.

    The per-ticker button covers one symbol; this is the same thing across the
    watchlist, which is what somebody coming back after a few days actually
    wants. Like that one it only refills bars and indicators — no alert is
    evaluated and nothing is delivered.
    """
    from fa.api.deps import build_market
    from fa.store import positions as positions_store
    from fa.warm import warm

    tickers = positions_store.tracked_tickers(db, account_id=account)
    if not tickers:
        return {"tickers": [], "total": 0, "status": "done"}

    running = refresh_state(account)
    if running.get("status") == "running":
        # Not an error: the honest answer is that it is already happening.
        return {**running, "tickers": tickers, "already": True}

    _set_refresh(account, status="running", done=0, total=len(tickers),
                 failed=[], current=tickers[0])

    def run(worker_db: Database) -> None:
        # Its own connection: this loop writes for as long as the provider
        # takes, and the client polls the status endpoint the whole time.
        market = build_market(worker_db)
        failed: list[str] = []
        for index, ticker in enumerate(tickers):
            _set_refresh(account, current=ticker, done=index)
            try:
                if not warm(worker_db, market, ticker, force=True):
                    failed.append(ticker)
            except Exception:
                # One dead provider must not abandon the rest of the list.
                logger.exception("bulk refresh of %s failed", ticker)
                failed.append(ticker)
            _set_refresh(account, done=index + 1, failed=failed)
        _set_refresh(account, status="done", current="", failed=failed)

    background.add_task(in_background(run))
    return {"tickers": tickers, "total": len(tickers), "status": "running"}


@router.get("/refresh/status")
def refresh_status(account: int = Depends(account_id)) -> dict[str, Any]:
    return refresh_state(account) or {"status": "idle"}


@router.get("/history")
def history(
    days: int = Query(365, ge=5, le=3650),
    db: Database = Depends(get_db),
    account: int = Depends(account_id),
) -> dict[str, Any]:
    """The equity curve, one point per day.

    Derived from the ledger and the stored bars rather than read back from the
    valuations table. The recorded points only start the day the feature was
    installed; the ledger knows what was held from the first purchase onwards
    and the bars know what it was worth, so the whole history is available —
    and a corrected trade moves every point that depended on it.
    """
    points = equity.curve(db, days=days, account_id=account)
    return {
        "sessions": len(points),
        "first_day": points[0]["day"] if points else None,
        "last_day": points[-1]["day"] if points else None,
        "day": [p["day"] for p in points],
        "market_value": [p["market_value"] for p in points],
        "cost_basis": [p["cost_basis"] for p in points],
        # Holdings plus the cash the ledger has produced. Selling moves money
        # from one to the other, so this line does not step down on a sale.
        "cash": [p["cash"] for p in points],
        "contributed": [p["contributed"] for p in points],
        "total": [p["total"] for p in points],
        "result": [p["result"] for p in points],
        "pnl_pct": [p["pnl_pct"] for p in points],
    }


@router.get("/transactions")
def transactions(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Database = Depends(get_db),
    account: int = Depends(account_id),
) -> dict[str, Any]:
    """The ledger, newest first.

    Returns the total alongside the page. A list cut off at a limit with no
    sign that it was cut is how an entry goes missing without anybody being
    told — which is exactly what happened to a holding whose only movement was
    the oldest one.
    """
    from fa.store import transactions as transactions_store

    entries = transactions_store.list_transactions(db, account_id=account)
    entries.reverse()
    page = entries[offset : offset + limit]
    return {
        "total": len(entries),
        "shown": len(page),
        "offset": offset,
        "entries": [
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
                "replaces_id": e.replaces_id,
                "corrected": e.source == "correction",
            }
            for e in page
        ],
    }


class TransactionRequest(BaseModel):
    """One ledger entry. Which fields matter depends on the kind."""

    # Absent for a deposit or a withdrawal, which belong to no symbol.
    ticker: str | None = Field(
        default=None, min_length=1, max_length=12, pattern=models.TICKER_PATTERN
    )
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
    models.DEPOSIT: ("amount",),
    models.WITHDRAW: ("amount",),
}


@router.post("/transactions", status_code=201)
def add_transaction(
    body: TransactionRequest,
    background: BackgroundTasks,
    db: Database = Depends(get_db),
    account: int = Depends(account_id),
) -> dict[str, Any]:
    """Append one entry to the ledger.

    Nothing is edited in place and nothing is recalculated on the positions
    table: the holding is derived from the entries, so adding one here is the
    whole operation.
    """
    from fa.store import positions as positions_store
    from fa.store import transactions as transactions_store
    from fa.warm import has_prices, warm

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
    cash_only = body.kind in models.CASH_KINDS
    if cash_only and body.ticker:
        raise HTTPException(
            status_code=422,
            detail=f"Un {body.kind} es plata que entra o sale de la cuenta, no de un papel.",
        )
    if not cash_only and not body.ticker:
        raise HTTPException(
            status_code=422, detail=f"Una entrada de tipo '{body.kind}' necesita: ticker."
        )
    if body.trade_date > date.today():
        raise HTTPException(status_code=422, detail="La fecha no puede estar en el futuro.")
    cedear = cedears.resolve(body.ticker or "")
    fx_rate = usd_price = None
    if body.currency.upper() != BASE_CURRENCY and cedear is not None and cedear.supported:
        # A CEDEAR is a fixed fraction of a dollar-quoted share, so a peso trade
        # on one converts from that day's own closes without any FX table. Any
        # other non-base trade still cannot, and falls through to the refusal.
        try:
            usd_cost, fx_rate = derive_cost(
                db,
                cedear,
                trade_date=body.trade_date,
                quantity=body.quantity or 0.0,
                ars_price=body.price or 0.0,
                fees=body.fees or 0.0,
            )
        except DataUnavailableError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        usd_price = usd_cost / body.quantity if body.quantity else None
    elif body.currency.upper() != BASE_CURRENCY:
        raise HTTPException(
            status_code=422,
            detail=(
                f"La cartera se lleva en {BASE_CURRENCY}. Para operar en "
                f"{body.currency.upper()} hace falta una tabla de cotizaciones que "
                "todavía no existe."
            ),
        )

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
            # Frozen here, at the rate of the trade's own date. Nothing later
            # recomputes them, so an old purchase's P&L stays put when the
            # dollar moves.
            fx_rate=fx_rate,
            usd_price=usd_price,
        ),
        account_id=account,
    )
    # The rollup is what the CLI and the alert engine read, so it moves with
    # the ledger regardless of which client wrote the entry. A cash movement
    # has no ticker and therefore no rollup and nothing to fetch.
    fetching = False
    if entry.ticker:
        positions_store.sync_from_ledger(db, entry.ticker, account_id=account)
        # A ticker with no stored prices cannot be valued. Telling the user to
        # go run a command would be asking them to do the program's job, so the
        # fetch is queued and the screen fills in when it lands.
        fetching = not has_prices(db, entry.ticker)
        if fetching:
            background.add_task(in_background(
                lambda worker, symbol=entry.ticker: warm(worker, build_market(worker), symbol)
            ))

    return {
        "id": entry.id,
        "ticker": entry.ticker,
        "kind": entry.kind,
        "trade_date": entry.trade_date.isoformat(),
        "cash_flow": entry.cash_flow,
        "fetching_prices": fetching,
    }


class TransactionPatch(BaseModel):
    """The fields a correction may change. Everything is optional: what is not
    sent keeps the value it had."""

    ticker: str | None = Field(
        default=None, min_length=1, max_length=12, pattern=models.TICKER_PATTERN
    )
    kind: str | None = None
    trade_date: date | None = None
    quantity: float | None = Field(default=None, gt=0)
    price: float | None = Field(default=None, gt=0)
    amount: float | None = None
    ratio: float | None = Field(default=None, gt=0)
    fees: float | None = Field(default=None, ge=0)
    note: str | None = Field(default=None, max_length=500)


@router.patch("/transactions/{transaction_id}")
def amend_transaction(
    transaction_id: int,
    body: TransactionPatch,
    db: Database = Depends(get_db),
    account: int = Depends(account_id),
) -> dict[str, Any]:
    """Correct an entry.

    Implemented as a replacement rather than an update: the original is retired
    and the correction points at it. The client calls this "editar" because
    that is what the user is doing; the ledger stays append-only underneath.
    """
    from fa.store import transactions as transactions_store

    changes = body.model_dump(exclude_none=True)
    if not changes:
        raise HTTPException(status_code=422, detail="No mandaste ningún cambio.")
    if "kind" in changes and changes["kind"] not in models.TRANSACTION_KINDS:
        raise HTTPException(
            status_code=422,
            detail=f"Tipo '{changes['kind']}' desconocido. Válidos: "
            f"{', '.join(models.TRANSACTION_KINDS)}.",
        )
    if changes.get("trade_date") and changes["trade_date"] > date.today():
        raise HTTPException(status_code=422, detail="La fecha no puede estar en el futuro.")
    if "ticker" in changes:
        changes["ticker"] = changes["ticker"].upper()
    if changes.get("kind") in models.CASH_KINDS and changes.get("ticker"):
        raise HTTPException(
            status_code=422,
            detail=f"Un {changes['kind']} no lleva ticker.",
        )

    corrected = transactions_store.amend(db, transaction_id, changes, account_id=account)
    if corrected is None:
        raise HTTPException(
            status_code=404, detail=f"No existe el movimiento {transaction_id}."
        )
    return {
        "id": corrected.id,
        "replaces_id": corrected.replaces_id,
        "ticker": corrected.ticker,
        "kind": corrected.kind,
        "trade_date": corrected.trade_date.isoformat(),
        "quantity": corrected.quantity,
        "price": corrected.price,
        "fees": corrected.fees,
        "cash_flow": corrected.cash_flow,
    }


@router.delete("/holdings/{ticker}")
def remove_holding(
    ticker: str,
    db: Database = Depends(get_db),
    account: int = Depends(account_id),
) -> dict[str, Any]:
    """Take a ticker out of the portfolio entirely.

    Retires every ledger entry it has rather than asking the person to find and
    remove each one. Soft, so the history keeps what happened; the equity curve
    is derived from the live entries, so the past stops counting it too.
    """
    from fa.store import transactions as transactions_store

    symbol = ticker.upper()
    retired = transactions_store.retire_ticker(db, symbol, account_id=account)
    if not retired:
        raise HTTPException(
            status_code=404, detail=f"{symbol} no tiene movimientos en la cartera."
        )
    return {"ticker": symbol, "retired": retired}


@router.delete("/transactions/{transaction_id}", status_code=204)
def remove_transaction(
    transaction_id: int,
    db: Database = Depends(get_db),
    account: int = Depends(account_id),
) -> None:
    """Retire an entry from the rollup without erasing it from the history."""
    from fa.store import transactions as transactions_store

    # soft_delete refreshes the rollup itself, so the two stay in step.
    if not transactions_store.soft_delete(db, transaction_id, account_id=account):
        raise HTTPException(
            status_code=404, detail=f"No existe el movimiento {transaction_id}."
        )
