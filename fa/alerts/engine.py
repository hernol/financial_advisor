"""Alert engine: loads active alerts, evaluates them against live data, notifies."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Sequence

from fa.alerts.rules import evaluate
from fa.analytics import build_snapshot
from fa.errors import DataUnavailableError
from fa.market import MarketService
from fa.models import Alert, Position, Signal
from fa.notify.dispatcher import Dispatcher, deliver
from fa.store import alerts as alerts_store
from fa.store import events as events_store
from fa.store import history as history_store
from fa.store import positions as positions_store
from fa.store import runs as runs_store
from fa.store.database import Database
from fa.warm import warm_fundamentals

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CheckReport:
    """Outcome of one alert-check run."""

    checked: int
    fired: Sequence[Signal]
    skipped_cooldown: int
    expired: int
    errors: Sequence[str]
    run_id: int | None = None

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


def _position_for(conn: Database, alert: Alert) -> Position | None:
    if alert.position_id is not None:
        return positions_store.get_position(conn, alert.position_id)
    open_positions = positions_store.positions_for_ticker(conn, alert.ticker)
    return open_positions[0] if open_positions else None


def run_checks(
    conn: Database,
    market: MarketService,
    dispatcher: Dispatcher,
    *,
    now: datetime | None = None,
    ticker: str | None = None,
    trigger: str = "manual",
) -> CheckReport:
    """Evaluate the active alerts once and deliver whatever fired.

    ``ticker`` narrows the run to a single symbol, used by the per-ticker
    workspace in the menu.

    Every run leaves a trace even when nothing fires: the run itself, one
    evaluation row per alert, an indicator snapshot per ticker and one row per
    delivery attempt. That is what makes "nothing happened today" distinguishable
    from "nothing has run since Tuesday".
    """
    moment = now or datetime.now(timezone.utc)
    run_id = runs_store.start_run(conn, trigger=trigger, ticker=ticker, started_at=moment)
    expired = alerts_store.expire_stale(conn, moment)
    active = alerts_store.list_alerts(conn, only_active=True, ticker=ticker)
    fired: list[Signal] = []
    errors: list[str] = []
    cooled = 0

    by_ticker: dict[str, list[Alert]] = {}
    for alert in active:
        by_ticker.setdefault(alert.ticker, []).append(alert)

    for symbol, ticker_alerts in by_ticker.items():
        positions = positions_store.positions_for_ticker(conn, symbol)
        oldest_buy = min((p.buy_date for p in positions), default=None)
        try:
            context = market.context(symbol, since=oldest_buy)
        except DataUnavailableError as exc:
            errors.append(f"{symbol}: {exc}")
            logger.warning("skipping %s: %s", symbol, exc)
            for alert in ticker_alerts:
                runs_store.record_evaluation(
                    conn,
                    run_id=run_id,
                    alert_id=alert.id,
                    ticker=symbol,
                    kind=alert.kind,
                    outcome=runs_store.SKIPPED,
                    detail={"error": str(exc)},
                    evaluated_at=moment,
                )
            continue

        _remember(conn, context, run_id, market)
        price = context.quote.price

        for alert in ticker_alerts:
            if in_cooldown(alert, moment):
                cooled += 1
                runs_store.record_evaluation(
                    conn,
                    run_id=run_id,
                    alert_id=alert.id,
                    ticker=symbol,
                    kind=alert.kind,
                    outcome=runs_store.COOLDOWN,
                    price=price,
                    detail={"last_fired_at": alert.last_fired_at.isoformat() if alert.last_fired_at else None},
                    evaluated_at=moment,
                )
                continue
            try:
                signal = evaluate(alert, context, _position_for(conn, alert))
            except Exception as exc:  # noqa: BLE001 - one broken rule must not kill the run
                errors.append(f"{symbol}/{alert.kind}: {exc}")
                logger.exception("rule %s failed for %s", alert.kind, symbol)
                runs_store.record_evaluation(
                    conn,
                    run_id=run_id,
                    alert_id=alert.id,
                    ticker=symbol,
                    kind=alert.kind,
                    outcome=runs_store.ERROR,
                    price=price,
                    detail={"error": f"{type(exc).__name__}: {exc}"},
                    evaluated_at=moment,
                )
                continue
            if signal is None:
                runs_store.record_evaluation(
                    conn,
                    run_id=run_id,
                    alert_id=alert.id,
                    ticker=symbol,
                    kind=alert.kind,
                    outcome=runs_store.QUIET,
                    price=price,
                    detail=dict(alert.params),
                    evaluated_at=moment,
                )
                continue

            results = deliver(dispatcher, signal)
            delivered = [r.channel for r in results if r.ok]
            event_id = events_store.record_event(
                conn, signal, delivered, run_id=run_id, price=price
            )
            for result in results:
                runs_store.record_delivery(
                    conn,
                    event_id=event_id,
                    channel=result.channel,
                    status=runs_store.SENT if result.ok else runs_store.FAILED,
                    error=result.error,
                    attempted_at=moment,
                )
            runs_store.record_evaluation(
                conn,
                run_id=run_id,
                alert_id=alert.id,
                ticker=symbol,
                kind=alert.kind,
                outcome=runs_store.FIRED,
                price=price,
                detail={"event_id": event_id, "severity": signal.severity, **dict(signal.payload)},
                evaluated_at=moment,
            )
            alerts_store.mark_fired(conn, alert, moment)
            fired.append(signal)

    runs_store.finish_run(
        conn,
        run_id,
        checked=len(active),
        fired=len(fired),
        skipped_cooldown=cooled,
        expired=expired,
        errors=errors,
    )
    return CheckReport(
        checked=len(active),
        fired=tuple(fired),
        skipped_cooldown=cooled,
        expired=expired,
        errors=tuple(errors),
        run_id=run_id,
    )


def _remember(conn: Database, context, run_id: int | None, market=None) -> None:
    """Persist the bars and indicators this run already paid to compute.

    Best effort on purpose: a failure to archive history must never stop an
    alert from being delivered, so it is logged and the run carries on.
    """
    try:
        history_store.save_bars(conn, context.ticker, context.history, context.quote.source)
        history_store.save_indicators(
            conn, build_snapshot(context), run_id=run_id, taken_at=context.evaluated_at
        )
    except Exception:  # noqa: BLE001 - archiving must never break alerting
        logger.exception("could not archive history for %s", context.ticker)

    if market is None:
        return
    try:
        # Statements age out in a week, so this is a no-op on almost every run
        # and a single extra fetch on the one after a quarterly filing.
        warm_fundamentals(conn, market, context.ticker)
    except Exception:  # noqa: BLE001 - same reason
        logger.exception("could not refresh the fundamentals of %s", context.ticker)
