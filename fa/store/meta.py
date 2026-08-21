"""Small key/value store used for session state (the ticker being worked on)."""
from __future__ import annotations

import sqlite3

CURRENT_TICKER = "current_ticker"


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()


def clear_meta(conn: sqlite3.Connection, key: str) -> None:
    conn.execute("DELETE FROM meta WHERE key = ?", (key,))
    conn.commit()


def get_current_ticker(conn: sqlite3.Connection) -> str | None:
    """The ticker the user is currently working on, if any."""
    return get_meta(conn, CURRENT_TICKER)


def set_current_ticker(conn: sqlite3.Connection, ticker: str) -> str:
    normalized = ticker.strip().upper()
    set_meta(conn, CURRENT_TICKER, normalized)
    return normalized


def clear_current_ticker(conn: sqlite3.Connection) -> None:
    clear_meta(conn, CURRENT_TICKER)
