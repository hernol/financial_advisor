"""CRUD for alert rules."""
from __future__ import annotations

from datetime import datetime, timezone

from fa.models import Alert
from fa.store.database import Database
from fa.store.schema import LOCAL_ACCOUNT_ID
from fa.store.serde import dump_json, row_to_alert, to_iso


def add_alert(conn: Database, alert: Alert, *, account_id: int = LOCAL_ACCOUNT_ID) -> Alert:
    now = datetime.now(timezone.utc)
    new_id = conn.insert(
        "INSERT INTO alerts(account_id, position_id, ticker, kind, params, active, one_shot, "
        "cooldown_hours, expires_at, note, created_at, updated_at) "
        "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            account_id,
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
            to_iso(now),
        ),
    )
    conn.commit()
    return alert.with_id(new_id)


def list_alerts(
    conn: Database,
    *,
    only_active: bool = False,
    ticker: str | None = None,
    include_deleted: bool = False,
    account_id: int | None = LOCAL_ACCOUNT_ID,
) -> list[Alert]:
    """``account_id=None`` crosses every account, which only the scheduled
    worker is allowed to do."""
    sql = "SELECT * FROM alerts"
    clauses: list[str] = [] if include_deleted else ["deleted_at IS NULL"]
    params: list[object] = []
    if account_id is not None:
        clauses.append("account_id = ?")
        params.append(account_id)
    if only_active:
        clauses.append("active = 1")
    if ticker:
        clauses.append("ticker = ?")
        params.append(ticker.upper())
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY ticker, id"
    return [row_to_alert(row) for row in conn.execute(sql, params)]


def get_alert(
    conn: Database, alert_id: int, *, account_id: int | None = LOCAL_ACCOUNT_ID
) -> Alert | None:
    sql = "SELECT * FROM alerts WHERE id = ?"
    params: list[object] = [alert_id]
    if account_id is not None:
        sql += " AND account_id = ?"
        params.append(account_id)
    row = conn.execute(sql, params).fetchone()
    return row_to_alert(row) if row else None


def mark_fired(conn: Database, alert: Alert, when: datetime | None = None) -> None:
    """Record the firing time and deactivate one-shot alerts."""
    stamp = when or datetime.now(timezone.utc)
    conn.execute(
        "UPDATE alerts SET last_fired_at = ?, active = ?, updated_at = ? WHERE id = ?",
        (to_iso(stamp), 0 if alert.one_shot else int(alert.active), to_iso(stamp), alert.id),
    )
    conn.commit()


def set_active(
    conn: Database, alert_id: int, active: bool, *, account_id: int = LOCAL_ACCOUNT_ID
) -> bool:
    cur = conn.execute(
        "UPDATE alerts SET active = ?, updated_at = ? WHERE id = ? AND account_id = ?",
        (int(active), to_iso(datetime.now(timezone.utc)), alert_id, account_id),
    )
    conn.commit()
    return cur.rowcount > 0


def delete_alert(
    conn: Database, alert_id: int, *, account_id: int = LOCAL_ACCOUNT_ID
) -> bool:
    """Soft delete. The alert stops being evaluated; everything it ever fired
    stays queryable, which a DELETE used to destroy along with it."""
    stamp = to_iso(datetime.now(timezone.utc))
    cur = conn.execute(
        "UPDATE alerts SET active = 0, deleted_at = ?, updated_at = ? "
        "WHERE id = ? AND account_id = ? AND deleted_at IS NULL",
        (stamp, stamp, alert_id, account_id),
    )
    conn.commit()
    return cur.rowcount > 0


def expire_stale(conn: Database, today: datetime | None = None) -> int:
    """Deactivate alerts past their expiry date. Returns how many were expired."""
    stamp = (today or datetime.now(timezone.utc)).date().isoformat()
    cur = conn.execute(
        "UPDATE alerts SET active = 0, updated_at = ? WHERE active = 1 "
        "AND expires_at IS NOT NULL AND expires_at < ?",
        (to_iso(today or datetime.now(timezone.utc)), stamp),
    )
    conn.commit()
    return cur.rowcount
