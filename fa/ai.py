"""Gemini powered strategic analysis, grounded on a live DATA PACK."""
from __future__ import annotations

import logging

from fa.ai_context import FRESHNESS_RULES, DataPack
from fa.alerts import kinds
from fa.alerts.suggestions import parse_suggestions
from fa.config import Settings
from fa.errors import ConfigError
from fa.jsonblock import extract_json, strip_json
from fa.local_tasks import catalogue_hint, repair_suggestions
from fa.localai import LocalAIClient
from fa.models import AIReport

logger = logging.getLogger(__name__)

PROMPT_TEMPLATE = """\
Sos un analista financiero senior de Wall Street y estratega de hedge fund. Analizá {ticker}.

{freshness_rules}

{data_pack}

## TEXTO EXTERNO PEGADO POR EL USUARIO (sin verificar)
\"\"\"{context}\"\"\"

## LO QUE TENÉS QUE DEVOLVER

Primero, un informe en markdown con estas secciones:
1. **Verificación de datos**: qué usaste, con qué fecha, y qué te faltó para concluir.
2. **Diagnóstico de balance**: liquidez, solvencia y calidad de conversión a FCF.
3. **Contraste con el texto externo**: qué afirmaciones se sostienen contra el DATA PACK y cuáles no.
4. **Revisión de la posición del usuario**: dado su costo y sus alertas activas, qué cambiarías.
5. **Recomendación (BUY / NO BUY / HOLD)** separada por horizonte: corto (0-3m), medio (3-12m), largo (1+a).

Después, y como ÚLTIMO elemento de tu respuesta, un bloque ```json con esta forma exacta:

```json
{{
  "suggested_alerts": [
    {{"kind": "<uno de los tipos válidos>", "params": {{...}}, "priority": "high|medium|low",
     "rationale": "<por qué, citando el dato del DATA PACK que lo justifica>"}}
  ],
  "suggested_actions": [
    {{"kind": "<acción concreta, ej: 'tomar ganancias parciales'>", "priority": "medium",
     "rationale": "<por qué y a qué precio/condición>"}}
  ]
}}
```

Tipos de alerta válidos y sus parámetros:
{catalogue}

Reglas del bloque JSON: usá sólo tipos de la lista; los precios objetivo tienen que ser
números concretos derivados del DATA PACK; máximo 6 alertas y 4 acciones; si no proponés
nada, devolvé listas vacías. El JSON va al final, después del informe, sin texto posterior."""


def _catalogue() -> str:
    return catalogue_hint(list(kinds.CATALOGUE.values()))


def build_prompt(ticker: str, data_pack: DataPack, external_context: str) -> str:
    return PROMPT_TEMPLATE.format(
        ticker=ticker,
        freshness_rules=FRESHNESS_RULES,
        data_pack=data_pack.text,
        context=external_context or "(el usuario no pegó texto externo)",
        catalogue=_catalogue(),
    )


def analyze(
    settings: Settings,
    ticker: str,
    data_pack: DataPack,
    external_context: str = "",
    *,
    local_client: LocalAIClient | None = None,
) -> AIReport:
    """Ask the remote model for a grounded report plus structured suggestions."""
    if not settings.gemini_api_key:
        raise ConfigError("GEMINI_API_KEY is not set; cannot run the AI evaluation.")
    try:
        from google import genai  # noqa: PLC0415 - optional dependency imported lazily
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise ConfigError("google-genai is not installed") from exc

    client = genai.Client(api_key=settings.gemini_api_key)
    prompt = build_prompt(ticker, data_pack, external_context)
    try:
        response = client.models.generate_content(model=settings.gemini_model, contents=prompt)
        text = response.text or ""
    except Exception as exc:  # noqa: BLE001 - vendor SDK raises many error types
        logger.exception("gemini call failed")
        return AIReport(
            ticker=ticker,
            text=f"Error connecting to Gemini API: {exc}",
            model=settings.gemini_model,
            provenance=data_pack.provenance,
        )

    return AIReport(
        ticker=ticker,
        # The block is parsed into suggestions below and shown as its own
        # cards, so keeping it in the prose only makes the reader scroll past
        # it. Suggestions are extracted from the original, not the stripped
        # text — stripping first would leave nothing to parse.
        text=strip_json(text) or "(Gemini devolvió una respuesta vacía)",
        suggestions=extract_suggestions(text, ticker, local_client),
        model=settings.gemini_model,
        provenance=data_pack.provenance,
    )


def extract_suggestions(text: str, ticker: str, local_client: LocalAIClient | None = None):
    """Parse the JSON block; fall back to the local model when it is malformed."""
    suggestions = parse_suggestions(extract_json(text), ticker)
    if suggestions or local_client is None or not local_client.available():
        return suggestions
    repaired = repair_suggestions(local_client, text, _catalogue())
    return parse_suggestions(repaired, ticker) if repaired else []
