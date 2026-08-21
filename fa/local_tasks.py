"""Auxiliary tasks delegated to the local model. Every one degrades to ``None``."""
from __future__ import annotations

import logging
from typing import Any, Mapping, Sequence

from fa.jsonblock import extract_json
from fa.localai import LocalAIClient, LocalAIError

logger = logging.getLogger(__name__)

EXTRACTOR_SYSTEM = """\
Sos un extractor de datos. Recibís texto de una app de inversión y devolvés SOLO un objeto JSON.
Claves posibles: fuente, fecha, rating, precio_objetivo, precio_mencionado, horizonte,
tesis_alcista, tesis_bajista, riesgos, numeros_citados (lista de strings), otros.
No inventes: si un dato no está en el texto, omití la clave. No agregues comentarios ni markdown."""

REPAIR_SYSTEM = """\
Convertís propuestas de alertas escritas en prosa a JSON válido. Devolvés SOLO un array JSON.
Cada elemento: {"category": "alert"|"action", "kind": "<tipo>", "params": {...},
"rationale": "<por qué>", "priority": "low"|"medium"|"high"}.
Tipos de alerta válidos y sus parámetros:
%s
Lo que no encaje en un tipo válido va como category "action" con kind describiendo la acción.
No inventes números que no estén en el texto."""

DIGEST_SYSTEM = """\
Sos un asistente que redacta un resumen diario de portfolio, en español rioplatense, breve
(máximo 12 líneas). Usá SOLO los datos que te paso: no agregues precios, noticias ni contexto
de mercado que no estén en el texto. Estructura: estado general, posiciones a mirar,
alertas cerca de dispararse, eventos próximos. Sin markdown pesado, sin disclaimers."""


def extract_claims(client: LocalAIClient, text: str) -> Mapping[str, Any] | None:
    """Turn the pasted app text into structured claims for the grounded prompt."""
    if not text.strip() or not client.available():
        return None
    try:
        raw = client.chat(EXTRACTOR_SYSTEM, text, max_tokens=800)
    except LocalAIError as exc:
        logger.info("local extraction unavailable: %s", exc)
        return None
    payload = extract_json(raw)
    return payload if isinstance(payload, dict) else None


def repair_suggestions(client: LocalAIClient, raw_text: str, catalogue_hint: str) -> list[Any] | None:
    """Second chance at structured suggestions when the remote model wrote prose."""
    if not raw_text.strip() or not client.available():
        return None
    try:
        raw = client.chat(REPAIR_SYSTEM % catalogue_hint, raw_text, max_tokens=1200)
    except LocalAIError as exc:
        logger.info("local suggestion repair unavailable: %s", exc)
        return None
    payload = extract_json(raw)
    if isinstance(payload, dict):
        payload = payload.get("suggestions") or payload.get("suggested_alerts")
    return payload if isinstance(payload, list) else None


def portfolio_digest(client: LocalAIClient, facts: str) -> tuple[str | None, str | None]:
    """Digest of the portfolio built only from ``facts``.

    Returns ``(prose, error)``: exactly one of them is set, so the caller can
    tell "no local model configured" apart from "the model timed out".
    """
    if not client.available():
        return None, "no hay modelo local configurado (FA_LOCAL_AI_MODEL)"
    try:
        return client.chat(DIGEST_SYSTEM, facts), None
    except LocalAIError as exc:
        logger.info("local digest unavailable: %s", exc)
        return None, str(exc)


def catalogue_hint(kinds: Sequence[Any]) -> str:
    """Compact description of the alert catalogue, embedded in local prompts."""
    return "\n".join(f"- {k.key}: {k.description} params={dict(k.defaults)}" for k in kinds)
