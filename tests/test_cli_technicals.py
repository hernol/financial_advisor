"""CLI wiring for the technicals command."""
from __future__ import annotations

import contextlib
import json
from datetime import date, datetime, timedelta, timezone

from fa.cli import main
from fa.models import MarketContext, PricePoint, Quote
from tests.conftest import make_app


class StubMarket:
    providers = ("stub",)
    benchmark = "SPY"

    def context(self, ticker, *, since=None, period="2y"):
        first = date(2023, 1, 2)
        history = [
            PricePoint(day=first + timedelta(days=i), close=100.0 + i * 0.5, high=101.0 + i * 0.5,
                       low=99.0 + i * 0.5, volume=1000.0)
            for i in range(300)
        ]
        return MarketContext(
            ticker=ticker.upper(),
            quote=Quote(ticker=ticker.upper(), price=250.0, currency="USD",
                        as_of=datetime(2024, 6, 3, tzinfo=timezone.utc), source="stub"),
            history=history,
            benchmark_ticker="SPY",
            benchmark_history=history,
            evaluated_at=datetime(2024, 6, 3, tzinfo=timezone.utc),
        )

    def quote(self, ticker):
        return self.context(ticker).quote


def run(monkeypatch, conn, tmp_path, argv):
    app = make_app(conn, tmp_path, market=StubMarket())

    @contextlib.contextmanager
    def fake_build_app(**kwargs):
        yield app

    monkeypatch.setattr("fa.cli.build_app", fake_build_app)
    return main(argv)


def test_technicals_prints_the_snapshot(monkeypatch, conn, tmp_path, capsys):
    assert run(monkeypatch, conn, tmp_path, ["technicals", "TEST"]) == 0
    assert "TÉCNICOS — TEST" in capsys.readouterr().out


def test_technicals_json_is_machine_readable(monkeypatch, conn, tmp_path, capsys):
    assert run(monkeypatch, conn, tmp_path, ["technicals", "TEST", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ticker"] == "TEST"
    assert payload["benchmark"] == "SPY"
    assert "relative_strength" in payload


def test_technicals_uses_the_active_ticker(monkeypatch, conn, tmp_path, capsys):
    from fa.store import meta as meta_store

    meta_store.set_current_ticker(conn, "PODD")
    assert run(monkeypatch, conn, tmp_path, ["technicals"]) == 0
    assert "PODD" in capsys.readouterr().out
