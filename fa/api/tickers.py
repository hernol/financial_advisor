"""Read endpoints for the ticker screen.

Everything here answers from what the scheduled run already stored. The API
never reaches out to a data provider: a request for RH must not depend on
Yahoo being up, and the hosted mode cannot afford one download per viewer.

The cost of that choice is that a ticker nobody has refreshed yet has nothing
to show, which the response says plainly instead of filling in.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Path, Query

from fa.api.auth import account_id
from fa.api.deps import get_db
from fa.indicators import sma_series
from fa.metrics import QUALITY_COLUMNS, SUMMARY_COLUMNS
from fa.models import TICKER_PATTERN
from fa.store import alerts as alerts_store
from fa.store import fundamentals as fundamentals_store
from fa.store import history as history_store
from fa.store import positions as positions_store
from fa.store import runs as runs_store
from fa.store.database import Database

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["tickers"])

FAST_SMA = 50
SLOW_SMA = 200

# Charting one indicator over time. Names match the columns the snapshot writes,
# and the store rejects anything outside its own whitelist.
SERIES = {
    "rsi": "RSI (14)",
    "price": "Precio",
    "atr_pct": "ATR %",
    "volatility_pct": "Volatilidad anual %",
    "percent_b": "%B Bollinger",
    "macd_histogram": "MACD histograma",
    "vs_sma_slow_pct": "Distancia a SMA200 %",
    "volume_ratio": "Volumen vs 20d",
    "from_high_pct": "Desde el máximo 52s %",
    "max_drawdown_pct": "Drawdown máximo %",
    "rel_strength_3m": "Fuerza relativa 3m",
}


def _tracked(db: Database, account: int) -> list[str]:
    return positions_store.tracked_tickers(db, account_id=account)


@router.get("/tickers")
def list_tickers(db: Database = Depends(get_db),
    account: int = Depends(account_id),
) -> list[dict[str, Any]]:
    """Everything being followed, with just enough to render a list row."""
    out: list[dict[str, Any]] = []
    for ticker in _tracked(db, account):
        coverage = history_store.bar_coverage(db, ticker)
        latest = history_store.latest_indicators(db, ticker)
        active = alerts_store.list_alerts(db, ticker=ticker, only_active=True, account_id=account)
        out.append(
            {
                "ticker": ticker,
                "sessions": coverage["sessions"],
                "first_day": coverage["first_day"],
                "last_day": coverage["last_day"],
                "price": latest["price"] if latest else None,
                "trend": latest["trend"] if latest else None,
                "rsi": latest["rsi"] if latest else None,
                "taken_at": latest["taken_at"] if latest else None,
                "active_alerts": len(active),
                "positions": len(positions_store.positions_for_ticker(db, ticker, account_id=account)),
            }
        )
    return out


@router.get("/tickers/{ticker}")
def ticker_detail(ticker: str, db: Database = Depends(get_db),
    account: int = Depends(account_id),
) -> dict[str, Any]:
    """The numbers panel: last reading of every indicator, plus its age."""
    symbol = ticker.upper()
    coverage = history_store.bar_coverage(db, symbol)
    if not coverage["sessions"]:
        raise HTTPException(
            status_code=404,
            detail=f"Todavía no hay datos de {symbol}.",
        )
    latest = history_store.latest_indicators(db, symbol)
    payload = dict(latest["payload"]) if latest else {}
    positions = positions_store.positions_for_ticker(db, symbol, account_id=account)
    price = latest["price"] if latest else None
    change_abs, change_pct = _session_change(db, symbol, price)
    return {
        "ticker": symbol,
        "followed": symbol in _tracked(db, account),
        "coverage": coverage,
        "taken_at": latest["taken_at"] if latest else None,
        "price": price,
        "change_abs": change_abs,
        "change_pct": change_pct,
        "indicators": payload,
        "position": (
            {
                "quantity": positions[0].quantity,
                "buy_price": positions[0].buy_price,
                "buy_date": positions[0].buy_date.isoformat(),
                "currency": positions[0].currency,
            }
            if positions
            else None
        ),
        "series": [{"name": key, "label": label} for key, label in SERIES.items()],
    }


def _session_change(db: Database, ticker: str, price: float | None) -> tuple[float | None, float | None]:
    """Move against the previous close.

    A price on its own is only half a number — the screen needs to say whether
    it is up or down on the session. The reference is the last completed bar
    before today, so an intraday quote is compared against yesterday's close
    rather than against the partial bar for today.
    """
    if price is None:
        return (None, None)
    # Intraday the provider carries a bar for today, and before the open it is
    # just yesterday's close repeated. Comparing against it reports a flat day
    # for every ticker every morning, so the reference is the last *completed*
    # session — the same reason completed_bars() exists for volume.
    recent = history_store.load_bars(db, ticker, limit=4)
    today = date.today()
    completed = [bar for bar in recent if bar.day < today]
    if not completed:
        return (None, None)
    previous = completed[-1].close
    if not previous:
        return (None, None)
    return (price - previous, (price - previous) / previous * 100.0)


@router.get("/tickers/{ticker}/bars")
def ticker_bars(
    ticker: str,
    days: int = Query(365, ge=5, le=3650),
    db: Database = Depends(get_db),
    account: int = Depends(account_id),
) -> dict[str, Any]:
    """Daily bars plus the two moving averages, shaped for the chart.

    Columns rather than rows: uPlot wants parallel arrays, and it roughly halves
    the payload for a few thousand sessions on a phone connection.

    No opening price: the provider layer keeps high, low and volume but not
    open, so the chart is a close line rather than candles. The column exists in
    ``daily_bars`` for when that changes.
    """
    symbol = ticker.upper()
    # Ask for more history than the window so the SMA200 is defined from the
    # first visible session instead of starting two hundred bars in.
    warmup = SLOW_SMA + 5
    bars = history_store.load_bars(db, symbol, limit=days + warmup)
    if not bars:
        raise HTTPException(status_code=404, detail=f"No hay velas guardadas de {symbol}.")

    closes = [b.close for b in bars]
    fast = _aligned(sma_series(closes, FAST_SMA), len(bars))
    slow = _aligned(sma_series(closes, SLOW_SMA), len(bars))

    keep = min(days, len(bars))
    start = len(bars) - keep
    window = bars[start:]
    return {
        "ticker": symbol,
        "sessions": len(window),
        "day": [b.day.isoformat() for b in window],
        "high": [b.high for b in window],
        "low": [b.low for b in window],
        "close": [b.close for b in window],
        "volume": [b.volume for b in window],
        "sma_fast": fast[start:],
        "sma_slow": slow[start:],
        "sma_fast_period": FAST_SMA,
        "sma_slow_period": SLOW_SMA,
    }


def _aligned(series: list[float], length: int) -> list[float | None]:
    """Pad a moving average with nulls so it lines up with the bars."""
    return [None] * (length - len(series)) + list(series)


@router.get("/tickers/{ticker}/indicators")
def ticker_indicator_series(
    ticker: str,
    name: str = Query("rsi"),
    limit: int = Query(500, ge=10, le=5000),
    db: Database = Depends(get_db),
    account: int = Depends(account_id),
) -> dict[str, Any]:
    """One indicator through time — the history nothing used to keep."""
    if name not in SERIES:
        raise HTTPException(status_code=400, detail=f"Indicador desconocido '{name}'.")
    points = history_store.indicator_series(db, ticker.upper(), name, limit=limit)
    return {
        "ticker": ticker.upper(),
        "name": name,
        "label": SERIES[name],
        "taken_at": [taken for taken, _ in points],
        "value": [value for _, value in points],
    }


@router.get("/tickers/{ticker}/alerts")
def ticker_alerts(ticker: str, db: Database = Depends(get_db),
    account: int = Depends(account_id),
) -> list[dict[str, Any]]:
    """Active and inactive alerts, each with how it last came out."""
    symbol = ticker.upper()
    out: list[dict[str, Any]] = []
    for alert in alerts_store.list_alerts(db, ticker=symbol, account_id=account):
        evaluations = runs_store.evaluations_for(db, alert.id, limit=1, account_id=account) if alert.id else []
        last = evaluations[-1] if evaluations else None
        out.append(
            {
                "id": alert.id,
                "kind": alert.kind,
                "params": dict(alert.params),
                "active": alert.active,
                "one_shot": alert.one_shot,
                "cooldown_hours": alert.cooldown_hours,
                "last_fired_at": alert.last_fired_at.isoformat() if alert.last_fired_at else None,
                "expires_at": alert.expires_at.isoformat() if alert.expires_at else None,
                "note": alert.note,
                "last_outcome": last["outcome"] if last else None,
                "last_evaluated_at": last["evaluated_at"] if last else None,
            }
        )
    return out


@router.get("/tickers/{ticker}/events")
def ticker_events(
    ticker: str,
    limit: int = Query(20, ge=1, le=200),
    db: Database = Depends(get_db),
    account: int = Depends(account_id),
) -> list[dict[str, Any]]:
    """What actually fired for this ticker, newest first."""
    symbol = ticker.upper()
    rows = db.execute(
        "SELECT * FROM alert_events WHERE ticker = ? AND account_id = ? "
        "AND deleted_at IS NULL ORDER BY fired_at DESC LIMIT ?",
        (symbol, account, limit),
    )
    from fa.store.serde import load_json

    return [
        {
            "id": row["id"],
            "kind": row["kind"],
            "title": row["title"],
            "message": row["message"],
            "severity": row["severity"],
            "price": row["price"],
            "fired_at": row["fired_at"],
            "acknowledged_at": row["acknowledged_at"],
            "delivered": load_json(row["delivered"], []),
        }
        for row in rows
    ]


# The columns the screen shows, split the way the terminal splits them: the
# headline numbers, then the ones that say whether the business is any good.
SUMMARY_FIELDS = list(SUMMARY_COLUMNS)
QUALITY_FIELDS = list(QUALITY_COLUMNS)


@router.post("/tickers/{ticker}/lookup", status_code=202)
def lookup_ticker(
    ticker: str = Path(pattern=TICKER_PATTERN),
    background: BackgroundTasks = None,  # noqa: B008 - FastAPI injects it
    db: Database = Depends(get_db),
    account: int = Depends(account_id),
) -> dict[str, Any]:
    """Bring in a ticker nobody is following yet.

    The terminal can analyse any symbol on the spot; the dashboard could only
    reach what was already stored, so a ticker you had never touched was
    unreachable. This is the write that introduces one: prices, indicators and
    statements, in the background.

    Looking one up does not start following it. That happens when you give it
    an alert or a position, which is what "following" has always meant here.
    """
    from fa.api.deps import build_market
    from fa.warm import has_prices, warm

    symbol = ticker.upper()
    if has_prices(db, symbol):
        return {"ticker": symbol, "status": "ready", "fetching": False}
    background.add_task(warm, db, build_market(db), symbol)
    return {"ticker": symbol, "status": "running", "fetching": True}


@router.get("/tickers/{ticker}/fundamentals")
def ticker_fundamentals(
    ticker: str,
    db: Database = Depends(get_db),
    account: int = Depends(account_id),
) -> dict[str, Any]:
    """The annual and quarterly metric tables, as last stored.

    A read, so it never fetches. When there is nothing yet the response says so
    and the client asks for a refresh, which is a write and may.
    """
    symbol = ticker.upper()
    stored = fundamentals_store.load_all(db, symbol)
    periods = {
        kind: {
            "rows": snapshot["rows"] if snapshot else [],
            "source": snapshot["source"] if snapshot else "",
            "fetched_at": snapshot["fetched_at"] if snapshot else None,
            "stale": fundamentals_store.is_stale(snapshot),
        }
        for kind, snapshot in stored.items()
    }
    estimated = any(
        row.get("Net_Debt_Estimated")
        for period in periods.values()
        for row in period["rows"]
    )
    return {
        "ticker": symbol,
        "summary_columns": SUMMARY_FIELDS,
        "quality_columns": QUALITY_FIELDS,
        "periods": periods,
        "missing": all(not p["rows"] for p in periods.values()),
        # The proxy is a real number with a caveat, and the caveat has to travel
        # with it: an estimated net debt propagates into EV and its yield.
        "net_debt_estimated": bool(estimated),
    }


@router.post("/tickers/{ticker}/fundamentals/refresh", status_code=202)
def refresh_fundamentals(
    ticker: str,
    background: BackgroundTasks,
    db: Database = Depends(get_db),
    account: int = Depends(account_id),
) -> dict[str, Any]:
    """Go and get the statements. A write, so this one is allowed to fetch."""
    from fa.api.deps import build_market
    from fa.warm import warm_fundamentals

    symbol = ticker.upper()
    background.add_task(warm_fundamentals, db, build_market(db), symbol, force=True)
    return {"ticker": symbol, "status": "running"}


@router.post("/tickers/{ticker}/check", status_code=202)
def check_now(
    ticker: str,
    background: BackgroundTasks,
    db: Database = Depends(get_db),
    account: int = Depends(account_id),
) -> dict[str, Any]:
    """Evaluate this ticker's alerts right now instead of waiting for the timer.

    A check fetches and can deliver, so it is not a read; it runs in the
    background and the client refreshes when the run is recorded.
    """
    from fa.api.deps import build_market
    from fa.config import load_settings
    from fa.notify.dispatcher import build_dispatcher

    symbol = ticker.upper()
    if not alerts_store.list_alerts(db, ticker=symbol, only_active=True, account_id=account):
        raise HTTPException(status_code=422, detail=f"{symbol} no tiene alertas activas.")

    def run() -> None:
        from fa.alerts.engine import run_checks

        settings = load_settings()
        try:
            run_checks(
                db,
                build_market(db),
                build_dispatcher(settings, echo=False),
                ticker=symbol,
                trigger="api",
            )
        except Exception:  # noqa: BLE001 - a failed check must not kill the worker
            logger.exception("on-demand check of %s failed", symbol)

    background.add_task(run)
    return {"ticker": symbol, "status": "running"}


@router.post("/tickers/{ticker}/refresh", status_code=202)
def refresh_prices(
    ticker: str,
    background: BackgroundTasks,
    db: Database = Depends(get_db),
    account: int = Depends(account_id),
) -> dict[str, Any]:
    """Re-fetch this ticker's prices right now, and nothing else.

    Distinct from ``/check`` on purpose: a check evaluates the alerts and can
    deliver a notification, which is far more than someone asking for a fresher
    number wants to set off. This only refills the bars and indicators.

    Asking for it is a write in the sense that matters here — the person asked,
    so the fetch is theirs, and the "reads never fetch" rule stays intact.
    """
    from fa.api.deps import build_market
    from fa.warm import warm

    symbol = ticker.upper()

    def run() -> None:
        try:
            warm(db, build_market(db), symbol, force=True)
        except Exception:
            # A provider being down must not kill the worker thread.
            logger.exception("on-demand refresh of %s failed", symbol)

    background.add_task(run)
    return {"ticker": symbol, "status": "running"}


@router.get("/health")
def health(db: Database = Depends(get_db),
    account: int = Depends(account_id),
) -> dict[str, Any]:
    """Is the scheduler alive? The question the CLI could never answer."""
    from fa.store import migrations

    state = dict(runs_store.health(db, account_id=account))
    tracked = _tracked(db, account)
    state.update(
        {
            "engine": db.dialect.name,
            "schema_version": migrations.current_version(db),
            "tracked_tickers": len(tracked),
            "stale": _is_stale(state.get("last_run_at")),
        }
    )
    return state


def _is_stale(last_run_at: str | None, tolerance_hours: int = 24) -> bool:
    """True when nothing has run recently enough to trust the numbers."""
    if not last_run_at:
        return True
    from datetime import datetime, timezone

    try:
        moment = datetime.fromisoformat(last_run_at)
    except ValueError:
        return True
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - moment > timedelta(hours=tolerance_hours)
