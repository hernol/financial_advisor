"""The AI report and its suggestions, from the phone.

An analysis fetches fundamentals and calls a remote model, so it takes tens of
seconds and does reach a provider. That is allowed for the same reason warming
a new ticker is: it is a write, the user asked for it explicitly, and it
persists something. What is not acceptable is holding an HTTP request open for
a minute, so it runs in the background and the client watches the job.

The job registry lives in memory. That is honest for a single process and stops
being true the day this runs on more than one; the reports themselves are in the
database, so the worst a restart costs is the status of one run in flight.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field

from fa import reporting
from fa.alerts.suggestions import actionable
from fa.api.auth import account_id
from fa.api.deps import build_market, get_db
from fa.config import load_settings
from fa.errors import FinancialAnalyzerError
from fa.localai import LocalAIClient
from fa.store import events as events_store
from fa.store import history as history_store
from fa.store import suggestions as suggestions_store
from fa.store.database import Database

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["analysis"])

RUNNING = "running"
DONE = "done"
ERROR = "error"

_jobs: dict[tuple[int, str], dict[str, Any]] = {}
_jobs_lock = threading.Lock()


def _job_key(account: int, ticker: str) -> tuple[int, str]:
    return (account, ticker.upper())


def _set_job(account: int, ticker: str, **fields: Any) -> None:
    with _jobs_lock:
        job = _jobs.setdefault(_job_key(account, ticker), {})
        job.update(fields)
        job["at"] = datetime.now(timezone.utc).isoformat()


# A process that dies mid-report leaves its job marked running forever, and a
# 409 that never clears would be worse than a duplicate report.
STALE_AFTER = timedelta(minutes=15)


def job_state(account: int, ticker: str) -> dict[str, Any]:
    with _jobs_lock:
        job = dict(_jobs.get(_job_key(account, ticker), {}))
    if job.get("status") == RUNNING and _is_stale(job.get("at")):
        job["status"] = ERROR
        job["detail"] = "El informe quedó sin terminar. Probá de nuevo."
    return job


def _is_stale(stamp: str | None) -> bool:
    if not stamp:
        return True
    try:
        started = datetime.fromisoformat(stamp)
    except ValueError:
        return True
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - started > STALE_AFTER


def reset_jobs() -> None:
    """Forget every job. Used by the tests, and safe at any time: the reports
    themselves live in the database, not here."""
    with _jobs_lock:
        _jobs.clear()


class AnalysisRequest(BaseModel):
    context: str = Field(default="", max_length=20000)


def _run(db: Database, account: int, ticker: str, context: str) -> None:
    settings = load_settings()
    try:
        report = reporting.run_report(
            db,
            build_market(db),
            settings,
            LocalAIClient.from_settings(settings),
            ticker,
            context,
            account_id=account,
        )
    except FinancialAnalyzerError as exc:
        # The domain errors already say what is missing — a key, a provider, a
        # model — in words meant for a person, so they are passed through.
        _set_job(account, ticker, status=ERROR, detail=str(exc))
        return
    except Exception as exc:  # noqa: BLE001 - a failed report must not kill the worker
        logger.exception("analysis of %s failed", ticker)
        _set_job(account, ticker, status=ERROR, detail=f"{type(exc).__name__}: {exc}")
        return
    _set_job(
        account,
        ticker,
        status=DONE,
        detail="",
        analysis_id=report.analysis_id,
        suggestions=len(report.suggestions),
    )


@router.post("/tickers/{ticker}/analysis", status_code=202)
def request_analysis(
    ticker: str,
    body: AnalysisRequest,
    background: BackgroundTasks,
    db: Database = Depends(get_db),
    account: int = Depends(account_id),
) -> dict[str, Any]:
    """Queue an analysis. Returns immediately; watch the status endpoint."""
    settings = load_settings()
    if not settings.gemini_api_key:
        raise HTTPException(
            status_code=422,
            detail="Falta GEMINI_API_KEY: sin eso no se puede pedir el informe.",
        )
    symbol = ticker.upper()
    if job_state(account, symbol).get("status") == RUNNING:
        raise HTTPException(status_code=409, detail=f"Ya hay un informe de {symbol} en curso.")

    _set_job(account, symbol, status=RUNNING, detail="", analysis_id=None)
    background.add_task(_run, db, account, symbol, body.context)
    return {"ticker": symbol, "status": RUNNING}


@router.get("/tickers/{ticker}/analysis/status")
def analysis_status(
    ticker: str,
    db: Database = Depends(get_db),
    account: int = Depends(account_id),
) -> dict[str, Any]:
    state = job_state(account, ticker)
    return {"ticker": ticker.upper(), "status": state.get("status", "idle"), **state}


@router.get("/tickers/{ticker}/analyses")
def list_analyses(
    ticker: str,
    limit: int = 5,
    db: Database = Depends(get_db),
    account: int = Depends(account_id),
) -> list[dict[str, Any]]:
    """Reports already written for this ticker, newest first."""
    rows = events_store.recent_analyses(db, ticker, limit=limit, account_id=account)
    return [
        {
            "id": row["id"],
            "ticker": row["ticker"],
            "model": row["model"],
            "report": row["report"],
            "created_at": row["created_at"],
            # The provenance line is the first block of the stored metrics, and
            # it is what says which numbers the model was actually given.
            "provenance": (row["metrics"] or "").split("\n\n")[0],
        }
        for row in rows
    ]


def _as_dict(suggestion) -> dict[str, Any]:
    return {
        "id": suggestion.id,
        "ticker": suggestion.ticker,
        "category": suggestion.category,
        "kind": suggestion.kind,
        "params": dict(suggestion.params),
        "rationale": suggestion.rationale,
        "priority": suggestion.priority,
        "status": suggestion.status,
        "headline": suggestion.headline,
        "model": suggestion.model,
        "actionable": suggestion.category == "alert" and bool(suggestion.kind),
    }


@router.get("/suggestions")
def list_suggestions(
    ticker: str | None = None,
    status: str = suggestions_store.PENDING,
    db: Database = Depends(get_db),
    account: int = Depends(account_id),
) -> list[dict[str, Any]]:
    rows = suggestions_store.list_suggestions(
        db, ticker=ticker, status=status, account_id=account
    )
    return [_as_dict(s) for s in rows]


class AcceptRequest(BaseModel):
    params: dict[str, Any] = Field(default_factory=dict)


@router.post("/suggestions/{suggestion_id}/accept", status_code=201)
def accept_suggestion(
    suggestion_id: int,
    body: AcceptRequest,
    db: Database = Depends(get_db),
    account: int = Depends(account_id),
) -> dict[str, Any]:
    """Turn a suggestion into a real alert, optionally tuning its parameters."""
    suggestion = _find(db, suggestion_id, account)
    settings = load_settings()
    try:
        alert = reporting.accept(
            db,
            suggestion,
            body.params,
            price_for=lambda symbol: _stored_price(db, symbol),
            default_cooldown_hours=settings.default_cooldown_hours,
            account_id=account,
        )
    except FinancialAnalyzerError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "alert_id": alert.id,
        "ticker": alert.ticker,
        "kind": alert.kind,
        "params": dict(alert.params),
    }


@router.post("/suggestions/{suggestion_id}/reject")
def reject_suggestion(
    suggestion_id: int,
    db: Database = Depends(get_db),
    account: int = Depends(account_id),
) -> dict[str, Any]:
    suggestion = _find(db, suggestion_id, account)
    reporting.reject(db, suggestion, account_id=account)
    return {"id": suggestion_id, "status": suggestions_store.REJECTED}


def _find(db: Database, suggestion_id: int, account: int):
    for status in (suggestions_store.PENDING, None):
        for suggestion in suggestions_store.list_suggestions(
            db, status=status, limit=500, account_id=account
        ):
            if suggestion.id == suggestion_id:
                return suggestion
    raise HTTPException(status_code=404, detail=f"No existe la sugerencia {suggestion_id}.")


def _stored_price(db: Database, ticker: str) -> float | None:
    bars = history_store.load_bars(db, ticker, limit=1)
    return bars[-1].close if bars else None


__all__ = ["router", "actionable"]
