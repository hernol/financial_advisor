"""The migration engine, exercised against a database built the old way."""
from __future__ import annotations

import sqlite3

import pytest

from fa.store import migrations
from fa.store.database import SQLITE_DIALECT, SqliteDatabase, render_ddl
from fa.store.db import BASELINE, connect

# The schema exactly as it shipped at version 2, kept here so the tests can
# build a legacy database and prove it survives the upgrade.
LEGACY_ALERTS = """
CREATE TABLE alerts (
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
"""


def legacy_database(path):
    """A version 2 database with a position, an alert and a fired event."""
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(render_ddl(BASELINE, SQLITE_DIALECT))
    conn.execute(
        "INSERT INTO positions(id, ticker, quantity, buy_price, buy_date, currency, notes, created_at) "
        "VALUES(1, 'PODD', 10, 100.0, '2026-01-15', 'USD', '', '2026-01-15T00:00:00+00:00')"
    )
    conn.execute(
        "INSERT INTO alerts(id, position_id, ticker, kind, params, created_at) "
        "VALUES(1, 1, 'PODD', 'pct_up', '{\"pct\": 10}', '2026-01-15T00:00:00+00:00')"
    )
    conn.execute(
        "INSERT INTO alert_events(id, alert_id, ticker, kind, title, message, fired_at) "
        "VALUES(1, 1, 'PODD', 'pct_up', 'subió', 'subió 10%', '2026-02-01T00:00:00+00:00')"
    )
    conn.execute(
        "INSERT INTO meta(key, value) VALUES('schema_version', '2')"
    )
    conn.commit()
    conn.close()


def test_a_fresh_database_lands_on_the_target_version(tmp_path):
    conn = connect(tmp_path / "fresh.db")
    assert migrations.current_version(conn) == migrations.TARGET_VERSION
    assert migrations.pending(conn) == ()


def test_a_legacy_database_upgrades_without_losing_rows(tmp_path):
    path = tmp_path / "legacy.db"
    legacy_database(path)
    conn = connect(path)
    assert migrations.current_version(conn) == migrations.TARGET_VERSION
    assert conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM alert_events").fetchone()[0] == 1
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_the_upgrade_backs_the_database_up_first(tmp_path):
    path = tmp_path / "legacy.db"
    legacy_database(path)
    connect(path)
    backups = list(tmp_path.glob("*.bak"))
    assert len(backups) == 1
    # The backup is a real database, not a truncated file.
    saved = sqlite3.connect(str(backups[0]))
    assert saved.execute("SELECT COUNT(*) FROM alerts").fetchone()[0] == 1


def test_migrating_twice_is_a_no_op(tmp_path):
    path = tmp_path / "legacy.db"
    legacy_database(path)
    connect(path).close()
    conn = connect(path)
    assert migrations.pending(conn) == ()


def test_deleting_an_alert_no_longer_erases_its_events(tmp_path):
    """The bug this migration exists for: CASCADE used to take the history."""
    path = tmp_path / "legacy.db"
    legacy_database(path)
    conn = connect(path)
    conn.execute("DELETE FROM alerts WHERE id = 1")
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM alert_events").fetchone()[0] == 1
    assert conn.execute("SELECT alert_id FROM alert_events").fetchone()[0] is None


def test_deleting_a_position_no_longer_erases_its_alerts(tmp_path):
    path = tmp_path / "legacy.db"
    legacy_database(path)
    conn = connect(path)
    conn.execute("DELETE FROM positions WHERE id = 1")
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0] == 1
    assert conn.execute("SELECT position_id FROM alerts").fetchone()[0] is None


def test_add_column_tolerates_being_applied_twice(tmp_path):
    conn = connect(tmp_path / "t.db")
    migrations.add_column(conn, "positions", "extra_note", "TEXT")
    migrations.add_column(conn, "positions", "extra_note", "TEXT")
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(positions)")}
    assert "extra_note" in columns


def test_a_failing_migration_leaves_the_version_untouched(tmp_path, monkeypatch):
    path = tmp_path / "legacy.db"
    legacy_database(path)

    def explode(conn):
        raise sqlite3.OperationalError("boom")

    broken = migrations.Migration(99, "broken", explode)
    monkeypatch.setattr(migrations, "MIGRATIONS", migrations.MIGRATIONS + (broken,))
    with pytest.raises(sqlite3.OperationalError):
        connect(path)
    raw = sqlite3.connect(str(path))
    raw.row_factory = sqlite3.Row
    reopened = SqliteDatabase(raw, path)
    # Everything before the broken step committed; the broken one did not.
    assert migrations.current_version(reopened) == migrations.TARGET_VERSION


def test_positions_created_before_the_ledger_get_their_buy_backfilled(tmp_path):
    """A v2 position had no transactions; a replay must not report zero shares."""
    from fa import ledger

    path = tmp_path / "legacy.db"
    legacy_database(path)
    conn = connect(path)
    holding = ledger.holding(conn, "PODD")
    assert holding.quantity == 10.0
    assert holding.average_cost == 100.0
    entry = conn.execute("SELECT * FROM transactions WHERE kind = 'buy'").fetchone()
    assert entry["source"] == "backfill"
    assert entry["trade_date"] == "2026-01-15"


def test_the_backfill_does_not_duplicate_on_a_second_run(tmp_path):
    from fa.store import transactions as transactions_store

    path = tmp_path / "legacy.db"
    legacy_database(path)
    connect(path).close()
    conn = connect(path)
    assert len(transactions_store.list_transactions(conn, ticker="PODD")) == 1


def test_migration_14_adds_the_frozen_conversion_columns(tmp_path):
    """A peso trade needs the rate of its own day kept, not recomputed later.

    Recomputing would make an old purchase's P&L move every time the dollar
    jumps, which says something false about a trade that already happened.
    """
    conn = connect(tmp_path / "fresh.db")
    assert "cost_basis_usd" in conn.columns("positions")
    assert {"fx_rate", "usd_price"} <= set(conn.columns("transactions"))


def test_a_legacy_database_gains_the_conversion_columns_too(tmp_path):
    """The columns arrive by migration, not only in a schema built from scratch."""
    path = tmp_path / "legacy.db"
    legacy_database(path)
    conn = connect(path)
    assert "cost_basis_usd" in conn.columns("positions")
    assert conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == 1
