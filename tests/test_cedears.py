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
