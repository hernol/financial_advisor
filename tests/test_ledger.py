"""The transaction ledger and the holdings derived from it."""
from __future__ import annotations

from datetime import date

from fa import ledger, models
from fa.models import Position, Transaction
from fa.store import positions as positions_store
from fa.store import transactions as transactions_store


def buy(conn, *, quantity=10.0, price=100.0, day=date(2026, 1, 15), ticker="PODD"):
    return positions_store.add_position(
        conn, Position(ticker=ticker, quantity=quantity, buy_price=price, buy_date=day)
    )


def test_adding_a_position_opens_its_ledger(conn):
    position = buy(conn)
    entries = transactions_store.list_transactions(conn, ticker="PODD")
    assert len(entries) == 1
    assert entries[0].kind == models.BUY
    assert entries[0].quantity == 10.0
    assert entries[0].price == 100.0
    # The rollup is built from the entry, and the entry is linked back to it.
    assert entries[0].position_id == position.id


def test_a_holding_is_replayed_from_its_entries(conn):
    buy(conn)
    holding = ledger.holding(conn, "PODD")
    assert holding.quantity == 10.0
    assert holding.average_cost == 100.0
    assert holding.cost_basis == 1000.0
    assert holding.is_open


def test_two_buys_average_out(conn):
    buy(conn, quantity=10.0, price=100.0)
    buy(conn, quantity=10.0, price=120.0, day=date(2026, 3, 1))
    holding = ledger.holding(conn, "PODD")
    assert holding.quantity == 20.0
    assert holding.average_cost == 110.0


def test_selling_realises_against_the_average(conn):
    position = buy(conn, quantity=10.0, price=100.0)
    positions_store.close_position(
        conn, position.id, price=150.0, close_date=date(2026, 6, 1)
    )
    holding = ledger.holding(conn, "PODD")
    assert holding.quantity == 0.0
    assert holding.realized_pnl == 500.0
    assert not holding.is_open


def test_fees_reduce_the_realised_result(conn):
    position = buy(conn, quantity=10.0, price=100.0)
    positions_store.close_position(conn, position.id, price=150.0, fees=20.0)
    assert ledger.holding(conn, "PODD").realized_pnl == 480.0


def test_closing_without_a_price_records_no_sale(conn):
    """Archiving an old position must not invent a number."""
    position = buy(conn)
    closed = positions_store.close_position(conn, position.id)
    assert closed.realized_pnl is None
    assert transactions_store.list_transactions(conn, kind=models.SELL) == []


def test_a_split_keeps_the_original_purchase_on_file(conn):
    """The bug this exists for: the split used to overwrite the real cost."""
    position = buy(conn, quantity=10.0, price=400.0)
    positions_store.apply_split(conn, position.id, 4.0)

    rolled_up = positions_store.get_position(conn, position.id)
    assert rolled_up.quantity == 40.0
    assert rolled_up.buy_price == 100.0

    original = transactions_store.list_transactions(conn, kind=models.BUY)[0]
    assert original.price == 400.0
    assert original.quantity == 10.0


def test_a_split_rescales_the_holding_without_changing_the_money(conn):
    position = buy(conn, quantity=10.0, price=400.0)
    positions_store.apply_split(conn, position.id, 4.0)
    holding = ledger.holding(conn, "PODD")
    assert holding.quantity == 40.0
    assert holding.average_cost == 100.0
    assert holding.cost_basis == 4000.0


def test_dividends_accumulate_without_touching_the_shares(conn):
    buy(conn)
    transactions_store.record(
        conn,
        Transaction(
            ticker="PODD", kind=models.DIVIDEND, trade_date=date(2026, 4, 1), amount=25.0
        ),
    )
    holding = ledger.holding(conn, "PODD")
    assert holding.dividends == 25.0
    assert holding.quantity == 10.0


def test_unrealised_pnl_uses_the_average_cost(conn):
    buy(conn, quantity=10.0, price=100.0)
    absolute, percentage = ledger.holding(conn, "PODD").unrealized(150.0)
    assert absolute == 500.0
    assert percentage == 50.0


def test_a_soft_deleted_entry_leaves_the_rollup_but_stays_on_file(conn):
    buy(conn)
    entry = transactions_store.list_transactions(conn)[0]
    assert transactions_store.soft_delete(conn, entry.id)
    assert ledger.holding(conn, "PODD").quantity == 0.0
    assert len(transactions_store.list_transactions(conn, include_deleted=True)) == 1


def test_deleting_a_position_keeps_its_ledger(conn):
    position = buy(conn)
    positions_store.delete_position(conn, position.id)
    assert positions_store.list_positions(conn) == []
    assert len(transactions_store.list_transactions(conn, ticker="PODD")) == 1


def test_holdings_lists_only_what_is_still_open(conn):
    buy(conn, ticker="PODD")
    sold = buy(conn, ticker="LDOS")
    positions_store.close_position(conn, sold.id, price=150.0)
    assert [h.ticker for h in ledger.holdings(conn)] == ["PODD"]
    assert len(ledger.holdings(conn, open_only=False)) == 2


def test_selling_more_than_held_never_goes_negative(conn):
    buy(conn, quantity=10.0, price=100.0)
    transactions_store.record(
        conn,
        Transaction(
            ticker="PODD", kind=models.SELL, trade_date=date(2026, 6, 1), quantity=25.0, price=110.0
        ),
    )
    holding = ledger.holding(conn, "PODD")
    assert holding.quantity == 0.0
    assert holding.realized_pnl == 100.0
