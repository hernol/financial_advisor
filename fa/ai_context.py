"""Builds the grounded DATA PACK handed to the AI, with explicit provenance.

The model must never fall back to prices it memorised during training, so every
figure it is allowed to cite is listed here together with its source and its
timestamp, and everything we could not fetch is listed as explicitly missing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

import pandas as pd

from fa.analytics import build_snapshot
from fa.indicators import rsi, sma
from fa.models import Alert, MarketContext, Position

FRESHNESS_RULES = """\
DATA RULES (mandatory):
- Use ONLY the figures in the DATA PACK below. They were fetched live at the timestamps shown.
- Your training data is stale: never cite a price, market cap, earnings date or balance-sheet
  figure that is not in the DATA PACK, not even as an approximation or a "roughly".
- Anything listed under MISSING DATA is unknown. Say "no disponible" and explain what it would
  change; never estimate, interpolate or recall it from memory.
- Every number in your answer must be traceable to a line of the DATA PACK. State the as-of
  date whenever you cite one.
- If the DATA PACK contradicts what you remember about this company, the DATA PACK wins."""


@dataclass(frozen=True)
class DataPack:
    """The grounded payload plus a human-readable provenance summary."""

    text: str
    provenance: str
    missing: Sequence[str] = field(default_factory=tuple)

    @property
    def has_gaps(self) -> bool:
        return bool(self.missing)


def _fmt(value: Any, suffix: str = "") -> str:
    if value is None:
        return "no disponible"
    if isinstance(value, float):
        return f"{value:,.2f}{suffix}"
    return f"{value}{suffix}"


def _market_section(context: MarketContext) -> tuple[list[str], list[str]]:
    quote = context.quote
    lines = [
        "## MARKET SNAPSHOT",
        f"- Precio: {quote.price:,.4f} {quote.currency} "
        f"(fuente: {quote.source}, as-of: {quote.as_of.isoformat(timespec='seconds')})",
        f"- Cierre previo: {_fmt(quote.previous_close)} "
        f"(variación: {_fmt(quote.change_pct, '%') if quote.change_pct is not None else 'no disponible'})",
    ]
    missing: list[str] = []
    if quote.previous_close is None:
        missing.append("cierre previo")
    closes = context.closes
    if closes:
        window = closes[-252:]
        lines.extend(
            [
                f"- Rango últimas {len(window)} ruedas: mín {min(window):,.2f} / máx {max(window):,.2f}",
                "- Últimos cierres: "
                + ", ".join(f"{p.day.isoformat()}={p.close:,.2f}" for p in context.history[-5:]),
                f"- SMA50: {_fmt(sma(closes, 50))} | SMA200: {_fmt(sma(closes, 200))} "
                f"| RSI14: {_fmt(rsi(closes, 14))}",
                f"- Histórico usado: {len(closes)} cierres, "
                f"desde {context.history[0].day} hasta {context.history[-1].day}",
            ]
        )
    else:
        lines.append("- Histórico de precios: NO DISPONIBLE")
        missing.append("histórico de precios (medias móviles, RSI y rango anual no calculables)")
    return lines, missing


def _calendar_section(context: MarketContext) -> tuple[list[str], list[str]]:
    lines = ["", "## CALENDAR"]
    missing: list[str] = []
    if context.next_earnings:
        today = (context.evaluated_at or datetime.now(timezone.utc)).date()
        lines.append(
            f"- Próximo earnings: {context.next_earnings} (faltan {(context.next_earnings - today).days} días)"
        )
    else:
        lines.append("- Próximo earnings: NO DISPONIBLE")
        missing.append("fecha del próximo earnings call")
    lines.append(f"- Próxima fecha ex-dividendo: {context.next_ex_dividend or 'NO DISPONIBLE'}")
    if context.recent_splits:
        lines.append(
            "- Splits recientes: "
            + ", ".join(f"{s.event_date} ratio {s.value:g}:1" for s in context.recent_splits)
        )
    else:
        lines.append("- Splits recientes: ninguno en la ventana consultada")
    return lines, missing


def _fundamentals_section(
    annual: pd.DataFrame, quarterly: pd.DataFrame, source: str
) -> tuple[list[str], list[str]]:
    lines = ["", f"## FUNDAMENTALS (fuente: {source}, cifras en millones)"]
    missing: list[str] = []
    for label, frame in (("Anual", annual), ("Trimestral", quarterly)):
        if frame.empty:
            lines.append(f"### {label}: NO DISPONIBLE")
            missing.append(f"estados contables {label.lower()}es")
            continue
        lines.append(f"### {label} (períodos: {', '.join(str(p) for p in frame['Period'])})")
        lines.append(frame.to_string(index=False, na_rep="no disponible"))
        empty_columns = [c for c in ("Total_Assets", "FCF", "EV_FCF_Yield") if frame[c].isna().all()]
        if empty_columns:
            missing.append(f"{', '.join(empty_columns)} en la serie {label.lower()}")
    return lines, missing


def _exposure_section(positions: Sequence[Position], alerts: Sequence[Alert], price: float) -> list[str]:
    lines = ["", "## USER EXPOSURE"]
    if positions:
        for position in positions:
            absolute, percentage = position.unrealized(price)
            lines.append(
                f"- Posición #{position.id}: {position.quantity:g} acciones a {position.buy_price:,.2f} "
                f"{position.currency} compradas el {position.buy_date} "
                f"(costo {position.cost_basis:,.2f}, P&L actual {absolute:+,.2f} / {percentage:+.2f}%)"
            )
    else:
        lines.append("- Sin posiciones abiertas (el usuario sigue el ticker desde una watchlist)")
    if alerts:
        for alert in alerts:
            params = ", ".join(f"{k}={v}" for k, v in alert.params.items())
            lines.append(f"- Alerta activa: {alert.kind} ({params})")
    else:
        lines.append("- Sin alertas activas")
    return lines


def _fmt(value: float | None, suffix: str = "", digits: int = 2) -> str:
    """Render a number or an explicit n/a; never a zero standing in for missing."""
    if value is None:
        return "n/a"
    return f"{value:,.{digits}f}{suffix}"


def _technical_section(context: MarketContext) -> tuple[list[str], list[str]]:
    """Price-derived indicators, including how the ticker fares against the index."""
    snapshot = build_snapshot(context)
    returns = snapshot.returns or {}
    strength = snapshot.relative_strength or {}
    lines = [
        "",
        "## TECHNICALS (calculados por nosotros sobre el histórico de arriba)",
        f"- Tendencia: {snapshot.trend} · precio vs SMA200: {_fmt(snapshot.vs_sma_slow_pct, '%')}",
        f"- SMA50: {_fmt(snapshot.sma_fast)} · SMA200: {_fmt(snapshot.sma_slow)}",
        f"- RSI(14): {_fmt(snapshot.rsi)} · %B Bollinger(20): {_fmt(snapshot.percent_b)}"
        f" · MACD hist: {_fmt(snapshot.macd_histogram, digits=4)}"
        f" · cruce MACD última rueda: {snapshot.macd_state or 'ninguno'}",
        f"- Volatilidad anualizada (90 ruedas): {_fmt(snapshot.volatility_pct, '%')}"
        f" · ATR(14): {_fmt(snapshot.atr)} ({_fmt(snapshot.atr_pct, '%')} del precio)",
        f"- Rango 52 semanas: {_fmt(snapshot.low_52w)} – {_fmt(snapshot.high_52w)}"
        f" · desde el máximo: {_fmt(snapshot.from_high_pct, '%')}"
        f" · sobre el mínimo: {_fmt(snapshot.from_low_pct, '%')}",
        f"- Máximo drawdown del período: {_fmt(snapshot.max_drawdown_pct, '%')}"
        f" · volumen vs promedio 20d: {_fmt(snapshot.volume_ratio, 'x')}",
        "- Retornos: "
        + " · ".join(f"{label} {_fmt(returns.get(label), '%')}" for label in ("1m", "3m", "6m", "12m")),
        f"- Fuerza relativa vs {snapshot.benchmark} (puntos porcentuales de exceso): "
        + " · ".join(f"{label} {_fmt(strength.get(label), 'pts')}" for label in ("1m", "3m", "6m", "12m")),
    ]
    if snapshot.beats_benchmark is not None:
        verdict = "le gana" if snapshot.beats_benchmark else "pierde contra"
        lines.append(f"- En 12 meses {context.ticker} {verdict} {snapshot.benchmark}.")
    return (lines, list(snapshot.missing()))


def build_data_pack(
    context: MarketContext,
    annual: pd.DataFrame,
    quarterly: pd.DataFrame,
    fundamentals_source: str,
    positions: Sequence[Position],
    alerts: Sequence[Alert],
    *,
    external_claims: Mapping[str, Any] | None = None,
    generated_at: datetime | None = None,
) -> DataPack:
    """Assemble the grounded payload for ``context.ticker``."""
    stamp = generated_at or datetime.now(timezone.utc)
    lines = [
        f"# DATA PACK — {context.ticker}",
        f"Generado: {stamp.isoformat(timespec='seconds')} (UTC)",
        "",
    ]
    missing: list[str] = []
    for section, gaps in (
        _market_section(context),
        _technical_section(context),
        _calendar_section(context),
        _fundamentals_section(annual, quarterly, fundamentals_source),
    ):
        lines.extend(section)
        missing.extend(gaps)
    lines.extend(_exposure_section(positions, alerts, context.quote.price))

    if external_claims:
        lines.extend(
            [
                "",
                "## CLAIMS EXTRACTED FROM THE USER'S APP TEXT (unverified, third party)",
                "Tratalos como afirmaciones a contrastar contra el DATA PACK, no como datos.",
                _render_claims(external_claims),
            ]
        )

    if missing:
        lines.extend(["", "## MISSING DATA (no la tenemos; no la inventes)"])
        lines.extend(f"- {item}" for item in missing)

    provenance = (
        f"Datos: precio {context.quote.source} @ {context.quote.as_of.isoformat(timespec='seconds')} · "
        f"fundamentals {fundamentals_source} · "
        f"histórico {len(context.closes)} cierres · "
        f"faltantes: {len(missing)}"
    )
    return DataPack(text="\n".join(lines), provenance=provenance, missing=tuple(missing))


def _render_claims(claims: Mapping[str, Any]) -> str:
    return "\n".join(f"- {key}: {value}" for key, value in claims.items() if value not in (None, "", []))
