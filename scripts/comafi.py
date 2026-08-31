"""Parse the CEDEAR program table Comafi publishes, and translate its symbols.

Kept apart from the script that fetches it so this half is pure and testable:
the network belongs in update_cedears.py, the rules belong here.
"""
from __future__ import annotations

import html
import re

# Comafi writes the underlying in Bloomberg notation. Every rule below was
# checked against the live provider on 2026-08-31; anything not listed is left
# unsupported rather than guessed, because inventing a foreign listing's Yahoo
# suffix is inventing a market datum.
_EXCHANGE_SUFFIX = {
    "US": "",         # "VIST US" -> "VIST", 71.46 USD
    "LI": ".IL",      # "SMSN LI" -> "SMSN.IL", a London GDR quoted in USD
}
_REFUSED_SUFFIX = {
    "GR": (
        "cotiza en EUR (listado de Frankfurt), y convertirlo pediría un segundo "
        "salto de FX"
    ),
}


def split_ratio(text: str) -> tuple[int, int]:
    """``"20:1"`` into ``(20, 1)``. Both sides stay whole numbers.

    Anything else raises. The 312 programs are all ``a:1`` or ``1:b`` with
    integers; if a new shape appears it must be looked at, not rounded.
    """
    match = re.fullmatch(r"\s*(\d+)\s*:\s*(\d+)\s*", text or "")
    if not match:
        raise ValueError(f"ratio ilegible: {text!r}")
    cedears, shares = int(match.group(1)), int(match.group(2))
    if cedears <= 0 or shares <= 0:
        raise ValueError(f"ratio con un lado en cero: {text!r}")
    return cedears, shares


def translate(symbol: str) -> tuple[str | None, str]:
    """Comafi's symbol as Yahoo spells it, or ``(None, reason)``."""
    raw = (symbol or "").strip()
    if not raw:
        return None, "sin ticker de subyacente"
    if "/" in raw:
        # Share classes: Comafi writes BRK/B, Yahoo writes BRK-B.
        return raw.replace("/", "-"), ""
    # The same class, written the other way: AKO.B is Yahoo's AKO-B, quoted at
    # 29 USD. Deliberately narrow - one trailing letter - so it cannot swallow
    # an exchange suffix like the .IL this function produces just below.
    if re.fullmatch(r"[A-Za-z]+\.[A-Za-z]", raw):
        return raw.replace(".", "-"), ""
    if " " in raw:
        base, _, code = raw.rpartition(" ")
        if code in _REFUSED_SUFFIX:
            return None, _REFUSED_SUFFIX[code]
        if code in _EXCHANGE_SUFFIX:
            return base.strip() + _EXCHANGE_SUFFIX[code], ""
        return None, f"código de bolsa desconocido: {code!r}"
    return raw, ""


def _cells(row: str) -> list[str]:
    return [
        re.sub(r"\s+", " ", html.unescape(re.sub(r"<.*?>", " ", cell, flags=re.S))).strip()
        for cell in re.findall(r"<t[dh].*?</t[dh]>", row, re.S | re.I)
    ]


def parse(page: str) -> list[dict]:
    """Every program row in the page, unvalidated and untranslated."""
    tables = re.findall(r"<table.*?</table>", page, re.S | re.I)
    table = next((t for t in tables if re.search(r"ratio", t, re.I)), None)
    if table is None:
        raise ValueError("no se encontró la tabla de programas de CEDEARs")
    rows: list[dict] = []
    for raw_row in re.findall(r"<tr.*?</tr>", table, re.S | re.I):
        cells = _cells(raw_row)
        # A program row is recognised by a ratio in the third cell, which skips
        # the header and anything else the page carries.
        if len(cells) < 8 or not re.fullmatch(r"\s*\d+\s*:\s*\d+\s*", cells[2]):
            continue
        rows.append({
            "name": cells[0],
            "ratio_text": cells[2],
            "local": cells[5],
            "under_raw": cells[6],
            "isin_subyacente": cells[7].replace("ISIN", "").strip(),
        })
    if not rows:
        raise ValueError("la tabla no tenía ninguna fila de programa")
    return rows
