"""End-to-end CLI commands, with the app context stubbed (no network, no keys)."""
from __future__ import annotations

import contextlib
import json
from datetime import date

import pytest

from fa import cli
from fa.market import MarketService
from fa.models import Position, Signal, Suggestion
from fa.store import meta as meta_store
from fa.store import positions as positions_store
from fa.store import suggestions as suggestions_store
from tests.conftest import make_app
from tests.test_market import FakeChain


class NullDispatcher:
    names = ("test",)

    def send(self, signal: Signal) -> list[str]:
        return ["test"]


@pytest.fixture()
def cli_app(conn, tmp_path, monkeypatch):
    app = make_app(
        conn, tmp_path, market=MarketService(FakeChain(), conn), dispatcher=NullDispatcher()
    )

    @contextlib.contextmanager
    def fake_build_app(**kwargs):
        yield app

    monkeypatch.setattr(cli, "build_app", fake_build_app)
    return app


def test_use_sets_the_active_ticker(cli_app, capsys):
    assert cli.main(["use", "podd"]) == 0
    assert meta_store.get_current_ticker(cli_app.conn) == "PODD"
    assert "Ticker activo: PODD" in capsys.readouterr().out


def test_unuse_clears_it(cli_app, capsys):
    meta_store.set_current_ticker(cli_app.conn, "PODD")
    assert cli.main(["unuse"]) == 0
    assert meta_store.get_current_ticker(cli_app.conn) is None


def test_analyze_uses_the_active_ticker(cli_app, capsys):
    meta_store.set_current_ticker(cli_app.conn, "PODD")
    assert cli.main(["analyze", "--period", "annual"]) == 0
    assert "COMPARATIVA ANUAL" in capsys.readouterr().out


def test_analyze_without_ticker_or_session_fails_clearly(cli_app, capsys):
    assert cli.main(["analyze"]) == 1
    assert "use TICKER" in capsys.readouterr().err


def test_add_position_and_portfolio(cli_app, capsys):
    assert cli.main(["add-position", "PODD", "--qty", "10", "--price", "100", "--date", "2026-01-01"]) == 0
    assert cli.main(["portfolio"]) == 0
    out = capsys.readouterr().out
    assert "Posición #1" in out and "TOTAL" in out


def test_add_alert_uses_the_active_ticker(cli_app, capsys):
    meta_store.set_current_ticker(cli_app.conn, "PODD")
    assert cli.main(["add-alert", "--kind", "price_above", "--param", "price=200"]) == 0
    assert "price_above" in capsys.readouterr().out


def test_check_alerts_json_is_machine_readable(cli_app, capsys):
    positions_store.add_position(
        cli_app.conn, Position(ticker="PODD", quantity=10, buy_price=100.0, buy_date=date(2026, 1, 1))
    )
    cli.main(["add-alert", "PODD", "--kind", "pct_up", "--param", "pct=10"])
    capsys.readouterr()
    assert cli.main(["check-alerts", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["fired"][0]["kind"] == "pct_up"


def test_check_alerts_can_be_scoped_to_a_ticker(cli_app, capsys):
    cli.main(["add-alert", "AAPL", "--kind", "price_above", "--param", "price=1"])
    capsys.readouterr()
    assert cli.main(["check-alerts", "--ticker", "PODD", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["checked"] == 0


def test_suggestions_listing_and_review(cli_app, capsys, monkeypatch):
    suggestions_store.add_suggestions(
        cli_app.conn,
        [Suggestion(ticker="PODD", category="alert", kind="price_above", params={"price": 200.0},
                    rationale="resistencia")],
        analysis_id=None,
        model="test",
    )
    assert cli.main(["suggestions"]) == 0
    assert "price_above" in capsys.readouterr().out
    monkeypatch.setattr("builtins.input", lambda *_: "s")
    assert cli.main(["suggestions", "--review"]) == 0
    assert suggestions_store.pending_count(cli_app.conn) == 0


def test_digest_facts_only_needs_no_local_model(cli_app, capsys):
    assert cli.main(["digest", "--facts-only"]) == 0
    assert "POSICIONES" in capsys.readouterr().out


def test_digest_without_a_local_model_exits_with_two(cli_app, capsys):
    assert cli.main(["digest"]) == 2
    assert "FA_LOCAL_AI_MODEL" in capsys.readouterr().err


def test_local_ai_reports_a_missing_server(cli_app, capsys):
    assert cli.main(["local-ai"]) == 2
    assert "unreachable" in capsys.readouterr().err


def test_positions_alerts_and_history_render(cli_app, capsys):
    cli.main(["add-position", "PODD", "--qty", "1", "--price", "100", "--date", "2026-01-01"])
    capsys.readouterr()
    assert cli.main(["positions"]) == 0
    assert cli.main(["alerts"]) == 0
    assert cli.main(["history"]) == 0
    out = capsys.readouterr().out
    assert "PODD" in out and "No hay alertas" in out
