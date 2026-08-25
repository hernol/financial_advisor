"""Creating, silencing and deleting alerts, and acknowledging what fired.

Validation is not reimplemented here. Every request goes through
``fa.alerts.authoring``, the same path the CLI takes, so a rule that is invalid
in the terminal is invalid on the phone and the error text is the same one.

The reference price for a percentage alert comes from the last stored close.
The API never calls a provider, and creating an alert is no reason to break
that: an alert anchored to yesterday's close is honest, one that silently
waited three seconds for Yahoo is not.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Mapping

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from fa.alerts import authoring, kinds
from fa.api.auth import SUPABASE, Principal, account_id, current_principal, mode_for
from fa.api.deps import get_db
from fa.config import load_settings
from fa.errors import ValidationError
from fa.store import alerts as alerts_store
from fa.store import events as events_store
from fa.store import history as history_store
from fa.store.database import Database

router = APIRouter(prefix="/api", tags=["alerts"])


class AlertRequest(BaseModel):
    """A new alert. Params are validated against the kind's own catalogue."""

    kind: str
    params: dict[str, Any] = Field(default_factory=dict)
    cooldown_hours: int | None = Field(default=None, ge=0, le=24 * 365)
    one_shot: bool | None = None
    expires_at: date | None = None
    note: str = Field(default="", max_length=500)


class AlertPatch(BaseModel):
    active: bool


def _stored_price(db: Database, ticker: str) -> float | None:
    bars = history_store.load_bars(db, ticker, limit=1)
    return bars[-1].close if bars else None


def _as_dict(alert) -> dict[str, Any]:
    return {
        "id": alert.id,
        "ticker": alert.ticker,
        "kind": alert.kind,
        "params": dict(alert.params),
        "active": alert.active,
        "one_shot": alert.one_shot,
        "cooldown_hours": alert.cooldown_hours,
        "expires_at": alert.expires_at.isoformat() if alert.expires_at else None,
        "note": alert.note,
        "last_fired_at": alert.last_fired_at.isoformat() if alert.last_fired_at else None,
    }


@router.get("/session")
def session(principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    """Who the caller is, and how the server decided that.

    The client calls this on start-up: it is how the app knows whether to show
    a login screen at all, without hardcoding the deployment mode.
    """
    return {
        "mode": principal.mode,
        "account_id": principal.account_id,
        "email": principal.email,
        "anonymous": principal.is_anonymous,
    }


@router.get("/auth-mode")
def auth_mode() -> dict[str, Any]:
    """What credential this deployment wants. Deliberately unauthenticated:
    a login screen has to know what to ask for before it can ask for it."""
    settings = load_settings()
    mode = mode_for(settings)
    return {
        "mode": mode,
        "supabase_url": settings.supabase_url if mode == SUPABASE else "",
        "supabase_anon_key": settings.supabase_anon_key if mode == SUPABASE else "",
    }


@router.get("/alert-kinds")
def alert_kinds() -> list[dict[str, Any]]:
    """The catalogue, so the form can be built from it rather than hardcoded.

    A new alert kind then appears in the app without touching the client.
    """
    return [
        {
            "key": kind.key,
            "label": kind.label,
            "description": kind.description,
            "defaults": dict(kind.defaults),
            "required": list(kind.required),
            "requires_position": kind.requires_position,
            "one_shot": kind.one_shot,
            "choices": _choices(kind.key),
        }
        for kind in sorted(kinds.CATALOGUE.values(), key=lambda k: k.label)
    ]


def _choices(key: str) -> Mapping[str, list[str]]:
    """Which params are a closed set, so the form shows a picker not a text box."""
    options: dict[str, list[str]] = {}
    if key in {kinds.PCT_UP, kinds.PCT_DOWN}:
        options["reference"] = ["buy", "baseline"]
    if key == kinds.REL_STRENGTH:
        options["window"] = list(kinds.WINDOWS)
    if key == kinds.SMA_BREAK:
        options["direction"] = ["above", "below"]
    if key in {kinds.SMA_CROSS, kinds.MACD_CROSS}:
        options["direction"] = list(kinds.DIRECTIONS)
    return options


@router.post("/tickers/{ticker}/alerts", status_code=201)
def create_alert(
    ticker: str,
    body: AlertRequest,
    db: Database = Depends(get_db),
    account: int = Depends(account_id),
) -> dict[str, Any]:
    try:
        alert = authoring.create_alert(
            db,
            ticker,
            body.kind,
            body.params,
            price_for=lambda symbol: _stored_price(db, symbol),
            cooldown_hours=body.cooldown_hours,
            one_shot=body.one_shot,
            expires_at=body.expires_at,
            note=body.note,
            account_id=account,
        )
    except ValidationError as exc:
        # The user asked for something the catalogue refuses; that is a bad
        # request, not a server fault, and the message is already written for
        # a person to read.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _as_dict(alert)


@router.patch("/alerts/{alert_id}")
def set_alert_active(
    alert_id: int,
    body: AlertPatch,
    db: Database = Depends(get_db),
    account: int = Depends(account_id),
) -> dict[str, Any]:
    """Silence an alert without losing it or its history."""
    if not alerts_store.set_active(db, alert_id, body.active, account_id=account):
        raise HTTPException(status_code=404, detail=f"No existe la alerta {alert_id}.")
    alert = alerts_store.get_alert(db, alert_id, account_id=account)
    return _as_dict(alert)


@router.delete("/alerts/{alert_id}", status_code=204)
def delete_alert(
    alert_id: int,
    db: Database = Depends(get_db),
    account: int = Depends(account_id),
) -> None:
    """Soft delete: the rule stops running, everything it fired stays queryable."""
    if not alerts_store.delete_alert(db, alert_id, account_id=account):
        raise HTTPException(status_code=404, detail=f"No existe la alerta {alert_id}.")


@router.post("/events/{event_id}/ack")
def acknowledge_event(
    event_id: int,
    db: Database = Depends(get_db),
    account: int = Depends(account_id),
) -> dict[str, Any]:
    if not events_store.acknowledge(db, event_id, account_id=account):
        raise HTTPException(
            status_code=404, detail=f"No existe el aviso {event_id}, o ya estaba visto."
        )
    return {"id": event_id, "acknowledged": True}


@router.post("/events/ack")
def acknowledge_all(
    db: Database = Depends(get_db), account: int = Depends(account_id)
) -> dict[str, int]:
    return {"acknowledged": events_store.acknowledge_all(db, account_id=account)}
