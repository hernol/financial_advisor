"""Persistence for everything the providers hand us and the app computes.

Until now a run downloaded a year of OHLCV bars, derived twenty indicators from
them and exited without writing any of it down. These tables are the memory:
bars are immutable once a session closes, indicator snapshots are stamped with
the moment they were taken, and portfolio valuations build the equity curve.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Mapping, Sequence

from fa.analytics import TechnicalSnapshot, to_payload
from fa.models import PricePoint
from fa.store.database import Database
from fa.store.serde import dump_json, load_json, parse_date, to_iso


def save_bars(
    conn: Database, ticker: str, bars: Sequence[PricePoint], source: str = ""
) -> int:
    """Upsert daily bars. Returns how many rows were written.

    A bar for a session still in progress will be revised later in the day, so
    the upsert overwrites rather than ignoring: the last write of the day wins
    and the row settles once the market closes.
    """
    if not bars:
        return 0
    stamp = to_iso(datetime.now(timezone.utc))
    rows = [
        (
            ticker.upper(),
            bar.day.isoformat(),
            None,
            bar.high,
            bar.low,
            bar.close,
            bar.volume,
            source,
            stamp,
        )
        for bar in bars
        if bar.day is not None
    ]
    conn.executemany(
        "INSERT INTO daily_bars(ticker, day, open, high, low, close, volume, source, updated_at) "
        "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(ticker, day) DO UPDATE SET "
        "high = COALESCE(excluded.high, daily_bars.high), "
        "low = COALESCE(excluded.low, daily_bars.low), "
        "close = excluded.close, "
        "volume = COALESCE(excluded.volume, daily_bars.volume), "
        "source = excluded.source, updated_at = excluded.updated_at",
        rows,
    )
    conn.commit()
    return len(rows)


def load_bars(
    conn: Database, ticker: str, *, since: date | None = None, limit: int | None = None
) -> list[PricePoint]:
    """Read cached bars back, oldest first."""
    sql = "SELECT * FROM daily_bars WHERE ticker = ?"
    params: list[object] = [ticker.upper()]
    if since is not None:
        sql += " AND day >= ?"
        params.append(since.isoformat())
    sql += " ORDER BY day DESC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    rows = list(conn.execute(sql, params))
    rows.reverse()
    return [
        PricePoint(
            day=parse_date(row["day"]),
            close=row["close"],
            high=row["high"],
            low=row["low"],
            volume=row["volume"],
        )
        for row in rows
    ]


def bar_coverage(conn: Database, ticker: str) -> Mapping[str, Any]:
    """How much history we hold for a ticker, for the dashboard's data page."""
    row = conn.execute(
        "SELECT COUNT(*) AS sessions, MIN(day) AS first_day, MAX(day) AS last_day "
        "FROM daily_bars WHERE ticker = ?",
        (ticker.upper(),),
    ).fetchone()
    return dict(row) if row else {"sessions": 0, "first_day": None, "last_day": None}


def save_indicators(
    conn: Database,
    snapshot: TechnicalSnapshot,
    *,
    run_id: int | None = None,
    taken_at: datetime | None = None,
) -> int:
    """Store one indicator reading.

    The hot columns are duplicated out of the payload so a chart can query
    ``SELECT taken_at, rsi`` without parsing JSON on every row, while the full
    payload keeps whatever the columns do not cover.
    """
    payload = to_payload(snapshot)
    relative = snapshot.relative_strength or {}
    new_id = conn.insert(
        "INSERT INTO indicator_snapshots(ticker, taken_at, price, rsi, sma_fast, sma_slow, "
        "vs_sma_slow_pct, macd_histogram, percent_b, atr, atr_pct, volatility_pct, volume_ratio, "
        "from_high_pct, from_low_pct, max_drawdown_pct, rel_strength_3m, trend, payload, run_id) "
        "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            snapshot.ticker.upper(),
            to_iso(taken_at or datetime.now(timezone.utc)),
            snapshot.price,
            snapshot.rsi,
            snapshot.sma_fast,
            snapshot.sma_slow,
            snapshot.vs_sma_slow_pct,
            snapshot.macd_histogram,
            snapshot.percent_b,
            snapshot.atr,
            snapshot.atr_pct,
            snapshot.volatility_pct,
            snapshot.volume_ratio,
            snapshot.from_high_pct,
            snapshot.from_low_pct,
            snapshot.max_drawdown_pct,
            relative.get("3m"),
            snapshot.trend,
            dump_json(payload),
            run_id,
        ),
    )
    conn.commit()
    return new_id


def indicator_series(
    conn: Database, ticker: str, column: str, *, limit: int = 500
) -> list[tuple[str, float | None]]:
    """(timestamp, value) pairs for charting one indicator over time."""
    allowed = {
        "price", "rsi", "sma_fast", "sma_slow", "vs_sma_slow_pct", "macd_histogram",
        "percent_b", "atr", "atr_pct", "volatility_pct", "volume_ratio",
        "from_high_pct", "from_low_pct", "max_drawdown_pct", "rel_strength_3m",
    }
    if column not in allowed:
        raise ValueError(f"Unknown indicator column '{column}'")
    rows = list(
        conn.execute(
            f"SELECT taken_at, {column} AS value FROM indicator_snapshots "
            "WHERE ticker = ? ORDER BY taken_at DESC LIMIT ?",
            (ticker.upper(), limit),
        )
    )
    rows.reverse()
    return [(row["taken_at"], row["value"]) for row in rows]


def latest_indicators(conn: Database, ticker: str) -> Mapping[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM indicator_snapshots WHERE ticker = ? ORDER BY taken_at DESC LIMIT 1",
        (ticker.upper(),),
    ).fetchone()
    if row is None:
        return None
    item = dict(row)
    item["payload"] = load_json(row["payload"], {})
    return item


def save_valuation(
    conn: Database,
    *,
    cost_basis: float,
    market_value: float,
    pnl_abs: float,
    pnl_pct: float,
    positions: int,
    holdings: Sequence[Mapping[str, Any]] = (),
    currency: str = "USD",
    taken_at: datetime | None = None,
) -> int:
    """One point on the equity curve."""
    moment = taken_at or datetime.now(timezone.utc)
    new_id = conn.insert(
        "INSERT INTO portfolio_valuations(taken_at, day, cost_basis, market_value, pnl_abs, "
        "pnl_pct, positions, currency, holdings) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            to_iso(moment),
            moment.date().isoformat(),
            cost_basis,
            market_value,
            pnl_abs,
            pnl_pct,
            positions,
            currency,
            dump_json(list(holdings)),
        ),
    )
    conn.commit()
    return new_id


def equity_curve(conn: Database, *, days: int = 365) -> list[Mapping[str, Any]]:
    """Daily closing valuation, one row per day (the last of each day wins).

    Joining against the per-day maximum rather than selecting bare columns
    alongside ``MAX()``: SQLite would allow the shorter form and pick the right
    row, but Postgres rejects it outright, and the join says what is meant on
    both.
    """
    rows = conn.execute(
        "SELECT v.day, v.taken_at, v.market_value, v.cost_basis, v.pnl_abs, v.pnl_pct "
        "FROM portfolio_valuations v "
        "JOIN (SELECT day AS d, MAX(taken_at) AS latest FROM portfolio_valuations GROUP BY day) m "
        "ON m.d = v.day AND m.latest = v.taken_at "
        "ORDER BY v.day DESC LIMIT ?",
        (days,),
    )
    out = [dict(row) for row in rows]
    out.reverse()
    return out
