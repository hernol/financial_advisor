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
REL_STRENGTH = "rel_strength"
ATR_STOP = "atr_stop"
VOLUME_SPIKE = "volume_spike"
NEW_52W_HIGH = "new_52w_high"
NEW_52W_LOW = "new_52w_low"
SMA_BREAK = "sma_break"
MACD_CROSS = "macd_cross"


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
    REL_STRENGTH: AlertKind(
        key=REL_STRENGTH,
        label="Pierde contra el índice",
        description=(
            "Avisa cuando el ticker rinde X puntos porcentuales menos que el "
            "benchmark en la ventana elegida (1m/3m/6m/12m)."
        ),
        defaults={"pct": 10.0, "window": "3m"},
        required=("pct",),
    ),
    ATR_STOP: AlertKind(
        key=ATR_STOP,
        label="Stop por ATR",
        description=(
            "Trailing stop dimensionado por volatilidad: dispara si el precio cae "
            "N veces el ATR desde el máximo. Con lookback_days=0 el máximo es el "
            "posterior a la compra; con un valor mayor sólo mira esa ventana."
        ),
        defaults={"multiple": 3.0, "period": 14, "lookback_days": 0},
        required=("multiple",),
        requires_position=True,
    ),
    VOLUME_SPIKE: AlertKind(
        key=VOLUME_SPIKE,
        label="Volumen inusual",
        description="Avisa cuando el volumen del día supera N veces su promedio reciente.",
        defaults={"ratio": 2.5, "period": 20},
        required=("ratio",),
    ),
    NEW_52W_HIGH: AlertKind(
        key=NEW_52W_HIGH,
        label="Máximo de 52 semanas",
        description="Avisa cuando el precio marca un nuevo máximo de 52 semanas.",
        defaults={"tolerance_pct": 0.0},
    ),
    NEW_52W_LOW: AlertKind(
        key=NEW_52W_LOW,
        label="Mínimo de 52 semanas",
        description="Avisa cuando el precio marca un nuevo mínimo de 52 semanas.",
        defaults={"tolerance_pct": 0.0},
    ),
    SMA_BREAK: AlertKind(
        key=SMA_BREAK,
        label="Cruce de la media",
        description=(
            "Filtro de tendencia: avisa cuando el precio cruza su media móvil "
            "(por defecto la SMA200) en la dirección indicada."
        ),
        defaults={"period": 200, "direction": "below"},
    ),
    MACD_CROSS: AlertKind(
        key=MACD_CROSS,
        label="Cruce de MACD",
        description=(
            "Avisa cuando la línea MACD cruza su señal en la última rueda, si el "
            "cruce tiene cuerpo. min_strength mide la separación entre las líneas "
            "en unidades del movimiento diario típico del papel: 0.02 descarta el "
            "cuarto más flojo de los cruces, 0 avisa de todos."
        ),
        defaults={
            "fast": 12, "slow": 26, "signal": 9, "direction": "any",
            "min_strength": 0.02,
        },
    ),
}

NUMERIC_FIELDS = {
    "min_strength",
    "pct",
    "price",
    "days",
    "months",
    "fast",
    "slow",
    "signal",
    "period",
    "overbought",
    "oversold",
    "lookback_days",
    "multiple",
    "ratio",
    "tolerance_pct",
}
INTEGER_FIELDS = {"fast", "slow", "signal", "period", "days", "months", "lookback_days"}
WINDOWS = ("1m", "3m", "6m", "12m")
DIRECTIONS = ("any", "above", "below")


def get_kind(key: str) -> AlertKind:
    kind = CATALOGUE.get(key)
    if kind is None:
        raise ValidationError(f"No existe la alerta '{key}'. Válidas: {', '.join(sorted(CATALOGUE))}.")
    return kind


def normalize_params(key: str, params: Mapping[str, Any] | None) -> dict[str, Any]:
    """Merge user params over defaults and validate them."""
    kind = get_kind(key)
    merged: dict[str, Any] = {**kind.defaults, **(params or {})}
    for field_name in kind.required:
        if merged.get(field_name) in (None, ""):
            raise ValidationError(f"La alerta '{key}' necesita el parámetro '{field_name}'.")
    for name, value in list(merged.items()):
        if name in NUMERIC_FIELDS and value is not None:
            try:
                merged[name] = int(value) if name in INTEGER_FIELDS else float(value)
            except (TypeError, ValueError) as exc:
                raise ValidationError(f"El parámetro '{name}' de '{key}' tiene que ser un número.") from exc
            if merged[name] < 0:
                raise ValidationError(f"El parámetro '{name}' de '{key}' no puede ser negativo.")
    if key == SMA_CROSS and merged["fast"] >= merged["slow"]:
        raise ValidationError("En sma_cross la media rápida tiene que ser menor que la lenta.")
    if key in {PCT_UP, PCT_DOWN} and merged.get("reference") not in {"buy", "baseline"}:
        raise ValidationError("La referencia tiene que ser 'buy' (precio de compra) o 'baseline' (precio de hoy).")
    if key == PERIOD_ELAPSED and not (merged.get("months") or merged.get("days")):
        raise ValidationError("period_elapsed necesita 'months' o 'days'.")
    if key == MACD_CROSS and merged["fast"] >= merged["slow"]:
        raise ValidationError("En macd_cross la media rápida tiene que ser menor que la lenta.")
    if key == REL_STRENGTH and merged.get("window") not in WINDOWS:
        raise ValidationError(f"La ventana tiene que ser una de: {', '.join(WINDOWS)}.")
    if key == SMA_BREAK and merged.get("direction") not in {"above", "below"}:
        raise ValidationError("sma_break necesita dirección 'above' (hacia arriba) o 'below' (hacia abajo).")
    if key == MACD_CROSS and merged.get("direction") not in DIRECTIONS:
        raise ValidationError(f"La dirección tiene que ser una de: {', '.join(DIRECTIONS)}.")
    return merged
