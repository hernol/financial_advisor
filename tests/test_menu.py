"""General menu and ticker workspace flows, with scripted stdin."""
from __future__ import annotations

from datetime import date

import pytest

from fa.app import App
from fa.market import MarketService
from fa.models import Position, Signal
from fa.store import alerts as alerts_store
from fa.store import meta as meta_store
from fa.store import positions as positions_store
from fa.ui import menu, ticker_menu
from tests.conftest import make_app
from tests.test_market import FakeChain


class RecordingDispatcher:
    names = ("test",)

    def __init__(self) -> None:
        self.sent: list[Signal] = []

    def send(self, signal: Signal) -> list[str]:
        self.sent.append(signal)
        return ["test"]


@pytest.fixture()
def app(conn, tmp_path) -> App:
    return make_app(
        conn, tmp_path, market=MarketService(FakeChain(), conn), dispatcher=RecordingDispatcher()
    )


def feed(monkeypatch, *answers: str) -> None:
    queue = list(answers)
    monkeypatch.setattr("builtins.input", lambda *_: queue.pop(0) if queue else "")


def buy(app: App, ticker: str = "PODD", price: float = 100.0) -> Position:
    return positions_store.add_position(
        app.conn, Position(ticker=ticker, quantity=10, buy_price=price, buy_date=date(2026, 1, 1))
    )


# --- general menu -----------------------------------------------------------


def test_run_exits_on_zero(app, monkeypatch, capsys):
    feed(monkeypatch, "0")
    menu.run(app)
    assert "Saliendo" in capsys.readouterr().out


def test_run_reports_an_invalid_option(app, monkeypatch, capsys):
    feed(monkeypatch, "99", "0")
    menu.run(app)
    assert "Opción inválida" in capsys.readouterr().out


def test_selecting_a_ticker_persists_it(app, monkeypatch, capsys):
    feed(monkeypatch, "1", "podd", "x", "0")
    menu.run(app)
    out = capsys.readouterr().out
    assert "TRABAJANDO SOBRE PODD" in out
    assert meta_store.get_current_ticker(app.conn) is None, "la 'x' debe des-setear el ticker"


def test_the_workspace_survives_a_restart(app, monkeypatch, capsys):
    meta_store.set_current_ticker(app.conn, "PODD")
    feed(monkeypatch, "0")
    menu.run(app)
    assert "TRABAJANDO SOBRE PODD" in capsys.readouterr().out


def test_changing_ticker_from_the_workspace(app, monkeypatch, capsys):
    meta_store.set_current_ticker(app.conn, "PODD")
    feed(monkeypatch, "c", "aapl", "0")
    menu.run(app)
    assert meta_store.get_current_ticker(app.conn) == "AAPL"


def test_general_portfolio_view(app, monkeypatch, capsys):
    buy(app)
    menu._portfolio(app)
    assert "TOTAL" in capsys.readouterr().out


def test_general_check_all(app, monkeypatch, capsys):
    menu._check_all(app)
    assert "Alertas evaluadas" in capsys.readouterr().out


def test_digest_without_a_local_model_shows_raw_facts(app, capsys):
    buy(app)
    menu._digest(app)
    out = capsys.readouterr().out
    assert "Sin modelo local" in out or "modelo local" in out
    assert "POSICIONES" in out


def test_manage_positions_can_close(app, monkeypatch):
    position = buy(app)
    feed(monkeypatch, "c", str(position.id))
    menu._manage_positions(app)
    assert positions_store.list_positions(app.conn) == []


# --- ticker workspace -------------------------------------------------------


def test_header_shows_position_and_alert_counts(app, capsys):
    buy(app)
    ticker_menu.header(app, "PODD")
    out = capsys.readouterr().out
    assert "posición: 10 acciones" in out and "alertas activas: 0" in out


def test_header_flags_a_watchlist_ticker(app, capsys):
    ticker_menu.header(app, "AAPL")
    assert "sin posición (watchlist)" in capsys.readouterr().out


def test_dispatch_signals(app, monkeypatch):
    assert ticker_menu.dispatch(app, "PODD", "0") == "quit"
    assert ticker_menu.dispatch(app, "PODD", "x") == "leave"
    assert ticker_menu.dispatch(app, "PODD", "c") == "change"
    assert ticker_menu.dispatch(app, "PODD", "zzz") == "stay"


def test_annual_analysis_does_not_ask_for_the_ticker(app, capsys):
    ticker_menu.dispatch(app, "PODD", "1")
    assert "COMPARATIVA ANUAL" in capsys.readouterr().out


def test_quarterly_analysis_handles_an_empty_frame(app, capsys):
    ticker_menu.dispatch(app, "PODD", "2")
    assert "Sin datos" in capsys.readouterr().out


def test_add_position_from_the_workspace(app, monkeypatch):
    feed(monkeypatch, "10", "141.5", "2026-03-01", "USD", "nota", "n")
    ticker_menu.dispatch(app, "PODD", "6")
    positions = positions_store.list_positions(app.conn)
    assert len(positions) == 1 and positions[0].ticker == "PODD"


def test_add_position_creates_the_suggested_alerts(app, monkeypatch):
    feed(monkeypatch, "10", "141.5", "2026-03-01", "USD", "", "s")
    ticker_menu.dispatch(app, "PODD", "6")
    kinds_created = {a.kind for a in alerts_store.list_alerts(app.conn)}
    assert {"pct_up", "pct_down", "trailing_stop", "earnings_near", "period_elapsed", "split_detected"} == kinds_created


def test_add_alert_from_the_catalogue(app, monkeypatch):
    feed(monkeypatch, "3", "200", "24")  # 3rd entry is price_above
    ticker_menu.dispatch(app, "PODD", "5")
    alert = alerts_store.list_alerts(app.conn)[0]
    assert alert.kind == "price_above" and alert.params["price"] == 200.0


def test_check_scoped_to_the_ticker(app, monkeypatch, capsys):
    buy(app, "PODD", price=100.0)
    alerts_store.add_alert(
        app.conn, alerts_store.Alert(ticker="AAPL", kind="pct_up", params={"pct": 1, "reference": "baseline", "baseline_price": 1.0})
    )
    ticker_menu.dispatch(app, "PODD", "7")
    assert "Alertas evaluadas: 0" in capsys.readouterr().out


def test_history_is_filtered_by_ticker(app, capsys):
    ticker_menu.dispatch(app, "PODD", "8")
    assert "Sin alertas disparadas" in capsys.readouterr().out


def test_split_adjustment_flow(app, monkeypatch):
    position = buy(app, price=200.0)
    feed(monkeypatch, str(position.id), "4")
    ticker_menu.dispatch(app, "PODD", "9")
    updated = positions_store.get_position(app.conn, position.id)
    assert (updated.buy_price, updated.quantity) == (50.0, 40.0)


def test_quote_line_degrades_when_data_is_missing(app, monkeypatch):
    from fa.errors import DataUnavailableError

    monkeypatch.setattr(app.market, "quote", lambda t: (_ for _ in ()).throw(DataUnavailableError("caído")))
    assert "no disponible" in ticker_menu.quote_line(app, "PODD")
