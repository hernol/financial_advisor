"""PEG, P/E and earnings growth.

The interesting part is not the division, it is everything the division must
refuse to do: a company that lost money has no P/E, and a company whose
earnings shrank has no PEG. Both cases produce a number that sorts as cheap
next to real ones, which is worse than a blank.
"""
from __future__ import annotations

from datetime import date

import pytest

from fa import ratios
from fa.metrics import QUALITY_COLUMNS, SUMMARY_COLUMNS, build_frame
from fa.models import PricePoint
from fa.store.fundamentals import frame_to_rows


def frame(rows, price=30.0, shares=50.0, closes=((2024, 20.0), (2025, 30.0))):
    history = [PricePoint(day=date(y, 12, 31), close=c) for y, c in closes]
    return build_frame(rows, history, shares, price)


def year(label, revenue, net_income, **extra):
    return {
        "label": label,
        "period_end": date(int(label), 12, 31),
        "revenue": revenue,
        "net_income": net_income,
        "operating_cash_flow": revenue * 0.15,
        "capex": revenue * 0.03,
        "total_assets": revenue * 0.9,
        "total_liabilities": revenue * 0.4,
        **extra,
    }


# --- the arithmetic ---------------------------------------------------------


def test_eps_is_earnings_over_shares():
    assert ratios.eps(500.0, 100.0) == 5.0


def test_pe_is_price_over_eps():
    assert ratios.price_earnings(200.0, 5.0) == 40.0


def test_peg_divides_the_pe_by_the_growth_rate():
    assert ratios.peg(40.0, 20.0) == 2.0


def test_the_frame_computes_peg_from_its_own_period():
    """The P/E uses the close as at that period end, not today's price."""
    rows = [year("2024", 1000.0, 100.0), year("2025", 1300.0, 125.0)]
    last = frame(rows).iloc[-1]
    assert last["EPS"] == pytest.approx(2.5)          # 125 / 50 shares
    assert last["PE"] == pytest.approx(12.0)          # 30.0 close / 2.5
    assert last["Earnings_Growth"] == pytest.approx(25.0)
    assert last["PEG"] == pytest.approx(0.48)         # 12 / 25


# --- what it refuses to answer ---------------------------------------------


def test_a_loss_has_no_pe():
    """A negative P/E sorts as the cheapest thing on the screen."""
    assert ratios.price_earnings(200.0, -3.0) is None


def test_shrinking_earnings_have_no_peg():
    """Dividing by a contraction yields a negative PEG, which reads as a
    bargain while describing a company going backwards."""
    assert ratios.peg(40.0, -15.0) is None


def test_flat_earnings_have_no_peg():
    assert ratios.peg(40.0, 0.0) is None


def test_peg_needs_a_pe():
    assert ratios.peg(None, 20.0) is None


def test_a_losing_year_leaves_pe_and_peg_blank_for_the_client():
    rows = [year("2024", 1000.0, 100.0), year("2025", 1300.0, -40.0)]
    last = frame_to_rows(frame(rows))[-1]
    assert last["EPS"] == pytest.approx(-0.8)   # the loss itself is real
    assert last["PE"] is None
    assert last["PEG"] is None


def test_the_first_period_has_no_growth_so_no_peg():
    """Nothing to compare against yet."""
    first = frame_to_rows(frame([year("2024", 1000.0, 100.0)]))[0]
    assert first["PE"] is not None
    assert first["PEG"] is None


def test_eps_needs_a_share_count():
    assert ratios.eps(500.0, None) is None
    assert ratios.eps(500.0, 0.0) is None


# --- where it shows up ------------------------------------------------------


def test_pe_and_peg_are_in_the_summary_table():
    assert "PE" in SUMMARY_COLUMNS
    assert "PEG" in SUMMARY_COLUMNS


def test_the_growth_rate_sits_next_to_the_peg_it_produced():
    """A PEG without its denominator on screen cannot be judged: 0.03 off a
    894% one-year swing looks like the cheapest stock ever listed."""
    assert SUMMARY_COLUMNS.index("Earnings_Growth") == SUMMARY_COLUMNS.index("PEG") - 1
    assert "Earnings_Growth" not in QUALITY_COLUMNS


# --- statements and quote in different currencies ---------------------------


def mixed(rows, **kw):
    return frame_to_rows(
        frame(rows, **kw)
    )


def test_a_foreign_currency_statement_blanks_the_price_derived_ratios():
    """TSMC reports in TWD and its ADR trades in USD. Dividing one by the other
    gave a P/E of 0.93 against a real ~28 — the exchange rate wearing a metric's
    name."""
    rows = [year("2024", 1000.0, 100.0), year("2025", 1300.0, 125.0)]
    history = [PricePoint(day=date(2025, 12, 31), close=30.0)]
    row = frame_to_rows(
        build_frame(rows, history, 50.0, 30.0,
                    statement_currency="TWD", quote_currency="USD")
    )[-1]
    assert row["PE"] is None
    assert row["PEG"] is None
    assert row["EPS"] is None
    assert row["FCF_Yield"] is None
    assert row["EV"] is None
    assert row["Currency_Mismatch"] is True


def test_the_ratios_that_never_crossed_currencies_survive():
    """Both sides of a margin are in the same money, so it was never wrong."""
    rows = [year("2024", 1000.0, 100.0), year("2025", 1300.0, 125.0)]
    history = [PricePoint(day=date(2025, 12, 31), close=30.0)]
    row = frame_to_rows(
        build_frame(rows, history, 50.0, 30.0,
                    statement_currency="TWD", quote_currency="USD")
    )[-1]
    assert row["Net_Margin"] == pytest.approx(9.615, rel=1e-3)
    assert row["Earnings_Growth"] == pytest.approx(25.0)
    assert row["Revenue"] == pytest.approx(1300.0)


def test_the_same_currency_changes_nothing():
    rows = [year("2024", 1000.0, 100.0), year("2025", 1300.0, 125.0)]
    history = [PricePoint(day=date(2025, 12, 31), close=30.0)]
    row = frame_to_rows(
        build_frame(rows, history, 50.0, 30.0,
                    statement_currency="USD", quote_currency="USD")
    )[-1]
    assert row["PE"] == pytest.approx(12.0)
    assert row["Currency_Mismatch"] is False


def test_an_unknown_currency_is_not_treated_as_a_mismatch():
    """Blanking on a guess would hide figures that are almost certainly fine.
    Most providers say nothing and most companies report in their quote
    currency."""
    rows = [year("2024", 1000.0, 100.0), year("2025", 1300.0, 125.0)]
    history = [PricePoint(day=date(2025, 12, 31), close=30.0)]
    row = frame_to_rows(
        build_frame(rows, history, 50.0, 30.0, statement_currency="", quote_currency="USD")
    )[-1]
    assert row["PE"] == pytest.approx(12.0)
    assert row["Currency_Mismatch"] is False


def test_the_ai_payload_carries_the_caveat():
    """The model reads the tables; blanks with no explanation invite it to
    speculate about why they are empty."""
    from fa.metrics import to_payload

    rows = [year("2024", 1000.0, 100.0), year("2025", 1300.0, 125.0)]
    history = [PricePoint(day=date(2025, 12, 31), close=30.0)]
    tweaked = build_frame(rows, history, 50.0, 30.0,
                          statement_currency="TWD", quote_currency="USD")
    text = to_payload(tweaked, tweaked, "yahoo")
    assert "different currency" in text
