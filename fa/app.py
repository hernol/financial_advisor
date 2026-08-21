"""Application wiring: settings, database, providers and notification channels."""
from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

from fa.config import Settings, load_settings
from fa.errors import ConfigError
from fa.localai import LocalAIClient
from fa.market import MarketService
from fa.notify.dispatcher import Dispatcher, build_dispatcher
from fa.providers.chain import build_chain
from fa.store.db import connect


@dataclass(frozen=True)
class App:
    """Everything the commands need, built once per process."""

    settings: Settings
    conn: sqlite3.Connection
    market: MarketService
    dispatcher: Dispatcher
    local_ai: LocalAIClient


def configure_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


@contextmanager
def build_app(*, echo_notifications: bool = True) -> Iterator[App]:
    """Create the application context and always close the database."""
    settings = load_settings()
    try:
        conn = connect(settings.db_path)
    except sqlite3.OperationalError as exc:
        # Typically a database left behind by a root-owned container run.
        raise ConfigError(
            f"No se puede abrir la base {settings.db_path}: {exc}. "
            "Si la creó Docker corriendo como root, devolvela con "
            f"'sudo chown -R $(id -u):$(id -g) {settings.db_path.parent}'."
        ) from exc
    try:
        market = MarketService(build_chain(settings), conn)
        yield App(
            settings=settings,
            conn=conn,
            market=market,
            dispatcher=build_dispatcher(settings, echo=echo_notifications),
            local_ai=LocalAIClient.from_settings(settings),
        )
    finally:
        conn.close()
