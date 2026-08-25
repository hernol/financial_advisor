"""Creating an alert, with one definition of what is valid.

The CLI, the interactive menu and the API all end up here. Duplicating the
rules — which kinds need a position, what the defaults are, how a percentage
alert anchors its reference — is how two front ends start disagreeing about
what the same alert means.

The one thing that genuinely differs is where the reference price comes from.
The CLI can ask a provider; the API must not, because a viewer creating an
alert should never trigger a download. So the caller passes a lookup and this
module stays ignorant of the difference.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Callable, Mapping

from fa.alerts import kinds
from fa.errors import ValidationError
from fa.models import Alert
from fa.store import alerts as alerts_store
from fa.store import positions as positions_store
from fa.store.database import Database
from fa.store.schema import LOCAL_ACCOUNT_ID

PriceLookup = Callable[[str], float | None]

DEFAULT_COOLDOWN_HOURS = 24


def create_alert(
    conn: Database,
    ticker: str,
    kind: str,
    params: Mapping[str, Any] | None = None,
    *,
    price_for: PriceLookup,
    position_id: int | None = None,
    cooldown_hours: int | None = None,
    default_cooldown_hours: int = DEFAULT_COOLDOWN_HOURS,
    one_shot: bool | None = None,
    expires_at: date | None = None,
    note: str = "",
    account_id: int = LOCAL_ACCOUNT_ID,
) -> Alert:
    """Validate and persist an alert, resolving the position it belongs to."""
    definition = kinds.get_kind(kind)
    resolved = kinds.normalize_params(kind, params)
    symbol = ticker.upper()

    if position_id is None:
        open_positions = positions_store.positions_for_ticker(conn, symbol, account_id=account_id)
        position_id = open_positions[0].id if open_positions else None
    if definition.requires_position and position_id is None:
        raise ValidationError(
            f"La alerta '{kind}' necesita una posición cargada para {symbol} "
            "(agregá la compra primero)."
        )
    if kind in {kinds.PCT_UP, kinds.PCT_DOWN}:
        resolved = resolve_reference(symbol, resolved, position_id, price_for)

    alert = Alert(
        ticker=symbol,
        kind=kind,
        params=resolved,
        position_id=position_id,
        one_shot=definition.one_shot if one_shot is None else one_shot,
        cooldown_hours=default_cooldown_hours if cooldown_hours is None else cooldown_hours,
        expires_at=expires_at,
        note=note,
    )
    return alerts_store.add_alert(conn, alert, account_id=account_id)


def resolve_reference(
    ticker: str,
    params: dict[str, Any],
    position_id: int | None,
    price_for: PriceLookup,
) -> dict[str, Any]:
    """A 'baseline' percentage alert freezes a reference price at creation."""
    if params.get("reference") == "buy":
        if position_id is None:
            raise ValidationError(
                "reference='buy' necesita una posición; usá reference='baseline' "
                "para anclar al precio actual."
            )
        return params
    if params.get("baseline_price"):
        return params
    price = price_for(ticker)
    if price is None:
        raise ValidationError(
            f"No hay precio para anclar la referencia de {ticker}. "
            "Pasá 'baseline_price' o esperá a que el próximo chequeo traiga datos."
        )
    return {**params, "baseline_price": price}
