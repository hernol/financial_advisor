"""Untrusted text must not be able to become markup.

Two sources meet in the dashboard: fields a person types, and the AI's free
text — which was written after showing the model whatever the user pasted as
context, so a prompt injection in that paste comes back out as a rationale.
Because the session token lives in localStorage, script running on the page
walks off with the credential, which is what makes this worth pinning down.

The client escapes on the way out; these tests keep the bad value out of the
database on the way in.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fa.api import deps
from fa.api.app import create_app
from fa.models import TICKER_PATTERN

WEB = Path(__file__).resolve().parent.parent / "web"

PAYLOADS = [
    '<img src=x onerror=1>',
    '"><script>x</script>',
    "'onload='x",
    '<svg onload=x>',
    'AA"><b',
]


@pytest.fixture()
def client(conn):
    deps.set_database(conn)
    with TestClient(create_app(serve_web=False)) as test_client:
        yield test_client
    deps.set_database(None)


# --- what may be stored -----------------------------------------------------


@pytest.mark.parametrize("payload", PAYLOADS)
def test_a_ticker_that_looks_like_markup_is_refused(client, payload):
    response = client.post(
        "/api/portfolio/transactions",
        json={"ticker": payload, "kind": "buy", "trade_date": "2026-01-10",
              "quantity": 1, "price": 1.0},
    )
    assert response.status_code == 422, payload


@pytest.mark.parametrize("symbol", ["RH", "BRK.B", "TAP", "ldos", "AIR.PA", "0700.HK"])
def test_real_symbols_still_pass(client, symbol):
    response = client.post(
        "/api/portfolio/transactions",
        json={"ticker": symbol, "kind": "buy", "trade_date": "2026-01-10",
              "quantity": 1, "price": 1.0},
    )
    assert response.status_code == 201, symbol


def test_a_correction_cannot_smuggle_one_in(client):
    created = client.post(
        "/api/portfolio/transactions",
        json={"ticker": "RH", "kind": "buy", "trade_date": "2026-01-10",
              "quantity": 1, "price": 1.0},
    ).json()
    response = client.patch(
        f"/api/portfolio/transactions/{created['id']}", json={"ticker": "<img src=x>"}
    )
    assert response.status_code == 422


@pytest.mark.parametrize("payload", PAYLOADS)
def test_an_alert_cannot_be_created_for_a_markup_ticker(client, conn, payload):
    """The status varies — a payload with a slash never matches the route at
    all — so the property asserted is that nothing was stored."""
    from fa.store import alerts as alerts_store

    response = client.post(f"/api/tickers/{payload}/alerts", json={"kind": "rsi"})
    assert response.status_code in (404, 422), payload
    assert alerts_store.list_alerts(conn) == []


def test_the_pattern_matches_symbols_and_not_markup():
    assert re.match(TICKER_PATTERN, "BRK.B")
    assert not re.match(TICKER_PATTERN, "<b>")
    assert not re.match(TICKER_PATTERN, 'a"b')


# --- what the client does with it -------------------------------------------
# The AI's rationale is free text and cannot be pattern-validated, so the
# defence there is escaping. These read the client source, which is crude, but
# an unescaped interpolation is exactly the kind of thing that comes back.


def read_client() -> str:
    return (WEB / "app.js").read_text(encoding="utf-8")


def test_the_escape_encodes_quotes_too():
    """The textContent trick does not, and a class attribute needs it."""
    source = read_client()
    assert "'\"': '&quot;'" in source
    assert '"\'": \'&#39;\'' in source


def test_the_ai_rationale_is_escaped():
    assert "${escapeHtml(s.rationale)}" in read_client()


def test_the_ai_priority_is_whitelisted_not_escaped_into_a_class():
    source = read_client()
    assert "safeClass(s.priority, PRIORITY_CLASSES" in source
    assert '<span class="priority ${s.priority}"' not in source


def test_no_class_attribute_takes_a_raw_value():
    """A class is an attribute; only a known set may reach one."""
    source = read_client()
    for raw in ('class="chip ${e.severity}"', 'class="tx ${t.kind}"',
                'class="chip ${row.trend}"'):
        assert raw not in source, raw


def test_form_values_are_escaped_into_their_attribute():
    source = read_client()
    assert 'value="${v}"' in source
    assert "const v = escapeHtml(value);" in source
