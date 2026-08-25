"""The portability layer: parameter styles, generated keys and dialect DDL."""
from __future__ import annotations

import pytest

from fa.store import schema
from fa.store.database import (
    POSTGRES_DIALECT,
    SQLITE,
    SQLITE_DIALECT,
    render_ddl,
    to_pyformat,
    with_returning_id,
)
from fa.store.db import connect

# --- parameter style --------------------------------------------------------


def test_question_marks_become_pyformat():
    assert to_pyformat("SELECT * FROM t WHERE a = ? AND b = ?") == (
        "SELECT * FROM t WHERE a = %s AND b = %s"
    )


def test_a_question_mark_inside_a_literal_is_left_alone():
    """Alert titles and messages carry Spanish punctuation into SQL literals."""
    assert to_pyformat("SELECT '¿subió?' , ? FROM t") == "SELECT '¿subió?' , %s FROM t"


def test_a_literal_percent_is_escaped():
    """psycopg reads the statement as a format string, so % must be doubled."""
    assert to_pyformat("SELECT * FROM t WHERE k LIKE 'a%'") == (
        "SELECT * FROM t WHERE k LIKE 'a%%'"
    )


def test_double_quoted_identifiers_are_left_alone():
    assert to_pyformat('SELECT "we?ird" FROM t WHERE a = ?') == (
        'SELECT "we?ird" FROM t WHERE a = %s'
    )


# --- generated keys ---------------------------------------------------------


def test_returning_is_appended_once():
    assert with_returning_id("INSERT INTO t(a) VALUES(?)") == (
        "INSERT INTO t(a) VALUES(?) RETURNING id"
    )


def test_an_existing_returning_clause_is_respected():
    sql = "INSERT INTO t(a) VALUES(?) RETURNING id, a"
    assert with_returning_id(sql) == sql


def test_a_trailing_semicolon_does_not_break_the_clause():
    assert with_returning_id("INSERT INTO t(a) VALUES(?);").endswith("VALUES(?) RETURNING id")


def test_insert_returns_the_new_id(tmp_path):
    db = connect(tmp_path / "t.db")
    first = db.insert(
        "INSERT INTO accounts(name, created_at) VALUES(?, ?)", ("otra", "2026-08-24")
    )
    second = db.insert(
        "INSERT INTO accounts(name, created_at) VALUES(?, ?)", ("tercera", "2026-08-24")
    )
    assert second == first + 1


# --- dialect DDL ------------------------------------------------------------


def test_placeholders_resolve_per_engine():
    ddl = "CREATE TABLE x (id {ID_PK}, v {REAL}, p {JSON}, n {FK_ID})"
    assert render_ddl(ddl, SQLITE_DIALECT) == (
        "CREATE TABLE x (id INTEGER PRIMARY KEY AUTOINCREMENT, v REAL, p TEXT, n INTEGER)"
    )
    assert render_ddl(ddl, POSTGRES_DIALECT) == (
        "CREATE TABLE x (id BIGSERIAL PRIMARY KEY, v DOUBLE PRECISION, p JSONB, n BIGINT)"
    )


def test_an_unknown_placeholder_is_left_for_the_engine_to_reject():
    """Silently swallowing a typo would produce a table with a missing type."""
    assert render_ddl("CREATE TABLE x (a {NOPE})", SQLITE_DIALECT) == "CREATE TABLE x (a {NOPE})"


# --- routing ----------------------------------------------------------------


def test_a_path_opens_sqlite(tmp_path):
    assert connect(tmp_path / "t.db").dialect.name == SQLITE


def test_a_postgres_url_is_recognised():
    from fa.store.db import is_postgres_url

    assert is_postgres_url("postgresql://user:pw@host:5432/db")
    assert is_postgres_url("postgres://user@host/db")
    assert not is_postgres_url("/data/financial_analyzer.db")


def test_postgres_without_the_driver_explains_itself(monkeypatch):
    """A missing driver must name the fix, not raise ImportError from an import."""
    import builtins

    from fa.store import database

    real_import = builtins.__import__

    def refuse(name, *args, **kwargs):
        if name == "psycopg":
            raise ImportError("no psycopg here")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", refuse)
    with pytest.raises(RuntimeError, match="psycopg"):
        database.PostgresDatabase("postgresql://localhost/x")


# --- tenancy ----------------------------------------------------------------


def test_a_fresh_database_has_the_local_account(tmp_path):
    db = connect(tmp_path / "t.db")
    row = db.execute("SELECT * FROM accounts WHERE id = ?", (schema.LOCAL_ACCOUNT_ID,)).fetchone()
    assert row is not None
    assert row["plan"] == "local"


def test_every_tenant_table_carries_an_account(tmp_path):
    db = connect(tmp_path / "t.db")
    for table in schema.TENANT_TABLES:
        assert "account_id" in db.columns(table), table


def test_shared_tables_have_no_account(tmp_path):
    """Market data is the same for everybody; giving it an owner would mean
    downloading the same candles once per user."""
    db = connect(tmp_path / "t.db")
    for table in schema.SHARED_TABLES:
        assert "account_id" not in db.columns(table), table


def test_rows_written_without_an_account_land_in_the_local_one(tmp_path):
    from datetime import date

    from fa.models import Position
    from fa.store import positions as positions_store

    db = connect(tmp_path / "t.db")
    positions_store.add_position(
        db, Position(ticker="PODD", quantity=10, buy_price=100.0, buy_date=date(2026, 1, 15))
    )
    row = db.execute("SELECT account_id FROM positions").fetchone()
    assert row["account_id"] == schema.LOCAL_ACCOUNT_ID


def test_an_upgraded_database_adopts_its_rows(tmp_path):
    """A database from before accounts existed must not end up with orphans."""
    from tests.test_migrations import legacy_database

    path = tmp_path / "legacy.db"
    legacy_database(path)
    db = connect(path)
    for table in ("positions", "alerts", "alert_events"):
        rows = db.execute(f"SELECT account_id FROM {table}").fetchall()
        assert rows, table
        assert all(r["account_id"] == schema.LOCAL_ACCOUNT_ID for r in rows), table
