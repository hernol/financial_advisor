"""Taking the machine-readable block out of the prose.

The model appends a JSON block that becomes the suggestion cards. Leaving it in
the report means scrolling past a wall of JSON to reach nothing new.
"""
from __future__ import annotations

import pytest

from fa.jsonblock import extract_json, strip_json

REPORT = """# Informe de RH

**Veredicto:** mantener.

## Alertas sugeridas (JSON)

```json
[{"kind": "rsi", "params": {"period": 14}}]
```

Fin del informe."""


def test_the_block_is_removed():
    assert "```" not in strip_json(REPORT)
    assert '"kind"' not in strip_json(REPORT)


def test_the_prose_survives():
    cleaned = strip_json(REPORT)
    assert "# Informe de RH" in cleaned
    assert "**Veredicto:** mantener." in cleaned
    assert "Fin del informe." in cleaned


def test_the_heading_that_announced_it_goes_too():
    """Otherwise the report ends on a promise it no longer keeps."""
    assert "Alertas sugeridas" not in strip_json(REPORT)


def test_a_sentence_mentioning_json_is_prose_and_stays():
    text = "El proveedor devolvió un JSON inválido, así que faltan datos."
    assert strip_json(text) == text


def test_the_gap_left_behind_is_closed():
    assert "\n\n\n" not in strip_json(REPORT)


def test_an_unfenced_brace_is_left_alone():
    """A bare brace can be part of a sentence; losing prose is worse."""
    text = "El margen {bruto} cayó."
    assert strip_json(text) == text


def test_several_blocks_all_go():
    text = "Uno\n\n```json\n{}\n```\n\nDos\n\n```json\n[]\n```\n\nTres"
    cleaned = strip_json(text)
    assert "```" not in cleaned
    assert cleaned.count("\n\n") <= 2


def test_a_report_without_a_block_is_untouched():
    text = "# Informe\n\nSin sugerencias esta vez."
    assert strip_json(text) == text


@pytest.mark.parametrize("value", ["", None])
def test_nothing_in_nothing_out(value):
    assert strip_json(value) == value


def test_the_suggestions_are_parsed_before_the_prose_is_stripped():
    """Stripping first would leave nothing to parse — the order matters."""
    assert extract_json(REPORT) == [{"kind": "rsi", "params": {"period": 14}}]
    assert extract_json(strip_json(REPORT)) is None


def test_a_rule_left_dangling_at_the_end_goes():
    """It used to separate the prose from the block; now it separates nothing."""
    text = "Veredicto: mantener.\n\n---\n\n```json\n[]\n```\n"
    assert strip_json(text) == "Veredicto: mantener."


def test_a_rule_in_the_middle_stays():
    text = "Uno\n\n---\n\nDos"
    assert strip_json(text) == text
