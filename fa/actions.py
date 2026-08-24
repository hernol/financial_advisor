"""High level operations shared by the CLI and the interactive menu."""
from __future__ import annotations

import logging
from datetime import date
from typing import Any, Mapping

import pandas as pd

from fa import ai
from fa.ai_context import DataPack, build_data_pack
from fa.alerts import kinds
from fa.alerts.engine import CheckReport, run_checks
from fa.alerts.suggestions import actionable
from fa.app import App
from fa.digest import collect_facts
from fa.errors import ValidationError
from fa.local_tasks import extract_claims, portfolio_digest
from fa.metrics import QUALITY_COLUMNS, SUMMARY_COLUMNS
from fa.models import AIReport, Alert, MarketContext, Position, Quote, Suggestion
from fa.portfolio import build_portfolio
from fa.store import alerts as alerts_store
from fa.store import events as events_store
from fa.store import positions as positions_store
from fa.store import suggestions as suggestions_store
from fa.ui.charts import draw_bar_chart, print_table

logger = logging.getLogger(__name__)


def snapshot_text(quote: Quote) -> str:
    change = f" ({quote.change_pct:+.2f}% vs cierre previo)" if quote.change_pct is not None else ""
    return (
        f"{quote.source}: {quote.ticker} = {quote.price:,.2f} {quote.currency}{change} "
        f"@ {quote.as_of.isoformat(timespec='seconds')}"
    )


def load_analysis(app: App, ticker: str) -> tuple[pd.DataFrame, pd.DataFrame, MarketContext, str]:
    """Fetch live fundamentals and build the metric tables."""
    oldest = _oldest_buy_date(app, ticker)
    annual, quarterly, fundamentals, context = app.market.analysis_tables(ticker, since=oldest)
    return annual, quarterly, context, fundamentals.source


def _oldest_buy_date(app: App, ticker: str):
    positions = positions_store.positions_for_ticker(app.conn, ticker)
    return min((p.buy_date for p in positions), default=None)


def show_metrics(frame: pd.DataFrame, ticker: str, title: str, unit_label: str) -> None:
    print(f"\n📊 {title} — {ticker}")
    print_table(frame, SUMMARY_COLUMNS)
    if _has_quality_data(frame):
        print(f"\n🏭 CALIDAD DEL NEGOCIO ({unit_label})")
        print_table(frame, QUALITY_COLUMNS)
    if _estimated_debt(frame):
        print(
            "   ⚠️  El proveedor no reportó deuda total y caja en algún período: "
            "ahí Net_Debt es una estimación y el EV la hereda."
        )
    draw_bar_chart(frame["FCF"].tolist(), frame["Period"].tolist(), f"FCF {unit_label} (millones)")
    draw_bar_chart(
        frame["EV_FCF_Yield"].tolist(), frame["Period"].tolist(), f"EV-Based FCF Yield {unit_label} (%)", unit="%"
    )


def _has_quality_data(frame: pd.DataFrame) -> bool:
    """True when at least one income-statement ratio could be computed."""
    columns = [c for c in QUALITY_COLUMNS if c != "Period" and c in frame.columns]
    return any(frame[column].notna().any() for column in columns)


def _estimated_debt(frame: pd.DataFrame) -> bool:
    if "Net_Debt_Estimated" not in frame.columns:
        return False
    return bool(frame["Net_Debt_Estimated"].fillna(False).any())


def build_data_pack_for(app: App, ticker: str, external_context: str = "") -> DataPack:
    """Assemble the grounded payload: live prices, fundamentals, exposure and gaps."""
    annual, quarterly, context, source = load_analysis(app, ticker)
    claims = extract_claims(app.local_ai, external_context) if external_context else None
    return build_data_pack(
        context,
        annual,
        quarterly,
        source,
        positions_store.positions_for_ticker(app.conn, ticker),
        alerts_store.list_alerts(app.conn, ticker=ticker, only_active=True),
        external_claims=claims,
    )


def run_ai_report(app: App, ticker: str, external_context: str = "") -> AIReport:
    """Ground the model on live data, ask for the report, persist it with its suggestions."""
    data_pack = build_data_pack_for(app, ticker, external_context)
    report = ai.analyze(
        app.settings, ticker, data_pack, external_context, local_client=app.local_ai
    )
    analysis_id = events_store.save_analysis(
        app.conn,
        ticker,
        report.model,
        f"{data_pack.provenance}\n\n{data_pack.text}",
        external_context,
        report.text,
    )
    stored = suggestions_store.add_suggestions(
        app.conn, report.suggestions, analysis_id=analysis_id, model=report.model
    )
    return AIReport(
        ticker=report.ticker,
        text=report.text,
        suggestions=stored,
        model=report.model,
        provenance=data_pack.provenance,
        analysis_id=analysis_id,
    )


def accept_suggestion(app: App, suggestion: Suggestion, overrides: dict[str, Any] | None = None) -> Alert:
    """Turn an accepted suggestion into a real alert and mark it as such."""
    if suggestion.category != "alert" or suggestion.kind not in kinds.CATALOGUE:
        raise ValidationError(
            f"La sugerencia '{suggestion.headline}' es una acción, no una alerta automatizable."
        )
    params = {**dict(suggestion.params), **(overrides or {})}
    alert = add_alert(app, suggestion.ticker, suggestion.kind, params, note=suggestion.rationale[:200])
    if suggestion.id is not None:
        suggestions_store.resolve(app.conn, suggestion.id, suggestions_store.ACCEPTED, alert.id)
    return alert


def reject_suggestion(app: App, suggestion: Suggestion, *, status: str | None = None) -> None:
    if suggestion.id is not None:
        suggestions_store.resolve(
            app.conn, suggestion.id, status or suggestions_store.REJECTED, None
        )


def pending_suggestions(app: App, ticker: str | None = None) -> list[Suggestion]:
    return suggestions_store.list_suggestions(app.conn, ticker=ticker, status=suggestions_store.PENDING)


def applicable_suggestions(app: App, ticker: str | None = None) -> list[Suggestion]:
    """Pending suggestions that can actually become an alert."""
    return actionable(pending_suggestions(app, ticker))


def add_position(
    app: App,
    ticker: str,
    quantity: float,
    buy_price: float,
    buy_date: date,
    *,
    currency: str = "USD",
    notes: str = "",
) -> Position:
    if quantity <= 0:
        raise ValidationError("La cantidad debe ser mayor a cero.")
    if buy_price <= 0:
        raise ValidationError("El precio de compra debe ser mayor a cero.")
    if buy_date > date.today():
        raise ValidationError("La fecha de compra no puede estar en el futuro.")
    position = Position(
        ticker=ticker.upper(),
        quantity=quantity,
        buy_price=buy_price,
        buy_date=buy_date,
        currency=currency.upper(),
        notes=notes,
    )
    return positions_store.add_position(app.conn, position)


def add_alert(
    app: App,
    ticker: str,
    kind: str,
    params: Mapping[str, Any] | None = None,
    *,
    position_id: int | None = None,
    cooldown_hours: int | None = None,
    one_shot: bool | None = None,
    expires_at: date | None = None,
    note: str = "",
) -> Alert:
    """Validate and persist an alert, resolving the position it belongs to."""
    definition = kinds.get_kind(kind)
    resolved = kinds.normalize_params(kind, params)

    if position_id is None:
        open_positions = positions_store.positions_for_ticker(app.conn, ticker)
        position_id = open_positions[0].id if open_positions else None
    if definition.requires_position and position_id is None:
        raise ValidationError(
            f"La alerta '{kind}' necesita una posición cargada para {ticker.upper()} "
            "(agregá la compra primero)."
        )
    if kind in {kinds.PCT_UP, kinds.PCT_DOWN}:
        resolved = _resolve_reference(app, ticker, resolved, position_id)

    alert = Alert(
        ticker=ticker.upper(),
        kind=kind,
        params=resolved,
        position_id=position_id,
        one_shot=definition.one_shot if one_shot is None else one_shot,
        cooldown_hours=app.settings.default_cooldown_hours if cooldown_hours is None else cooldown_hours,
        expires_at=expires_at,
        note=note,
    )
    return alerts_store.add_alert(app.conn, alert)


def _resolve_reference(
    app: App, ticker: str, params: dict[str, Any], position_id: int | None
) -> dict[str, Any]:
    """A 'baseline' percentage alert freezes today's price as its reference."""
    if params.get("reference") == "buy":
        if position_id is None:
            raise ValidationError(
                "reference='buy' necesita una posición; usá reference='baseline' para anclar al precio de hoy."
            )
        return params
    if params.get("baseline_price"):
        return params
    quote = app.market.quote(ticker)
    return {**params, "baseline_price": quote.price}


def check_alerts(app: App, ticker: str | None = None, *, trigger: str = "manual") -> CheckReport:
    """Run the alert check and, on a full sweep, stamp the equity curve.

    The valuation rides along with the scheduled run because that is the only
    thing guaranteed to happen on a timer. Left to the ``portfolio`` command the
    curve would only have points on the days somebody happened to look.
    """
    report = run_checks(app.conn, app.market, app.dispatcher, ticker=ticker, trigger=trigger)
    if ticker is None:
        record_valuation(app)
    return report


def record_valuation(app: App) -> None:
    """Add a point to the equity curve, never at the cost of the run itself."""
    try:
        build_portfolio(app.conn, app.market)
    except Exception:  # noqa: BLE001 - the curve must never break a check
        logger.exception("could not record the portfolio valuation")


def adjust_for_split(app: App, position_id: int, ratio: float) -> Position:
    """Re-base a position's cost after a split (ratio 4 means 4-for-1)."""
    if ratio <= 0:
        raise ValidationError("El ratio del split debe ser mayor a cero.")
    position = positions_store.get_position(app.conn, position_id)
    if position is None:
        raise ValidationError(f"No existe la posición {position_id}.")
    updated = positions_store.apply_split(app.conn, position_id, ratio)
    assert updated is not None  # noqa: S101 - just re-read the row we wrote
    return updated


def digest_facts(app: App) -> str:
    """The raw fact sheet behind the digest (also useful on its own)."""
    return collect_facts(app.conn, app.market)


def run_digest(app: App) -> tuple[str, str | None, str | None]:
    """Return (facts, prose, error). Only one of prose/error is ever set."""
    facts = digest_facts(app)
    prose, error = portfolio_digest(app.local_ai, facts)
    return facts, prose, error
