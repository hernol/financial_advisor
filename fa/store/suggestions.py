"""Persistence of the alert/action suggestions produced by the AI report."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence

from fa.models import Suggestion
from fa.store.database import Database
from fa.store.serde import dump_json, row_to_suggestion, to_iso

PENDING = "pending"
ACCEPTED = "accepted"
REJECTED = "rejected"
SKIPPED = "skipped"


def add_suggestions(
    conn: Database, suggestions: Sequence[Suggestion], *, analysis_id: int | None, model: str
) -> list[Suggestion]:
    """Persist a batch of fresh suggestions as ``pending``."""
    now = to_iso(datetime.now(timezone.utc))
    stored: list[Suggestion] = []
    for suggestion in suggestions:
        new_id = conn.insert(
            "INSERT INTO ai_suggestions(analysis_id, ticker, category, kind, params, rationale, "
            "priority, status, model, created_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
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
    conn: Database, *, ticker: str | None = None, status: str | None = PENDING, limit: int = 50
) -> list[Suggestion]:
    sql = "SELECT * FROM ai_suggestions"
    clauses: list[str] = []
    params: list[object] = []
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


def resolve(conn: Database, suggestion_id: int, status: str, alert_id: int | None = None) -> bool:
    cur = conn.execute(
        "UPDATE ai_suggestions SET status = ?, alert_id = ?, decided_at = ? WHERE id = ?",
        (status, alert_id, to_iso(datetime.now(timezone.utc)), suggestion_id),
    )
    conn.commit()
    return cur.rowcount > 0


def pending_count(conn: Database, ticker: str | None = None) -> int:
    sql = "SELECT COUNT(*) AS total FROM ai_suggestions WHERE status = ?"
    params: list[object] = [PENDING]
    if ticker:
        sql += " AND ticker = ?"
        params.append(ticker.upper())
    return int(conn.execute(sql, params).fetchone()["total"])
