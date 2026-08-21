"""Alert engine: loads active alerts, evaluates them against live data, notifies."""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Sequence

from fa.alerts.rules import evaluate
from fa.errors import DataUnavailableError
from fa.market import MarketService
from fa.models import Alert, Position, Signal
from fa.notify.dispatcher import Dispatcher
from fa.store import alerts as alerts_store
from fa.store import events as events_store
from fa.store import positions as positions_store

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CheckReport:
    """Outcome of one alert-check run."""

    checked: int
    fired: Sequence[Signal]
    skipped_cooldown: int
    expired: int
    errors: Sequence[str]

    @property
    def ok(self) -> bool:
        return not self.errors


def in_cooldown(alert: Alert, now: datetime) -> bool:
    """True when the alert fired too recently to fire again."""
    if alert.last_fired_at is None or alert.cooldown_hours <= 0:
        return False
    last = alert.last_fired_at
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return now - last < timedelta(hours=alert.cooldown_hours)


def _position_for(conn: sqlite3.Connection, alert: Alert) -> Position | None:
    if alert.position_id is not None:
        return positions_store.get_position(conn, alert.position_id)
    open_positions = positions_store.positions_for_ticker(conn, alert.ticker)
    return open_positions[0] if open_positions else None


def run_checks(
    conn: sqlite3.Connection,
    market: MarketService,
    dispatcher: Dispatcher,
    *,
    now: datetime | None = None,
    ticker: str | None = None,
) -> CheckReport:
    """Evaluate the active alerts once and deliver whatever fired.

    ``ticker`` narrows the run to a single symbol, used by the per-ticker
    workspace in the menu.
    """
    moment = now or datetime.now(timezone.utc)
    expired = alerts_store.expire_stale(conn, moment)
    active = alerts_store.list_alerts(conn, only_active=True, ticker=ticker)
    fired: list[Signal] = []
    errors: list[str] = []
    cooled = 0

    by_ticker: dict[str, list[Alert]] = {}
    for alert in active:
        by_ticker.setdefault(alert.ticker, []).append(alert)

    for ticker, ticker_alerts in by_ticker.items():
        positions = positions_store.positions_for_ticker(conn, ticker)
        oldest_buy = min((p.buy_date for p in positions), default=None)
        try:
            context = market.context(ticker, since=oldest_buy)
        except DataUnavailableError as exc:
            errors.append(f"{ticker}: {exc}")
            logger.warning("skipping %s: %s", ticker, exc)
            continue
        for alert in ticker_alerts:
            if in_cooldown(alert, moment):
                cooled += 1
                continue
            try:
                signal = evaluate(alert, context, _position_for(conn, alert))
            except Exception as exc:  # noqa: BLE001 - one broken rule must not kill the run
                errors.append(f"{ticker}/{alert.kind}: {exc}")
                logger.exception("rule %s failed for %s", alert.kind, ticker)
                continue
            if signal is None:
                continue
            delivered = dispatcher.send(signal)
            events_store.record_event(conn, signal, delivered)
            alerts_store.mark_fired(conn, alert, moment)
            fired.append(signal)

    return CheckReport(
        checked=len(active), fired=tuple(fired), skipped_cooldown=cooled, expired=expired, errors=tuple(errors)
    )
