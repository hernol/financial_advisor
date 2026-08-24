"""Opening a database, in whichever engine this install is configured for.

Two paths lead to the same schema. A database that does not exist yet gets
:data:`fa.store.schema.SCHEMA` in one shot and is stamped at the current
version; a database that already has tables walks the incremental migrations.
That split is why Postgres never has to replay SQLite's history.

:data:`BASELINE` is the schema exactly as it shipped at version 2. It is frozen
and only ever used by databases created back then, plus the tests that prove
those still upgrade cleanly.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from fa.store import migrations, schema
from fa.store.database import Database, PostgresDatabase, SqliteDatabase, render_ddl

SCHEMA_VERSION = migrations.TARGET_VERSION

BASELINE = """
CREATE TABLE IF NOT EXISTS positions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker        TEXT    NOT NULL,
    quantity      REAL    NOT NULL,
    buy_price     REAL    NOT NULL,
    buy_date      TEXT    NOT NULL,
    currency      TEXT    NOT NULL DEFAULT 'USD',
    notes         TEXT    NOT NULL DEFAULT '',
    created_at    TEXT    NOT NULL,
    closed_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_positions_ticker ON positions(ticker);

CREATE TABLE IF NOT EXISTS alerts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id    INTEGER REFERENCES positions(id) ON DELETE CASCADE,
    ticker         TEXT    NOT NULL,
    kind           TEXT    NOT NULL,
    params         TEXT    NOT NULL DEFAULT '{}',
    active         INTEGER NOT NULL DEFAULT 1,
    one_shot       INTEGER NOT NULL DEFAULT 0,
    cooldown_hours INTEGER NOT NULL DEFAULT 24,
    last_fired_at  TEXT,
    expires_at     TEXT,
    note           TEXT    NOT NULL DEFAULT '',
    created_at     TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alerts_active ON alerts(active, ticker);

CREATE TABLE IF NOT EXISTS alert_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id   INTEGER REFERENCES alerts(id) ON DELETE CASCADE,
    ticker     TEXT NOT NULL,
    kind       TEXT NOT NULL,
    title      TEXT NOT NULL,
    message    TEXT NOT NULL,
    severity   TEXT NOT NULL DEFAULT 'info',
    payload    TEXT NOT NULL DEFAULT '{}',
    delivered  TEXT NOT NULL DEFAULT '[]',
    fired_at   TEXT NOT NULL,
    acknowledged_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_fired ON alert_events(fired_at DESC);

CREATE TABLE IF NOT EXISTS price_snapshots (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker   TEXT NOT NULL,
    price    REAL NOT NULL,
    source   TEXT NOT NULL DEFAULT '',
    taken_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_snapshots_ticker ON price_snapshots(ticker, taken_at DESC);

CREATE TABLE IF NOT EXISTS analyses (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker     TEXT NOT NULL,
    model      TEXT NOT NULL DEFAULT '',
    metrics    TEXT NOT NULL DEFAULT '',
    context    TEXT NOT NULL DEFAULT '',
    report     TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_analyses_ticker ON analyses(ticker, created_at DESC);

CREATE TABLE IF NOT EXISTS ai_suggestions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_id INTEGER REFERENCES analyses(id) ON DELETE SET NULL,
    ticker      TEXT    NOT NULL,
    category    TEXT    NOT NULL DEFAULT 'alert',
    kind        TEXT    NOT NULL DEFAULT '',
    params      TEXT    NOT NULL DEFAULT '{}',
    rationale   TEXT    NOT NULL DEFAULT '',
    priority    TEXT    NOT NULL DEFAULT 'medium',
    status      TEXT    NOT NULL DEFAULT 'pending',
    alert_id    INTEGER REFERENCES alerts(id) ON DELETE SET NULL,
    model       TEXT    NOT NULL DEFAULT '',
    created_at  TEXT    NOT NULL,
    decided_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_suggestions_status ON ai_suggestions(status, ticker);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def is_postgres_url(target: object) -> bool:
    return str(target).startswith(("postgres://", "postgresql://"))


def connect(target: Path | str, *, threadsafe: bool = False) -> Database:
    """Open the database named by a path or a Postgres URL, fully migrated.

    ``threadsafe`` lifts SQLite's same-thread check, which the API server needs
    because uvicorn runs sync endpoints on a worker pool. SQLite itself
    serialises access; what the caller still owes is not interleaving two
    transactions, which the API does with a lock. Leave it off everywhere else:
    the check catches genuine mistakes.
    """
    if is_postgres_url(target):
        db: Database = PostgresDatabase(str(target))
        migrate(db)
        return db

    path = Path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        str(path), detect_types=sqlite3.PARSE_DECLTYPES, check_same_thread=not threadsafe
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    sqlite_db = SqliteDatabase(conn, path)
    migrate(sqlite_db)
    conn.execute("PRAGMA foreign_keys = ON")
    return sqlite_db


def migrate(db: Database) -> None:
    """Bring the database to the current version, whichever state it is in.

    Safe to call on every start: an up-to-date database does no work.
    """
    if _is_empty(db):
        _create(db)
        return
    if not db.table_exists("meta"):
        # A version 2 database predates the meta table only in theory, but a
        # missing stamp must not be read as "brand new" and wipe nothing.
        db.executescript(render_ddl(BASELINE, db.dialect))
    _stamp_if_unstamped(db, migrations.BASELINE_VERSION)
    db.commit()
    migrations.run(db)


def _is_empty(db: Database) -> bool:
    """True when nothing has ever been created here."""
    return not db.table_exists("positions") and not db.table_exists("meta")


def _create(db: Database) -> None:
    """Fresh install: the whole current schema at once, then stamp it."""
    db.executescript(render_ddl(schema.SCHEMA, db.dialect))
    db.commit()
    migrations.ensure_local_account(db)
    db.commit()
    _stamp_if_unstamped(db, migrations.TARGET_VERSION)
    db.commit()


def _stamp_if_unstamped(db: Database, version: int) -> None:
    if migrations.current_version(db) == 0:
        db.execute(
            "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(version),),
        )


@contextmanager
def session(target: Path | str) -> Iterator[Database]:
    """Context manager yielding a ready database, always closed afterwards."""
    db = connect(target)
    try:
        yield db
    finally:
        db.close()
