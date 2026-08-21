"""Validation and wiring of the action layer, with a stubbed market."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from fa import actions
from fa.alerts import kinds
from fa.errors import ValidationError
from fa.store import alerts as alerts_store
from tests.conftest import make_app, make_quote


class StubMarket:
    providers = ("test",)

    def quote(self, ticker):
        return make_quote(150.0, ticker=ticker.upper())




def test_add_position_rejects_a_future_purchase(conn, tmp_path):
    app = make_app(conn, tmp_path, market=StubMarket())
    with pytest.raises(ValidationError):
        actions.add_position(app, "PODD", 1, 100, date.today() + timedelta(days=1))


def test_add_position_rejects_non_positive_quantity(conn, tmp_path):
    app = make_app(conn, tmp_path, market=StubMarket())
    with pytest.raises(ValidationError):
        actions.add_position(app, "PODD", 0, 100, date.today())


def test_alert_requiring_a_position_is_rejected_without_one(conn, tmp_path):
    app = make_app(conn, tmp_path, market=StubMarket())
    with pytest.raises(ValidationError):
        actions.add_alert(app, "PODD", kinds.TRAILING_STOP, {"pct": 15})


def test_alert_binds_itself_to_the_open_position(conn, tmp_path):
    app = make_app(conn, tmp_path, market=StubMarket())
    position = actions.add_position(app, "PODD", 10, 100, date(2026, 1, 1))
    alert = actions.add_alert(app, "PODD", kinds.TRAILING_STOP, {"pct": 15})
    assert alert.position_id == position.id


def test_baseline_percentage_alert_freezes_todays_price(conn, tmp_path):
    app = make_app(conn, tmp_path, market=StubMarket())
    alert = actions.add_alert(app, "PODD", kinds.PCT_UP, {"pct": 10, "reference": "baseline"})
    assert alert.params["baseline_price"] == 150.0


def test_buy_referenced_alert_needs_a_position(conn, tmp_path):
    app = make_app(conn, tmp_path, market=StubMarket())
    with pytest.raises(ValidationError):
        actions.add_alert(app, "PODD", kinds.PCT_UP, {"pct": 10, "reference": "buy"})


def test_one_shot_default_comes_from_the_catalogue(conn, tmp_path):
    app = make_app(conn, tmp_path, market=StubMarket())
    alert = actions.add_alert(app, "PODD", kinds.PRICE_ABOVE, {"price": 200})
    assert alert.one_shot is True


def test_split_adjustment_keeps_the_cost_basis(conn, tmp_path):
    app = make_app(conn, tmp_path, market=StubMarket())
    position = actions.add_position(app, "PODD", 10, 200, date(2026, 1, 1))
    adjusted = actions.adjust_for_split(app, position.id, 4.0)
    assert adjusted.buy_price == 50.0
    assert adjusted.quantity == 40.0
    assert adjusted.cost_basis == position.cost_basis


def test_split_adjustment_rejects_a_zero_ratio(conn, tmp_path):
    app = make_app(conn, tmp_path, market=StubMarket())
    position = actions.add_position(app, "PODD", 10, 200, date(2026, 1, 1))
    with pytest.raises(ValidationError):
        actions.adjust_for_split(app, position.id, 0)


def test_cooldown_falls_back_to_the_configured_default(conn, tmp_path):
    app = make_app(conn, tmp_path, market=StubMarket())
    actions.add_position(app, "PODD", 10, 100, date(2026, 1, 1))
    alert = actions.add_alert(app, "PODD", kinds.PCT_UP, {"pct": 10})
    assert alerts_store.get_alert(conn, alert.id).cooldown_hours == 24
