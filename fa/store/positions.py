"""CRUD for stock positions."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from fa.models import Position
from fa.store.serde import row_to_position, to_iso


def add_position(conn: sqlite3.Connection, position: Position) -> Position:
    now = datetime.now(timezone.utc)
    cur = conn.execute(
        "INSERT INTO positions(ticker, quantity, buy_price, buy_date, currency, notes, created_at) "
        "VALUES(?, ?, ?, ?, ?, ?, ?)",
        (
            position.ticker.upper(),
            position.quantity,
            position.buy_price,
            to_iso(position.buy_date),
            position.currency,
            position.notes,
            to_iso(now),
        ),
    )
    conn.commit()
    return position.with_id(int(cur.lastrowid))


def list_positions(conn: sqlite3.Connection, *, include_closed: bool = False) -> list[Position]:
    sql = "SELECT * FROM positions"
    if not include_closed:
        sql += " WHERE closed_at IS NULL"
    sql += " ORDER BY ticker, buy_date"
    return [row_to_position(row) for row in conn.execute(sql)]


def get_position(conn: sqlite3.Connection, position_id: int) -> Position | None:
    row = conn.execute("SELECT * FROM positions WHERE id = ?", (position_id,)).fetchone()
    return row_to_position(row) if row else None


def positions_for_ticker(conn: sqlite3.Connection, ticker: str) -> list[Position]:
    rows = conn.execute(
        "SELECT * FROM positions WHERE ticker = ? AND closed_at IS NULL ORDER BY buy_date",
        (ticker.upper(),),
    )
    return [row_to_position(row) for row in rows]


def close_position(conn: sqlite3.Connection, position_id: int) -> bool:
    cur = conn.execute(
        "UPDATE positions SET closed_at = ? WHERE id = ? AND closed_at IS NULL",
        (to_iso(datetime.now(timezone.utc)), position_id),
    )
    conn.commit()
    return cur.rowcount > 0


def delete_position(conn: sqlite3.Connection, position_id: int) -> bool:
    cur = conn.execute("DELETE FROM positions WHERE id = ?", (position_id,))
    conn.commit()
    return cur.rowcount > 0


def update_buy_price(conn: sqlite3.Connection, position_id: int, buy_price: float, quantity: float) -> bool:
    """Used after a stock split to keep the cost basis coherent."""
    cur = conn.execute(
        "UPDATE positions SET buy_price = ?, quantity = ? WHERE id = ?",
        (buy_price, quantity, position_id),
    )
    conn.commit()
    return cur.rowcount > 0


def tracked_tickers(conn: sqlite3.Connection) -> list[str]:
    """Every ticker with an open position or an active alert."""
    rows = conn.execute(
        "SELECT ticker FROM positions WHERE closed_at IS NULL "
        "UNION SELECT ticker FROM alerts WHERE active = 1 ORDER BY ticker"
    )
    return [row["ticker"] for row in rows]
