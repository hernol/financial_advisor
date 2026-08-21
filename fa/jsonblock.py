"""Extraction of JSON payloads embedded in model prose."""
from __future__ import annotations

import json
import re
from typing import Any

FENCED = re.compile(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", re.DOTALL)


def extract_json(text: str) -> Any | None:
    """Return the first JSON object/array found in ``text``, or ``None``.

    Models wrap payloads in code fences, prepend prose, or emit bare JSON. All
    three shapes are handled; anything unparseable yields ``None`` so the caller
    can fall back instead of crashing.
    """
    if not text:
        return None
    for candidate in _candidates(text):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return None


def _candidates(text: str) -> list[str]:
    found = [match.group(1) for match in FENCED.finditer(text)]
    stripped = text.strip()
    if stripped.startswith(("{", "[")):
        found.append(stripped)
    found.extend(_balanced(text, "{", "}"))
    found.extend(_balanced(text, "[", "]"))
    return found


def _balanced(text: str, opener: str, closer: str) -> list[str]:
    """Slices of ``text`` that start at ``opener`` and close in balance."""
    slices: list[str] = []
    depth = 0
    start = -1
    for index, char in enumerate(text):
        if char == opener:
            if depth == 0:
                start = index
            depth += 1
        elif char == closer and depth:
            depth -= 1
            if depth == 0 and start >= 0:
                slices.append(text[start : index + 1])
    return slices
