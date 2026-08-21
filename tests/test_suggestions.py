"""Parsing, validation and persistence of AI suggestions."""
from __future__ import annotations

from fa.alerts.suggestions import actionable, parse_suggestions
from fa.models import Suggestion
from fa.store import suggestions as suggestions_store

PAYLOAD = {
    "suggested_alerts": [
        {"kind": "price_above", "params": {"price": 200}, "priority": "high", "rationale": "resistencia"},
        {"kind": "trailing_stop", "params": {"pct": 12}, "rationale": "proteger ganancia"},
    ],
    "suggested_actions": [
        {"kind": "tomar ganancias parciales", "rationale": "concentración alta", "priority": "medium"}
    ],
}


def test_alerts_and_actions_are_both_parsed():
    parsed = parse_suggestions(PAYLOAD, "podd")
    assert len(parsed) == 3
    assert {s.category for s in parsed} == {"alert", "action"}
    assert all(s.ticker == "PODD" for s in parsed)


def test_params_are_normalised_against_the_catalogue():
    parsed = parse_suggestions({"suggested_alerts": [{"kind": "price_above", "params": {"price": "200"}}]}, "PODD")
    assert parsed[0].params["price"] == 200.0


def test_invalid_params_downgrade_to_an_action_instead_of_being_lost():
    parsed = parse_suggestions(
        {"suggested_alerts": [{"kind": "sma_cross", "params": {"fast": 200, "slow": 50}}]}, "PODD"
    )
    assert parsed[0].category == "action"
    assert "inválidos" in parsed[0].rationale


def test_unknown_kind_becomes_an_action():
    parsed = parse_suggestions([{"kind": "vender_todo", "rationale": "pánico"}], "PODD")
    assert parsed[0].category == "action" and parsed[0].kind == "vender_todo"


def test_unknown_priority_falls_back_to_medium():
    parsed = parse_suggestions([{"kind": "price_below", "params": {"price": 1}, "priority": "urgentísimo"}], "PODD")
    assert parsed[0].priority == "medium"


def test_plain_list_payload_is_accepted():
    assert len(parse_suggestions([{"kind": "rsi", "params": {}}], "PODD")) == 1


def test_garbage_payloads_yield_nothing():
    assert parse_suggestions(None, "PODD") == []
    assert parse_suggestions("texto", "PODD") == []
    assert parse_suggestions([1, 2, 3], "PODD") == []


def test_empty_entries_are_dropped():
    assert parse_suggestions([{"params": {}}], "PODD") == []


def test_the_batch_is_capped():
    payload = [{"kind": "price_above", "params": {"price": i}} for i in range(1, 30)]
    assert len(parse_suggestions(payload, "PODD")) == 12


def test_actionable_filters_out_free_form_actions():
    parsed = parse_suggestions(PAYLOAD, "PODD")
    assert len(actionable(parsed)) == 2


def test_suggestions_round_trip_and_resolve(conn):
    from fa.models import Alert
    from fa.store import alerts as alerts_store

    alert = alerts_store.add_alert(conn, Alert(ticker="PODD", kind="price_above", params={"price": 200}))
    stored = suggestions_store.add_suggestions(
        conn, parse_suggestions(PAYLOAD, "PODD"), analysis_id=None, model="test-model"
    )
    assert all(s.id is not None for s in stored)
    assert suggestions_store.pending_count(conn, "PODD") == 3
    suggestions_store.resolve(conn, stored[0].id, suggestions_store.ACCEPTED, alert_id=alert.id)
    assert len(suggestions_store.list_suggestions(conn, ticker="PODD")) == 2
    accepted = suggestions_store.list_suggestions(conn, status=suggestions_store.ACCEPTED)
    assert accepted[0].alert_id == alert.id and accepted[0].decided_at is not None


def test_headline_is_readable():
    alert = Suggestion(ticker="PODD", category="alert", kind="price_above", params={"price": 200.0})
    action = Suggestion(ticker="PODD", category="action", kind="vender mitad")
    assert alert.headline == "price_above(price=200.0)"
    assert action.headline == "vender mitad"
