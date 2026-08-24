"""Validation tests for the alert kinds added with the indicator set."""
from __future__ import annotations

import pytest

from fa.alerts import kinds
from fa.alerts.rules import REGISTRY
from fa.errors import ValidationError


def test_every_catalogued_kind_has_a_rule():
    assert set(kinds.CATALOGUE) == set(REGISTRY)


def test_defaults_of_every_kind_normalise_cleanly():
    for key, kind in kinds.CATALOGUE.items():
        if kind.required and any(kind.defaults.get(f) in (None, "", 0.0) for f in kind.required):
            continue  # price targets legitimately require a user supplied value
        assert kinds.normalize_params(key, {}) is not None


def test_rel_strength_window_must_be_known():
    assert kinds.normalize_params(kinds.REL_STRENGTH, {"pct": 5, "window": "6m"})["window"] == "6m"
    with pytest.raises(ValidationError, match="window"):
        kinds.normalize_params(kinds.REL_STRENGTH, {"pct": 5, "window": "9m"})


def test_atr_stop_multiple_is_numeric_and_positive():
    assert kinds.normalize_params(kinds.ATR_STOP, {"multiple": "2.5"})["multiple"] == pytest.approx(2.5)
    with pytest.raises(ValidationError, match="positive"):
        kinds.normalize_params(kinds.ATR_STOP, {"multiple": -1})
    with pytest.raises(ValidationError, match="numeric"):
        kinds.normalize_params(kinds.ATR_STOP, {"multiple": "mucho"})


def test_sma_break_direction_is_mandatory_and_bounded():
    assert kinds.normalize_params(kinds.SMA_BREAK, {"direction": "above"})["direction"] == "above"
    with pytest.raises(ValidationError, match="direction"):
        kinds.normalize_params(kinds.SMA_BREAK, {"direction": "any"})


def test_macd_cross_rejects_inverted_periods():
    with pytest.raises(ValidationError, match="fast < slow"):
        kinds.normalize_params(kinds.MACD_CROSS, {"fast": 30, "slow": 10})


def test_macd_cross_direction_is_validated():
    with pytest.raises(ValidationError, match="direction"):
        kinds.normalize_params(kinds.MACD_CROSS, {"direction": "sideways"})


def test_period_stays_an_integer_while_ratios_stay_floats():
    params = kinds.normalize_params(kinds.VOLUME_SPIKE, {"ratio": "3", "period": "10"})
    assert isinstance(params["period"], int)
    assert isinstance(params["ratio"], float)


def test_kinds_needing_a_position_are_marked():
    assert kinds.get_kind(kinds.ATR_STOP).requires_position is True
    assert kinds.get_kind(kinds.VOLUME_SPIKE).requires_position is False
