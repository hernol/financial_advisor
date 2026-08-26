"""The equity curve, derived rather than only recorded.

``portfolio_valuations`` holds what was observed: one point per scheduled run,
which means the curve starts the day the feature was installed and shows a
single dot. But the past is not missing — it is implied. The ledger says what
was held on any date and ``daily_bars`` says what it was worth, so the whole
history can be rebuilt from the first purchase onwards.

Deriving it rather than backfilling rows also keeps it honest: correct a trade
and every point that depended on it moves, instead of leaving the stored curve
telling yesterday's version of the story.

Two readings, because one of them answers a question the other cannot:

* **holdings** — what the shares are worth. Selling drops it, which is correct
  and also why it is not the whole story: the money did not evaporate.
* **total** — holdings plus cash: what the account is worth. Buying moves money
  from cash into shares and leaves it flat; selling moves it back and leaves it
  flat. It only moves when the market does, so it is continuous across a sale.
* **result** — the same figure minus what the holder put in, which is the part
  the market produced. A deposit lifts ``total`` and leaves ``result`` alone.

Cash is the running sum of what every entry did to it. With no deposit on file
it starts at zero, ``contributed`` is zero and the two lines coincide; record
the deposits and cash becomes a real balance while the result stays the result.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Mapping, Sequence

from fa import ledger, models
from fa.models import Transaction
from fa.store import history as history_store
from fa.store import transactions as transactions_store
from fa.store.database import Database
from fa.store.schema import LOCAL_ACCOUNT_ID


def _closes_by_day(conn: Database, ticker: str) -> Mapping[date, float]:
    return {bar.day: bar.close for bar in history_store.load_bars(conn, ticker)}


def _price_on(closes: Mapping[date, float], ordered: Sequence[date], day: date) -> float | None:
    """The last close on or before ``day``.

    Markets are shut on weekends and holidays; a portfolio still has a value on
    a Sunday, and it is Friday's.
    """
    best: float | None = None
    for session in ordered:
        if session > day:
            break
        best = closes[session]
    return best


def curve(
    conn: Database,
    *,
    days: int = 365,
    account_id: int = LOCAL_ACCOUNT_ID,
    today: date | None = None,
) -> list[dict[str, Any]]:
    """Daily value and cost of the portfolio, from the ledger and stored bars."""
    entries = transactions_store.list_transactions(conn, account_id=account_id)
    if not entries:
        return []

    end = today or date.today()
    # Money going in starts the account, even before the first purchase.
    first_trade = min(e.trade_date for e in entries)
    start = max(first_trade, end - timedelta(days=days - 1))
    if start > end:
        return []

    # Cash movements name no ticker; they reach the curve through the cash sum.
    tickers = sorted({e.ticker for e in entries if e.ticker})
    by_ticker: dict[str, list[Transaction]] = {
        ticker: [e for e in entries if e.ticker == ticker] for ticker in tickers
    }
    closes = {ticker: _closes_by_day(conn, ticker) for ticker in tickers}
    ordered = {ticker: sorted(closes[ticker]) for ticker in tickers}

    points: list[dict[str, Any]] = []
    day = start
    while day <= end:
        value = 0.0
        cost = 0.0
        priced = 0
        open_holdings = 0
        # What every entry up to this day did to the cash side. Sums across
        # tickers, so it is computed here rather than per holding.
        so_far_all = [e for e in entries if e.trade_date <= day]
        cash = sum(e.cash_flow for e in so_far_all)
        put_in = models.contributed(so_far_all)
        for ticker in tickers:
            so_far = [e for e in by_ticker[ticker] if e.trade_date <= day]
            if not so_far:
                continue
            # Replayed through the same function the live holdings use, rather
            # than a second implementation of the same arithmetic that could
            # drift from it.
            holding = ledger.replay(ticker, so_far)
            if not holding.is_open:
                continue
            open_holdings += 1
            price = _price_on(closes[ticker], ordered[ticker], day)
            if price is None:
                continue
            value += holding.quantity * price
            cost += holding.cost_basis
            priced += 1
        # A day with nothing held is still a day: after selling out, the
        # holdings are zero and the cash is the whole story. Stopping the curve
        # there would hide exactly the result the sale produced.
        if priced or open_holdings == 0:
            points.append(
                {
                    "day": day.isoformat(),
                    "market_value": round(value, 4),
                    "cost_basis": round(cost, 4),
                    "cash": round(cash, 4),
                    "contributed": round(put_in, 4),
                    "total": round(value + cash, 4),
                    "result": round(value + cash - put_in, 4),
                    "pnl_abs": round(value - cost, 4),
                    "pnl_pct": round((value - cost) / cost * 100.0, 4) if cost else None,
                    "holdings": priced,
                    "unpriced": open_holdings - priced,
                }
            )
        day += timedelta(days=1)
    return points
