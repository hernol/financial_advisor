"""Database access for the API process.

One connection is opened at startup and shared. Both drivers serialise access
to a connection themselves — ``sqlite3.threadsafety`` is 3 and psycopg declares
level 2 — so the worker threads uvicorn runs sync endpoints on can share it.

The lock guards opening and closing, and nothing else. It used to be held for
the whole of every request, which serialised the entire API behind one caller
and, because FastAPI finalises ``yield`` dependencies *after* background tasks
run, froze every endpoint for as long as an AI report took: forty seconds during
which the page could not even ask whether the report was done.

When this grows to the hosted mode the shared connection becomes a real pool —
which is why every endpoint asks through :func:`get_db` rather than reaching for
the module level global.
"""
from __future__ import annotations

import threading
from typing import Any, Iterator

from fa.config import load_settings
from fa.store.database import Database
from fa.store.db import connect

_lock = threading.Lock()
_database: Database | None = None
_owned = False


def open_database(target: object | None = None) -> Database:
    """Open the shared connection. Called once, at startup."""
    global _database, _owned
    with _lock:
        if _database is None:
            _database = connect(target or load_settings().database_target, threadsafe=True)
            _owned = True
        return _database


def close_database() -> None:
    """Close the connection, but only if this module was the one that opened it.

    Shutdown must not close a database somebody else handed over: the tests
    inject one they still need for their own teardown, and an embedding process
    would be in the same position.
    """
    global _database, _owned
    with _lock:
        if _database is not None and _owned:
            _database.close()
        if _owned:
            _database = None
        _owned = False


def set_database(database: Database | None) -> None:
    """Point the API at an already open database, keeping ownership with the
    caller. Used by the tests."""
    global _database, _owned
    _database = database
    _owned = False


def get_db() -> Iterator[Database]:
    """FastAPI dependency: the shared database.

    Deliberately does not hold ``_lock``. The driver serialises statements, and
    holding a lock across the request would put every caller in a queue behind
    the slowest one.
    """
    yield _database or open_database()


_market_factory: Any = None


def build_market(db: Database) -> Any:
    """A market service for the few write paths allowed to fetch.

    Built on demand rather than held on the app: reads must not have one within
    reach, so that "the API does not call providers" stays structural instead of
    a rule someone has to remember. Tests replace the factory so the suite never
    reaches the network.
    """
    if _market_factory is not None:
        return _market_factory(db)
    from fa.market import MarketService
    from fa.providers.chain import build_chain

    settings = load_settings()
    return MarketService(build_chain(settings), db, benchmark=settings.benchmark)


def set_market_factory(factory: Any) -> None:
    """Point the write paths at a different market. Used by the tests."""
    global _market_factory
    _market_factory = factory
