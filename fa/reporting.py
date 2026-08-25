"""The AI report and its suggestions, without depending on the App.

Same reason as :mod:`fa.alerts.authoring`: the CLI has an ``App`` with a market
service and a local model wired in, the API does not, and neither should own a
second copy of what an analysis is. The dependencies come in as arguments and
both front ends call the same thing.
"""
from __future__ import annotations

from typing import Any

from fa import ai
from fa.ai_context import DataPack, build_data_pack
from fa.alerts import authoring, kinds
from fa.config import Settings
from fa.errors import ValidationError
from fa.local_tasks import extract_claims
from fa.models import AIReport, Alert, Suggestion
from fa.store import alerts as alerts_store
from fa.store import events as events_store
from fa.store import positions as positions_store
from fa.store import suggestions as suggestions_store
from fa.store.database import Database
from fa.store.schema import LOCAL_ACCOUNT_ID


def build_pack(
    conn: Database,
    market: Any,
    local_ai: Any,
    ticker: str,
    external_context: str = "",
    *,
    account_id: int = LOCAL_ACCOUNT_ID,
) -> DataPack:
    """Assemble the grounded payload: live prices, fundamentals, exposure, gaps."""
    positions = positions_store.positions_for_ticker(conn, ticker, account_id=account_id)
    oldest = min((p.buy_date for p in positions), default=None)
    annual, quarterly, fundamentals, context = market.analysis_tables(ticker, since=oldest)
    claims = extract_claims(local_ai, external_context) if external_context else None
    return build_data_pack(
        context,
        annual,
        quarterly,
        fundamentals.source,
        positions,
        alerts_store.list_alerts(conn, ticker=ticker, only_active=True, account_id=account_id),
        external_claims=claims,
    )


def run_report(
    conn: Database,
    market: Any,
    settings: Settings,
    local_ai: Any,
    ticker: str,
    external_context: str = "",
    *,
    account_id: int = LOCAL_ACCOUNT_ID,
) -> AIReport:
    """Ground the model on live data, ask for the report, persist it."""
    data_pack = build_pack(
        conn, market, local_ai, ticker, external_context, account_id=account_id
    )
    report = ai.analyze(settings, ticker, data_pack, external_context, local_client=local_ai)
    analysis_id = events_store.save_analysis(
        conn,
        ticker,
        report.model,
        f"{data_pack.provenance}\n\n{data_pack.text}",
        external_context,
        report.text,
        account_id=account_id,
    )
    stored = suggestions_store.add_suggestions(
        conn,
        report.suggestions,
        analysis_id=analysis_id,
        model=report.model,
        account_id=account_id,
    )
    return AIReport(
        ticker=report.ticker,
        text=report.text,
        suggestions=stored,
        model=report.model,
        provenance=data_pack.provenance,
        analysis_id=analysis_id,
    )


def accept(
    conn: Database,
    suggestion: Suggestion,
    overrides: dict[str, Any] | None = None,
    *,
    price_for: authoring.PriceLookup,
    default_cooldown_hours: int = authoring.DEFAULT_COOLDOWN_HOURS,
    account_id: int = LOCAL_ACCOUNT_ID,
) -> Alert:
    """Turn an accepted suggestion into a real alert and mark it as such."""
    if suggestion.category != "alert" or suggestion.kind not in kinds.CATALOGUE:
        raise ValidationError(
            f"La sugerencia '{suggestion.headline}' es una acción, no una alerta automatizable."
        )
    params = {**dict(suggestion.params), **(overrides or {})}
    alert = authoring.create_alert(
        conn,
        suggestion.ticker,
        suggestion.kind,
        params,
        price_for=price_for,
        default_cooldown_hours=default_cooldown_hours,
        note=suggestion.rationale[:200],
        account_id=account_id,
    )
    if suggestion.id is not None:
        suggestions_store.resolve(
            conn, suggestion.id, suggestions_store.ACCEPTED, alert.id, account_id=account_id
        )
    return alert


def reject(
    conn: Database,
    suggestion: Suggestion,
    *,
    status: str | None = None,
    account_id: int = LOCAL_ACCOUNT_ID,
) -> None:
    if suggestion.id is not None:
        suggestions_store.resolve(
            conn,
            suggestion.id,
            status or suggestions_store.REJECTED,
            None,
            account_id=account_id,
        )
