"""Conversion helpers between database rows and immutable domain models."""
from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any, Mapping

from fa.models import Alert, Position, Suggestion, Transaction
from fa.store.database import Row


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


def load_json(value: Any, fallback: Any) -> Any:
    """Decode a stored JSON payload, whichever engine handed it over.

    SQLite keeps these columns as TEXT and returns a string; Postgres types them
    JSONB and psycopg returns the decoded object already. Both arrive here.
    """
    if value is None or value == "":
        return fallback
    if not isinstance(value, (str, bytes, bytearray)):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def _optional(row: Row, column: str) -> Any:
    """Read a column that older rows may predate."""
    return row[column] if column in row.keys() else None


def row_to_position(row: Row) -> Position:
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
        close_price=_optional(row, "close_price"),
        close_date=parse_date(_optional(row, "close_date")),
        realized_pnl=_optional(row, "realized_pnl"),
    )


def row_to_alert(row: Row) -> Alert:
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


def row_to_suggestion(row: Row) -> Suggestion:
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


def row_to_transaction(row: Row) -> Transaction:
    return Transaction(
        id=row["id"],
        position_id=row["position_id"],
        ticker=row["ticker"],
        kind=row["kind"],
        trade_date=parse_date(row["trade_date"]),
        quantity=row["quantity"],
        price=row["price"],
        amount=row["amount"],
        ratio=row["ratio"],
        fees=row["fees"],
        currency=row["currency"],
        note=row["note"],
        source=row["source"],
        created_at=parse_datetime(row["created_at"]),
        updated_at=parse_datetime(row["updated_at"]),
    )
