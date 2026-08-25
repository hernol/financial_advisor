"""Persistence of fired alert events, price snapshots and AI analyses."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from fa.models import Quote, Signal
from fa.store.database import Database
from fa.store.schema import LOCAL_ACCOUNT_ID
from fa.store.serde import dump_json, load_json, to_iso


def record_event(
    conn: Database,
    signal: Signal,
    delivered: Sequence[str],
    *,
    run_id: int | None = None,
    price: float | None = None,
    account_id: int = LOCAL_ACCOUNT_ID,
) -> int:
    stamp = to_iso(datetime.now(timezone.utc))
    new_id = conn.insert(
        "INSERT INTO alert_events(account_id, alert_id, run_id, ticker, kind, title, message, "
        "severity, payload, delivered, price, fired_at, updated_at) "
        "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            account_id,
            signal.alert.id,
            run_id,
            signal.alert.ticker,
            signal.alert.kind,
            signal.title,
            signal.message,
            signal.severity,
            dump_json(dict(signal.payload)),
            dump_json(list(delivered)),
            price,
            stamp,
            stamp,
        ),
    )
    conn.commit()
    return new_id


def recent_events(
    conn: Database,
    limit: int = 20,
    *,
    unacknowledged_only: bool = False,
    account_id: int = LOCAL_ACCOUNT_ID,
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM alert_events WHERE deleted_at IS NULL AND account_id = ?"
    if unacknowledged_only:
        sql += " AND acknowledged_at IS NULL"
    sql += " ORDER BY fired_at DESC LIMIT ?"
    out: list[dict[str, Any]] = []
    for row in conn.execute(sql, (account_id, limit)):
        item = dict(row)
        item["payload"] = load_json(row["payload"], {})
        item["delivered"] = load_json(row["delivered"], [])
        out.append(item)
    return out


def acknowledge_all(conn: Database, *, account_id: int = LOCAL_ACCOUNT_ID) -> int:
    stamp = to_iso(datetime.now(timezone.utc))
    cur = conn.execute(
        "UPDATE alert_events SET acknowledged_at = ?, updated_at = ? "
        "WHERE acknowledged_at IS NULL AND account_id = ?",
        (stamp, stamp, account_id),
    )
    conn.commit()
    return cur.rowcount


def acknowledge(conn: Database, event_id: int, *, account_id: int = LOCAL_ACCOUNT_ID) -> bool:
    """Mark a single event as seen, which is what a phone actually does."""
    stamp = to_iso(datetime.now(timezone.utc))
    cur = conn.execute(
        "UPDATE alert_events SET acknowledged_at = ?, updated_at = ? "
        "WHERE id = ? AND account_id = ? AND acknowledged_at IS NULL",
        (stamp, stamp, event_id, account_id),
    )
    conn.commit()
    return cur.rowcount > 0


def events_for_alert(
    conn: Database, alert_id: int, limit: int = 100, *, account_id: int = LOCAL_ACCOUNT_ID
) -> list[dict[str, Any]]:
    """Firing history of one alert; survives the alert being deleted."""
    rows = conn.execute(
        "SELECT * FROM alert_events WHERE alert_id = ? AND account_id = ? "
        "AND deleted_at IS NULL ORDER BY fired_at DESC LIMIT ?",
        (alert_id, account_id, limit),
    )
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["payload"] = load_json(row["payload"], {})
        item["delivered"] = load_json(row["delivered"], [])
        out.append(item)
    return out


def save_snapshot(conn: Database, quote: Quote) -> None:
    conn.execute(
        "INSERT INTO price_snapshots(ticker, price, source, taken_at, change_pct, previous_close) "
        "VALUES(?, ?, ?, ?, ?, ?)",
        (
            quote.ticker,
            quote.price,
            quote.source,
            to_iso(quote.as_of),
            quote.change_pct,
            quote.previous_close,
        ),
    )
    conn.commit()


def max_snapshot_since(conn: Database, ticker: str, since: str) -> float | None:
    row = conn.execute(
        "SELECT MAX(price) AS peak FROM price_snapshots WHERE ticker = ? AND taken_at >= ?",
        (ticker.upper(), since),
    ).fetchone()
    return row["peak"] if row and row["peak"] is not None else None


def save_analysis(
    conn: Database,
    ticker: str,
    model: str,
    metrics: str,
    context: str,
    report: str,
    *,
    account_id: int = LOCAL_ACCOUNT_ID,
) -> int:
    stamp = to_iso(datetime.now(timezone.utc))
    new_id = conn.insert(
        "INSERT INTO analyses(account_id, ticker, model, metrics, context, report, created_at, "
        "updated_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
        (account_id, ticker.upper(), model, metrics, context, report, stamp, stamp),
    )
    conn.commit()
    return new_id


def recent_analyses(
    conn: Database,
    ticker: str | None = None,
    limit: int = 10,
    *,
    account_id: int = LOCAL_ACCOUNT_ID,
) -> list[Mapping[str, Any]]:
    if ticker:
        rows = conn.execute(
            "SELECT * FROM analyses WHERE ticker = ? AND account_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (ticker.upper(), account_id, limit),
        )
    else:
        rows = conn.execute(
            "SELECT * FROM analyses WHERE account_id = ? ORDER BY created_at DESC LIMIT ?",
            (account_id, limit),
        )
    return [dict(row) for row in rows]
