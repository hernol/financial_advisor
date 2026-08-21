"""Input helpers, driven with a scripted stdin."""
from __future__ import annotations

from datetime import date

import pytest

from fa.errors import ValidationError
from fa.ui import prompts


def feed(monkeypatch, *answers: str) -> None:
    queue = list(answers)
    monkeypatch.setattr("builtins.input", lambda *_: queue.pop(0))


def test_ask_uses_the_default_on_empty_input(monkeypatch):
    feed(monkeypatch, "")
    assert prompts.ask("Moneda", "USD") == "USD"


def test_ask_float_retries_until_valid(monkeypatch, capsys):
    feed(monkeypatch, "mucho", "141.5")
    assert prompts.ask_float("Precio") == 141.5
    assert "número válido" in capsys.readouterr().out


def test_ask_int_retries_until_valid(monkeypatch):
    feed(monkeypatch, "x", "7")
    assert prompts.ask_int("Días") == 7


def test_ask_date_retries_until_valid(monkeypatch):
    feed(monkeypatch, "ayer", "2026-03-01")
    assert prompts.ask_date("Fecha") == date(2026, 3, 1)


def test_ask_date_accepts_the_default(monkeypatch):
    feed(monkeypatch, "")
    assert prompts.ask_date("Fecha", date(2026, 1, 2)) == date(2026, 1, 2)


def test_ask_yes_no_accepts_spanish_and_english(monkeypatch):
    feed(monkeypatch, "si")
    assert prompts.ask_yes_no("¿Seguro?") is True
    feed(monkeypatch, "n")
    assert prompts.ask_yes_no("¿Seguro?") is False


def test_ask_ticker_upper_cases(monkeypatch):
    feed(monkeypatch, "podd")
    assert prompts.ask_ticker() == "PODD"


def test_ask_ticker_rejects_empty(monkeypatch):
    feed(monkeypatch, "")
    with pytest.raises(ValidationError):
        prompts.ask_ticker()


def test_read_multiline_stops_on_an_empty_line(monkeypatch, capsys):
    feed(monkeypatch, "linea 1", "linea 2", "")
    assert prompts.read_multiline("pega:") == "linea 1\nlinea 2"
