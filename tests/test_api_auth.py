"""Authentication, and the isolation it is there to make possible.

The second half of this file is the one that matters. Account scoping is spread
across seven store modules and roughly sixty statements; missing one leaks
another person's portfolio. Rather than trusting the review, every endpoint is
exercised with two accounts and asserted not to cross.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from fa.alerts import authoring
from fa.api import auth, deps
from fa.api.app import create_app
from fa.models import Position, PricePoint, Transaction
from fa.store import events as events_store
from fa.store import history as history_store
from fa.store import positions as positions_store
from fa.store import runs as runs_store
from fa.store import transactions as transactions_store
from fa.store.schema import LOCAL_ACCOUNT_ID
from tests.conftest import make_settings

TOKEN = "un-token-largo-y-secreto"


@pytest.fixture()
def client(conn):
    deps.set_database(conn)
    with TestClient(create_app(serve_web=False)) as test_client:
        yield test_client
    deps.set_database(None)


def with_settings(monkeypatch, tmp_path, **overrides):
    settings = make_settings(tmp_path, **overrides)
    monkeypatch.setattr("fa.api.auth.load_settings", lambda: settings)
    monkeypatch.setattr("fa.api.alerts.load_settings", lambda: settings)
    return settings


# --- modes ------------------------------------------------------------------


def test_without_a_token_the_api_is_open(client):
    body = client.get("/api/session").json()
    assert body["mode"] == "open"
    assert body["anonymous"] is True
    assert body["account_id"] == LOCAL_ACCOUNT_ID


def test_a_configured_token_is_demanded(client, monkeypatch, tmp_path):
    with_settings(monkeypatch, tmp_path, api_token=TOKEN)
    assert client.get("/api/tickers").status_code == 401
    assert client.get("/api/portfolio").status_code == 401


def test_the_right_token_gets_in(client, monkeypatch, tmp_path):
    with_settings(monkeypatch, tmp_path, api_token=TOKEN)
    headers = {"Authorization": f"Bearer {TOKEN}"}
    assert client.get("/api/tickers", headers=headers).status_code == 200
    assert client.get("/api/session", headers=headers).json()["mode"] == "token"


def test_a_wrong_token_is_refused(client, monkeypatch, tmp_path):
    with_settings(monkeypatch, tmp_path, api_token=TOKEN)
    response = client.get("/api/tickers", headers={"Authorization": "Bearer otra-cosa"})
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_a_token_in_the_wrong_scheme_does_not_pass(client, monkeypatch, tmp_path):
    with_settings(monkeypatch, tmp_path, api_token=TOKEN)
    assert client.get("/api/tickers", headers={"Authorization": TOKEN}).status_code == 401
    assert client.get(
        "/api/tickers", headers={"Authorization": f"Basic {TOKEN}"}
    ).status_code == 401


def test_writes_are_guarded_too(client, monkeypatch, tmp_path):
    """A read-only guard would be worse than none: it reads as protection."""
    with_settings(monkeypatch, tmp_path, api_token=TOKEN)
    assert client.post("/api/tickers/PODD/alerts", json={"kind": "rsi"}).status_code == 401
    assert client.post(
        "/api/portfolio/transactions",
        json={"ticker": "PODD", "kind": "buy", "trade_date": "2026-01-01",
              "quantity": 1, "price": 1.0},
    ).status_code == 401


def test_the_login_screen_can_ask_what_is_needed(client, monkeypatch, tmp_path):
    """auth-mode stays open on purpose: a client cannot present a credential it
    has not been told to collect."""
    with_settings(monkeypatch, tmp_path, api_token=TOKEN)
    body = client.get("/api/auth-mode").json()
    assert body["mode"] == "token"
    assert body["supabase_url"] == ""


def test_supabase_settings_pick_the_supabase_mode(tmp_path):
    settings = make_settings(
        tmp_path, supabase_url="https://x.supabase.co", supabase_jwt_secret="una-clave-de-al-menos-32-bytes-para-hs256"
    )
    assert auth.mode_for(settings) == auth.SUPABASE


# --- supabase tokens --------------------------------------------------------


def encode(payload: dict, secret: str = "una-clave-de-al-menos-32-bytes-para-hs256") -> str:
    jwt = pytest.importorskip("jwt")
    return jwt.encode(payload, secret, algorithm="HS256")


def supabase(monkeypatch, tmp_path):
    return with_settings(
        monkeypatch,
        tmp_path,
        supabase_url="https://x.supabase.co",
        supabase_jwt_secret="una-clave-de-al-menos-32-bytes-para-hs256",
        supabase_anon_key="anon",
    )


def claims(subject="user-1", email="a@b.com", minutes=30):
    return {
        "sub": subject,
        "email": email,
        "aud": "authenticated",
        "exp": datetime.now(timezone.utc) + timedelta(minutes=minutes),
    }


def test_a_valid_supabase_token_creates_its_account(client, conn, monkeypatch, tmp_path):
    supabase(monkeypatch, tmp_path)
    token = encode(claims())
    body = client.get("/api/session", headers={"Authorization": f"Bearer {token}"}).json()
    assert body["mode"] == "supabase"
    assert body["email"] == "a@b.com"
    assert body["account_id"] != LOCAL_ACCOUNT_ID
    row = conn.execute("SELECT * FROM account_users WHERE auth_id = ?", ("user-1",)).fetchone()
    assert row is not None


def test_the_same_user_keeps_the_same_account(client, monkeypatch, tmp_path):
    supabase(monkeypatch, tmp_path)
    headers = {"Authorization": f"Bearer {encode(claims())}"}
    first = client.get("/api/session", headers=headers).json()["account_id"]
    second = client.get("/api/session", headers=headers).json()["account_id"]
    assert first == second


def test_two_users_get_two_accounts(client, monkeypatch, tmp_path):
    supabase(monkeypatch, tmp_path)
    one = client.get("/api/session", headers={
        "Authorization": f"Bearer {encode(claims('user-1', 'a@b.com'))}"}).json()
    two = client.get("/api/session", headers={
        "Authorization": f"Bearer {encode(claims('user-2', 'c@d.com'))}"}).json()
    assert one["account_id"] != two["account_id"]


def test_an_expired_token_is_refused(client, monkeypatch, tmp_path):
    supabase(monkeypatch, tmp_path)
    token = encode(claims(minutes=-5))
    response = client.get("/api/session", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert "expiró" in response.json()["detail"]


def test_a_token_signed_with_another_key_is_refused(client, monkeypatch, tmp_path):
    supabase(monkeypatch, tmp_path)
    token = encode(claims(), secret="otra-clave-igual-de-larga-que-la-primera!!")
    assert client.get(
        "/api/session", headers={"Authorization": f"Bearer {token}"}
    ).status_code == 401


def test_the_reason_a_token_failed_is_not_disclosed(client, monkeypatch, tmp_path):
    """Telling a caller why their forgery failed helps them forge a better one."""
    supabase(monkeypatch, tmp_path)
    token = encode({**claims(), "aud": "otra-audiencia"})
    detail = client.get(
        "/api/session", headers={"Authorization": f"Bearer {token}"}
    ).json()["detail"]
    assert detail == "Token inválido."


def test_a_token_without_a_subject_is_refused(client, monkeypatch, tmp_path):
    supabase(monkeypatch, tmp_path)
    payload = claims()
    payload.pop("sub")
    assert client.get(
        "/api/session", headers={"Authorization": f"Bearer {encode(payload)}"}
    ).status_code == 401


# --- isolation --------------------------------------------------------------


def seed(conn, account: int, ticker: str) -> dict:
    """A full set of rows for one account: position, ledger, alert, run, event."""
    position = positions_store.add_position(
        conn,
        Position(ticker=ticker, quantity=10, buy_price=100.0, buy_date=date(2026, 1, 15)),
        account_id=account,
    )
    history_store.save_bars(
        conn, ticker,
        [PricePoint(day=date(2026, 8, 19), close=100.0), PricePoint(day=date(2026, 8, 20), close=130.0)],
        "test",
    )
    transactions_store.record(
        conn,
        Transaction(ticker=ticker, kind="dividend", trade_date=date(2026, 4, 1), amount=11.0),
        account_id=account,
    )
    alert = authoring.create_alert(
        conn, ticker, "rsi", {}, price_for=lambda s: 100.0, account_id=account
    )
    run_id = runs_store.start_run(conn, trigger="test", account_id=account)
    runs_store.record_evaluation(
        conn, run_id=run_id, alert_id=alert.id, ticker=ticker, kind="rsi",
        outcome=runs_store.QUIET, account_id=account,
    )
    runs_store.finish_run(conn, run_id, checked=1, fired=0, skipped_cooldown=0, expired=0)
    history_store.save_valuation(
        conn, cost_basis=1000.0, market_value=1300.0, pnl_abs=300.0, pnl_pct=30.0,
        positions=1, account_id=account,
    )
    return {"position": position, "alert": alert, "run_id": run_id}


@pytest.fixture()
def two_accounts(client, conn, monkeypatch, tmp_path):
    """Two Supabase users, each with a full set of their own rows."""
    supabase(monkeypatch, tmp_path)
    headers = {}
    for subject, ticker in (("user-1", "AAA"), ("user-2", "BBB")):
        token = encode(claims(subject, f"{subject}@x.com"))
        head = {"Authorization": f"Bearer {token}"}
        account = client.get("/api/session", headers=head).json()["account_id"]
        seed(conn, account, ticker)
        headers[subject] = head
    return headers


ISOLATED_PATHS = [
    "/api/tickers",
    "/api/portfolio",
    "/api/portfolio/history",
    "/api/portfolio/transactions",
    "/api/health",
]


@pytest.mark.parametrize("path", ISOLATED_PATHS)
def test_no_endpoint_shows_another_account_rows(two_accounts, client, path):
    """The guarantee, endpoint by endpoint: BBB must never appear for user-1."""
    body = client.get(path, headers=two_accounts["user-1"]).text
    assert "BBB" not in body, path
    other = client.get(path, headers=two_accounts["user-2"]).text
    assert "AAA" not in other, path


def test_each_account_sees_only_its_own_tickers(two_accounts, client):
    mine = client.get("/api/tickers", headers=two_accounts["user-1"]).json()
    assert [row["ticker"] for row in mine] == ["AAA"]


def test_each_account_sees_only_its_own_holdings(two_accounts, client):
    mine = client.get("/api/portfolio", headers=two_accounts["user-1"]).json()
    assert [h["ticker"] for h in mine["holdings"]] == ["AAA"]
    assert mine["dividends"] == 11.0


def test_market_data_is_shared_not_isolated(two_accounts, client):
    """Bars belong to a ticker, not to a person; both accounts read the same rows."""
    for headers in two_accounts.values():
        assert client.get("/api/tickers/AAA/bars", headers=headers).status_code == 200


def test_an_alert_of_another_account_cannot_be_read(two_accounts, client, conn):
    theirs = client.get("/api/tickers/BBB/alerts", headers=two_accounts["user-2"]).json()
    assert theirs
    mine = client.get("/api/tickers/BBB/alerts", headers=two_accounts["user-1"]).json()
    assert mine == []


def test_an_alert_of_another_account_cannot_be_silenced(two_accounts, client, conn):
    from fa.store import alerts as alerts_store

    theirs = client.get("/api/tickers/BBB/alerts", headers=two_accounts["user-2"]).json()[0]
    response = client.patch(
        f"/api/alerts/{theirs['id']}", json={"active": False}, headers=two_accounts["user-1"]
    )
    assert response.status_code == 404
    assert alerts_store.get_alert(conn, theirs["id"], account_id=None).active is True


def test_an_alert_of_another_account_cannot_be_deleted(two_accounts, client, conn):
    theirs = client.get("/api/tickers/BBB/alerts", headers=two_accounts["user-2"]).json()[0]
    assert client.delete(
        f"/api/alerts/{theirs['id']}", headers=two_accounts["user-1"]
    ).status_code == 404
    # The soft-delete stamp is on the row, not on the model, so check the column.
    row = conn.execute(
        "SELECT deleted_at FROM alerts WHERE id = ?", (theirs["id"],)
    ).fetchone()
    assert row["deleted_at"] is None


def test_a_ledger_entry_of_another_account_cannot_be_deleted(two_accounts, client, conn):
    theirs = client.get(
        "/api/portfolio/transactions", headers=two_accounts["user-2"]
    ).json()[0]
    assert client.delete(
        f"/api/portfolio/transactions/{theirs['id']}", headers=two_accounts["user-1"]
    ).status_code == 404


def test_an_event_of_another_account_cannot_be_acknowledged(two_accounts, client, conn):
    from fa.models import Alert, Signal

    alert = Alert(id=1, ticker="BBB", kind="rsi")
    account = client.get("/api/session", headers=two_accounts["user-2"]).json()["account_id"]
    event_id = events_store.record_event(
        conn, Signal(alert=alert, title="t", message="m"), ["console"], account_id=account
    )
    assert client.post(
        f"/api/events/{event_id}/ack", headers=two_accounts["user-1"]
    ).status_code == 404
    assert client.post(
        f"/api/events/{event_id}/ack", headers=two_accounts["user-2"]
    ).status_code == 200


def test_a_new_alert_lands_in_the_callers_account(two_accounts, client, conn):
    from fa.store import alerts as alerts_store

    account = client.get("/api/session", headers=two_accounts["user-1"]).json()["account_id"]
    body = client.post(
        "/api/tickers/AAA/alerts", json={"kind": "sma_break"}, headers=two_accounts["user-1"]
    ).json()
    stored = alerts_store.get_alert(conn, body["id"], account_id=account)
    assert stored is not None
    assert alerts_store.get_alert(conn, body["id"], account_id=LOCAL_ACCOUNT_ID) is None


# --- concurrency ------------------------------------------------------------


def test_a_request_does_not_hold_the_database_lock():
    """It used to, for the whole request — and because FastAPI finalises yield
    dependencies after background tasks, that meant every endpoint froze for as
    long as an AI report took. Forty seconds during which the page could not
    even ask whether the report had finished."""
    import threading

    from fa.api import deps

    generator = deps.get_db()
    next(generator)  # the dependency is now "inside" a request
    acquired = deps._lock.acquire(blocking=False)
    if acquired:
        deps._lock.release()
    generator.close()
    assert acquired, "get_db held the lock across the request"

    # And it is still acquirable from another thread, which is where uvicorn
    # runs sync endpoints.
    result = []
    generator = deps.get_db()
    next(generator)
    thread = threading.Thread(
        target=lambda: result.append(deps._lock.acquire(timeout=1))
    )
    thread.start()
    thread.join()
    if result and result[0]:
        deps._lock.release()
    generator.close()
    assert result == [True]
