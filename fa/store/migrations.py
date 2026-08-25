"""Versioned schema migrations.

The baseline in :mod:`fa.store.db` is frozen at version 2: it is the schema as
it shipped, and it must never change again. Every later evolution is a
migration in ``MIGRATIONS`` below, applied in order and recorded in ``meta``.

Two rules keep historical data safe:

* Migrations only add. Dropping a column or a table needs an explicit decision,
  never a side effect of a refactor.
* No foreign key cascades into history. Deleting an alert used to erase every
  event it had ever fired; those relationships are now ``ON DELETE SET NULL``
  and the rows survive their parent.
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from fa.store.database import SQLITE, Database, render_ddl
from fa.store.schema import LOCAL_ACCOUNT_ID, TENANT_TABLES

logger = logging.getLogger(__name__)

BASELINE_VERSION = 2


@dataclass(frozen=True)
class Migration:
    """One irreversible step forward."""

    version: int
    name: str
    apply: Callable[[Database], None]
    # Versions 3 to 9 rewrite a schema that only ever existed in SQLite, so they
    # are skipped on any other engine, which reaches the same shape through the
    # squashed schema instead.
    sqlite_only: bool = False


def _script(*statements: str) -> Callable[[Database], None]:
    def run(db: Database) -> None:
        for statement in statements:
            db.execute(render_ddl(statement, db.dialect))

    return run


def add_column(db: Database, table: str, column: str, declaration: str) -> None:
    """``ALTER TABLE ADD COLUMN`` that tolerates already having been applied.

    Neither engine rewrites the table for this, so it is cheap even on a large
    database.
    """
    if column in db.columns(table):
        return
    db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {render_ddl(declaration, db.dialect)}")


def sync_identity(db: Database, table: str) -> None:
    """Move a Postgres identity sequence past the rows already inserted.

    Writing an explicit id — which the local account does, because it has to be
    a known constant — leaves ``nextval`` behind it, so the next generated id
    collides. SQLite tracks the maximum itself and needs nothing.
    """
    if db.dialect.name == SQLITE:
        return
    db.execute(
        f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
        f"GREATEST((SELECT COALESCE(MAX(id), 1) FROM {table}), 1))"
    )


def ensure_local_account(db: Database) -> None:
    """Guarantee the account every local install runs under.

    Hosted deployments create one account per sign-up; this one exists so a
    single user install never has to think about accounts at all, and so the
    tenant columns have something valid to default to.

    Does not commit: it runs inside whatever transaction the caller opened.
    """
    row = db.execute("SELECT id FROM accounts WHERE id = ?", (LOCAL_ACCOUNT_ID,)).fetchone()
    if row is not None:
        return
    db.execute(
        "INSERT INTO accounts(id, name, plan, created_at, updated_at) VALUES(?, ?, ?, ?, ?)",
        (
            LOCAL_ACCOUNT_ID,
            "personal",
            "local",
            datetime.now(timezone.utc).isoformat(),
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    sync_identity(db, "accounts")


# --- v3: the transaction ledger -------------------------------------------
# Positions stop being the source of truth and become a rollup. Every buy,
# sell, split and dividend lands here first, so the original cost basis is
# recoverable even after a split rewrites the position.

_TRANSACTIONS = """
CREATE TABLE IF NOT EXISTS transactions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id INTEGER REFERENCES positions(id) ON DELETE SET NULL,
    ticker      TEXT    NOT NULL,
    kind        TEXT    NOT NULL,
    trade_date  TEXT    NOT NULL,
    quantity    REAL,
    price       REAL,
    amount      REAL,
    ratio       REAL,
    fees        REAL    NOT NULL DEFAULT 0,
    currency    TEXT    NOT NULL DEFAULT 'USD',
    note        TEXT    NOT NULL DEFAULT '',
    source      TEXT    NOT NULL DEFAULT 'manual',
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL,
    deleted_at  TEXT
);
"""


def _migrate_ledger(db: Database) -> None:
    db.execute(_TRANSACTIONS)
    db.execute("CREATE INDEX IF NOT EXISTS idx_tx_ticker ON transactions(ticker, trade_date)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_tx_position ON transactions(position_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_tx_updated ON transactions(updated_at)")
    for column, declaration in (
        ("updated_at", "TEXT"),
        ("deleted_at", "TEXT"),
        ("close_price", "REAL"),
        ("close_date", "TEXT"),
        ("realized_pnl", "REAL"),
    ):
        add_column(db, "positions", column, declaration)
    db.execute("UPDATE positions SET updated_at = created_at WHERE updated_at IS NULL")


# --- v4: stop the cascades from erasing history ----------------------------
# ``alert_events.alert_id`` was ON DELETE CASCADE, so deleting an alert deleted
# every event it had fired, and deleting a position cascaded all the way down.
# SQLite cannot alter a foreign key in place, so both tables are rebuilt.

_ALERTS_V4 = """
CREATE TABLE alerts_v4 (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id    INTEGER REFERENCES positions(id) ON DELETE SET NULL,
    ticker         TEXT    NOT NULL,
    kind           TEXT    NOT NULL,
    params         TEXT    NOT NULL DEFAULT '{}',
    active         INTEGER NOT NULL DEFAULT 1,
    one_shot       INTEGER NOT NULL DEFAULT 0,
    cooldown_hours INTEGER NOT NULL DEFAULT 24,
    last_fired_at  TEXT,
    expires_at     TEXT,
    note           TEXT    NOT NULL DEFAULT '',
    created_at     TEXT    NOT NULL,
    updated_at     TEXT,
    deleted_at     TEXT
);
"""

_EVENTS_V4 = """
CREATE TABLE alert_events_v4 (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id        INTEGER REFERENCES alerts(id) ON DELETE SET NULL,
    run_id          INTEGER,
    ticker          TEXT NOT NULL,
    kind            TEXT NOT NULL,
    title           TEXT NOT NULL,
    message         TEXT NOT NULL,
    severity        TEXT NOT NULL DEFAULT 'info',
    payload         TEXT NOT NULL DEFAULT '{}',
    delivered       TEXT NOT NULL DEFAULT '[]',
    price           REAL,
    fired_at        TEXT NOT NULL,
    acknowledged_at TEXT
);
"""


def _migrate_no_cascade(db: Database) -> None:
    db.execute(_ALERTS_V4)
    db.execute(
        "INSERT INTO alerts_v4(id, position_id, ticker, kind, params, active, one_shot, "
        "cooldown_hours, last_fired_at, expires_at, note, created_at, updated_at) "
        "SELECT id, position_id, ticker, kind, params, active, one_shot, cooldown_hours, "
        "last_fired_at, expires_at, note, created_at, created_at FROM alerts"
    )
    db.execute("DROP TABLE alerts")
    db.execute("ALTER TABLE alerts_v4 RENAME TO alerts")
    db.execute("CREATE INDEX IF NOT EXISTS idx_alerts_active ON alerts(active, ticker)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_alerts_updated ON alerts(updated_at)")

    db.execute(_EVENTS_V4)
    db.execute(
        "INSERT INTO alert_events_v4(id, alert_id, ticker, kind, title, message, severity, "
        "payload, delivered, fired_at, acknowledged_at) "
        "SELECT id, alert_id, ticker, kind, title, message, severity, payload, delivered, "
        "fired_at, acknowledged_at FROM alert_events"
    )
    db.execute("DROP TABLE alert_events")
    db.execute("ALTER TABLE alert_events_v4 RENAME TO alert_events")
    db.execute("CREATE INDEX IF NOT EXISTS idx_events_fired ON alert_events(fired_at DESC)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_events_alert ON alert_events(alert_id, fired_at DESC)")


# --- v5: market history ----------------------------------------------------
# The providers already download full OHLCV bars on every run and the process
# used to drop them on exit. Bars are immutable once the session closes, so an
# upsert keyed by (ticker, day) is all that is needed.

_MARKET_HISTORY = (
    """
    CREATE TABLE IF NOT EXISTS daily_bars (
        ticker     TEXT NOT NULL,
        day        TEXT NOT NULL,
        open       REAL,
        high       REAL,
        low        REAL,
        close      REAL NOT NULL,
        volume     REAL,
        source     TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL,
        PRIMARY KEY (ticker, day)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_bars_day ON daily_bars(day DESC)",
    """
    CREATE TABLE IF NOT EXISTS indicator_snapshots (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker          TEXT NOT NULL,
        taken_at        TEXT NOT NULL,
        price           REAL NOT NULL,
        rsi             REAL,
        sma_fast        REAL,
        sma_slow        REAL,
        vs_sma_slow_pct REAL,
        macd_histogram  REAL,
        percent_b       REAL,
        atr             REAL,
        atr_pct         REAL,
        volatility_pct  REAL,
        volume_ratio    REAL,
        from_high_pct   REAL,
        from_low_pct    REAL,
        max_drawdown_pct REAL,
        rel_strength_3m REAL,
        trend           TEXT NOT NULL DEFAULT '',
        payload         TEXT NOT NULL DEFAULT '{}',
        run_id          INTEGER
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_indicators_ticker ON indicator_snapshots(ticker, taken_at DESC)",
)


# --- v6: what the system did ----------------------------------------------
# Every check run is recorded even when nothing fires, which is the difference
# between "the market was quiet" and "the daemon has been dead for three days".
# Each evaluation is recorded too, so a rule that never fires still leaves a
# trace of how close it came.

_OPERATIONS = (
    """
    CREATE TABLE IF NOT EXISTS check_runs (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        trigger          TEXT NOT NULL DEFAULT 'manual',
        ticker           TEXT,
        started_at       TEXT NOT NULL,
        finished_at      TEXT,
        duration_ms      INTEGER,
        checked          INTEGER NOT NULL DEFAULT 0,
        fired            INTEGER NOT NULL DEFAULT 0,
        skipped_cooldown INTEGER NOT NULL DEFAULT 0,
        expired          INTEGER NOT NULL DEFAULT 0,
        errors           TEXT NOT NULL DEFAULT '[]',
        ok               INTEGER NOT NULL DEFAULT 1
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_runs_started ON check_runs(started_at DESC)",
    """
    CREATE TABLE IF NOT EXISTS alert_evaluations (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id      INTEGER REFERENCES check_runs(id) ON DELETE SET NULL,
        alert_id    INTEGER REFERENCES alerts(id) ON DELETE SET NULL,
        ticker      TEXT NOT NULL,
        kind        TEXT NOT NULL,
        outcome     TEXT NOT NULL,
        price       REAL,
        detail      TEXT NOT NULL DEFAULT '{}',
        evaluated_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_evals_alert ON alert_evaluations(alert_id, evaluated_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_evals_run ON alert_evaluations(run_id)",
    """
    CREATE TABLE IF NOT EXISTS delivery_attempts (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id     INTEGER REFERENCES alert_events(id) ON DELETE SET NULL,
        channel      TEXT NOT NULL,
        status       TEXT NOT NULL,
        error        TEXT NOT NULL DEFAULT '',
        attempted_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_delivery_event ON delivery_attempts(event_id)",
)


# --- v7: portfolio and provenance -----------------------------------------
# The equity curve nobody was recording, plus which provider actually answered
# and whether a fallback was used.

_PORTFOLIO = (
    """
    CREATE TABLE IF NOT EXISTS portfolio_valuations (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        taken_at    TEXT NOT NULL,
        day         TEXT NOT NULL,
        cost_basis  REAL NOT NULL,
        market_value REAL NOT NULL,
        pnl_abs     REAL NOT NULL,
        pnl_pct     REAL NOT NULL,
        positions   INTEGER NOT NULL DEFAULT 0,
        currency    TEXT NOT NULL DEFAULT 'USD',
        holdings    TEXT NOT NULL DEFAULT '[]'
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_valuations_day ON portfolio_valuations(day DESC)",
    """
    CREATE TABLE IF NOT EXISTS data_fetches (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker     TEXT NOT NULL,
        kind       TEXT NOT NULL,
        provider   TEXT NOT NULL DEFAULT '',
        ok         INTEGER NOT NULL DEFAULT 1,
        error      TEXT NOT NULL DEFAULT '',
        duration_ms INTEGER,
        fetched_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_fetches_ticker ON data_fetches(ticker, fetched_at DESC)",
)


# --- v8: sync bookkeeping --------------------------------------------------
# A mobile client cannot refetch everything on every open: it needs to ask for
# what changed since a cursor. That requires updated_at on every readable table
# and a soft delete instead of a DELETE.

def _migrate_sync(db: Database) -> None:
    for table in ("analyses", "ai_suggestions", "alert_events"):
        add_column(db, table, "updated_at", "TEXT")
        add_column(db, table, "deleted_at", "TEXT")
    db.execute("UPDATE analyses SET updated_at = created_at WHERE updated_at IS NULL")
    db.execute("UPDATE ai_suggestions SET updated_at = created_at WHERE updated_at IS NULL")
    db.execute("UPDATE alert_events SET updated_at = fired_at WHERE updated_at IS NULL")
    db.execute("CREATE INDEX IF NOT EXISTS idx_analyses_updated ON analyses(updated_at)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_suggestions_updated ON ai_suggestions(updated_at)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_events_updated ON alert_events(updated_at)")
    add_column(db, "price_snapshots", "change_pct", "REAL")
    add_column(db, "price_snapshots", "previous_close", "REAL")



# --- v9: give existing positions the ledger entry they never had -----------
# Positions created before v3 have no opening buy, so a replay would report a
# holding of zero. This reconstructs it from the rollup itself, which is the
# only record those positions ever had.

def _migrate_backfill(db: Database) -> None:
    rows = db.execute(
        "SELECT p.id, p.ticker, p.quantity, p.buy_price, p.buy_date, p.currency, p.notes, "
        "p.created_at FROM positions p LEFT JOIN transactions t "
        "ON t.position_id = p.id AND t.kind = 'buy' WHERE t.id IS NULL"
    ).fetchall()
    for row in rows:
        db.execute(
            "INSERT INTO transactions(position_id, ticker, kind, trade_date, quantity, price, "
            "currency, note, source, created_at, updated_at) "
            "VALUES(?, ?, 'buy', ?, ?, ?, ?, ?, 'backfill', ?, ?)",
            (
                row["id"],
                row["ticker"],
                row["buy_date"],
                row["quantity"],
                row["buy_price"],
                row["currency"],
                row["notes"],
                row["created_at"],
                row["created_at"],
            ),
        )
    # A position closed before v3 also lost its sale; only the ones that
    # recorded a price can be reconstructed, the rest stay unknown.
    sold = db.execute(
        "SELECT p.id, p.ticker, p.quantity, p.close_price, p.close_date, p.closed_at, p.currency "
        "FROM positions p LEFT JOIN transactions t "
        "ON t.position_id = p.id AND t.kind = 'sell' "
        "WHERE t.id IS NULL AND p.close_price IS NOT NULL"
    ).fetchall()
    for row in sold:
        db.execute(
            "INSERT INTO transactions(position_id, ticker, kind, trade_date, quantity, price, "
            "currency, source, created_at, updated_at) "
            "VALUES(?, ?, 'sell', ?, ?, ?, ?, 'backfill', ?, ?)",
            (
                row["id"],
                row["ticker"],
                row["close_date"] or row["closed_at"][:10],
                row["quantity"],
                row["close_price"],
                row["currency"],
                row["closed_at"],
                row["closed_at"],
            ),
        )



# --- v10: multi-tenancy ----------------------------------------------------
# Every personal table gains an account_id and existing rows are adopted into
# the local account. On a hosted deployment this column is what row level
# security keys on; on a laptop it is always 1 and never surfaces.

_ACCOUNTS = """
CREATE TABLE IF NOT EXISTS accounts (
    id          {ID_PK},
    name        TEXT NOT NULL DEFAULT 'personal',
    owner_email TEXT NOT NULL DEFAULT '',
    plan        TEXT NOT NULL DEFAULT 'local',
    created_at  TEXT NOT NULL,
    updated_at  TEXT,
    deleted_at  TEXT
);
"""

_ACCOUNT_USERS = """
CREATE TABLE IF NOT EXISTS account_users (
    id         {ID_PK},
    account_id {FK_ID} NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    auth_id    TEXT NOT NULL,
    email      TEXT NOT NULL DEFAULT '',
    role       TEXT NOT NULL DEFAULT 'owner',
    created_at TEXT NOT NULL
);
"""


_TENANT_INDEXES = (
    ("idx_positions_ticker", "positions(account_id, ticker)"),
    ("idx_tx_ticker", "transactions(account_id, ticker, trade_date)"),
    ("idx_alerts_active", "alerts(account_id, active, ticker)"),
    ("idx_events_fired", "alert_events(account_id, fired_at DESC)"),
    ("idx_runs_started", "check_runs(account_id, started_at DESC)"),
    ("idx_valuations_day", "portfolio_valuations(account_id, day DESC)"),
    ("idx_analyses_ticker", "analyses(account_id, ticker, created_at DESC)"),
    ("idx_suggestions_status", "ai_suggestions(account_id, status, ticker)"),
)


def _migrate_tenancy(db: Database) -> None:
    db.execute(render_ddl(_ACCOUNTS, db.dialect))
    db.execute(render_ddl(_ACCOUNT_USERS, db.dialect))
    db.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_account_users_auth ON account_users(auth_id)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_account_users_account ON account_users(account_id)"
    )
    ensure_local_account(db)
    for table in TENANT_TABLES:
        if not db.table_exists(table):
            continue
        add_column(db, table, "account_id", "{FK_ID} NOT NULL DEFAULT 1")
        db.execute(
            f"UPDATE {table} SET account_id = ? WHERE account_id IS NULL",
            (LOCAL_ACCOUNT_ID,),
        )
    # Every lookup is now scoped to one account, so account_id has to lead each
    # composite index. Rebuilding them here is also what keeps an upgraded
    # database byte-identical to one created fresh from the squashed schema.
    for name, definition in _TENANT_INDEXES:
        db.execute(f"DROP INDEX IF EXISTS {name}")
        db.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {definition}")


# --- v11: corrections -------------------------------------------------------
# Editing a ledger entry in place would make a corrected typo and a rewritten
# number look identical afterwards. A correction is therefore a new entry that
# points at the one it replaces, and the original stays on file, retired.

def _migrate_corrections(db: Database) -> None:
    add_column(db, "transactions", "replaces_id", "{FK_ID}")
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_tx_replaces ON transactions(replaces_id)"
    )


MIGRATIONS: tuple[Migration, ...] = (
    Migration(3, "transaction ledger", _migrate_ledger, sqlite_only=True),
    Migration(4, "drop destructive cascades", _migrate_no_cascade, sqlite_only=True),
    Migration(5, "persist bars and indicators", _script(*_MARKET_HISTORY), sqlite_only=True),
    Migration(6, "record runs, evaluations and deliveries", _script(*_OPERATIONS), sqlite_only=True),
    Migration(7, "portfolio valuations and provenance", _script(*_PORTFOLIO), sqlite_only=True),
    Migration(8, "sync cursors and soft deletes", _migrate_sync, sqlite_only=True),
    Migration(9, "backfill the ledger from existing positions", _migrate_backfill, sqlite_only=True),
    Migration(10, "accounts and the account_id column", _migrate_tenancy),
    Migration(11, "corrections point at what they replace", _migrate_corrections),
)

TARGET_VERSION = max(m.version for m in MIGRATIONS)


def current_version(db: Database) -> int:
    if not db.table_exists("meta"):
        return 0
    row = db.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    if row is None:
        return 0
    try:
        return int(row["value"])
    except (TypeError, ValueError):
        return 0


def pending(db: Database) -> tuple[Migration, ...]:
    """Migrations this database still needs, minus those its engine skips."""
    version = current_version(db)
    return tuple(
        m
        for m in MIGRATIONS
        if m.version > version and (db.dialect.name == SQLITE or not m.sqlite_only)
    )


def backup(path: Path, version: int) -> Path | None:
    """Snapshot the database before the first schema change of a run.

    ``VACUUM INTO`` writes a consistent copy even with WAL pages outstanding,
    which a plain file copy would miss.
    """
    if not path.exists() or path.stat().st_size == 0:
        return None
    target = path.with_name(f"{path.name}.pre-v{version}.bak")
    if target.exists():
        target.unlink()
    with sqlite3.connect(str(path)) as source:
        source.execute("VACUUM INTO ?", (str(target),))
    return target


def run(db: Database, path: Path | None = None) -> tuple[Migration, ...]:
    """Apply every pending migration in order. Returns the ones applied."""
    todo = pending(db)
    if not todo:
        return ()

    sqlite = db.dialect.name == SQLITE
    target = path or getattr(db, "path", None)
    if sqlite and target is not None:
        saved = backup(Path(target), todo[0].version)
        if saved is not None:
            logger.info("database backed up to %s", saved)

    raw = getattr(db, "raw", None)
    previous_isolation = getattr(raw, "isolation_level", None) if sqlite else None
    if sqlite:
        # Explicit transaction control: a PRAGMA is a no-op inside a transaction,
        # and rebuilding a table needs foreign keys off while it happens.
        raw.isolation_level = None
        db.execute("PRAGMA foreign_keys = OFF")
    try:
        for migration in todo:
            # SQLite is in autocommit for the duration of the run, so the
            # transaction is opened by hand; psycopg already has one open.
            if sqlite:
                db.execute("BEGIN")
            try:
                migration.apply(db)
                db.execute(
                    "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (str(migration.version),),
                )
                if sqlite:
                    db.execute("COMMIT")
                else:
                    db.commit()
            except Exception:
                if sqlite:
                    db.execute("ROLLBACK")
                else:
                    db.rollback()
                logger.error("migration v%s (%s) failed", migration.version, migration.name)
                raise
            logger.info("applied migration v%s: %s", migration.version, migration.name)
        if sqlite:
            violations = db.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise sqlite3.IntegrityError(
                    f"foreign key check failed after migrating: {[tuple(v) for v in violations]}"
                )
    finally:
        if sqlite:
            db.execute("PRAGMA foreign_keys = ON")
            raw.isolation_level = previous_isolation
    return todo
