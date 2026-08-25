"""Local model client, auxiliary tasks and graceful degradation."""
from __future__ import annotations

import io
import json
import urllib.error

import pytest

from fa.local_tasks import (
    catalogue_hint,
    extract_claims,
    portfolio_digest,
    repair_suggestions,
)
from fa.localai import LocalAIClient, LocalAIError
from tests.conftest import make_settings

FENCE = "```"


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
        return False


def reply(content: str) -> FakeResponse:
    return FakeResponse(json.dumps({"choices": [{"message": {"content": content}}]}).encode())


def client(**overrides) -> LocalAIClient:
    base = {"base_url": "http://localhost:1234/v1", "model": "qwen-local", "timeout": 5}
    return LocalAIClient(**{**base, **overrides})


def test_client_is_unavailable_without_a_model():
    assert client(model=None).available() is False
    assert client().available() is True


def test_from_settings_reads_the_configuration(tmp_path):
    settings = make_settings(tmp_path, local_ai_model="qwen-local")
    built = LocalAIClient.from_settings(settings)
    assert built.model == "qwen-local" and built.base_url.endswith("/v1")


def test_chat_returns_the_assistant_message(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: reply("hola"))
    assert client().chat("sys", "user") == "hola"


def test_chat_without_a_model_raises():
    with pytest.raises(LocalAIError):
        client(model=None).chat("sys", "user")


def test_chat_wraps_connection_errors(monkeypatch):
    def boom(*a, **k):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    with pytest.raises(LocalAIError, match="unreachable"):
        client().chat("sys", "user")


def test_chat_rejects_an_unexpected_shape(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: FakeResponse(b'{"error": "nope"}'))
    with pytest.raises(LocalAIError):
        client().chat("sys", "user")


def test_ping_lists_models(monkeypatch):
    payload = json.dumps({"data": [{"id": "qwen-local"}, {"id": "llama-local"}]}).encode()
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: FakeResponse(payload))
    assert client().ping() == ["qwen-local", "llama-local"]


def test_ping_reports_a_dead_server(monkeypatch):
    def boom(*a, **k):
        raise urllib.error.URLError("down")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    with pytest.raises(LocalAIError):
        client().ping()


# --- tasks ------------------------------------------------------------------


def test_extract_claims_parses_the_json(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda *a, **k: reply(f'{FENCE}json\n{{"precio_objetivo": 220}}\n{FENCE}')
    )
    assert extract_claims(client(), "texto de la app") == {"precio_objetivo": 220}


def test_extract_claims_skips_empty_text():
    assert extract_claims(client(), "   ") is None


def test_extract_claims_degrades_when_the_server_is_down(monkeypatch):
    def boom(*a, **k):
        raise urllib.error.URLError("down")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    assert extract_claims(client(), "texto") is None


def test_extract_claims_ignores_non_object_answers(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: reply("no encontré nada"))
    assert extract_claims(client(), "texto") is None


def test_repair_suggestions_returns_a_list(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *a, **k: reply('[{"kind": "price_above", "params": {"price": 200}}]'),
    )
    repaired = repair_suggestions(client(), "prosa con una propuesta", "catálogo")
    assert repaired[0]["kind"] == "price_above"


def test_repair_suggestions_unwraps_an_object(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda *a, **k: reply('{"suggestions": [{"kind": "rsi"}]}')
    )
    assert repair_suggestions(client(), "prosa", "catálogo")[0]["kind"] == "rsi"


def test_repair_suggestions_without_a_local_model():
    assert repair_suggestions(client(model=None), "prosa", "catálogo") is None


def test_digest_returns_prose(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: reply("Tu portfolio está estable."))
    assert portfolio_digest(client(), "hechos") == ("Tu portfolio está estable.", None)


def test_digest_without_a_local_model_says_so():
    prose, error = portfolio_digest(client(model=None), "hechos")
    assert prose is None and "FA_LOCAL_AI_MODEL" in error


def test_digest_reports_a_timeout_instead_of_staying_silent(monkeypatch):
    def boom(*a, **k):
        raise TimeoutError("timed out")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    prose, error = portfolio_digest(client(), "hechos")
    assert prose is None and "unreachable" in error


def test_empty_content_is_reported_as_a_budget_problem(monkeypatch):
    """Reasoning models can burn the whole budget and answer nothing."""
    payload = json.dumps(
        {"choices": [{"finish_reason": "length", "message": {"content": "", "reasoning_content": "x" * 50}}]}
    ).encode()
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: FakeResponse(payload))
    prose, error = portfolio_digest(client(), "hechos")
    assert prose is None and "FA_LOCAL_AI_MAX_TOKENS" in error


def test_truncated_answers_still_come_back(monkeypatch, caplog):
    payload = json.dumps(
        {"choices": [{"finish_reason": "length", "message": {"content": "resumen cortado"}}]}
    ).encode()
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: FakeResponse(payload))
    assert client().chat("s", "u") == "resumen cortado"


def test_catalogue_hint_lists_every_kind():
    from fa.alerts import kinds

    hint = catalogue_hint(list(kinds.CATALOGUE.values()))
    assert "trailing_stop" in hint and "params=" in hint


def test_extract_suggestions_falls_back_to_the_local_model(monkeypatch):
    """A prose-only remote answer is rescued by the local repair pass."""
    from fa.ai import extract_suggestions

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *a, **k: reply('[{"kind": "price_above", "params": {"price": 200}, "rationale": "r"}]'),
    )
    parsed = extract_suggestions("Sugiero poner una alerta arriba de 200.", "PODD", client())
    assert parsed[0].kind == "price_above" and parsed[0].params["price"] == 200.0


def test_extract_suggestions_prefers_the_remote_json_block():
    from fa.ai import extract_suggestions

    text = f'informe\n{FENCE}json\n{{"suggested_alerts": [{{"kind": "rsi", "params": {{}}}}]}}\n{FENCE}'
    parsed = extract_suggestions(text, "PODD", None)
    assert len(parsed) == 1 and parsed[0].kind == "rsi"


def test_extract_suggestions_returns_nothing_without_a_local_fallback():
    from fa.ai import extract_suggestions

    assert extract_suggestions("prosa sin json", "PODD", None) == []
