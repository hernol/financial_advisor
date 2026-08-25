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


def strip_json(text: str) -> str:
    """The prose without the machine-readable block the model appended.

    The suggestions are parsed out of that block and shown as their own cards,
    so leaving it in the report means the reader scrolls past a wall of JSON to
    find nothing new. Only fenced blocks are removed: a bare brace could be part
    of a sentence, and losing prose would be worse than keeping a stray brace.

    Any heading that introduced the block goes with it, otherwise the report
    ends on a promise it no longer keeps.
    """
    if not text:
        return text
    cleaned = FENCED.sub("", text)
    cleaned = _drop_orphan_heading(cleaned)
    # Collapse the run of blank lines the removal leaves behind.
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    # A rule that separated the prose from the block now separates it from
    # nothing, and a report should not end on a divider.
    return re.sub(r"(?:\n\s*(?:-{3,}|\*{3,}|_{3,})\s*)+$", "", cleaned).strip()


# A heading whose whole purpose was to announce the block: "## Sugerencias
# (JSON)", "### Bloque JSON", "**Alertas sugeridas (JSON)**".
_ORPHAN_HEADING = re.compile(
    r"^[ \t]*(?:#{1,6}[ \t]*|\*\*)?[^\n]{0,80}\bJSON\b[^\n]{0,80}?(?:\*\*)?[ \t]*$\n?",
    re.IGNORECASE | re.MULTILINE,
)


def _drop_orphan_heading(text: str) -> str:
    """Remove a line that only exists to introduce the block, now that it is
    gone. A line with other content on it is left alone."""
    def keep_or_drop(match: re.Match[str]) -> str:
        line = match.group(0).strip()
        # A sentence that merely mentions JSON is prose; a heading is short and
        # marked up as one.
        looks_like_heading = line.startswith("#") or (
            line.startswith("**") and line.endswith("**")
        )
        return "" if looks_like_heading else match.group(0)

    return _ORPHAN_HEADING.sub(keep_or_drop, text)
