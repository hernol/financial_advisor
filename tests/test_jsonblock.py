"""Extraction of JSON payloads embedded in model prose."""
from __future__ import annotations

from fa.jsonblock import extract_json

FENCE = "```"


def test_fenced_json_block():
    text = f"informe bla bla\n{FENCE}json\n{{\"suggested_alerts\": []}}\n{FENCE}\n"
    assert extract_json(text) == {"suggested_alerts": []}


def test_unlabelled_fence():
    text = f"{FENCE}\n[1, 2]\n{FENCE}"
    assert extract_json(text) == [1, 2]


def test_bare_json_document():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_json_after_prose_without_a_fence():
    assert extract_json('Mi conclusión.\n{"kind": "pct_up", "params": {"pct": 10}}') == {
        "kind": "pct_up",
        "params": {"pct": 10},
    }


def test_nested_braces_are_balanced():
    payload = extract_json('texto {"a": {"b": {"c": 1}}} fin')
    assert payload == {"a": {"b": {"c": 1}}}


def test_prose_without_json_returns_none():
    assert extract_json("no hay nada estructurado acá") is None


def test_broken_json_returns_none():
    assert extract_json('{"a": ') is None


def test_empty_text_returns_none():
    assert extract_json("") is None
