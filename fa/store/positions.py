"""Positions: a rollup recomputed from the transaction ledger.

The ledger is the truth and this table is a cache of it. Every write goes the
same way — append an entry, then :func:`sync_from_ledger` rebuilds the row — so
it does not matter whether the entry came from the terminal or from the phone.
Before this, only ``add_position`` maintained the rollup, and a trade loaded
from the dashboard was invisible to the CLI and to the alerts that need a
position.

Deletes are soft: the row leaves the listings, the history stays.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from fa import models
from fa.models import Position, Transaction
from fa.store import transactions as transactions_store
from fa.store.database import Database
from fa.store.schema import LOCAL_ACCOUNT_ID
from fa.store.serde import row_to_position, to_iso


def add_position(
    conn: Database, position: Position, *, account_id: int = LOCAL_ACCOUNT_ID
) -> Position:
    """Record the purchase and return the resulting holding.

    Goes through the ledger like every other write, so buying the same ticker
    twice produces one position at the average cost instead of two rows.
    """
    transactions_store.record(
        conn,
        Transaction(
            ticker=position.ticker,
            kind=models.BUY,
            trade_date=position.buy_date,
            quantity=position.quantity,
            price=position.buy_price,
            currency=position.currency,
            note=position.notes,
        ),
        account_id=account_id,
    )
    stored = sync_from_ledger(conn, position.ticker, account_id=account_id)
    assert stored is not None  # noqa: S101 - a buy always leaves an open holding
    return stored


def sync_from_ledger(
    conn: Database, ticker: str, *, account_id: int = LOCAL_ACCOUNT_ID
) -> Position | None:
    """Rebuild one ticker's rollup from its ledger entries.

    The single writer of this table. Call it after any change to the ledger and
    the two can never drift; nothing else should write these columns.
    """
    from fa import ledger  # local: fa.ledger reads the transaction store

    symbol = ticker.upper()
    entries = transactions_store.list_transactions(conn, ticker=symbol, account_id=account_id)
    holding = ledger.replay(symbol, entries)
    row = conn.execute(
        "SELECT * FROM positions WHERE ticker = ? AND account_id = ? AND deleted_at IS NULL",
        (symbol, account_id),
    ).fetchone()
    now = to_iso(datetime.now(timezone.utc))

    if not entries:
        # Nothing left in the ledger: the rollup has nothing to summarise.
        if row is not None:
            conn.execute(
                "UPDATE positions SET deleted_at = ?, updated_at = ? WHERE id = ?",
                (now, now, row["id"]),
            )
            conn.commit()
        return None

    buys = [e for e in entries if e.kind == models.BUY]
    opened = buys[0].trade_date if buys else entries[0].trade_date
    sells = [e for e in entries if e.kind == models.SELL]
    # A position archived by hand has no sale to point at; leaving it closed
    # respects that instead of reopening it on the next unrelated entry.
    manually_closed = row is not None and row["closed_at"] and not sells
    closed = (not holding.is_open) or manually_closed
    last_sell = sells[-1] if sells else None

    # Cost in the base currency for a holding bought in another one. Each buy
    # carries the rate of its own day, frozen when it was recorded; this blends
    # them into one factor and applies it to the cost the ledger already tracks,
    # so a partial sale reduces the dollar basis exactly as it reduces the peso
    # one. Converting here against today's rate would rewrite what past trades
    # cost. None when nothing was ever converted, which is every ordinary
    # holding, and that is what leaves them untouched.
    converted = [e for e in buys if e.usd_price is not None and e.price]
    cost_basis_usd = None
    if converted:
        paid_foreign = sum(
            (e.quantity or 0.0) * (e.price or 0.0) + (e.fees or 0.0) for e in converted
        )
        paid_base = sum(
            (e.quantity or 0.0) * e.usd_price + (e.fees or 0.0) / (e.fx_rate or 1.0)
            for e in converted
        )
        if paid_foreign:
            cost_basis_usd = holding.cost_basis * (paid_base / paid_foreign)

    values = (
        holding.quantity,
        holding.average_cost,
        to_iso(opened),
        holding.currency,
        now if closed else None,
        last_sell.price if last_sell else (row["close_price"] if row else None),
        to_iso(last_sell.trade_date) if last_sell else (row["close_date"] if row else None),
        holding.realized_pnl or None,
        now,
        cost_basis_usd,
    )

    if row is None:
        conn.insert(
            "INSERT INTO positions(account_id, ticker, quantity, buy_price, buy_date, currency, "
            "notes, created_at, closed_at, close_price, close_date, realized_pnl, updated_at, "
            "cost_basis_usd) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (account_id, symbol, values[0], values[1], values[2], values[3],
             buys[0].note if buys else "", now, *values[4:]),
        )
    else:
        conn.execute(
            "UPDATE positions SET quantity = ?, buy_price = ?, buy_date = ?, currency = ?, "
            "closed_at = ?, close_price = ?, close_date = ?, realized_pnl = ?, updated_at = ?, "
            "cost_basis_usd = ? WHERE id = ?",
            (*values, row["id"]),
        )
    conn.commit()
    stored = get_position_for_ticker(conn, symbol, account_id=account_id)
    if stored is not None:
        # The entries are written before the rollup exists, so the back link is
        # filled in here rather than left dangling.
        conn.execute(
            "UPDATE transactions SET position_id = ? "
            "WHERE ticker = ? AND account_id = ? AND position_id IS NULL",
            (stored.id, symbol, account_id),
        )
        conn.commit()
    return stored


def get_position_for_ticker(
    conn: Database, ticker: str, *, account_id: int = LOCAL_ACCOUNT_ID
) -> Position | None:
    """The rollup row for a ticker, open or closed."""
    row = conn.execute(
        "SELECT * FROM positions WHERE ticker = ? AND account_id = ? AND deleted_at IS NULL",
        (ticker.upper(), account_id),
    ).fetchone()
    return row_to_position(row) if row else None


def list_positions(
    conn: Database, *, include_closed: bool = False, account_id: int = LOCAL_ACCOUNT_ID
) -> list[Position]:
    sql = "SELECT * FROM positions WHERE deleted_at IS NULL AND account_id = ?"
    if not include_closed:
        sql += " AND closed_at IS NULL"
    sql += " ORDER BY ticker, buy_date"
    return [row_to_position(row) for row in conn.execute(sql, (account_id,))]


def get_position(
    conn: Database, position_id: int, *, account_id: int | None = LOCAL_ACCOUNT_ID
) -> Position | None:
    """One position. ``account_id=None`` skips the scope check, for internal
    lookups that already know the row belongs to the caller."""
    sql = "SELECT * FROM positions WHERE id = ?"
    params: list[object] = [position_id]
    if account_id is not None:
        sql += " AND account_id = ?"
        params.append(account_id)
    row = conn.execute(sql, params).fetchone()
    return row_to_position(row) if row else None


def positions_for_ticker(
    conn: Database, ticker: str, *, account_id: int = LOCAL_ACCOUNT_ID
) -> list[Position]:
    rows = conn.execute(
        "SELECT * FROM positions WHERE ticker = ? AND account_id = ? "
        "AND closed_at IS NULL AND deleted_at IS NULL ORDER BY buy_date",
        (ticker.upper(), account_id),
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
    account_id: int = LOCAL_ACCOUNT_ID,
) -> Position | None:
    """Close a position, recording the sale that closed it.

    ``price`` is optional only so an old position can be archived without
    inventing a number; when it is absent no sale is recorded and the realised
    P&L stays unknown rather than being guessed at.
    """
    position = get_position(conn, position_id, account_id=account_id)
    if position is None or position.closed_at is not None:
        return None
    sold_on = close_date or datetime.now(timezone.utc).date()

    if price is None:
        # Archived by hand: no entry to append, so the rollup is closed here and
        # sync_from_ledger is told to respect it.
        stamp = to_iso(datetime.now(timezone.utc))
        conn.execute(
            "UPDATE positions SET closed_at = ?, updated_at = ? WHERE id = ?",
            (stamp, stamp, position_id),
        )
        conn.commit()
        return get_position(conn, position_id, account_id=account_id)

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
        account_id=account_id,
    )
    sync_from_ledger(conn, position.ticker, account_id=account_id)
    return get_position(conn, position_id, account_id=account_id)


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
    conn: Database,
    position_id: int,
    ratio: float,
    *,
    split_date: date | None = None,
    account_id: int = LOCAL_ACCOUNT_ID,
) -> Position | None:
    """Re-base a position after a split, keeping the original purchase on file.

    The rollup is recomputed — that is what it is for — but the buy that
    created it stays untouched in the ledger, so the price actually paid
    survives every split that follows.
    """
    position = get_position(conn, position_id, account_id=account_id)
    if position is None or ratio <= 0:
        return None
    transactions_store.record(
        conn,
        Transaction(
            position_id=position_id,
            ticker=position.ticker,
            kind=models.SPLIT,
            trade_date=split_date or datetime.now(timezone.utc).date(),
            ratio=ratio,
            currency=position.currency,
            source="split_detected",
            note=f"{ratio:g}:1 sobre {position.quantity:g} acciones a {position.buy_price:.4f}",
        ),
        account_id=account_id,
    )
    sync_from_ledger(conn, position.ticker, account_id=account_id)
    return get_position(conn, position_id, account_id=account_id)


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


def tracked_tickers(conn: Database, *, account_id: int = LOCAL_ACCOUNT_ID) -> list[str]:
    """Every ticker with an open position or an active alert, for one account."""
    rows = conn.execute(
        "SELECT ticker FROM positions "
        "WHERE closed_at IS NULL AND deleted_at IS NULL AND account_id = ? "
        "UNION SELECT ticker FROM alerts "
        "WHERE active = 1 AND deleted_at IS NULL AND account_id = ? ORDER BY ticker",
        (account_id, account_id),
    )
    return [row["ticker"] for row in rows]
