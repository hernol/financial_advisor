"""SQLite connection handling and schema migrations."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA_VERSION = 2

SCHEMA = """
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


def connect(db_path: Path | str) -> sqlite3.Connection:
    """Open a connection with sane defaults and the schema applied."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    migrate(conn)
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    """Apply the schema; safe to call on every start."""
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()


@contextmanager
def session(db_path: Path | str) -> Iterator[sqlite3.Connection]:
    """Context manager yielding a ready connection, always closed afterwards."""
    conn = connect(db_path)
    try:
        yield conn
    finally:
        conn.close()
