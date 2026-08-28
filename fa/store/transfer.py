"""Copying a whole database from one engine to another.

Written for the one move that matters: the SQLite file on a laptop becoming the
Postgres the server runs on. It is a table-by-table copy that keeps the primary
keys, because the ids are what every foreign key points at — renumbering would
mean rewriting the references, and a mistake there is silent.

The portability layer already did most of the work. Booleans are 0/1 integers on
both engines and timestamps are ISO strings on both, so there is nothing to
convert. The one real difference is JSON: TEXT on SQLite, JSONB on Postgres, so
those columns are cast on the way in.

Not idempotent, and deliberately so: it refuses to write into a database that
already holds data rather than trying to merge. Re-running a half-finished
migration is a restore-from-scratch, not a resume.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Iterator, Sequence
from typing import Any

from fa.store import migrations
from fa.store.database import POSTGRES, Database
from fa.store.schema import LOCAL_ACCOUNT_ID, SCHEMA

logger = logging.getLogger(__name__)

# Parents before children: every table lands after whatever its foreign keys
# point at. Derived by hand from the schema and pinned by a test, because a
# wrong order here fails loudly on Postgres and silently on SQLite.
TABLE_ORDER: tuple[str, ...] = (
    "accounts",
    "account_users",
    "positions",
    "analyses",
    "check_runs",
    "transactions",
    "alerts",
    "alert_events",
    "alert_evaluations",
    "delivery_attempts",
    "ai_suggestions",
    "portfolio_valuations",
    "daily_bars",
    "indicator_snapshots",
    "data_fetches",
    "price_snapshots",
    "fundamental_snapshots",
)

# The schema version belongs to whoever ran the migrations on the target, not
# to the source. Copying it would let a newer source quietly relabel an older
# target as up to date.
SKIP_TABLES = frozenset({"meta"})

BATCH = 500


def json_columns() -> dict[str, set[str]]:
    """Which columns are JSON, per table, read from the schema itself.

    Parsed rather than listed by hand: a column added later would otherwise be
    copied as a string into a JSONB column and fail at the worst moment.
    """
    found: dict[str, set[str]] = {}
    for block in re.finditer(
        r"CREATE TABLE IF NOT EXISTS (\w+)\s*\((.*?)\n\)", SCHEMA, re.DOTALL
    ):
        table, body = block.group(1), block.group(2)
        columns = {
            line.strip().split()[0]
            for line in body.splitlines()
            if "{JSON}" in line and line.strip() and not line.strip().startswith("--")
        }
        if columns:
            found[table] = columns
    return found


def _rows(db: Database, table: str) -> Iterator[dict[str, Any]]:
    cursor = db.execute(f"SELECT * FROM {table}")
    while True:
        batch = cursor.fetchmany(BATCH)
        if not batch:
            return
        for row in batch:
            yield dict(row)


def _count(db: Database, table: str) -> int:
    return db.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"]


def _existing_ids(db: Database, table: str) -> set[Any]:
    try:
        return {row["id"] for row in db.execute(f"SELECT id FROM {table}")}
    except Exception:  # noqa: BLE001 - a table without an id has none to skip
        return set()


def target_is_empty(db: Database) -> bool:
    """True when the target holds nothing but what the migrations seeded.

    The migrations create the local account, so one row there is expected and
    is not a reason to refuse.
    """
    for table in TABLE_ORDER:
        try:
            count = _count(db, table)
        except Exception as exc:  # noqa: BLE001 - a table the target has not created yet
            logger.debug("no se pudo contar %s en el destino: %s", table, exc)
            continue
        if table == "accounts":
            if count > 1:
                return False
            continue
        if count:
            return False
    return True


def copy_all(
    source: Database,
    target: Database,
    *,
    force: bool = False,
    on_table: Any = None,
) -> dict[str, int]:
    """Copy every table from ``source`` into ``target``. Returns rows per table.

    ``on_table(name, copied, total)`` is called as each table finishes, so a
    caller can show progress on a database where one table holds 25,000 rows
    and the rest hold a handful.
    """
    migrations.run(target)
    if not force and not target_is_empty(target):
        raise ValueError(
            "La base de destino ya tiene datos. Usá --force sólo si estás seguro "
            "de que querés escribir encima."
        )

    json_by_table = json_columns()
    written: dict[str, int] = {}

    for table in TABLE_ORDER:
        if table in SKIP_TABLES:
            continue
        total = _count(source, table)
        if not total:
            written[table] = 0
            if on_table:
                on_table(table, 0, 0)
            continue

        # The local account is seeded by the migrations on both sides with the
        # same fixed id, so copying it would collide on the primary key.
        skip = _existing_ids(target, table) if table == "accounts" else set()
        json_cols = json_by_table.get(table, set())
        copied = 0
        pending: list[dict[str, Any]] = []

        for row in _rows(source, table):
            if row.get("id") in skip and skip:
                continue
            pending.append(row)
            if len(pending) >= BATCH:
                copied += _insert(target, table, pending, json_cols)
                pending = []
        if pending:
            copied += _insert(target, table, pending, json_cols)

        target.commit()
        # Explicit ids leave the identity sequence behind them, so the first
        # row the server writes would collide. Harmless on SQLite.
        if "id" in (pending[0] if pending else {}) or _has_id(source, table):
            migrations.sync_identity(target, table)
        written[table] = copied
        if on_table:
            on_table(table, copied, total)

    target.commit()
    return written


def _has_id(db: Database, table: str) -> bool:
    row = db.execute(f"SELECT * FROM {table}").fetchone()
    return bool(row) and "id" in dict(row)


def _insert(
    target: Database, table: str, rows: Sequence[dict[str, Any]], json_cols: set[str]
) -> int:
    columns = list(rows[0])
    placeholders = ", ".join(
        # Postgres will not take a string for a JSONB column without being told
        # what it is; SQLite stores the same string as text and needs nothing.
        "CAST(? AS JSONB)" if (name in json_cols and target.dialect.name == POSTGRES) else "?"
        for name in columns
    )
    sql = f"INSERT INTO {table}({', '.join(columns)}) VALUES({placeholders})"
    for row in rows:
        target.execute(sql, [_value(row[name], name in json_cols) for name in columns])
    return len(rows)


def _value(value: Any, is_json: bool) -> Any:
    """Postgres hands JSONB back as parsed objects; going the other way it wants
    the text again."""
    if is_json and value is not None and not isinstance(value, (str, bytes)):
        import json

        return json.dumps(value)
    return value


def verify(source: Database, target: Database) -> list[str]:
    """Row counts that do not match, table by table. Empty means they do."""
    problems: list[str] = []
    for table in TABLE_ORDER:
        if table in SKIP_TABLES:
            continue
        try:
            here, there = _count(source, table), _count(target, table)
        except Exception as exc:  # noqa: BLE001 - report rather than crash mid-check
            problems.append(f"{table}: no se pudo contar ({exc})")
            continue
        if table == "accounts":
            # The target seeds the local account itself, so it may legitimately
            # hold one row the source never sent.
            if there < here:
                problems.append(f"{table}: origen {here}, destino {there}")
            continue
        if here != there:
            problems.append(f"{table}: origen {here}, destino {there}")
    return problems


def local_account_present(db: Database) -> bool:
    row = db.execute(
        "SELECT id FROM accounts WHERE id = ?", (LOCAL_ACCOUNT_ID,)
    ).fetchone()
    return row is not None
