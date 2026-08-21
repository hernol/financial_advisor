"""Validation of alert parameters."""
from __future__ import annotations

import pytest

from fa.alerts import kinds
from fa.errors import ValidationError


def test_defaults_are_applied():
    params = kinds.normalize_params(kinds.EARNINGS_NEAR, {})
    assert params == {"days": 7}


def test_user_params_override_defaults_and_are_cast():
    params = kinds.normalize_params(kinds.PCT_UP, {"pct": "12.5"})
    assert params["pct"] == pytest.approx(12.5)
    assert params["reference"] == "buy"


def test_unknown_kind_is_rejected():
    with pytest.raises(ValidationError):
        kinds.normalize_params("moon_phase", {})


def test_negative_values_are_rejected():
    with pytest.raises(ValidationError):
        kinds.normalize_params(kinds.PCT_DOWN, {"pct": -5})


def test_non_numeric_value_is_rejected():
    with pytest.raises(ValidationError):
        kinds.normalize_params(kinds.PRICE_ABOVE, {"price": "carísimo"})


def test_sma_cross_requires_fast_below_slow():
    with pytest.raises(ValidationError):
        kinds.normalize_params(kinds.SMA_CROSS, {"fast": 200, "slow": 50})


def test_pct_reference_must_be_known():
    with pytest.raises(ValidationError):
        kinds.normalize_params(kinds.PCT_UP, {"pct": 5, "reference": "luna"})


def test_period_elapsed_requires_a_horizon():
    with pytest.raises(ValidationError):
        kinds.normalize_params(kinds.PERIOD_ELAPSED, {"months": 0, "days": 0})
