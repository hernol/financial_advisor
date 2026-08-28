"""Moving a whole database between engines.

The ids come across unchanged, because they are what every foreign key points
at. Renumbering would mean rewriting the references, and a mistake there does
not raise — it quietly attaches a trade to somebody else's position.
"""
from __future__ import annotations

from datetime import date

import pytest

from fa import models
from fa.alerts import kinds
from fa.models import Alert, Position, PricePoint, Transaction
from fa.store import alerts as alerts_store
from fa.store import history as history_store
from fa.store import positions as positions_store
from fa.store import transactions as transactions_store
from fa.store import transfer
from fa.store.db import connect
from fa.store.schema import SCHEMA


@pytest.fixture()
def source(conn):
    """A database with something of every shape in it."""
    positions_store.add_position(
        conn, Position(ticker="AAA", quantity=10, buy_price=100.0, buy_date=date(2026, 8, 1))
    )
    transactions_store.record(
        conn,
        Transaction(ticker=None, kind=models.DEPOSIT, trade_date=date(2026, 7, 1), amount=5000.0),
    )
    alerts_store.add_alert(
        conn, Alert(ticker="AAA", kind=kinds.PCT_UP, params={"pct": 10, "reference": "buy"})
    )
    history_store.save_bars(
        conn, "AAA", [PricePoint(day=date(2026, 8, i + 1), close=100.0 + i) for i in range(5)], "test"
    )
    return conn


@pytest.fixture()
def target(tmp_path):
    db = connect(str(tmp_path / "destino.db"))
    yield db
    db.close()


# --- the copy --------------------------------------------------------------


def test_every_table_arrives(source, target):
    written = transfer.copy_all(source, target)
    assert written["positions"] == 1
    assert written["alerts"] == 1
    assert written["daily_bars"] == 5
    assert transfer.verify(source, target) == []


def test_the_ids_are_preserved(source, target):
    """A renumbered id is a foreign key pointing at the wrong row."""
    transfer.copy_all(source, target)
    here = [(p.id, p.ticker) for p in positions_store.list_positions(source)]
    there = [(p.id, p.ticker) for p in positions_store.list_positions(target)]
    assert here == there


def test_the_ledger_still_adds_up(source, target):
    transfer.copy_all(source, target)
    before = transactions_store.list_transactions(source)
    after = transactions_store.list_transactions(target)
    assert [e.id for e in before] == [e.id for e in after]
    assert sum(e.cash_flow for e in before) == sum(e.cash_flow for e in after)
    assert models.contributed(before) == models.contributed(after)


def test_json_columns_survive(source, target):
    transfer.copy_all(source, target)
    here = alerts_store.list_alerts(source)[0]
    there = alerts_store.list_alerts(target)[0]
    assert dict(here.params) == dict(there.params)


def test_the_source_is_left_alone(source, target):
    """Nothing is deleted on the way out; that is what makes it safe to try."""
    before = transfer.verify(source, source)
    transfer.copy_all(source, target)
    assert transfer.verify(source, source) == before
    assert len(positions_store.list_positions(source)) == 1


# --- refusing to make a mess -----------------------------------------------


def test_it_refuses_a_target_that_already_holds_data(source, target):
    transfer.copy_all(source, target)
    with pytest.raises(ValueError, match="ya tiene datos"):
        transfer.copy_all(source, target)


def test_force_skips_the_emptiness_check_without_changing_a_clean_run(source, target):
    """The flag only lifts the guard. On an empty target it must behave exactly
    like a normal run, so reaching for it is never a reason to doubt the copy."""
    written = transfer.copy_all(source, target, force=True)
    assert transfer.verify(source, target) == []
    assert written["positions"] == 1


def test_a_freshly_migrated_target_counts_as_empty(target):
    """The migrations seed the local account, and one row there is not data."""
    from fa.store import migrations

    migrations.run(target)
    assert transfer.target_is_empty(target)


# --- the things that would break silently ----------------------------------


def test_table_order_covers_the_whole_schema():
    """A table added later and forgotten here would simply not be copied, and
    the row counts would still be reported as matching for everything else."""
    declared = set(transfer.TABLE_ORDER) | transfer.SKIP_TABLES
    in_schema = set()
    for line in SCHEMA.splitlines():
        stripped = line.strip()
        if stripped.startswith("CREATE TABLE IF NOT EXISTS "):
            in_schema.add(stripped.split()[5].split("(")[0])
    assert in_schema - declared == set(), "hay tablas del esquema que nadie copia"


def test_parents_come_before_children():
    order = list(transfer.TABLE_ORDER)
    for child, parent in (
        ("transactions", "positions"), ("alerts", "positions"),
        ("alert_events", "alerts"), ("delivery_attempts", "alert_events"),
        ("ai_suggestions", "analyses"), ("alert_evaluations", "check_runs"),
        ("account_users", "accounts"),
    ):
        assert order.index(parent) < order.index(child), f"{parent} tiene que ir antes que {child}"


def test_json_columns_are_read_from_the_schema():
    """Hardcoding the list means a column added later is copied as a string
    into a JSONB column and fails at the worst possible moment."""
    found = transfer.json_columns()
    assert "params" in found["alerts"]
    assert "payload" in found["alert_events"]
    assert "rows" in found["fundamental_snapshots"]


def test_the_schema_version_is_not_copied():
    """The target's version belongs to whoever ran its migrations."""
    assert "meta" in transfer.SKIP_TABLES
