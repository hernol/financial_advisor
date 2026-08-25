"""Bringing a ticker's market data in for the first time.

The API answers reads from stored rows and never calls a provider: a viewer
opening a screen must not trigger a download. Adding a ticker you did not have
is a different event — it is a write, the user asked for it, and it happens
once per ticker in a lifetime. Refusing to fetch there does not protect
anything; it just tells the person to go run a command themselves.

So the rule is narrower than it was, and truer: **reads never fetch, and the
one write that introduces a new ticker does.**
"""
from __future__ import annotations

import logging

from fa.analytics import build_snapshot
from fa.errors import DataUnavailableError
from fa.market import MarketService
from fa.store import history as history_store
from fa.store.database import Database

logger = logging.getLogger(__name__)


def has_prices(conn: Database, ticker: str) -> bool:
    return bool(history_store.bar_coverage(conn, ticker)["sessions"])


def warm(conn: Database, market: MarketService, ticker: str, *, force: bool = False) -> bool:
    """Fetch and store a ticker's history and indicators. True if data landed.

    Best effort: a provider that is down must not undo a trade the user just
    recorded, so the failure is logged and reported, never raised at them.
    """
    symbol = ticker.upper()
    if not force and has_prices(conn, symbol):
        return True
    try:
        context = market.context(symbol)
    except DataUnavailableError as exc:
        logger.warning("could not warm %s: %s", symbol, exc)
        return False
    try:
        history_store.save_bars(conn, symbol, context.history, context.quote.source)
        history_store.save_indicators(conn, build_snapshot(context))
    except Exception:  # noqa: BLE001 - archiving must never break the caller
        logger.exception("could not archive the first fetch of %s", symbol)
        return False
    return has_prices(conn, symbol)
