"""Parsing and validation of the alert suggestions returned by the AI."""
from __future__ import annotations

import logging
from typing import Any, Mapping, Sequence

from fa.alerts import kinds
from fa.errors import ValidationError
from fa.models import Suggestion

logger = logging.getLogger(__name__)

PRIORITIES = {"low", "medium", "high"}
MAX_SUGGESTIONS = 12


def parse_suggestions(payload: Any, ticker: str) -> list[Suggestion]:
    """Turn a raw JSON payload into validated suggestions.

    Unknown alert kinds are not discarded: they are kept as free-form actions so
    the user still sees what the model proposed, but they can never be turned
    into an alert by mistake.
    """
    items = _as_items(payload)
    suggestions: list[Suggestion] = []
    for item in items[:MAX_SUGGESTIONS]:
        if not isinstance(item, Mapping):
            continue
        suggestion = _one(item, ticker)
        if suggestion is not None:
            suggestions.append(suggestion)
    return suggestions


def _as_items(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, Mapping):
        for key in ("suggested_alerts", "suggestions", "alerts", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                extra = payload.get("suggested_actions")
                return value + (extra if isinstance(extra, list) else [])
    return []


def _one(item: Mapping[str, Any], ticker: str) -> Suggestion | None:
    kind = str(item.get("kind") or item.get("type") or "").strip()
    category = str(item.get("category") or ("alert" if kind in kinds.CATALOGUE else "action")).strip()
    rationale = str(item.get("rationale") or item.get("why") or item.get("reason") or "").strip()
    priority = str(item.get("priority") or "medium").strip().lower()
    if priority not in PRIORITIES:
        priority = "medium"
    params = item.get("params") if isinstance(item.get("params"), Mapping) else {}

    if category == "alert" and kind in kinds.CATALOGUE:
        try:
            params = kinds.normalize_params(kind, params)
        except ValidationError as exc:
            logger.info("discarding invalid suggestion %s: %s", kind, exc)
            return Suggestion(
                ticker=ticker.upper(),
                category="action",
                kind=kind,
                params=dict(params),
                rationale=f"{rationale} (parámetros inválidos: {exc})".strip(),
                priority=priority,
            )
    else:
        category = "action"
        if not kind:
            kind = str(item.get("action") or item.get("title") or "").strip()
        if not kind and not rationale:
            return None
    return Suggestion(
        ticker=str(item.get("ticker") or ticker).upper(),
        category=category,
        kind=kind,
        params=dict(params),
        rationale=rationale,
        priority=priority,
    )


def actionable(suggestions: Sequence[Suggestion]) -> list[Suggestion]:
    """The subset that can be turned into a real alert."""
    return [s for s in suggestions if s.category == "alert" and s.kind in kinds.CATALOGUE]
