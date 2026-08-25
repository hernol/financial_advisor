"""The operational log: what the system did, whether or not anything fired.

A run that finds nothing still writes a row. That is the whole point: without
it there is no way to tell a quiet market from a scheduler that died three days
ago, which is the first question any dashboard has to answer.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from fa.store.database import Database
from fa.store.schema import LOCAL_ACCOUNT_ID
from fa.store.serde import dump_json, load_json, to_iso

FIRED = "fired"
QUIET = "quiet"
COOLDOWN = "cooldown"
ERROR = "error"
SKIPPED = "skipped"

SENT = "sent"
FAILED = "failed"


def start_run(
    conn: Database,
    *,
    trigger: str = "manual",
    ticker: str | None = None,
    started_at: datetime | None = None,
    account_id: int = LOCAL_ACCOUNT_ID,
) -> int:
    new_id = conn.insert(
        "INSERT INTO check_runs(account_id, trigger, ticker, started_at) VALUES(?, ?, ?, ?)",
        (
            account_id,
            trigger,
            ticker.upper() if ticker else None,
            to_iso(started_at or datetime.now(timezone.utc)),
        ),
    )
    conn.commit()
    return new_id


def finish_run(
    conn: Database,
    run_id: int,
    *,
    checked: int,
    fired: int,
    skipped_cooldown: int,
    expired: int,
    errors: Sequence[str] = (),
    finished_at: datetime | None = None,
) -> None:
    moment = finished_at or datetime.now(timezone.utc)
    row = conn.execute("SELECT started_at FROM check_runs WHERE id = ?", (run_id,)).fetchone()
    duration = None
    if row is not None:
        started = datetime.fromisoformat(row["started_at"])
        duration = int((moment - started).total_seconds() * 1000)
    conn.execute(
        "UPDATE check_runs SET finished_at = ?, duration_ms = ?, checked = ?, fired = ?, "
        "skipped_cooldown = ?, expired = ?, errors = ?, ok = ? WHERE id = ?",
        (
            to_iso(moment),
            duration,
            checked,
            fired,
            skipped_cooldown,
            expired,
            dump_json(list(errors)),
            0 if errors else 1,
            run_id,
        ),
    )
    conn.commit()


def record_evaluation(
    conn: Database,
    *,
    run_id: int | None,
    alert_id: int | None,
    ticker: str,
    kind: str,
    outcome: str,
    price: float | None = None,
    detail: Mapping[str, Any] | None = None,
    evaluated_at: datetime | None = None,
    account_id: int = LOCAL_ACCOUNT_ID,
) -> int:
    """One alert, one moment, one outcome — including the times it stayed quiet."""
    new_id = conn.insert(
        "INSERT INTO alert_evaluations(account_id, run_id, alert_id, ticker, kind, outcome, "
        "price, detail, evaluated_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            account_id,
            run_id,
            alert_id,
            ticker.upper(),
            kind,
            outcome,
            price,
            dump_json(dict(detail or {})),
            to_iso(evaluated_at or datetime.now(timezone.utc)),
        ),
    )
    conn.commit()
    return new_id


def record_delivery(
    conn: Database,
    *,
    event_id: int | None,
    channel: str,
    status: str,
    error: str = "",
    attempted_at: datetime | None = None,
    account_id: int = LOCAL_ACCOUNT_ID,
) -> int:
    new_id = conn.insert(
        "INSERT INTO delivery_attempts(account_id, event_id, channel, status, error, attempted_at) "
        "VALUES(?, ?, ?, ?, ?, ?)",
        (
            account_id,
            event_id,
            channel,
            status,
            error,
            to_iso(attempted_at or datetime.now(timezone.utc)),
        ),
    )
    conn.commit()
    return new_id


def record_fetch(
    conn: Database,
    *,
    ticker: str,
    kind: str,
    provider: str = "",
    ok: bool = True,
    error: str = "",
    duration_ms: int | None = None,
    fetched_at: datetime | None = None,
) -> int:
    """Which provider answered, and whether the chain had to fall back."""
    new_id = conn.insert(
        "INSERT INTO data_fetches(ticker, kind, provider, ok, error, duration_ms, fetched_at) "
        "VALUES(?, ?, ?, ?, ?, ?, ?)",
        (
            ticker.upper(),
            kind,
            provider,
            int(ok),
            error,
            duration_ms,
            to_iso(fetched_at or datetime.now(timezone.utc)),
        ),
    )
    conn.commit()
    return new_id


def recent_runs(
    conn: Database, limit: int = 50, *, account_id: int = LOCAL_ACCOUNT_ID
) -> list[Mapping[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM check_runs WHERE account_id = ? ORDER BY started_at DESC LIMIT ?",
        (account_id, limit),
    )
    out = []
    for row in rows:
        item = dict(row)
        item["errors"] = load_json(row["errors"], [])
        out.append(item)
    return out


def last_run(
    conn: Database, *, account_id: int = LOCAL_ACCOUNT_ID
) -> Mapping[str, Any] | None:
    runs = recent_runs(conn, limit=1, account_id=account_id)
    return runs[0] if runs else None


def evaluations_for(
    conn: Database, alert_id: int, *, limit: int = 200, account_id: int = LOCAL_ACCOUNT_ID
) -> list[Mapping[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM alert_evaluations WHERE alert_id = ? AND account_id = ? "
        "ORDER BY evaluated_at DESC LIMIT ?",
        (alert_id, account_id, limit),
    )
    out = []
    for row in rows:
        item = dict(row)
        item["detail"] = load_json(row["detail"], {})
        out.append(item)
    out.reverse()
    return out


def health(conn: Database, *, account_id: int = LOCAL_ACCOUNT_ID) -> Mapping[str, Any]:
    """Everything the dashboard's status badge needs in one query set."""
    latest = last_run(conn, account_id=account_id)
    # Timestamps are stored as ISO 8601 text on both engines, so the cutoff is
    # computed here and compared as a string: no date functions, no dialects.
    # SUM over a comparison would also differ — `ok = 0` is a boolean on
    # Postgres and SUM(boolean) does not exist — so CASE says it for both.
    cutoff = to_iso(datetime.now(timezone.utc) - timedelta(days=7))
    row = conn.execute(
        "SELECT COUNT(*) AS runs, SUM(fired) AS fired, "
        "SUM(CASE WHEN ok = 0 THEN 1 ELSE 0 END) AS failed "
        "FROM check_runs WHERE started_at >= ? AND account_id = ?",
        (cutoff, account_id),
    ).fetchone()
    return {
        "last_run_at": latest["started_at"] if latest else None,
        "last_run_ok": bool(latest["ok"]) if latest else None,
        "last_run_errors": latest["errors"] if latest else [],
        "runs_7d": (row["runs"] if row else 0) or 0,
        "fired_7d": (row["fired"] if row else 0) or 0,
        "failed_7d": (row["failed"] if row else 0) or 0,
    }
