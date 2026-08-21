"""CRUD for alert rules."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from fa.models import Alert
from fa.store.serde import dump_json, row_to_alert, to_iso


def add_alert(conn: sqlite3.Connection, alert: Alert) -> Alert:
    now = datetime.now(timezone.utc)
    cur = conn.execute(
        "INSERT INTO alerts(position_id, ticker, kind, params, active, one_shot, cooldown_hours, "
        "expires_at, note, created_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            alert.position_id,
            alert.ticker.upper(),
            alert.kind,
            dump_json(dict(alert.params)),
            int(alert.active),
            int(alert.one_shot),
            alert.cooldown_hours,
            to_iso(alert.expires_at),
            alert.note,
            to_iso(now),
        ),
    )
    conn.commit()
    return alert.with_id(int(cur.lastrowid))


def list_alerts(conn: sqlite3.Connection, *, only_active: bool = False, ticker: str | None = None) -> list[Alert]:
    sql = "SELECT * FROM alerts"
    clauses: list[str] = []
    params: list[object] = []
    if only_active:
        clauses.append("active = 1")
    if ticker:
        clauses.append("ticker = ?")
        params.append(ticker.upper())
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY ticker, id"
    return [row_to_alert(row) for row in conn.execute(sql, params)]


def get_alert(conn: sqlite3.Connection, alert_id: int) -> Alert | None:
    row = conn.execute("SELECT * FROM alerts WHERE id = ?", (alert_id,)).fetchone()
    return row_to_alert(row) if row else None


def mark_fired(conn: sqlite3.Connection, alert: Alert, when: datetime | None = None) -> None:
    """Record the firing time and deactivate one-shot alerts."""
    stamp = when or datetime.now(timezone.utc)
    conn.execute(
        "UPDATE alerts SET last_fired_at = ?, active = ? WHERE id = ?",
        (to_iso(stamp), 0 if alert.one_shot else int(alert.active), alert.id),
    )
    conn.commit()


def set_active(conn: sqlite3.Connection, alert_id: int, active: bool) -> bool:
    cur = conn.execute("UPDATE alerts SET active = ? WHERE id = ?", (int(active), alert_id))
    conn.commit()
    return cur.rowcount > 0


def delete_alert(conn: sqlite3.Connection, alert_id: int) -> bool:
    cur = conn.execute("DELETE FROM alerts WHERE id = ?", (alert_id,))
    conn.commit()
    return cur.rowcount > 0


def expire_stale(conn: sqlite3.Connection, today: datetime | None = None) -> int:
    """Deactivate alerts past their expiry date. Returns how many were expired."""
    stamp = (today or datetime.now(timezone.utc)).date().isoformat()
    cur = conn.execute(
        "UPDATE alerts SET active = 0 WHERE active = 1 AND expires_at IS NOT NULL AND expires_at < ?",
        (stamp,),
    )
    conn.commit()
    return cur.rowcount
