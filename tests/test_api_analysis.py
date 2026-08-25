"""Requesting an AI report and acting on what it suggests.

The report takes tens of seconds and does reach a provider, so it runs in the
background. What matters here is that the queueing, the failure path and the
accept/reject of suggestions behave — the model itself is stubbed.
"""
from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from fa.api import analysis, deps
from fa.api.app import create_app
from fa.models import AIReport, PricePoint, Suggestion
from fa.store import alerts as alerts_store
from fa.store import events as events_store
from fa.store import history as history_store
from fa.store import suggestions as suggestions_store
from tests.conftest import make_settings


@pytest.fixture()
def client(conn):
    deps.set_database(conn)
    with TestClient(create_app(serve_web=False)) as test_client:
        yield test_client
    deps.set_database(None)


@pytest.fixture()
def with_key(monkeypatch, tmp_path):
    settings = make_settings(tmp_path, gemini_api_key="k")
    monkeypatch.setattr("fa.api.analysis.load_settings", lambda: settings)
    return settings


def stub_report(monkeypatch, conn, *, suggestions=(), fails=None):
    """Replace the report itself; everything around it is what is under test."""
    def fake(db, market, settings, local_ai, ticker, context="", account_id=1, on_stage=None):
        if on_stage:
            on_stage("etapa de prueba")
        if fails:
            raise fails
        analysis_id = events_store.save_analysis(
            db, ticker, "test-model", "📊 Datos: yahoo\n\nDATA PACK", context,
            "Veredicto: mantener.", account_id=account_id,
        )
        stored = suggestions_store.add_suggestions(
            db, suggestions, analysis_id=analysis_id, model="test-model",
            account_id=account_id,
        )
        return AIReport(ticker=ticker, text="Veredicto: mantener.", suggestions=stored,
                        model="test-model", analysis_id=analysis_id)
    monkeypatch.setattr("fa.reporting.run_report", fake)


def suggestion(kind="rsi", params=None, category="alert", ticker="PODD"):
    return Suggestion(ticker=ticker, category=category, kind=kind,
                      params=params or {"period": 14}, rationale="porque sí")


# --- requesting -------------------------------------------------------------


def test_without_a_key_the_request_is_refused_by_name(client, monkeypatch, tmp_path):
    settings = make_settings(tmp_path, gemini_api_key=None)
    monkeypatch.setattr("fa.api.analysis.load_settings", lambda: settings)
    response = client.post("/api/tickers/PODD/analysis", json={})
    assert response.status_code == 422
    assert "GEMINI_API_KEY" in response.json()["detail"]


def test_a_report_is_queued_and_lands(client, conn, monkeypatch, with_key):
    stub_report(monkeypatch, conn)
    assert client.post("/api/tickers/PODD/analysis", json={}).status_code == 202
    # TestClient runs background tasks before returning, so it is already done.
    state = client.get("/api/tickers/PODD/analysis/status").json()
    assert state["status"] == "done"
    reports = client.get("/api/tickers/PODD/analyses").json()
    assert reports[0]["report"] == "Veredicto: mantener."
    assert reports[0]["provenance"].startswith("📊 Datos")


def test_a_failure_is_reported_in_words(client, conn, monkeypatch, with_key):
    from fa.errors import DataUnavailableError

    stub_report(monkeypatch, conn, fails=DataUnavailableError("ningún proveedor respondió"))
    client.post("/api/tickers/PODD/analysis", json={})
    state = client.get("/api/tickers/PODD/analysis/status").json()
    assert state["status"] == "error"
    assert "proveedor" in state["detail"]


def test_an_unexpected_failure_does_not_leave_the_job_running(client, conn, monkeypatch, with_key):
    stub_report(monkeypatch, conn, fails=RuntimeError("boom"))
    client.post("/api/tickers/PODD/analysis", json={})
    assert client.get("/api/tickers/PODD/analysis/status").json()["status"] == "error"


def test_a_ticker_never_analysed_is_idle(client):
    assert client.get("/api/tickers/PODD/analysis/status").json()["status"] == "idle"


# --- suggestions ------------------------------------------------------------


def test_suggestions_come_back_pending(client, conn, monkeypatch, with_key):
    stub_report(monkeypatch, conn, suggestions=[suggestion()])
    client.post("/api/tickers/PODD/analysis", json={})
    rows = client.get("/api/suggestions").json()
    assert len(rows) == 1
    assert rows[0]["kind"] == "rsi"
    assert rows[0]["actionable"] is True


def test_an_action_is_marked_as_not_automatable(client, conn, monkeypatch, with_key):
    """"Tomar ganancias parciales" is advice, not a rule the engine can run."""
    stub_report(monkeypatch, conn,
                suggestions=[suggestion(category="action", kind="tomar ganancias")])
    client.post("/api/tickers/PODD/analysis", json={})
    assert client.get("/api/suggestions").json()[0]["actionable"] is False


def test_accepting_a_suggestion_creates_the_alert(client, conn, monkeypatch, with_key):
    stub_report(monkeypatch, conn, suggestions=[suggestion()])
    client.post("/api/tickers/PODD/analysis", json={})
    pending = client.get("/api/suggestions").json()[0]

    created = client.post(f"/api/suggestions/{pending['id']}/accept", json={}).json()
    assert created["kind"] == "rsi"
    assert alerts_store.list_alerts(conn, ticker="PODD", only_active=True)
    assert client.get("/api/suggestions").json() == []


def test_the_parameters_can_be_tuned_while_accepting(client, conn, monkeypatch, with_key):
    stub_report(monkeypatch, conn, suggestions=[suggestion()])
    client.post("/api/tickers/PODD/analysis", json={})
    pending = client.get("/api/suggestions").json()[0]
    created = client.post(
        f"/api/suggestions/{pending['id']}/accept", json={"params": {"oversold": 25}}
    ).json()
    assert created["params"]["oversold"] == 25.0


def test_accepting_records_which_alert_it_became(client, conn, monkeypatch, with_key):
    stub_report(monkeypatch, conn, suggestions=[suggestion()])
    client.post("/api/tickers/PODD/analysis", json={})
    pending = client.get("/api/suggestions").json()[0]
    created = client.post(f"/api/suggestions/{pending['id']}/accept", json={}).json()
    resolved = suggestions_store.list_suggestions(conn, status="accepted")[0]
    assert resolved.alert_id == created["alert_id"]


def test_an_action_cannot_be_accepted_as_an_alert(client, conn, monkeypatch, with_key):
    stub_report(monkeypatch, conn,
                suggestions=[suggestion(category="action", kind="tomar ganancias")])
    client.post("/api/tickers/PODD/analysis", json={})
    pending = client.get("/api/suggestions").json()[0]
    response = client.post(f"/api/suggestions/{pending['id']}/accept", json={})
    assert response.status_code == 422
    assert "acción" in response.json()["detail"]


def test_a_suggestion_needing_a_baseline_uses_the_stored_close(client, conn, monkeypatch, with_key):
    """Accepting must not reach for a live quote, same as creating by hand."""
    history_store.save_bars(
        conn, "PODD", [PricePoint(day=date(2026, 8, 20), close=142.0)], "test"
    )
    stub_report(monkeypatch, conn, suggestions=[
        suggestion(kind="pct_down", params={"pct": 8, "reference": "baseline"})
    ])
    client.post("/api/tickers/PODD/analysis", json={})
    pending = client.get("/api/suggestions").json()[0]
    created = client.post(f"/api/suggestions/{pending['id']}/accept", json={}).json()
    assert created["params"]["baseline_price"] == 142.0


def test_rejecting_takes_it_off_the_list(client, conn, monkeypatch, with_key):
    stub_report(monkeypatch, conn, suggestions=[suggestion()])
    client.post("/api/tickers/PODD/analysis", json={})
    pending = client.get("/api/suggestions").json()[0]
    assert client.post(f"/api/suggestions/{pending['id']}/reject").status_code == 200
    assert client.get("/api/suggestions").json() == []
    assert alerts_store.list_alerts(conn) == []


def test_acting_on_a_missing_suggestion_is_a_404(client):
    assert client.post("/api/suggestions/999/accept", json={}).status_code == 404
    assert client.post("/api/suggestions/999/reject").status_code == 404


# --- on-demand check --------------------------------------------------------


def test_a_ticker_without_alerts_cannot_be_checked(client):
    response = client.post("/api/tickers/PODD/check")
    assert response.status_code == 422
    assert "alertas activas" in response.json()["detail"]


def test_a_check_can_be_asked_for(client, conn):
    client.post("/api/tickers/PODD/alerts", json={"kind": "rsi"})
    assert client.post("/api/tickers/PODD/check").status_code == 202


# --- what the wait looks like -----------------------------------------------


def test_the_job_reports_the_step_it_is_on(client, conn, monkeypatch, with_key):
    """Tens of seconds of silence is an unexplained wait; the steps are real."""
    seen = []

    def fake(db, market, settings, local_ai, ticker, context="", account_id=1, on_stage=None):
        seen.append(on_stage)
        on_stage("Consultando al modelo")
        return AIReport(ticker=ticker, text="ok", suggestions=(), model="m", analysis_id=1)

    monkeypatch.setattr("fa.reporting.run_report", fake)
    client.post("/api/tickers/PODD/analysis", json={})
    assert seen and seen[0] is not None


def test_a_queued_report_starts_at_the_first_step(client, conn, monkeypatch, with_key):
    from fa import reporting

    holding = {}

    def fake(db, market, settings, local_ai, ticker, context="", account_id=1, on_stage=None):
        holding["stage"] = analysis.job_state(1, ticker).get("stage")
        return AIReport(ticker=ticker, text="ok", suggestions=(), model="m", analysis_id=1)

    monkeypatch.setattr("fa.reporting.run_report", fake)
    client.post("/api/tickers/PODD/analysis", json={})
    assert holding["stage"] == reporting.FETCHING


def test_the_step_is_cleared_when_it_finishes(client, conn, monkeypatch, with_key):
    stub_report(monkeypatch, conn)
    client.post("/api/tickers/PODD/analysis", json={})
    assert client.get("/api/tickers/PODD/analysis/status").json()["stage"] == ""


def test_the_pasted_context_reaches_the_report(client, conn, monkeypatch, with_key):
    """The whole point of the field: it has to arrive, not be dropped."""
    seen = {}

    def fake(db, market, settings, local_ai, ticker, context="", account_id=1, on_stage=None):
        seen["context"] = context
        return AIReport(ticker=ticker, text="ok", suggestions=(), model="m", analysis_id=1)

    monkeypatch.setattr("fa.reporting.run_report", fake)
    client.post(
        "/api/tickers/PODD/analysis",
        json={"context": "El broker dice que el target es 200 USD."},
    )
    assert seen["context"] == "El broker dice que el target es 200 USD."


def test_reading_the_pasted_text_is_its_own_step(conn):
    """It only happens when there is something pasted, so it is announced then."""
    from fa import reporting

    stages: list[str] = []

    class Market:
        def analysis_tables(self, ticker, since=None):
            from datetime import datetime, timezone

            import pandas as pd

            from fa.models import Fundamentals, MarketContext, Quote

            quote = Quote(ticker=ticker, price=1.0, currency="USD",
                          as_of=datetime.now(timezone.utc), source="test")
            return (pd.DataFrame(), pd.DataFrame(),
                    Fundamentals(ticker=ticker, annual=(), quarterly=(),
                                 shares_outstanding=None, source="test"),
                    MarketContext(ticker=ticker, quote=quote))

    class Local:
        def available(self):
            return False

    reporting.build_pack(conn, Market(), Local(), "PODD", "texto pegado",
                         on_stage=stages.append)
    assert reporting.FETCHING in stages
    assert reporting.READING in stages


def test_without_pasted_text_that_step_is_skipped(conn):
    from fa import reporting

    stages: list[str] = []

    class Market:
        def analysis_tables(self, ticker, since=None):
            from datetime import datetime, timezone

            import pandas as pd

            from fa.models import Fundamentals, MarketContext, Quote

            quote = Quote(ticker=ticker, price=1.0, currency="USD",
                          as_of=datetime.now(timezone.utc), source="test")
            return (pd.DataFrame(), pd.DataFrame(),
                    Fundamentals(ticker=ticker, annual=(), quarterly=(),
                                 shares_outstanding=None, source="test"),
                    MarketContext(ticker=ticker, quote=quote))

    reporting.build_pack(conn, Market(), None, "PODD", "", on_stage=stages.append)
    assert reporting.READING not in stages
