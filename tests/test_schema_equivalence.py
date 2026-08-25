"""A fresh database and an upgraded one must end up identical.

This is the test that makes the squashed schema safe. ``fa.store.schema`` is a
hand written copy of where the migrations land, so nothing but a check like this
stops the two from drifting apart the first time somebody adds a column to one
and forgets the other.
"""
from __future__ import annotations

import pytest

from fa.store import migrations
from fa.store.database import SQLITE_DIALECT, render_ddl
from fa.store.db import connect
from tests.test_migrations import legacy_database


def tables(conn) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    )
    return {row["name"] for row in rows}


def columns(conn, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def indexes(conn) -> dict[str, str]:
    """Index name to the columns it covers, normalised for comparison."""
    out: dict[str, str] = {}
    for row in conn.execute(
        "SELECT name, tbl_name FROM sqlite_master WHERE type = 'index' AND sql IS NOT NULL"
    ):
        covered = [c["name"] for c in conn.execute(f"PRAGMA index_info({row['name']})")]
        out[row["name"]] = f"{row['tbl_name']}({','.join(covered)})"
    return out


@pytest.fixture()
def fresh(tmp_path):
    """Built in one shot from the squashed schema."""
    return connect(tmp_path / "fresh.db").raw


@pytest.fixture()
def upgraded(tmp_path):
    """Built as a version 2 database and walked forward migration by migration."""
    path = tmp_path / "legacy.db"
    legacy_database(path)
    return connect(path).raw


def test_both_paths_reach_the_same_version(fresh, upgraded, tmp_path):
    from fa.store.database import SqliteDatabase

    assert migrations.current_version(SqliteDatabase(fresh)) == migrations.TARGET_VERSION
    assert migrations.current_version(SqliteDatabase(upgraded)) == migrations.TARGET_VERSION


def test_both_paths_produce_the_same_tables(fresh, upgraded):
    assert tables(fresh) == tables(upgraded)


def test_both_paths_produce_the_same_columns(fresh, upgraded):
    mismatches = {
        table: (columns(fresh, table) ^ columns(upgraded, table))
        for table in sorted(tables(fresh))
        if columns(fresh, table) != columns(upgraded, table)
    }
    assert mismatches == {}


def test_both_paths_produce_the_same_indexes(fresh, upgraded):
    assert indexes(fresh) == indexes(upgraded)


def test_the_schema_renders_for_postgres_too():
    """The placeholders must all resolve; an unknown one would survive as text."""
    from fa.store import schema
    from fa.store.database import POSTGRES_DIALECT

    rendered = render_ddl(schema.SCHEMA, POSTGRES_DIALECT)
    assert "{ID_PK}" not in rendered
    assert "BIGSERIAL PRIMARY KEY" in rendered
    assert "JSONB" in rendered
    assert "DOUBLE PRECISION" in rendered
    # '{}' is a JSON default, not a placeholder, and must survive untouched.
    assert "DEFAULT '{}'" in rendered


def test_the_baseline_still_renders_for_sqlite():
    from fa.store.db import BASELINE

    assert "{" not in render_ddl(BASELINE, SQLITE_DIALECT).replace("'{}'", "")


# --- across engines ---------------------------------------------------------
# The tests above compare two SQLite paths to each other. These compare SQLite
# to Postgres, which is the claim the portability layer actually makes.


def test_postgres_creates_the_same_tables(fresh, pg_conn):
    postgres_tables = {
        row["tablename"]
        for row in pg_conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = current_schema()"
        )
    }
    assert postgres_tables == tables(fresh)


def test_postgres_creates_the_same_columns(fresh, pg_conn):
    mismatches = {}
    for table in sorted(tables(fresh)):
        expected = columns(fresh, table)
        actual = pg_conn.columns(table)
        if expected != actual:
            mismatches[table] = expected ^ actual
    assert mismatches == {}


def test_postgres_lands_on_the_target_version(pg_conn):
    assert migrations.current_version(pg_conn) == migrations.TARGET_VERSION
    assert migrations.pending(pg_conn) == ()


def test_postgres_skips_the_sqlite_only_history(pg_conn):
    """A fresh Postgres never replays migrations written against SQLite tables."""
    assert all(m.sqlite_only for m in migrations.MIGRATIONS if m.version < 10)
    assert migrations.pending(pg_conn) == ()


def test_postgres_has_the_local_account(pg_conn):
    from fa.store.schema import LOCAL_ACCOUNT_ID

    row = pg_conn.execute("SELECT plan FROM accounts WHERE id = ?", (LOCAL_ACCOUNT_ID,)).fetchone()
    assert row is not None and row["plan"] == "local"
