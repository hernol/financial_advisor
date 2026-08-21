"""Catalogue of alert kinds with their parameters and validation."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from fa.errors import ValidationError

PCT_UP = "pct_up"
PCT_DOWN = "pct_down"
PRICE_ABOVE = "price_above"
PRICE_BELOW = "price_below"
PERIOD_ELAPSED = "period_elapsed"
EARNINGS_NEAR = "earnings_near"
TRAILING_STOP = "trailing_stop"
SMA_CROSS = "sma_cross"
RSI = "rsi"
DIVIDEND_EX_NEAR = "dividend_ex_near"
SPLIT_DETECTED = "split_detected"


@dataclass(frozen=True)
class AlertKind:
    """Metadata describing one alert type."""

    key: str
    label: str
    description: str
    defaults: Mapping[str, Any] = field(default_factory=dict)
    required: tuple[str, ...] = ()
    requires_position: bool = False
    one_shot: bool = False


CATALOGUE: dict[str, AlertKind] = {
    PCT_UP: AlertKind(
        key=PCT_UP,
        label="Sube X%",
        description="Dispara cuando el precio sube X% sobre la referencia (compra o baseline).",
        defaults={"pct": 10.0, "reference": "buy"},
        required=("pct",),
    ),
    PCT_DOWN: AlertKind(
        key=PCT_DOWN,
        label="Baja X%",
        description="Dispara cuando el precio cae X% bajo la referencia (compra o baseline).",
        defaults={"pct": 10.0, "reference": "buy"},
        required=("pct",),
    ),
    PRICE_ABOVE: AlertKind(
        key=PRICE_ABOVE,
        label="Precio por encima de",
        description="Dispara cuando el precio supera un valor objetivo.",
        defaults={"price": 0.0},
        required=("price",),
        one_shot=True,
    ),
    PRICE_BELOW: AlertKind(
        key=PRICE_BELOW,
        label="Precio por debajo de",
        description="Dispara cuando el precio perfora un valor objetivo.",
        defaults={"price": 0.0},
        required=("price",),
        one_shot=True,
    ),
    PERIOD_ELAPSED: AlertKind(
        key=PERIOD_ELAPSED,
        label="Pasó un período desde la compra",
        description="Recordatorio de revisión: N meses o días desde la fecha de compra.",
        defaults={"months": 6},
        requires_position=True,
        one_shot=True,
    ),
    EARNINGS_NEAR: AlertKind(
        key=EARNINGS_NEAR,
        label="Earnings cerca",
        description="Avisa N días antes del próximo earnings call.",
        defaults={"days": 7},
    ),
    TRAILING_STOP: AlertKind(
        key=TRAILING_STOP,
        label="Trailing stop",
        description="Avisa si el precio cae X% desde el máximo alcanzado tras la compra.",
        defaults={"pct": 15.0},
        required=("pct",),
        requires_position=True,
    ),
    SMA_CROSS: AlertKind(
        key=SMA_CROSS,
        label="Cruce de medias",
        description="Golden/death cross entre dos medias móviles simples.",
        defaults={"fast": 50, "slow": 200, "direction": "any"},
    ),
    RSI: AlertKind(
        key=RSI,
        label="RSI sobrecompra/sobreventa",
        description="Avisa cuando el RSI cruza los umbrales configurados.",
        defaults={"period": 14, "overbought": 70.0, "oversold": 30.0},
    ),
    DIVIDEND_EX_NEAR: AlertKind(
        key=DIVIDEND_EX_NEAR,
        label="Ex-dividend cerca",
        description="Avisa N días antes de la próxima fecha ex-dividendo.",
        defaults={"days": 7},
    ),
    SPLIT_DETECTED: AlertKind(
        key=SPLIT_DETECTED,
        label="Split detectado",
        description="Avisa si hubo un split después de la compra (hay que ajustar el costo).",
        defaults={"lookback_days": 90},
        requires_position=True,
    ),
}

NUMERIC_FIELDS = {"pct", "price", "days", "months", "fast", "slow", "period", "overbought", "oversold", "lookback_days"}


def get_kind(key: str) -> AlertKind:
    kind = CATALOGUE.get(key)
    if kind is None:
        raise ValidationError(f"Unknown alert kind '{key}'. Valid: {', '.join(sorted(CATALOGUE))}")
    return kind


def normalize_params(key: str, params: Mapping[str, Any] | None) -> dict[str, Any]:
    """Merge user params over defaults and validate them."""
    kind = get_kind(key)
    merged: dict[str, Any] = {**kind.defaults, **(params or {})}
    for field_name in kind.required:
        if merged.get(field_name) in (None, ""):
            raise ValidationError(f"Alert '{key}' requires parameter '{field_name}'")
    for name, value in list(merged.items()):
        if name in NUMERIC_FIELDS and value is not None:
            try:
                merged[name] = float(value) if name not in {"fast", "slow", "period", "days", "months", "lookback_days"} else int(value)
            except (TypeError, ValueError) as exc:
                raise ValidationError(f"Parameter '{name}' of alert '{key}' must be numeric") from exc
            if merged[name] < 0:
                raise ValidationError(f"Parameter '{name}' of alert '{key}' must be positive")
    if key == SMA_CROSS and merged["fast"] >= merged["slow"]:
        raise ValidationError("sma_cross requires fast < slow")
    if key in {PCT_UP, PCT_DOWN} and merged.get("reference") not in {"buy", "baseline"}:
        raise ValidationError("reference must be 'buy' or 'baseline'")
    if key == PERIOD_ELAPSED and not (merged.get("months") or merged.get("days")):
        raise ValidationError("period_elapsed requires 'months' or 'days'")
    return merged
