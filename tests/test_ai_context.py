"""The DATA PACK must expose provenance and never hide a gap."""
from __future__ import annotations

from datetime import date

import pandas as pd

from fa.ai_context import FRESHNESS_RULES, build_data_pack
from fa.metrics import build_frame
from fa.models import Alert, CorporateEvent
from tests.conftest import make_context, make_history, make_position

ROWS = [
    {
        "label": "2025",
        "period_end": date(2025, 12, 31),
        "total_assets": 3100.0,
        "total_liabilities": 1720.0,
        "operating_cash_flow": 440.0,
        "capex": 62.0,
    }
]
ANNUAL = build_frame(ROWS, (), shares_outstanding=69.35, current_price=141.5)
EMPTY = pd.DataFrame(columns=ANNUAL.columns)


def full_context():
    return make_context(
        141.5,
        history=make_history([float(i) for i in range(100, 360)]),
        next_earnings=date(2026, 9, 3),
        next_ex_dividend=date(2026, 9, 10),
        recent_splits=(CorporateEvent(ticker="PODD", kind="split", event_date=date(2026, 5, 1), value=4.0),),
    )


def test_pack_states_the_price_source_and_timestamp():
    pack = build_data_pack(full_context(), ANNUAL, EMPTY, "yahoo", (), ())
    assert "fuente: test" in pack.text and "as-of: 2026-08-20" in pack.text


def test_pack_includes_indicators_when_history_is_present():
    pack = build_data_pack(full_context(), ANNUAL, EMPTY, "yahoo", (), ())
    assert "SMA50" in pack.text and "RSI14" in pack.text


def test_missing_history_is_declared_not_filled():
    pack = build_data_pack(make_context(141.5), ANNUAL, EMPTY, "yahoo", (), ())
    assert "Histórico de precios: NO DISPONIBLE" in pack.text
    assert any("histórico de precios" in item for item in pack.missing)
    assert pack.has_gaps is True


def test_missing_earnings_is_listed_as_a_gap():
    pack = build_data_pack(make_context(141.5), ANNUAL, EMPTY, "yahoo", (), ())
    assert "fecha del próximo earnings call" in pack.missing
    assert "MISSING DATA" in pack.text


def test_missing_quarterly_statements_are_flagged():
    pack = build_data_pack(full_context(), ANNUAL, EMPTY, "yahoo", (), ())
    assert "estados contables trimestrales" in pack.missing


def test_exposure_section_shows_cost_and_live_pnl():
    pack = build_data_pack(
        full_context(),
        ANNUAL,
        EMPTY,
        "yahoo",
        (make_position(100.0, date(2026, 1, 15)),),
        (Alert(id=1, ticker="PODD", kind="pct_up", params={"pct": 10}),),
    )
    assert "P&L actual" in pack.text and "+41.50%" in pack.text
    assert "Alerta activa: pct_up" in pack.text


def test_watchlist_ticker_is_declared_as_such():
    pack = build_data_pack(full_context(), ANNUAL, EMPTY, "yahoo", (), ())
    assert "Sin posiciones abiertas" in pack.text and "Sin alertas activas" in pack.text


def test_external_claims_are_marked_unverified():
    pack = build_data_pack(
        full_context(), ANNUAL, EMPTY, "yahoo", (), (), external_claims={"precio_objetivo": "220"}
    )
    assert "unverified" in pack.text and "precio_objetivo: 220" in pack.text


def test_provenance_summarises_sources_and_gap_count():
    pack = build_data_pack(full_context(), ANNUAL, EMPTY, "yahoo", (), ())
    assert "precio test" in pack.provenance and "fundamentals yahoo" in pack.provenance
    assert "faltantes:" in pack.provenance


def test_rules_forbid_using_training_data():
    assert "training data is stale" in FRESHNESS_RULES
    assert "never estimate" in FRESHNESS_RULES.lower() or "never cite" in FRESHNESS_RULES.lower()


def test_prompt_embeds_the_pack_and_the_rules():
    from fa.ai import build_prompt

    pack = build_data_pack(full_context(), ANNUAL, EMPTY, "yahoo", (), ())
    prompt = build_prompt("PODD", pack, "texto del usuario")
    assert pack.text in prompt
    assert FRESHNESS_RULES in prompt
    assert "suggested_alerts" in prompt and "trailing_stop" in prompt
    assert "texto del usuario" in prompt
