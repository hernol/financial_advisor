"""The transaction ledger: the append-only truth behind every position.

Nothing here is ever updated in place and nothing is ever hard deleted. A
correction is a new row; a removal sets ``deleted_at`` so the entry disappears
from the rollup while staying in the history.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Sequence

from fa.models import Transaction
from fa.store.database import Database
from fa.store.serde import row_to_transaction, to_iso


def record(conn: Database, transaction: Transaction) -> Transaction:
    """Append one entry to the ledger."""
    now = datetime.now(timezone.utc)
    new_id = conn.insert(
        "INSERT INTO transactions(position_id, ticker, kind, trade_date, quantity, price, "
        "amount, ratio, fees, currency, note, source, created_at, updated_at) "
        "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
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
) -> list[Transaction]:
    sql = "SELECT * FROM transactions"
    clauses: list[str] = []
    params: list[object] = []
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


def get_transaction(conn: Database, transaction_id: int) -> Transaction | None:
    row = conn.execute("SELECT * FROM transactions WHERE id = ?", (transaction_id,)).fetchone()
    return row_to_transaction(row) if row else None


def soft_delete(conn: Database, transaction_id: int) -> bool:
    """Retire an entry without erasing it."""
    stamp = to_iso(datetime.now(timezone.utc))
    cur = conn.execute(
        "UPDATE transactions SET deleted_at = ?, updated_at = ? WHERE id = ? AND deleted_at IS NULL",
        (stamp, stamp, transaction_id),
    )
    conn.commit()
    return cur.rowcount > 0


def tickers(conn: Database) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT ticker FROM transactions WHERE deleted_at IS NULL ORDER BY ticker"
    )
    return [row["ticker"] for row in rows]


def changed_since(conn: Database, cursor: str) -> Sequence[Transaction]:
    """Rows touched after ``cursor``, for incremental clients."""
    rows = conn.execute(
        "SELECT * FROM transactions WHERE updated_at > ? ORDER BY updated_at", (cursor,)
    )
    return [row_to_transaction(row) for row in rows]
