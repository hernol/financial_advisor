"""CEDEARs resolve to the underlying share, ratio and all.

The ratio is kept as two integers on purpose. Fourteen programs are inverted -
SID is 1:8, one CEDEAR is eight shares - so a single float would have to pick a
direction and be wrong by the ratio squared for those.
"""
from __future__ import annotations

import json

import pytest

from fa.cedears import load_table, resolve


def test_a_plain_ticker_is_not_a_cedear():
    """The whole design rests on leaving an ordinary share alone."""
    assert resolve("AAPL") is None
    assert resolve("PODD") is None


def test_a_ba_suffix_resolves_to_the_underlying():
    cedear = resolve("AAPL.BA")
    assert cedear is not None
    assert cedear.underlying == "AAPL"


def test_the_suffix_is_case_insensitive():
    assert resolve("aapl.ba") is not None


def test_twenty_cedears_are_one_share():
    assert resolve("AAPL.BA").shares_per_cedear == pytest.approx(1 / 20)


def test_an_inverted_ratio_gives_many_shares_per_cedear():
    """SID is 1:8. Dividing by a single ratio would be wrong by 64x."""
    assert resolve("SID.BA").shares_per_cedear == pytest.approx(8.0)


def test_an_unknown_ba_ticker_is_not_invented():
    assert resolve("NOSUCH.BA") is None


def test_the_table_carries_why_a_cedear_is_unsupported(tmp_path):
    table = tmp_path / "t.json"
    table.write_text(json.dumps([{
        "local": "BAS", "yahoo": "BAS.BA", "underlying": "BAS.DE",
        "cedears": 2, "shares": 1, "name": "BASF SE",
        "supported": False, "reason": "cotiza en EUR",
    }]))
    loaded = load_table(table)
    assert loaded["BAS.BA"].supported is False
    assert "EUR" in loaded["BAS.BA"].reason


# --- the Comafi parser ------------------------------------------------------

from pathlib import Path  # noqa: E402

from scripts.comafi import parse, split_ratio, translate  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "comafi-sample.html"


def _rows():
    return {r["local"]: r for r in parse(FIXTURE.read_text(encoding="utf-8"))}


def test_the_parser_reads_every_program_row():
    rows = _rows()
    assert len(rows) == 8
    assert rows["AAPL"]["name"] == "APPLE INC"


def test_a_ratio_splits_into_two_integers():
    assert split_ratio("20:1") == (20, 1)
    assert split_ratio("1:8") == (1, 8)
    assert split_ratio(" 60 : 1 ") == (60, 1)


def test_a_ratio_that_is_not_two_integers_is_refused():
    """Rounding a shape we have never seen would be inventing the ratio."""
    with pytest.raises(ValueError):
        split_ratio("1.5:1")
    with pytest.raises(ValueError):
        split_ratio("varios")


def test_a_plain_symbol_translates_to_itself():
    assert translate("NOK") == ("NOK", "")


def test_a_share_class_slash_becomes_a_dash():
    assert translate("BRK/B") == ("BRK-B", "")


def test_a_us_suffix_is_dropped():
    assert translate("VIST US") == ("VIST", "")


def test_a_london_suffix_becomes_the_yahoo_one():
    assert translate("SMSN LI") == ("SMSN.IL", "")


def test_a_frankfurt_listing_is_refused_with_a_reason():
    """It quotes in euros, so valuing through it would need a second FX hop -
    the very thing this design exists to avoid."""
    symbol, reason = translate("BAS GR")
    assert symbol is None
    assert "EUR" in reason
