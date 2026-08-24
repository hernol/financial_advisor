"""Positions: a rollup kept in step with the transaction ledger.

Every mutation here also appends to :mod:`fa.store.transactions`, so the
position row is a convenience view that can always be rebuilt. Deletes are
soft: the row leaves the listings, the history stays.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from fa import models
from fa.models import Position, Transaction
from fa.store import transactions as transactions_store
from fa.store.database import Database
from fa.store.serde import row_to_position, to_iso


def add_position(conn: Database, position: Position) -> Position:
    """Store the position and open its ledger with the matching buy."""
    now = datetime.now(timezone.utc)
    new_id = conn.insert(
        "INSERT INTO positions(ticker, quantity, buy_price, buy_date, currency, notes, "
        "created_at, updated_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
        (
            position.ticker.upper(),
            position.quantity,
            position.buy_price,
            to_iso(position.buy_date),
            position.currency,
            position.notes,
            to_iso(now),
            to_iso(now),
        ),
    )
    stored = position.with_id(new_id)
    conn.commit()
    transactions_store.record(
        conn,
        Transaction(
            position_id=stored.id,
            ticker=stored.ticker,
            kind=models.BUY,
            trade_date=stored.buy_date,
            quantity=stored.quantity,
            price=stored.buy_price,
            currency=stored.currency,
            note=stored.notes,
        ),
    )
    return stored


def list_positions(conn: Database, *, include_closed: bool = False) -> list[Position]:
    sql = "SELECT * FROM positions WHERE deleted_at IS NULL"
    if not include_closed:
        sql += " AND closed_at IS NULL"
    sql += " ORDER BY ticker, buy_date"
    return [row_to_position(row) for row in conn.execute(sql)]


def get_position(conn: Database, position_id: int) -> Position | None:
    row = conn.execute("SELECT * FROM positions WHERE id = ?", (position_id,)).fetchone()
    return row_to_position(row) if row else None


def positions_for_ticker(conn: Database, ticker: str) -> list[Position]:
    rows = conn.execute(
        "SELECT * FROM positions WHERE ticker = ? AND closed_at IS NULL AND deleted_at IS NULL "
        "ORDER BY buy_date",
        (ticker.upper(),),
    )
    return [row_to_position(row) for row in rows]


def close_position(
    conn: Database,
    position_id: int,
    *,
    price: float | None = None,
    close_date: date | None = None,
    fees: float = 0.0,
    note: str = "",
) -> Position | None:
    """Close a position, recording the sale that closed it.

    ``price`` is optional only so an old position can be archived without
    inventing a number; when it is absent no sale is recorded and the realised
    P&L stays ``NULL`` rather than being guessed at.
    """
    position = get_position(conn, position_id)
    if position is None or position.closed_at is not None:
        return None
    now = datetime.now(timezone.utc)
    sold_on = close_date or now.date()
    realized = None
    if price is not None:
        realized = (price - position.buy_price) * position.quantity - fees
    conn.execute(
        "UPDATE positions SET closed_at = ?, updated_at = ?, close_price = ?, close_date = ?, "
        "realized_pnl = ? WHERE id = ?",
        (to_iso(now), to_iso(now), price, to_iso(sold_on), realized, position_id),
    )
    conn.commit()
    if price is not None:
        transactions_store.record(
            conn,
            Transaction(
                position_id=position_id,
                ticker=position.ticker,
                kind=models.SELL,
                trade_date=sold_on,
                quantity=position.quantity,
                price=price,
                fees=fees,
                currency=position.currency,
                note=note,
            ),
        )
    return get_position(conn, position_id)


def delete_position(conn: Database, position_id: int) -> bool:
    """Soft delete: the position disappears, its ledger and alerts do not."""
    stamp = to_iso(datetime.now(timezone.utc))
    cur = conn.execute(
        "UPDATE positions SET deleted_at = ?, updated_at = ? WHERE id = ? AND deleted_at IS NULL",
        (stamp, stamp, position_id),
    )
    conn.commit()
    return cur.rowcount > 0


def apply_split(
    conn: Database, position_id: int, ratio: float, *, split_date: date | None = None
) -> Position | None:
    """Re-base a position after a split, keeping the original purchase on file.

    The rollup is overwritten — that is what it is for — but the buy that
    created it stays untouched in the ledger, so the price you actually paid
    survives every split that follows.
    """
    position = get_position(conn, position_id)
    if position is None or ratio <= 0:
        return None
    now = datetime.now(timezone.utc)
    conn.execute(
        "UPDATE positions SET buy_price = ?, quantity = ?, updated_at = ? WHERE id = ?",
        (position.buy_price / ratio, position.quantity * ratio, to_iso(now), position_id),
    )
    conn.commit()
    transactions_store.record(
        conn,
        Transaction(
            position_id=position_id,
            ticker=position.ticker,
            kind=models.SPLIT,
            trade_date=split_date or now.date(),
            ratio=ratio,
            currency=position.currency,
            source="split_detected",
            note=f"{ratio:g}:1 sobre {position.quantity:g} acciones a {position.buy_price:.4f}",
        ),
    )
    return get_position(conn, position_id)


def update_buy_price(
    conn: Database, position_id: int, buy_price: float, quantity: float
) -> bool:
    """Raw rollup correction. Prefer :func:`apply_split` for splits."""
    cur = conn.execute(
        "UPDATE positions SET buy_price = ?, quantity = ?, updated_at = ? WHERE id = ?",
        (buy_price, quantity, to_iso(datetime.now(timezone.utc)), position_id),
    )
    conn.commit()
    return cur.rowcount > 0


def tracked_tickers(conn: Database) -> list[str]:
    """Every ticker with an open position or an active alert."""
    rows = conn.execute(
        "SELECT ticker FROM positions WHERE closed_at IS NULL AND deleted_at IS NULL "
        "UNION SELECT ticker FROM alerts WHERE active = 1 AND deleted_at IS NULL ORDER BY ticker"
    )
    return [row["ticker"] for row in rows]
