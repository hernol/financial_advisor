"""Regenerate fa/data/cedears.json from the Comafi program table.

Run by hand, never by the application. It validates every underlying against
the live provider and records the currency it answered with, so the table ships
knowing which entries actually work instead of discovering it in production
while valuing somebody's portfolio.

    python scripts/update_cedears.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fa.cedears import TABLE_PATH, load_table  # noqa: E402
from scripts.comafi import parse, split_ratio, translate  # noqa: E402

SOURCE = "https://www.comafi.com.ar/custodiaglobal/2483-Programas-Cedears.note.aspx"


def fetch(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read().decode("utf-8", errors="replace")


# What a provider check concluded. The distinction matters: only the first two
# are facts about the instrument and safe to bake into a committed file.
USD_CONFIRMED = "usd"
FOREIGN = "foreign"
NO_ANSWER = "no_answer"


def check(symbol: str) -> tuple[str, str]:
    """Ask the provider what this symbol quotes in: ``(verdict, detail)``.

    Deliberately does NOT decide whether the CEDEAR is usable. This asks
    yfinance alone, while the application asks a three-provider chain, so a
    shrug here says nothing about whether the price can be had - Alpha Vantage
    or Finnhub may well have it. Baking "unsupported" into a versioned file on
    one provider's silence would refuse a perfectly good holding for good, and
    the owner would have no way to see why.

    Measured on 2026-08-31: BK, MMC, WBA, ERJ and X - all live, liquid NYSE
    tickers - returned no data through this path in the same run where AAPL
    answered normally. A currency that is not USD is a different matter: that
    is a property of the listing, and it is recorded as such.
    """
    import yfinance as yf

    try:
        info = yf.Ticker(symbol).fast_info
        price, currency = info.get("lastPrice"), info.get("currency")
    except Exception as exc:  # noqa: BLE001 - one bad symbol must not stop the run
        return NO_ANSWER, f"el proveedor no contestó ({type(exc).__name__})"
    if not price or not currency:
        return NO_ANSWER, "el proveedor no tiene datos hoy"
    if str(currency).upper() != "USD":
        return FOREIGN, f"cotiza en {str(currency).upper()}, no en USD"
    return USD_CONFIRMED, ""


def build(*, verbose: bool = True) -> list[dict]:
    rows = parse(fetch(SOURCE))
    entries: list[dict] = []
    for index, row in enumerate(rows, start=1):
        cedears, shares = split_ratio(row["ratio_text"])
        symbol, reason = translate(row["under_raw"])
        verdict, detail = (NO_ANSWER, "sin símbolo que consultar")
        if symbol is not None:
            verdict, detail = check(symbol)
        # Unsupported only where the reason is structural: the symbol could not
        # be translated at all, or the listing was seen quoting another
        # currency. A provider that simply did not answer leaves the entry
        # usable and unverified, and the runtime refuses honestly if the whole
        # chain really cannot price it.
        supported = symbol is not None and verdict != FOREIGN
        if verdict == FOREIGN:
            reason = detail
        if verbose:
            mark = {USD_CONFIRMED: "ok ", FOREIGN: "NO ", NO_ANSWER: "?  "}[verdict]
            print(f"  {index:3}/{len(rows)} {mark} {row['local']:<8} "
                  f"{symbol or row['under_raw']:<10} {reason or detail}", file=sys.stderr)
        entries.append({
            "local": row["local"],
            "yahoo": f"{row['local']}.BA",
            "underlying": symbol or row["under_raw"],
            "cedears": cedears,
            "shares": shares,
            "name": row["name"],
            "isin_subyacente": row["isin_subyacente"],
            "supported": supported,
            "reason": reason,
            # Advisory only; the application does not read it. It records
            # whether the underlying was seen quoting in dollars when the table
            # was generated, so a later gap can be told from a never-worked.
            "verified": verdict == USD_CONFIRMED,
            "checked": detail,
        })
    return sorted(entries, key=lambda e: e["local"])


def diff(old: dict, new: list[dict]) -> list[str]:
    """What changed, so a ratio swap is seen before it is committed."""
    lines: list[str] = []
    fresh = {e["yahoo"].upper(): e for e in new}
    for key in sorted(set(old) | set(fresh)):
        before, after = old.get(key), fresh.get(key)
        if before is None:
            lines.append(f"+ {key} ({after['cedears']}:{after['shares']})")
        elif after is None:
            lines.append(f"- {key}")
        elif (before.cedears, before.shares) != (after["cedears"], after["shares"]):
            lines.append(
                f"~ {key} ratio {before.cedears}:{before.shares} -> "
                f"{after['cedears']}:{after['shares']}"
            )
        elif before.supported != after["supported"]:
            state = "soportado" if after["supported"] else f"no soportado ({after['reason']})"
            lines.append(f"~ {key} ahora {state}")
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="no escribe la tabla")
    parser.add_argument("--quiet", action="store_true", help="sin progreso por ticker")
    args = parser.parse_args()

    entries = build(verbose=not args.quiet)
    old = load_table(TABLE_PATH) if TABLE_PATH.exists() else {}
    changes = diff(old, entries)

    supported = sum(1 for e in entries if e["supported"])
    verified = sum(1 for e in entries if e["verified"])
    print(f"\n{len(entries)} programas, {supported} soportados "
          f"({verified} con precio en USD confirmado, {supported - verified} sin "
          f"verificar hoy), {len(entries) - supported} no soportados")
    for line in changes:
        print(f"  {line}")
    if not changes:
        print("  sin cambios")

    if args.dry_run:
        print("(--dry-run: no se escribió nada)")
        return 0
    TABLE_PATH.parent.mkdir(parents=True, exist_ok=True)
    TABLE_PATH.write_text(
        json.dumps(entries, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    print(f"escrita {TABLE_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
