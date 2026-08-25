"""The metric tables, stored instead of recomputed.

``build_tables`` produced these on every run and the process dropped them on
exit, so showing them anywhere meant downloading a company's statements again.
They belong to the company rather than to an account, so they sit with the price
bars on the shared side.

Rows are stored as they come out of the metric builder: a list of dicts, one per
period. NaN does not survive JSON, so it is normalised to null on the way in —
which is also the honest representation, since a missing line in a filing is not
a zero.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

import pandas as pd

from fa.store.database import Database
from fa.store.serde import dump_json, load_json, to_iso

ANNUAL = "annual"
QUARTERLY = "quarterly"

# Statements move once a quarter, so anything younger than this is current
# enough and re-downloading it would only spend a provider call.
FRESH_FOR = timedelta(days=7)


def _clean(value: Any) -> Any:
    """NaN and numpy scalars into something JSON can carry."""
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, (bool,)):
        return bool(value)
    if hasattr(value, "item"):  # numpy scalar
        value = value.item()
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def frame_to_rows(frame: pd.DataFrame) -> list[dict[str, Any]]:
    return [{k: _clean(v) for k, v in row.items()} for row in frame.to_dict(orient="records")]


def save(
    conn: Database,
    ticker: str,
    period_kind: str,
    frame: pd.DataFrame,
    *,
    source: str = "",
    shares: float | None = None,
    price: float | None = None,
    fetched_at: datetime | None = None,
) -> int:
    stamp = to_iso(fetched_at or datetime.now(timezone.utc))
    conn.execute(
        "INSERT INTO fundamental_snapshots(ticker, period_kind, rows, source, shares, price, "
        "fetched_at) VALUES(?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(ticker, period_kind) DO UPDATE SET rows = excluded.rows, "
        "source = excluded.source, shares = excluded.shares, price = excluded.price, "
        "fetched_at = excluded.fetched_at",
        (ticker.upper(), period_kind, dump_json(frame_to_rows(frame)), source, shares, price, stamp),
    )
    conn.commit()
    return len(frame.index)


def load(conn: Database, ticker: str, period_kind: str) -> Mapping[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM fundamental_snapshots WHERE ticker = ? AND period_kind = ?",
        (ticker.upper(), period_kind),
    ).fetchone()
    if row is None:
        return None
    item = dict(row)
    item["rows"] = load_json(row["rows"], [])
    return item


def load_all(conn: Database, ticker: str) -> dict[str, Any]:
    return {kind: load(conn, ticker, kind) for kind in (ANNUAL, QUARTERLY)}


def is_stale(snapshot: Mapping[str, Any] | None, *, now: datetime | None = None) -> bool:
    """True when there is nothing stored, or what is stored has aged out."""
    if snapshot is None or not snapshot.get("rows"):
        return True
    try:
        fetched = datetime.fromisoformat(str(snapshot["fetched_at"]))
    except (TypeError, ValueError):
        return True
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=timezone.utc)
    return (now or datetime.now(timezone.utc)) - fetched > FRESH_FOR


def tickers(conn: Database) -> Sequence[str]:
    rows = conn.execute("SELECT DISTINCT ticker FROM fundamental_snapshots ORDER BY ticker")
    return [row["ticker"] for row in rows]
