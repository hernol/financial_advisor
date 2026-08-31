"""CEDEARs: Argentine receipts over a foreign share.

A CEDEAR is contractually a fixed fraction of the underlying share, which is
the whole reason this module exists: it turns a peso instrument into a dollar
one without ever inventing an exchange rate. Valuing a holding needs the
underlying's USD price and a published constant, and nothing else.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

# Inside the package, not in data/: that directory is gitignored (it holds the
# SQLite database) and is mounted as a Docker volume, so a table there would
# neither be committed nor survive into production. The Dockerfile copies fa/.
TABLE_PATH = Path(__file__).resolve().parent / "data" / "cedears.json"

SUFFIX = ".BA"


@dataclass(frozen=True)
class Cedear:
    """One CEDEAR program, as the depositary publishes it."""

    local: str          # BYMA symbol, "AAPL"
    yahoo: str          # what the user types, "AAPL.BA"
    underlying: str     # the share it represents, "AAPL"
    cedears: int        # left side of the ratio
    shares: int         # right side of the ratio
    name: str = ""
    supported: bool = True
    reason: str = ""

    @property
    def shares_per_cedear(self) -> float:
        """How many underlying shares one CEDEAR is worth.

        Both sides are kept because fourteen programs are inverted: SID is 1:8,
        one CEDEAR is eight shares. Collapsing this to a single float would have
        to choose a direction and be wrong by the ratio squared for those.
        """
        return self.shares / self.cedears


def load_table(path: Path) -> dict[str, Cedear]:
    """Read the table, keyed by the ticker a user would type."""
    entries = json.loads(path.read_text(encoding="utf-8"))
    table: dict[str, Cedear] = {}
    for entry in entries:
        cedear = Cedear(
            local=entry["local"],
            yahoo=entry["yahoo"],
            underlying=entry["underlying"],
            cedears=int(entry["cedears"]),
            shares=int(entry["shares"]),
            name=entry.get("name", ""),
            supported=bool(entry.get("supported", True)),
            reason=entry.get("reason", ""),
        )
        table[cedear.yahoo.upper()] = cedear
    return table


_TABLE: dict[str, Cedear] | None = None


def table() -> dict[str, Cedear]:
    global _TABLE
    if _TABLE is None:
        _TABLE = load_table(TABLE_PATH)
    return _TABLE


def resolve(ticker: str) -> Cedear | None:
    """The CEDEAR a ticker names, or ``None`` when it names an ordinary share.

    Returning ``None`` for anything without the suffix is what keeps every
    existing code path exactly as it was. Not every ``.BA`` symbol is a CEDEAR
    either - Argentine shares live there too - so an unknown one is also
    ``None`` rather than a guess.
    """
    symbol = (ticker or "").strip().upper()
    if not symbol.endswith(SUFFIX):
        return None
    return table().get(symbol)
