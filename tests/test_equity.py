"""Rebuilding the equity curve from what is already on file.

The valuations table records what was observed, which begins the day the
feature was installed. The ledger says what was held on any date and the bars
say what it was worth, so the curve does not have to start there.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from fa import equity, models
from fa.models import Position, PricePoint, Transaction
from fa.store import history as history_store
from fa.store import positions as positions_store
from fa.store import transactions as transactions_store

END = date(2026, 8, 20)


def bars(conn, ticker, closes, end=END):
    history_store.save_bars(
        conn, ticker,
        [
            PricePoint(day=end - timedelta(days=len(closes) - 1 - i), close=c)
            for i, c in enumerate(closes)
        ],
        "test",
    )


def buy(conn, ticker="PODD", quantity=10.0, price=100.0, day=date(2026, 8, 18)):
    return positions_store.add_position(
        conn, Position(ticker=ticker, quantity=quantity, buy_price=price, buy_date=day)
    )


# --- the shape --------------------------------------------------------------


def test_an_empty_ledger_has_no_curve(conn):
    assert equity.curve(conn, today=END) == []


def test_one_point_per_calendar_day(conn):
    buy(conn, day=date(2026, 8, 18))
    bars(conn, "PODD", (100.0, 110.0, 130.0))
    points = equity.curve(conn, today=END)
    assert [p["day"] for p in points] == ["2026-08-18", "2026-08-19", "2026-08-20"]


def test_it_starts_at_the_first_purchase_not_at_the_first_bar(conn):
    """Before you owned it, the portfolio had no value to plot."""
    bars(conn, "PODD", tuple(float(100 + i) for i in range(30)))
    buy(conn, day=date(2026, 8, 19))
    points = equity.curve(conn, today=END)
    assert points[0]["day"] == "2026-08-19"


def test_the_window_is_respected(conn):
    buy(conn, day=date(2026, 7, 1))
    bars(conn, "PODD", tuple(float(100 + i) for i in range(60)))
    points = equity.curve(conn, days=5, today=END)
    assert len(points) == 5
    assert points[-1]["day"] == "2026-08-20"


# --- the arithmetic ---------------------------------------------------------


def test_the_value_follows_the_close_of_each_day(conn):
    buy(conn, quantity=10.0, price=100.0, day=date(2026, 8, 18))
    bars(conn, "PODD", (100.0, 110.0, 130.0))
    values = [p["market_value"] for p in equity.curve(conn, today=END)]
    assert values == [1000.0, 1100.0, 1300.0]


def test_a_purchase_mid_window_lifts_the_cost_that_day(conn):
    buy(conn, quantity=10.0, price=100.0, day=date(2026, 8, 18))
    transactions_store.record(
        conn,
        Transaction(ticker="PODD", kind=models.BUY, trade_date=date(2026, 8, 20),
                    quantity=10.0, price=130.0),
    )
    positions_store.sync_from_ledger(conn, "PODD")
    bars(conn, "PODD", (100.0, 110.0, 130.0))
    points = equity.curve(conn, today=END)
    assert [p["cost_basis"] for p in points] == [1000.0, 1000.0, 2300.0]
    assert points[-1]["market_value"] == 2600.0


def test_a_sale_takes_the_holding_out_from_that_day(conn):
    position = buy(conn, quantity=10.0, price=100.0, day=date(2026, 8, 18))
    positions_store.close_position(
        conn, position.id, price=110.0, close_date=date(2026, 8, 19)
    )
    bars(conn, "PODD", (100.0, 110.0, 130.0))
    points = equity.curve(conn, today=END)
    # Held on the 18th, sold on the 19th: the holdings go to zero from there,
    # and the curve carries on so the realised result stays visible.
    assert [p["day"] for p in points] == ["2026-08-18", "2026-08-19", "2026-08-20"]
    assert [p["market_value"] for p in points] == [1000.0, 0.0, 0.0]


def test_two_holdings_are_added_together(conn):
    buy(conn, ticker="AAA", quantity=10.0, price=10.0, day=date(2026, 8, 19))
    buy(conn, ticker="BBB", quantity=5.0, price=20.0, day=date(2026, 8, 19))
    bars(conn, "AAA", (10.0, 12.0))
    bars(conn, "BBB", (20.0, 22.0))
    last = equity.curve(conn, today=END)[-1]
    assert last["market_value"] == pytest.approx(10 * 12.0 + 5 * 22.0)
    assert last["holdings"] == 2


def test_a_split_does_not_move_the_curve(conn):
    """Same money, more shares: the value is unchanged across the split."""
    position = buy(conn, quantity=10.0, price=100.0, day=date(2026, 8, 18))
    bars(conn, "PODD", (100.0, 100.0, 25.0))
    positions_store.apply_split(conn, position.id, 4.0, split_date=date(2026, 8, 20))
    points = equity.curve(conn, today=END)
    assert points[0]["market_value"] == 1000.0
    assert points[-1]["market_value"] == 1000.0  # 40 shares at 25


# --- days the market was shut -----------------------------------------------


def test_a_weekend_carries_fridays_close(conn):
    """A portfolio still has a value on a Sunday, and it is Friday's."""
    friday, sunday = date(2026, 8, 21), date(2026, 8, 23)
    buy(conn, quantity=10.0, price=100.0, day=friday)
    history_store.save_bars(
        conn, "PODD", [PricePoint(day=friday, close=150.0)], "test"
    )
    points = equity.curve(conn, today=sunday)
    assert [p["market_value"] for p in points] == [1500.0, 1500.0, 1500.0]


def test_a_ticker_with_no_bars_is_left_out_rather_than_zeroed(conn):
    buy(conn, ticker="AAA", quantity=10.0, price=10.0, day=date(2026, 8, 19))
    buy(conn, ticker="NOPE", quantity=10.0, price=10.0, day=date(2026, 8, 19))
    bars(conn, "AAA", (10.0, 12.0))
    last = equity.curve(conn, today=END)[-1]
    assert last["market_value"] == 120.0
    assert last["holdings"] == 1


def test_days_before_any_price_are_skipped(conn):
    """No stored close means no honest value, so there is no point to draw."""
    buy(conn, quantity=10.0, price=100.0, day=date(2026, 8, 10))
    bars(conn, "PODD", (130.0,), end=END)
    points = equity.curve(conn, today=END)
    assert [p["day"] for p in points] == ["2026-08-20"]


# --- corrections ------------------------------------------------------------


def test_correcting_a_trade_moves_the_whole_curve(conn):
    """Derived rather than stored, so the past is not left telling yesterday's
    version of the story."""
    buy(conn, quantity=10.0, price=100.0, day=date(2026, 8, 18))
    bars(conn, "PODD", (100.0, 110.0, 130.0))
    before = [p["market_value"] for p in equity.curve(conn, today=END)]

    entry = transactions_store.list_transactions(conn, ticker="PODD")[0]
    transactions_store.amend(conn, entry.id, {"quantity": 20.0})
    after = [p["market_value"] for p in equity.curve(conn, today=END)]

    assert before == [1000.0, 1100.0, 1300.0]
    assert after == [2000.0, 2200.0, 2600.0]


# --- the cash side ----------------------------------------------------------


def test_buying_moves_money_from_cash_into_shares(conn):
    """Neither side is created or destroyed, so the total does not move."""
    buy(conn, quantity=10.0, price=100.0, day=date(2026, 8, 18))
    bars(conn, "PODD", (100.0, 100.0, 100.0))
    point = equity.curve(conn, today=END)[0]
    assert point["market_value"] == 1000.0
    assert point["cash"] == -1000.0
    assert point["total"] == 0.0


def test_the_total_moves_only_when_the_market_does(conn):
    buy(conn, quantity=10.0, price=100.0, day=date(2026, 8, 18))
    bars(conn, "PODD", (100.0, 110.0, 130.0))
    totals = [p["total"] for p in equity.curve(conn, today=END)]
    assert totals == [0.0, 100.0, 300.0]


def test_selling_does_not_step_the_total_down(conn):
    """The complaint this exists for: the shares go, the money does not."""
    position = buy(conn, quantity=10.0, price=100.0, day=date(2026, 8, 18))
    bars(conn, "PODD", (100.0, 130.0, 130.0))
    positions_store.close_position(
        conn, position.id, price=130.0, close_date=date(2026, 8, 19)
    )
    points = equity.curve(conn, today=END)
    # The 19th onward has nothing held, and the curve continues: the holdings
    # go to zero and the gain is sitting in cash.
    assert [p["market_value"] for p in points] == [1000.0, 0.0, 0.0]
    assert [p["total"] for p in points] == [0.0, 300.0, 300.0]


def test_a_sale_leaves_the_gain_in_cash(conn):
    buy(conn, ticker="AAA", quantity=10.0, price=100.0, day=date(2026, 8, 18))
    buy(conn, ticker="BBB", quantity=10.0, price=100.0, day=date(2026, 8, 18))
    bars(conn, "AAA", (100.0, 130.0, 130.0))
    bars(conn, "BBB", (100.0, 100.0, 100.0))
    transactions_store.record(
        conn,
        Transaction(ticker="AAA", kind=models.SELL, trade_date=date(2026, 8, 19),
                    quantity=10.0, price=130.0),
    )
    positions_store.sync_from_ledger(conn, "AAA")
    points = equity.curve(conn, today=END)
    before, after = points[0], points[-1]
    assert before["market_value"] == 2000.0
    assert after["market_value"] == 1000.0   # only BBB is left
    assert after["cash"] == -700.0           # -2000 spent, +1300 back
    assert before["total"] == 0.0
    assert after["total"] == 300.0           # the gain survived the sale


def test_a_dividend_lands_in_cash(conn):
    buy(conn, quantity=10.0, price=100.0, day=date(2026, 8, 18))
    bars(conn, "PODD", (100.0, 100.0, 100.0))
    transactions_store.record(
        conn,
        Transaction(ticker="PODD", kind=models.DIVIDEND, trade_date=date(2026, 8, 19),
                    amount=50.0),
    )
    totals = [p["total"] for p in equity.curve(conn, today=END)]
    assert totals == [0.0, 50.0, 50.0]


def test_fees_come_out_of_the_total(conn):
    transactions_store.record(
        conn,
        Transaction(ticker="PODD", kind=models.BUY, trade_date=date(2026, 8, 18),
                    quantity=10.0, price=100.0, fees=25.0),
    )
    positions_store.sync_from_ledger(conn, "PODD")
    bars(conn, "PODD", (100.0, 100.0, 100.0))
    assert equity.curve(conn, today=END)[0]["total"] == -25.0
