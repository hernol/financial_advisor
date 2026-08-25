"""Persistence of the alert/action suggestions produced by the AI report."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence

from fa.models import Suggestion
from fa.store.database import Database
from fa.store.schema import LOCAL_ACCOUNT_ID
from fa.store.serde import dump_json, row_to_suggestion, to_iso

PENDING = "pending"
ACCEPTED = "accepted"
REJECTED = "rejected"
SKIPPED = "skipped"


def add_suggestions(
    conn: Database,
    suggestions: Sequence[Suggestion],
    *,
    analysis_id: int | None,
    model: str,
    account_id: int = LOCAL_ACCOUNT_ID,
) -> list[Suggestion]:
    """Persist a batch of fresh suggestions as ``pending``."""
    now = to_iso(datetime.now(timezone.utc))
    stored: list[Suggestion] = []
    for suggestion in suggestions:
        new_id = conn.insert(
            "INSERT INTO ai_suggestions(account_id, analysis_id, ticker, category, kind, params, "
            "rationale, priority, status, model, created_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                account_id,
                analysis_id,
                suggestion.ticker.upper(),
                suggestion.category,
                suggestion.kind,
                dump_json(dict(suggestion.params)),
                suggestion.rationale,
                suggestion.priority,
                PENDING,
                model,
                now,
            ),
        )
        stored.append(suggestion.with_id(new_id))
    conn.commit()
    return stored


def list_suggestions(
    conn: Database,
    *,
    ticker: str | None = None,
    status: str | None = PENDING,
    limit: int = 50,
    account_id: int = LOCAL_ACCOUNT_ID,
) -> list[Suggestion]:
    sql = "SELECT * FROM ai_suggestions"
    clauses: list[str] = ["account_id = ?"]
    params: list[object] = [account_id]
    if ticker:
        clauses.append("ticker = ?")
        params.append(ticker.upper())
    if status:
        clauses.append("status = ?")
        params.append(status)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
    params.append(limit)
    return [row_to_suggestion(row) for row in conn.execute(sql, params)]


def resolve(
    conn: Database,
    suggestion_id: int,
    status: str,
    alert_id: int | None = None,
    *,
    account_id: int = LOCAL_ACCOUNT_ID,
) -> bool:
    cur = conn.execute(
        "UPDATE ai_suggestions SET status = ?, alert_id = ?, decided_at = ? "
        "WHERE id = ? AND account_id = ?",
        (status, alert_id, to_iso(datetime.now(timezone.utc)), suggestion_id, account_id),
    )
    conn.commit()
    return cur.rowcount > 0


def pending_count(
    conn: Database, ticker: str | None = None, *, account_id: int = LOCAL_ACCOUNT_ID
) -> int:
    sql = "SELECT COUNT(*) AS total FROM ai_suggestions WHERE status = ? AND account_id = ?"
    params: list[object] = [PENDING, account_id]
    if ticker:
        sql += " AND ticker = ?"
        params.append(ticker.upper())
    return int(conn.execute(sql, params).fetchone()["total"])
