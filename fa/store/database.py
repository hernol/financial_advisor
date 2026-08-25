"""One database interface over SQLite and Postgres.

The application ships in three shapes: a local SQLite file, somebody else's
Postgres, and a hosted Supabase project. Rather than an ORM, this module keeps
the hand written SQL the store modules already use and absorbs the only three
places the two engines actually disagree:

* **Parameter style.** SQLite wants ``?``, psycopg wants ``%s``.
* **Generated keys.** ``lastrowid`` does not exist on Postgres, so every insert
  goes through ``RETURNING id``, which both engines support.
* **Types in DDL.** ``INTEGER PRIMARY KEY AUTOINCREMENT`` against
  ``BIGSERIAL PRIMARY KEY``, and so on. Migrations write placeholders and
  :func:`render_ddl` fills them in.

Everything else in the schema is ordinary SQL that runs unchanged on both,
which was measured before this layer was written rather than assumed.
"""
from __future__ import annotations

import logging
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol, Sequence, runtime_checkable

logger = logging.getLogger(__name__)

SQLITE = "sqlite"
POSTGRES = "postgres"

# One result row, whichever driver produced it. sqlite3.Row and psycopg's
# dict_row both answer row["column"] and .keys(), which is all the serde layer
# ever asks of them.
Row = Mapping[str, Any]


@dataclass(frozen=True)
class Dialect:
    """The engine specific spellings a migration needs."""

    name: str
    id_pk: str
    fk_id: str
    text: str
    real: str
    integer: str
    boolean: str
    json: str
    timestamp: str
    now: str


SQLITE_DIALECT = Dialect(
    name=SQLITE,
    id_pk="INTEGER PRIMARY KEY AUTOINCREMENT",
    fk_id="INTEGER",
    text="TEXT",
    real="REAL",
    integer="INTEGER",
    # SQLite has no boolean type; the codebase already stores 0/1 and converts
    # on the way out, so the column stays an integer on both engines to keep a
    # single set of read paths.
    boolean="INTEGER",
    json="TEXT",
    timestamp="TEXT",
    now="CURRENT_TIMESTAMP",
)

POSTGRES_DIALECT = Dialect(
    name=POSTGRES,
    id_pk="BIGSERIAL PRIMARY KEY",
    fk_id="BIGINT",
    text="TEXT",
    real="DOUBLE PRECISION",
    integer="INTEGER",
    boolean="SMALLINT",
    json="JSONB",
    # Timestamps are written as ISO 8601 strings by the serde layer and read
    # back with fromisoformat, so they stay text on Postgres too. Moving them to
    # timestamptz is a later migration, not a portability requirement.
    timestamp="TEXT",
    now="CURRENT_TIMESTAMP",
)

DIALECTS = {SQLITE: SQLITE_DIALECT, POSTGRES: POSTGRES_DIALECT}

_PLACEHOLDER = re.compile(r"\{(ID_PK|FK_ID|TEXT|REAL|INTEGER|BOOLEAN|JSON|TIMESTAMP|NOW)\}")


def render_ddl(sql: str, dialect: Dialect) -> str:
    """Replace the ``{ID_PK}`` style placeholders with this engine's spelling."""
    mapping = {
        "ID_PK": dialect.id_pk,
        "FK_ID": dialect.fk_id,
        "TEXT": dialect.text,
        "REAL": dialect.real,
        "INTEGER": dialect.integer,
        "BOOLEAN": dialect.boolean,
        "JSON": dialect.json,
        "TIMESTAMP": dialect.timestamp,
        "NOW": dialect.now,
    }
    return _PLACEHOLDER.sub(lambda m: mapping[m.group(1)], sql)


def to_pyformat(sql: str) -> str:
    """Rewrite ``?`` placeholders as ``%s`` for psycopg.

    A question mark inside a quoted literal stays a question mark. A literal
    ``%`` is doubled wherever it appears — including inside quotes, which is
    exactly where LIKE patterns live — because psycopg reads the whole statement
    as a format string.
    """
    out: list[str] = []
    quote: str | None = None
    for char in sql:
        if quote:
            out.append("%%" if char == "%" else char)
            if char == quote:
                quote = None
            continue
        if char in ("'", '"'):
            quote = char
            out.append(char)
        elif char == "?":
            out.append("%s")
        elif char == "%":
            out.append("%%")
        else:
            out.append(char)
    return "".join(out)


def with_returning_id(sql: str) -> str:
    """Append ``RETURNING id`` unless the statement already asks for something."""
    if re.search(r"\bRETURNING\b", sql, re.IGNORECASE):
        return sql
    return f"{sql.rstrip().rstrip(';')} RETURNING id"


@runtime_checkable
class Database(Protocol):
    """What the store modules need from a connection.

    Deliberately shaped like :class:`sqlite3.Connection` so the existing calls
    read the same; :meth:`insert` is the one addition, replacing ``lastrowid``.
    """

    dialect: Dialect

    def execute(self, sql: str, params: Sequence[Any] = ()) -> Any: ...
    def executemany(self, sql: str, seq: Iterable[Sequence[Any]]) -> None: ...
    def insert(self, sql: str, params: Sequence[Any] = ()) -> int: ...
    def executescript(self, sql: str) -> None: ...
    def table_exists(self, name: str) -> bool: ...
    def columns(self, table: str) -> set[str]: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...
    def close(self) -> None: ...


class SqliteDatabase:
    """The local, single file engine. Also what every test runs against."""

    dialect = SQLITE_DIALECT

    def __init__(self, conn: sqlite3.Connection, path: Path | None = None) -> None:
        self._conn = conn
        self.path = path

    @property
    def raw(self) -> sqlite3.Connection:
        """The underlying connection, for the few places that need PRAGMA."""
        return self._conn

    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        return self._conn.execute(sql, params)

    def executemany(self, sql: str, seq: Iterable[Sequence[Any]]) -> None:
        self._conn.executemany(sql, seq)

    def insert(self, sql: str, params: Sequence[Any] = ()) -> int:
        row = self._conn.execute(with_returning_id(sql), params).fetchone()
        return int(row[0] if not isinstance(row, sqlite3.Row) else row["id"])

    def executescript(self, sql: str) -> None:
        self._conn.executescript(sql)

    def table_exists(self, name: str) -> bool:
        row = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
        ).fetchone()
        return row is not None

    def columns(self, table: str) -> set[str]:
        return {row["name"] for row in self._conn.execute(f"PRAGMA table_info({table})")}

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        self._conn.close()


class PostgresDatabase:
    """Supabase or any other Postgres.

    psycopg is imported lazily so the local install never needs the driver, and
    a missing one fails with an instruction instead of an ImportError at start.
    """

    dialect = POSTGRES_DIALECT

    def __init__(self, dsn: str) -> None:
        psycopg, dict_row = _load_psycopg()
        self._conn = psycopg.connect(dsn, row_factory=dict_row, autocommit=False)
        self.path = None

    @property
    def raw(self) -> Any:
        return self._conn

    def execute(self, sql: str, params: Sequence[Any] = ()) -> Any:
        cursor = self._conn.cursor()
        cursor.execute(to_pyformat(sql), tuple(params))
        return cursor

    def executemany(self, sql: str, seq: Iterable[Sequence[Any]]) -> None:
        cursor = self._conn.cursor()
        cursor.executemany(to_pyformat(sql), [tuple(row) for row in seq])

    def insert(self, sql: str, params: Sequence[Any] = ()) -> int:
        cursor = self.execute(with_returning_id(sql), params)
        row = cursor.fetchone()
        return int(row["id"])

    def executescript(self, sql: str) -> None:
        # psycopg sends a multi statement string as one implicit transaction,
        # which is exactly what executescript means here.
        self._conn.cursor().execute(sql)

    def table_exists(self, name: str) -> bool:
        # to_regclass resolves through search_path, so this is still correct when
        # the deployment puts the tables somewhere other than public.
        cursor = self.execute("SELECT to_regclass(?) AS oid", (name,))
        row = cursor.fetchone()
        return row is not None and row["oid"] is not None

    def columns(self, table: str) -> set[str]:
        cursor = self.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = ?",
            (table,),
        )
        return {row["column_name"] for row in cursor}

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        self._conn.close()


def _load_psycopg() -> tuple[Any, Any]:
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise RuntimeError(
            "DATABASE_URL apunta a Postgres pero psycopg no está instalado. "
            "Instalá 'psycopg[binary]' o dejá DATABASE_URL vacío para usar SQLite."
        ) from exc
    return psycopg, dict_row


def rows_to_dicts(cursor: Any) -> list[dict[str, Any]]:
    """Normalise a result set to plain dicts, whichever engine produced it."""
    return [dict(row) for row in cursor]


def row_keys(row: Mapping[str, Any] | sqlite3.Row) -> set[str]:
    """Column names of one row, for code that tolerates older schemas."""
    return set(row.keys())
