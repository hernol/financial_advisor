"""Persistence of fired alert events, price snapshots and AI analyses."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from fa.models import Quote, Signal
from fa.store.serde import dump_json, load_json, to_iso


def record_event(conn: sqlite3.Connection, signal: Signal, delivered: Sequence[str]) -> int:
    cur = conn.execute(
        "INSERT INTO alert_events(alert_id, ticker, kind, title, message, severity, payload, delivered, fired_at) "
        "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            signal.alert.id,
            signal.alert.ticker,
            signal.alert.kind,
            signal.title,
            signal.message,
            signal.severity,
            dump_json(dict(signal.payload)),
            dump_json(list(delivered)),
            to_iso(datetime.now(timezone.utc)),
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def recent_events(conn: sqlite3.Connection, limit: int = 20, *, unacknowledged_only: bool = False) -> list[dict[str, Any]]:
    sql = "SELECT * FROM alert_events"
    if unacknowledged_only:
        sql += " WHERE acknowledged_at IS NULL"
    sql += " ORDER BY fired_at DESC LIMIT ?"
    out: list[dict[str, Any]] = []
    for row in conn.execute(sql, (limit,)):
        item = dict(row)
        item["payload"] = load_json(row["payload"], {})
        item["delivered"] = load_json(row["delivered"], [])
        out.append(item)
    return out


def acknowledge_all(conn: sqlite3.Connection) -> int:
    cur = conn.execute(
        "UPDATE alert_events SET acknowledged_at = ? WHERE acknowledged_at IS NULL",
        (to_iso(datetime.now(timezone.utc)),),
    )
    conn.commit()
    return cur.rowcount


def save_snapshot(conn: sqlite3.Connection, quote: Quote) -> None:
    conn.execute(
        "INSERT INTO price_snapshots(ticker, price, source, taken_at) VALUES(?, ?, ?, ?)",
        (quote.ticker, quote.price, quote.source, to_iso(quote.as_of)),
    )
    conn.commit()


def max_snapshot_since(conn: sqlite3.Connection, ticker: str, since: str) -> float | None:
    row = conn.execute(
        "SELECT MAX(price) AS peak FROM price_snapshots WHERE ticker = ? AND taken_at >= ?",
        (ticker.upper(), since),
    ).fetchone()
    return row["peak"] if row and row["peak"] is not None else None


def save_analysis(
    conn: sqlite3.Connection, ticker: str, model: str, metrics: str, context: str, report: str
) -> int:
    cur = conn.execute(
        "INSERT INTO analyses(ticker, model, metrics, context, report, created_at) VALUES(?, ?, ?, ?, ?, ?)",
        (ticker.upper(), model, metrics, context, report, to_iso(datetime.now(timezone.utc))),
    )
    conn.commit()
    return int(cur.lastrowid)


def recent_analyses(conn: sqlite3.Connection, ticker: str | None = None, limit: int = 10) -> list[Mapping[str, Any]]:
    if ticker:
        rows = conn.execute(
            "SELECT * FROM analyses WHERE ticker = ? ORDER BY created_at DESC LIMIT ?",
            (ticker.upper(), limit),
        )
    else:
        rows = conn.execute("SELECT * FROM analyses ORDER BY created_at DESC LIMIT ?", (limit,))
    return [dict(row) for row in rows]
