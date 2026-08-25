"""The transaction ledger: the append-only truth behind every position.

Nothing here is ever updated in place and nothing is ever hard deleted. A
correction is a new row; a removal sets ``deleted_at`` so the entry disappears
from the rollup while staying in the history.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone
from typing import Any, Mapping, Sequence

from fa.models import Transaction
from fa.store.database import Database
from fa.store.schema import LOCAL_ACCOUNT_ID
from fa.store.serde import row_to_transaction, to_iso


def record(
    conn: Database, transaction: Transaction, *, account_id: int = LOCAL_ACCOUNT_ID
) -> Transaction:
    """Append one entry to the ledger."""
    now = datetime.now(timezone.utc)
    new_id = conn.insert(
        "INSERT INTO transactions(account_id, position_id, ticker, kind, trade_date, quantity, "
        "price, amount, ratio, fees, currency, note, source, replaces_id, created_at, "
        "updated_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            account_id,
            transaction.position_id,
            transaction.ticker.upper(),
            transaction.kind,
            to_iso(transaction.trade_date),
            transaction.quantity,
            transaction.price,
            transaction.amount,
            transaction.ratio,
            transaction.fees,
            transaction.currency.upper(),
            transaction.note,
            transaction.source,
            transaction.replaces_id,
            to_iso(now),
            to_iso(now),
        ),
    )
    conn.commit()
    return transaction.with_id(new_id)


def list_transactions(
    conn: Database,
    *,
    ticker: str | None = None,
    position_id: int | None = None,
    kind: str | None = None,
    since: date | None = None,
    include_deleted: bool = False,
    limit: int | None = None,
    account_id: int = LOCAL_ACCOUNT_ID,
) -> list[Transaction]:
    sql = "SELECT * FROM transactions"
    clauses: list[str] = ["account_id = ?"]
    params: list[object] = [account_id]
    if not include_deleted:
        clauses.append("deleted_at IS NULL")
    if ticker:
        clauses.append("ticker = ?")
        params.append(ticker.upper())
    if position_id is not None:
        clauses.append("position_id = ?")
        params.append(position_id)
    if kind:
        clauses.append("kind = ?")
        params.append(kind)
    if since is not None:
        clauses.append("trade_date >= ?")
        params.append(since.isoformat())
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY trade_date, id"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    return [row_to_transaction(row) for row in conn.execute(sql, params)]


def get_transaction(
    conn: Database, transaction_id: int, *, account_id: int = LOCAL_ACCOUNT_ID
) -> Transaction | None:
    """One entry, retired or not: a correction has to be able to read what it
    replaced."""
    row = conn.execute(
        "SELECT * FROM transactions WHERE id = ? AND account_id = ?",
        (transaction_id, account_id),
    ).fetchone()
    return row_to_transaction(row) if row else None


def soft_delete(
    conn: Database, transaction_id: int, *, account_id: int = LOCAL_ACCOUNT_ID
) -> bool:
    """Retire an entry without erasing it, and refresh what summarised it."""
    entry = get_transaction(conn, transaction_id, account_id=account_id)
    stamp = to_iso(datetime.now(timezone.utc))
    cur = conn.execute(
        "UPDATE transactions SET deleted_at = ?, updated_at = ? "
        "WHERE id = ? AND account_id = ? AND deleted_at IS NULL",
        (stamp, stamp, transaction_id, account_id),
    )
    conn.commit()
    if cur.rowcount and entry is not None:
        # The rollup summarises the ledger, so removing an entry has to move it.
        from fa.store import positions as positions_store

        positions_store.sync_from_ledger(conn, entry.ticker, account_id=account_id)
    return cur.rowcount > 0


def tickers(conn: Database, *, account_id: int = LOCAL_ACCOUNT_ID) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT ticker FROM transactions "
        "WHERE deleted_at IS NULL AND account_id = ? ORDER BY ticker",
        (account_id,),
    )
    return [row["ticker"] for row in rows]


def changed_since(conn: Database, cursor: str) -> Sequence[Transaction]:
    """Rows touched after ``cursor``, for incremental clients."""
    rows = conn.execute(
        "SELECT * FROM transactions WHERE updated_at > ? ORDER BY updated_at", (cursor,)
    )
    return [row_to_transaction(row) for row in rows]


def amend(
    conn: Database,
    transaction_id: int,
    changes: Mapping[str, Any],
    *,
    account_id: int = LOCAL_ACCOUNT_ID,
) -> Transaction | None:
    """Correct an entry by replacing it, never by rewriting it.

    The original is retired and a new entry takes its place, pointing back at
    what it replaced. That keeps the ledger append-only: a fixed typo and a
    number quietly changed months later stay distinguishable, which is the
    whole reason for having a ledger instead of a table of current values.

    Returns the correction, or ``None`` if there was nothing to correct.
    """
    original = get_transaction(conn, transaction_id, account_id=account_id)
    if original is None:
        return None

    editable = {
        "ticker", "kind", "trade_date", "quantity", "price",
        "amount", "ratio", "fees", "currency", "note",
    }
    unknown = set(changes) - editable
    if unknown:
        raise ValueError(f"No se puede editar: {', '.join(sorted(unknown))}")

    corrected = replace(
        original,
        **{k: v for k, v in changes.items()},
        id=None,
        position_id=original.position_id,
        source="correction",
        replaces_id=original.id,
    )
    soft_delete(conn, transaction_id, account_id=account_id)
    stored = record(conn, corrected, account_id=account_id)

    from fa.store import positions as positions_store

    # A correction that moves the entry to another ticker leaves the old one
    # with one entry fewer, so both rollups have to be rebuilt.
    for ticker in {original.ticker, stored.ticker}:
        positions_store.sync_from_ledger(conn, ticker, account_id=account_id)
    return stored


def history_of(
    conn: Database, transaction_id: int, *, account_id: int = LOCAL_ACCOUNT_ID
) -> list[Transaction]:
    """An entry and every version it replaced, newest first."""
    chain: list[Transaction] = []
    current = get_transaction(conn, transaction_id, account_id=account_id)
    seen: set[int] = set()
    while current is not None and current.id not in seen:
        seen.add(current.id)
        chain.append(current)
        if current.replaces_id is None:
            break
        current = get_transaction(conn, current.replaces_id, account_id=account_id)
    return chain