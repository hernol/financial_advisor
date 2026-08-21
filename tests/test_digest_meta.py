"""Digest fact sheet, ticker session state and the suggestion review flow."""
from __future__ import annotations

from datetime import date

from fa import actions
from fa.digest import collect_facts
from fa.market import MarketService
from fa.models import Alert, Position, Suggestion
from fa.store import alerts as alerts_store
from fa.store import meta as meta_store
from fa.store import positions as positions_store
from fa.store import suggestions as suggestions_store
from fa.ui.suggestions_ui import review
from tests.conftest import make_app
from tests.test_market import FakeChain


def app_with(conn, tmp_path):
    return make_app(conn, tmp_path, market=MarketService(FakeChain(), conn))


# --- session ----------------------------------------------------------------


def test_current_ticker_round_trip(conn):
    assert meta_store.get_current_ticker(conn) is None
    assert meta_store.set_current_ticker(conn, " podd ") == "PODD"
    assert meta_store.get_current_ticker(conn) == "PODD"
    meta_store.clear_current_ticker(conn)
    assert meta_store.get_current_ticker(conn) is None


def test_setting_a_ticker_twice_overwrites(conn):
    meta_store.set_current_ticker(conn, "PODD")
    meta_store.set_current_ticker(conn, "AAPL")
    assert meta_store.get_current_ticker(conn) == "AAPL"


def test_meta_does_not_collide_with_the_schema_version(conn):
    meta_store.set_current_ticker(conn, "PODD")
    assert meta_store.get_meta(conn, "schema_version") is not None


# --- digest -----------------------------------------------------------------


def test_facts_include_positions_alerts_and_calendar(conn, tmp_path):
    app = app_with(conn, tmp_path)
    positions_store.add_position(
        conn, Position(ticker="PODD", quantity=10, buy_price=100.0, buy_date=date(2026, 1, 1))
    )
    alerts_store.add_alert(conn, Alert(ticker="PODD", kind="price_above", params={"price": 200.0}))
    facts = collect_facts(conn, app.market, today=date(2026, 8, 20))
    assert "FECHA: 2026-08-20" in facts
    assert "P&L +415.00" in facts
    assert "objetivo 200.00" in facts
    assert "earnings 2026-09-03" in facts


def test_facts_are_explicit_when_the_portfolio_is_empty(conn, tmp_path):
    facts = collect_facts(conn, app_with(conn, tmp_path).market, today=date(2026, 8, 20))
    assert "POSICIONES:\n- (ninguna)" in facts and "ALERTAS ACTIVAS:\n- (ninguna)" in facts


def test_pct_alert_distance_uses_the_buy_price(conn, tmp_path):
    app = app_with(conn, tmp_path)
    positions_store.add_position(
        conn, Position(ticker="PODD", quantity=1, buy_price=100.0, buy_date=date(2026, 1, 1))
    )
    alerts_store.add_alert(
        conn, Alert(ticker="PODD", kind="pct_up", params={"pct": 10.0, "reference": "buy"})
    )
    facts = collect_facts(conn, app.market, today=date(2026, 8, 20))
    assert "variación +41.50% contra umbral +10.00%" in facts


def test_run_digest_without_a_local_model_explains_why(conn, tmp_path):
    facts, prose, error = actions.run_digest(app_with(conn, tmp_path))
    assert prose is None and "FECHA" in facts
    assert "FA_LOCAL_AI_MODEL" in error


# --- suggestion review ------------------------------------------------------


def seed_suggestions(conn, ticker="PODD"):
    return suggestions_store.add_suggestions(
        conn,
        [
            Suggestion(ticker=ticker, category="alert", kind="price_above", params={"price": 200.0},
                       rationale="resistencia", priority="high"),
            Suggestion(ticker=ticker, category="alert", kind="price_below", params={"price": 120.0},
                       rationale="soporte"),
            Suggestion(ticker=ticker, category="action", kind="tomar ganancias", rationale="concentración"),
        ],
        analysis_id=None,
        model="test-model",
    )


def feed(monkeypatch, *answers):
    queue = list(answers)
    monkeypatch.setattr("builtins.input", lambda *_: queue.pop(0) if queue else "")


def test_review_creates_only_the_accepted_alert(conn, tmp_path, monkeypatch, capsys):
    app = app_with(conn, tmp_path)
    pending = seed_suggestions(conn)
    feed(monkeypatch, "s", "n", "")  # aceptar, rechazar, dejar la acción pendiente
    created, discarded = review(app, pending)
    assert (created, discarded) == (1, 1)
    alerts = alerts_store.list_alerts(conn)
    assert len(alerts) == 1 and alerts[0].kind == "price_above"
    assert alerts[0].note.startswith("resistencia")
    assert suggestions_store.pending_count(conn, "PODD") == 1


def test_review_can_edit_the_parameters_before_creating(conn, tmp_path, monkeypatch):
    app = app_with(conn, tmp_path)
    pending = seed_suggestions(conn)[:1]
    feed(monkeypatch, "e", "250", "")
    review(app, pending)
    assert alerts_store.list_alerts(conn)[0].params["price"] == 250.0


def test_review_quits_early_without_touching_the_rest(conn, tmp_path, monkeypatch):
    app = app_with(conn, tmp_path)
    feed(monkeypatch, "q")
    created, discarded = review(app, seed_suggestions(conn))
    assert (created, discarded) == (0, 0)
    assert suggestions_store.pending_count(conn, "PODD") == 3


def test_review_of_an_empty_list_is_safe(conn, tmp_path, capsys):
    review(app_with(conn, tmp_path), [])
    assert "No hay sugerencias pendientes" in capsys.readouterr().out


def test_accepting_a_free_form_action_is_rejected(conn, tmp_path):
    import pytest

    from fa.errors import ValidationError

    app = app_with(conn, tmp_path)
    action = seed_suggestions(conn)[2]
    with pytest.raises(ValidationError):
        actions.accept_suggestion(app, action)


def test_accepted_suggestion_links_back_to_the_alert(conn, tmp_path):
    app = app_with(conn, tmp_path)
    suggestion = seed_suggestions(conn)[0]
    alert = actions.accept_suggestion(app, suggestion)
    stored = suggestions_store.list_suggestions(conn, status=suggestions_store.ACCEPTED)[0]
    assert stored.alert_id == alert.id and stored.status == "accepted"


def test_applicable_suggestions_excludes_actions(conn, tmp_path):
    app = app_with(conn, tmp_path)
    seed_suggestions(conn)
    assert len(actions.pending_suggestions(app, "PODD")) == 3
    assert len(actions.applicable_suggestions(app, "PODD")) == 2
