"""The ticker screen's API, against a database built in the test.

No network: every endpoint answers from stored rows, which is the property that
makes the hosted mode affordable and is worth pinning down here.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from fa.alerts import kinds
from fa.alerts.engine import run_checks
from fa.api import deps
from fa.api.app import create_app
from fa.models import Alert, Position, PricePoint
from fa.store import alerts as alerts_store
from fa.store import history as history_store
from fa.store import positions as positions_store
from tests.conftest import make_history
from tests.test_engine import FakeMarket, RecordingDispatcher

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def client(conn):
    deps.set_database(conn)
    with TestClient(create_app(serve_web=False)) as test_client:
        yield test_client
    deps.set_database(None)


def seed_bars(conn, ticker="PODD", sessions=300, start=100.0):
    bars = [
        PricePoint(
            day=date(2026, 8, 20) - timedelta(days=sessions - 1 - i),
            close=start + i * 0.5,
            high=start + i * 0.5 + 1,
            low=start + i * 0.5 - 1,
            volume=1000.0 + i,
        )
        for i in range(sessions)
    ]
    history_store.save_bars(conn, ticker, bars, "test")
    return bars


def seed_alert(conn, ticker="PODD", kind=kinds.PCT_UP, params=None):
    position = positions_store.add_position(
        conn, Position(ticker=ticker, quantity=10, buy_price=100.0, buy_date=date(2026, 1, 1))
    )
    return alerts_store.add_alert(
        conn,
        Alert(
            ticker=ticker,
            kind=kind,
            params=kinds.normalize_params(kind, params or {"pct": 10}),
            position_id=position.id,
        ),
    )


# --- health -----------------------------------------------------------------


def test_health_reports_no_runs_as_stale(client):
    body = client.get("/api/health").json()
    assert body["last_run_at"] is None
    assert body["stale"] is True
    assert body["engine"] in {"sqlite", "postgres"}


def test_health_turns_fresh_after_a_run(client, conn):
    seed_alert(conn)
    run_checks(conn, FakeMarket(price=101.0, history=make_history([100.0, 101.0])),
               RecordingDispatcher(), now=datetime.now(timezone.utc))
    body = client.get("/api/health").json()
    assert body["last_run_ok"] is True
    assert body["stale"] is False


# --- list -------------------------------------------------------------------


def test_the_list_is_empty_before_anything_is_tracked(client):
    assert client.get("/api/tickers").json() == []


def test_the_list_shows_what_is_tracked(client, conn):
    seed_alert(conn)
    seed_bars(conn)
    rows = client.get("/api/tickers").json()
    assert len(rows) == 1
    assert rows[0]["ticker"] == "PODD"
    assert rows[0]["sessions"] == 300
    assert rows[0]["active_alerts"] == 1
    assert rows[0]["positions"] == 1


# --- detail -----------------------------------------------------------------


def test_a_ticker_with_no_stored_data_says_so(client):
    response = client.get("/api/tickers/NOPE")
    assert response.status_code == 404
    assert "No hay datos guardados" in response.json()["detail"]


def test_detail_carries_the_indicator_payload(client, conn):
    seed_alert(conn)
    run_checks(
        conn,
        FakeMarket(price=140.0, history=make_history([float(100 + i) for i in range(260)])),
        RecordingDispatcher(),
        now=NOW,
    )
    body = client.get("/api/tickers/PODD").json()
    assert body["ticker"] == "PODD"
    assert body["price"] == 140.0
    assert body["indicators"]["rsi"] is not None
    assert body["position"]["quantity"] == 10
    assert {s["name"] for s in body["series"]} >= {"rsi", "price"}


# --- bars -------------------------------------------------------------------


def test_bars_come_back_as_parallel_arrays(client, conn):
    seed_bars(conn, sessions=300)
    body = client.get("/api/tickers/PODD/bars?days=100").json()
    assert body["sessions"] == 100
    assert len(body["day"]) == 100
    assert len(body["close"]) == 100
    assert body["day"] == sorted(body["day"])


def test_the_slow_average_is_defined_from_the_first_visible_session(client, conn):
    """Asking for 100 days must not return 100 nulls for the SMA200."""
    seed_bars(conn, sessions=400)
    body = client.get("/api/tickers/PODD/bars?days=100").json()
    assert body["sma_slow"][0] is not None
    assert body["sma_fast"][0] is not None


def test_a_short_history_pads_the_average_with_nulls(client, conn):
    seed_bars(conn, sessions=60)
    body = client.get("/api/tickers/PODD/bars?days=60").json()
    assert body["sma_slow"] == [None] * 60
    assert body["sma_fast"][0] is None
    assert body["sma_fast"][-1] is not None


def test_bars_are_refused_for_an_unknown_ticker(client):
    assert client.get("/api/tickers/NOPE/bars").status_code == 404


def test_an_absurd_window_is_rejected_by_validation(client, conn):
    seed_bars(conn)
    assert client.get("/api/tickers/PODD/bars?days=99999").status_code == 422


# --- indicator series -------------------------------------------------------


def test_an_unknown_series_is_refused(client, conn):
    seed_bars(conn)
    response = client.get("/api/tickers/PODD/indicators?name=drop_table")
    assert response.status_code == 400


def test_the_series_is_empty_until_a_run_records_one(client, conn):
    seed_bars(conn)
    body = client.get("/api/tickers/PODD/indicators?name=rsi").json()
    assert body["value"] == []


def test_each_run_adds_a_point_to_the_series(client, conn):
    seed_alert(conn)
    market = FakeMarket(price=140.0, history=make_history([float(100 + i) for i in range(260)]))
    for offset in range(3):
        run_checks(conn, market, RecordingDispatcher(), now=NOW + timedelta(hours=offset))
    body = client.get("/api/tickers/PODD/indicators?name=rsi").json()
    assert len(body["value"]) == 3
    assert body["taken_at"] == sorted(body["taken_at"])


# --- alerts and events ------------------------------------------------------


def test_alerts_carry_their_last_outcome(client, conn):
    seed_alert(conn)
    run_checks(conn, FakeMarket(price=101.0, history=make_history([100.0, 101.0])),
               RecordingDispatcher(), now=NOW)
    rows = client.get("/api/tickers/PODD/alerts").json()
    assert rows[0]["last_outcome"] == "quiet"
    assert rows[0]["active"] is True


def test_events_list_what_fired(client, conn):
    seed_alert(conn)
    run_checks(conn, FakeMarket(price=130.0, history=make_history([100.0, 130.0])),
               RecordingDispatcher(), now=NOW)
    rows = client.get("/api/tickers/PODD/events").json()
    assert len(rows) == 1
    assert rows[0]["delivered"] == ["test"]
    assert rows[0]["price"] == 130.0


def test_the_api_never_calls_a_provider(client, conn):
    """A viewer must not trigger a download; that is the worker's job alone."""
    seed_alert(conn)
    seed_bars(conn)

    class Exploding:
        def context(self, *a, **k):
            raise AssertionError("the API reached for live data")

        def quote(self, *a, **k):
            raise AssertionError("the API reached for live data")

    for path in (
        "/api/health",
        "/api/tickers",
        "/api/tickers/PODD",
        "/api/tickers/PODD/bars",
        "/api/tickers/PODD/indicators?name=rsi",
        "/api/tickers/PODD/alerts",
        "/api/tickers/PODD/events",
    ):
        assert client.get(path).status_code == 200, path


def test_detail_reports_the_session_change(client, conn):
    """A price with no move next to it is half a number."""
    seed_alert(conn)
    seed_bars(conn, sessions=10, start=100.0)  # closes rise by 0.5 each session
    run_checks(
        conn,
        FakeMarket(price=105.0, history=make_history([float(100 + i) for i in range(30)])),
        RecordingDispatcher(),
        now=NOW,
    )
    body = client.get("/api/tickers/PODD").json()
    assert body["change_abs"] is not None
    assert body["change_pct"] is not None


def test_the_change_is_absent_when_there_is_only_one_bar(client, conn):
    seed_alert(conn)
    seed_bars(conn, sessions=1)
    body = client.get("/api/tickers/PODD").json()
    assert body["change_abs"] is None
    assert body["change_pct"] is None


# --- the web shell ----------------------------------------------------------


def test_the_page_links_versioned_assets(tmp_path, conn):
    """A cached stylesheet outliving an edit is how a redesign fails to ship."""
    from fa.api.app import asset_version, render_index

    deps.set_database(conn)
    with TestClient(create_app(serve_web=True)) as web:
        body = web.get("/").text
    deps.set_database(None)

    version = asset_version()
    assert "{ASSETS}" not in body
    assert f"/static/styles.css?v={version}" in body
    assert f"/static/app.js?v={version}" in body
    assert render_index() == body


def test_the_asset_version_follows_the_files(monkeypatch, tmp_path):
    from fa.api import app as api_app

    original = api_app.WEB_ROOT
    monkeypatch.setattr(api_app, "WEB_ROOT", tmp_path)
    (tmp_path / "styles.css").write_text("a{}")
    (tmp_path / "app.js").write_text("//")
    first = api_app.asset_version()
    (tmp_path / "styles.css").write_text("a{color:red}")
    assert api_app.asset_version() != first
    monkeypatch.setattr(api_app, "WEB_ROOT", original)


def test_the_day_change_ignores_the_session_in_progress(client, conn):
    """Before the open the provider's bar for today just repeats yesterday's
    close; comparing against it reports every ticker flat every morning."""
    from datetime import date as real_date

    today = real_date.today()
    history_store.save_bars(
        conn, "PODD",
        [
            PricePoint(day=today - timedelta(days=2), close=100.0),
            PricePoint(day=today - timedelta(days=1), close=120.0),
            PricePoint(day=today, close=120.0),          # partial, carried over
        ],
        "test",
    )
    seed_alert(conn)
    run_checks(conn, FakeMarket(price=132.0, history=make_history([100.0, 120.0])),
               RecordingDispatcher(), now=NOW)
    body = client.get("/api/tickers/PODD").json()
    # 132 against yesterday's 120, not against today's placeholder.
    assert body["change_abs"] == pytest.approx(12.0)
    assert body["change_pct"] == pytest.approx(10.0)


def test_a_ticker_with_only_a_partial_bar_has_no_day_change(conn):
    """Nothing completed to compare against, so the move is unknown, not zero.

    Straight at the helper: driving this through a run would have the fake
    market archive older bars and quietly give the comparison what it needs.
    """
    from datetime import date as real_date

    from fa.api.tickers import _session_change

    history_store.save_bars(
        conn, "PODD", [PricePoint(day=real_date.today(), close=120.0)], "test"
    )
    assert _session_change(conn, "PODD", 132.0) == (None, None)
