"""Conversion helpers between SQLite rows and immutable domain models."""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from typing import Any, Mapping

from fa.models import Alert, Position, Suggestion


def to_iso(value: datetime | date | None) -> str | None:
    return value.isoformat() if value is not None else None


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value[:10])


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def dump_json(value: Mapping[str, Any] | list[Any]) -> str:
    return json.dumps(value, default=str, sort_keys=True)


def load_json(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def row_to_position(row: sqlite3.Row) -> Position:
    return Position(
        id=row["id"],
        ticker=row["ticker"],
        quantity=row["quantity"],
        buy_price=row["buy_price"],
        buy_date=parse_date(row["buy_date"]),
        currency=row["currency"],
        notes=row["notes"],
        created_at=parse_datetime(row["created_at"]),
        closed_at=parse_datetime(row["closed_at"]),
    )


def row_to_alert(row: sqlite3.Row) -> Alert:
    return Alert(
        id=row["id"],
        position_id=row["position_id"],
        ticker=row["ticker"],
        kind=row["kind"],
        params=load_json(row["params"], {}),
        active=bool(row["active"]),
        one_shot=bool(row["one_shot"]),
        cooldown_hours=row["cooldown_hours"],
        last_fired_at=parse_datetime(row["last_fired_at"]),
        expires_at=parse_date(row["expires_at"]),
        note=row["note"],
        created_at=parse_datetime(row["created_at"]),
    )


def row_to_suggestion(row: sqlite3.Row) -> Suggestion:
    return Suggestion(
        id=row["id"],
        analysis_id=row["analysis_id"],
        ticker=row["ticker"],
        category=row["category"],
        kind=row["kind"],
        params=load_json(row["params"], {}),
        rationale=row["rationale"],
        priority=row["priority"],
        status=row["status"],
        alert_id=row["alert_id"],
        model=row["model"],
        created_at=parse_datetime(row["created_at"]),
        decided_at=parse_datetime(row["decided_at"]),
    )
