"""Unit tests for the pure technical indicators."""
from __future__ import annotations

import pytest

from fa.indicators import detect_cross, drawdown_pct, rsi, sma, sma_series


def test_sma_returns_none_when_series_is_shorter_than_period():
    assert sma([1.0, 2.0], 3) is None


def test_sma_averages_the_last_period_closes():
    assert sma([1.0, 2.0, 3.0, 4.0], 2) == pytest.approx(3.5)


def test_sma_series_length_matches_window_count():
    assert sma_series([1.0, 2.0, 3.0, 4.0], 2) == [1.5, 2.5, 3.5]


def test_rsi_is_100_when_every_move_is_a_gain():
    assert rsi([float(i) for i in range(1, 20)], 14) == pytest.approx(100.0)


def test_rsi_is_zero_when_every_move_is_a_loss():
    assert rsi([float(i) for i in range(20, 1, -1)], 14) == pytest.approx(0.0)


def test_rsi_needs_more_closes_than_the_period():
    assert rsi([1.0, 2.0, 3.0], 14) is None


def test_detect_cross_reports_golden_cross_on_the_last_session():
    closes = [10.0] * 10 + [9.0, 9.0, 20.0]
    assert detect_cross(closes, 2, 5) == "golden"


def test_detect_cross_reports_death_cross_on_the_last_session():
    closes = [10.0] * 10 + [11.0, 11.0, 1.0]
    assert detect_cross(closes, 2, 5) == "death"


def test_detect_cross_returns_none_without_a_fresh_cross():
    assert detect_cross([10.0] * 20, 2, 5) is None


def test_detect_cross_rejects_fast_period_above_slow():
    assert detect_cross([float(i) for i in range(50)], 200, 50) is None


def test_drawdown_is_zero_above_the_peak():
    assert drawdown_pct(120.0, 100.0) == 0.0


def test_drawdown_percentage_from_peak():
    assert drawdown_pct(85.0, 100.0) == pytest.approx(15.0)
