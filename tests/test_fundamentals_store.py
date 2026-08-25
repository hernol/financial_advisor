"""Storing the metric tables instead of rebuilding them from a download.

They describe a company rather than an account, so they sit on the shared side
with the price bars, and they age out on a quarterly rhythm rather than an
hourly one.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from fa.api import deps
from fa.api.app import create_app
from fa.store import fundamentals as fundamentals_store


@pytest.fixture()
def client(conn):
    deps.set_database(conn)
    with TestClient(create_app(serve_web=False)) as test_client:
        yield test_client
    deps.set_database(None)


def frame(**overrides) -> pd.DataFrame:
    row = {
        "Period": "2026", "Revenue": 3200.0, "FCF": 249.3, "Net_Debt": 3928.0,
        "FCF_Yield": 6.62, "EV_FCF_Yield": 3.1, "Gross_Margin": 44.5,
        "Net_Debt_Estimated": False, "Interest_Coverage": float("nan"),
        **overrides,
    }
    return pd.DataFrame([row])


# --- storing ----------------------------------------------------------------


def test_a_table_round_trips(conn):
    fundamentals_store.save(conn, "RH", fundamentals_store.ANNUAL, frame(), source="yahoo")
    stored = fundamentals_store.load(conn, "RH", fundamentals_store.ANNUAL)
    assert stored["source"] == "yahoo"
    assert stored["rows"][0]["Revenue"] == 3200.0
    assert stored["rows"][0]["Period"] == "2026"


def test_a_missing_line_stays_missing(conn):
    """NaN does not survive JSON, and a line absent from a filing is not zero."""
    fundamentals_store.save(conn, "RH", fundamentals_store.ANNUAL, frame())
    stored = fundamentals_store.load(conn, "RH", fundamentals_store.ANNUAL)
    assert stored["rows"][0]["Interest_Coverage"] is None


def test_booleans_survive(conn):
    """Net_Debt_Estimated decides whether a caveat is shown next to the EV."""
    fundamentals_store.save(
        conn, "RH", fundamentals_store.ANNUAL, frame(Net_Debt_Estimated=True)
    )
    stored = fundamentals_store.load(conn, "RH", fundamentals_store.ANNUAL)
    assert stored["rows"][0]["Net_Debt_Estimated"] is True


def test_saving_twice_replaces_rather_than_duplicates(conn):
    fundamentals_store.save(conn, "RH", fundamentals_store.ANNUAL, frame(Revenue=1.0))
    fundamentals_store.save(conn, "RH", fundamentals_store.ANNUAL, frame(Revenue=2.0))
    stored = fundamentals_store.load(conn, "RH", fundamentals_store.ANNUAL)
    assert stored["rows"][0]["Revenue"] == 2.0
    assert conn.execute(
        "SELECT COUNT(*) c FROM fundamental_snapshots WHERE ticker = 'RH'"
    ).fetchone()["c"] == 1


def test_the_two_period_kinds_are_separate(conn):
    fundamentals_store.save(conn, "RH", fundamentals_store.ANNUAL, frame(Revenue=1.0))
    fundamentals_store.save(conn, "RH", fundamentals_store.QUARTERLY, frame(Revenue=2.0))
    both = fundamentals_store.load_all(conn, "RH")
    assert both["annual"]["rows"][0]["Revenue"] == 1.0
    assert both["quarterly"]["rows"][0]["Revenue"] == 2.0


# --- ageing -----------------------------------------------------------------


def test_nothing_stored_counts_as_stale(conn):
    assert fundamentals_store.is_stale(None) is True


def test_an_empty_table_counts_as_stale(conn):
    fundamentals_store.save(conn, "RH", fundamentals_store.ANNUAL, pd.DataFrame())
    assert fundamentals_store.is_stale(fundamentals_store.load(conn, "RH", "annual")) is True


def test_a_recent_table_is_fresh(conn):
    fundamentals_store.save(conn, "RH", fundamentals_store.ANNUAL, frame())
    assert fundamentals_store.is_stale(fundamentals_store.load(conn, "RH", "annual")) is False


def test_a_table_older_than_a_week_is_stale(conn):
    old = datetime.now(timezone.utc) - timedelta(days=8)
    fundamentals_store.save(conn, "RH", fundamentals_store.ANNUAL, frame(), fetched_at=old)
    assert fundamentals_store.is_stale(fundamentals_store.load(conn, "RH", "annual")) is True


# --- the API ----------------------------------------------------------------


def test_an_unknown_ticker_says_it_has_nothing(client):
    body = client.get("/api/tickers/NOPE/fundamentals").json()
    assert body["missing"] is True
    assert body["periods"]["annual"]["rows"] == []
    assert body["periods"]["annual"]["stale"] is True


def test_the_api_returns_what_is_stored(client, conn):
    fundamentals_store.save(conn, "RH", fundamentals_store.ANNUAL, frame(), source="yahoo")
    body = client.get("/api/tickers/RH/fundamentals").json()
    assert body["missing"] is False
    assert body["periods"]["annual"]["source"] == "yahoo"
    assert body["periods"]["annual"]["rows"][0]["FCF"] == 249.3
    assert body["summary_columns"][0] == "Period"
    assert "Gross_Margin" in body["quality_columns"]


def test_an_estimated_net_debt_is_flagged_for_the_whole_screen(client, conn):
    """The caveat has to travel with the number: it propagates into the EV."""
    fundamentals_store.save(
        conn, "RH", fundamentals_store.ANNUAL, frame(Net_Debt_Estimated=True)
    )
    assert client.get("/api/tickers/RH/fundamentals").json()["net_debt_estimated"] is True


def test_real_numbers_are_not_flagged(client, conn):
    fundamentals_store.save(conn, "RH", fundamentals_store.ANNUAL, frame())
    assert client.get("/api/tickers/RH/fundamentals").json()["net_debt_estimated"] is False


def test_reading_them_never_fetches(client, conn):
    """The test market refuses every call, so a fetch here would fail loudly."""
    fundamentals_store.save(conn, "RH", fundamentals_store.ANNUAL, frame())
    assert client.get("/api/tickers/RH/fundamentals").status_code == 200


def test_a_refresh_can_be_asked_for(client):
    """The write is the one allowed to go and get them."""
    assert client.post("/api/tickers/RH/fundamentals/refresh").status_code == 202
