"""Exchange rates for statements reported in a currency other than the quote's.

TSMC reports in TWD and its ADR trades in USD; the same split runs through the
Japanese, Korean, Brazilian and European ADRs. Dividing a dollar price by a
TWD earnings line gave a P/E of 0.93 against a real ~28, so those ratios were
left blank. Converting with the rate of the period's own close unlocks them
without inventing anything: the rate is a real market price, fetched through
the same provider chain as everything else.

What this is not: today's rate applied to an old balance sheet. That was the
alternative the codebase rejected, and rightly - the rate moved 15% over the
five years measured, so it would put a made-up precision on every old figure.
"""
from __future__ import annotations

import re
from typing import Any, Mapping

# Yahoo spells a rate "units of CUR per one USD" as "CUR=X": TWD=X came back at
# 31.69 with its own currency field reading TWD, JPY=X at 160.15, EUR=X at
# 0.8624 - which is exactly 1 / 1.1596, the EURUSD=X quote. Uniform, and
# self-checking, because the provider states the currency it answered in.
SUFFIX = "=X"

_CODE = re.compile(r"^[A-Za-z]{3}$")

# The statement lines that carry money. Counts and labels are deliberately
# absent: shares_outstanding is a number of shares and converting it would be
# nonsense that silently wrecks every per-share figure.
MONETARY_KEYS = (
    "revenue",
    "gross_profit",
    "operating_income",
    "net_income",
    "total_assets",
    "total_liabilities",
    "total_debt",
    "cash",
    "operating_cash_flow",
    "capex",
    "interest_expense",
)


def pair_symbol(currency: str) -> str:
    """The provider symbol for how many ``currency`` one dollar buys.

    Refuses anything that is not a three-letter code. A guessed symbol would
    not fail loudly, it would fetch some unrelated instrument and convert every
    figure by its price.
    """
    code = (currency or "").strip()
    if not _CODE.match(code):
        raise ValueError(f"no es un código de moneda de tres letras: {currency!r}")
    return f"{code.upper()}{SUFFIX}"


def to_usd(amount: float, rate: float) -> float:
    """Divide, because the rate is units of the currency per one dollar.

    A one-line function whose whole purpose is to name the direction. Inverting
    it turns 3,170 TWD into 100,489 dollars instead of 100, and the result
    still looks like a number.
    """
    return amount / rate


def row_to_usd(row: Mapping[str, Any], rate: float) -> dict[str, Any]:
    """A copy of a statement row with its money lines in dollars."""
    converted = dict(row)
    for key in MONETARY_KEYS:
        value = converted.get(key)
        if value is not None:
            converted[key] = to_usd(value, rate)
    return converted
