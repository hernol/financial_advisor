"""Database access for the API process.

One connection is opened at startup and shared, guarded by a lock. The API only
reads, and a local install has one user, so a pool would be ceremony; the lock
is what makes it safe for the worker threads uvicorn runs sync endpoints on.

When this grows to the hosted mode the lock becomes a real pool — which is why
every endpoint asks for the database through :func:`get_db` rather than reaching
for a module level global.
"""
from __future__ import annotations

import threading
from typing import Iterator

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
    """FastAPI dependency: the shared database, serialised across threads."""
    database = _database or open_database()
    with _lock:
        yield database
