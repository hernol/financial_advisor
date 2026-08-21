"""CLI argument parsing and the commands that need no live data."""
from __future__ import annotations

import json

import pytest

from fa.cli import _parse_params, build_parser, main
from fa.errors import FinancialAnalyzerError


def test_no_command_defaults_to_the_interactive_menu():
    assert build_parser().parse_args([]).command is None


def test_check_alerts_flags():
    args = build_parser().parse_args(["check-alerts", "--json", "--quiet"])
    assert args.command == "check-alerts" and args.json and args.quiet


def test_add_position_requires_quantity_and_price():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["add-position", "PODD"])


def test_add_position_parses_the_purchase():
    args = build_parser().parse_args(
        ["add-position", "PODD", "--qty", "10", "--price", "141.5", "--date", "2026-03-01"]
    )
    assert (args.qty, args.price, args.date) == (10.0, 141.5, "2026-03-01")


def test_add_alert_rejects_an_unknown_kind():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["add-alert", "PODD", "--kind", "moon_phase"])


def test_add_alert_collects_repeated_params():
    args = build_parser().parse_args(
        ["add-alert", "PODD", "--kind", "pct_down", "--param", "pct=10", "--param", "reference=buy"]
    )
    assert args.param == ["pct=10", "reference=buy"]


def test_parse_params_builds_a_mapping():
    assert _parse_params(["pct=10", "reference=buy"]) == {"pct": "10", "reference": "buy"}


def test_parse_params_rejects_a_malformed_pair():
    with pytest.raises(FinancialAnalyzerError):
        _parse_params(["pct"])


def test_kinds_command_runs_without_database_or_network(capsys):
    assert main(["kinds", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    keys = {item["kind"] for item in payload}
    assert {"pct_up", "trailing_stop", "earnings_near", "split_detected"} <= keys


def test_kinds_command_human_output(capsys):
    assert main(["kinds"]) == 0
    assert "trailing_stop" in capsys.readouterr().out


def test_use_and_unuse_are_parsed():
    parser = build_parser()
    assert parser.parse_args(["use", "PODD"]).ticker == "PODD"
    assert parser.parse_args(["unuse"]).command == "unuse"


def test_ticker_is_optional_on_the_scoped_commands():
    parser = build_parser()
    assert parser.parse_args(["analyze"]).ticker is None
    assert parser.parse_args(["add-alert", "--kind", "rsi"]).ticker is None


def test_check_alerts_accepts_a_ticker_filter():
    assert build_parser().parse_args(["check-alerts", "--ticker", "PODD"]).ticker == "PODD"


def test_suggestions_flags():
    args = build_parser().parse_args(["suggestions", "--review", "--ticker", "PODD"])
    assert args.review and args.ticker == "PODD" and args.status == "pending"


def test_digest_facts_only_flag():
    assert build_parser().parse_args(["digest", "--facts-only"]).facts_only is True


def test_local_ai_command_exists():
    assert build_parser().parse_args(["local-ai"]).command == "local-ai"


def test_resolve_ticker_prefers_the_explicit_argument(conn, tmp_path):
    from fa.cli import _resolve_ticker
    from fa.store import meta as meta_store
    from tests.conftest import make_app

    app = make_app(conn, tmp_path)
    meta_store.set_current_ticker(conn, "AAPL")
    assert _resolve_ticker(app, "podd") == "PODD"
    assert _resolve_ticker(app, None) == "AAPL"


def test_resolve_ticker_without_a_session_explains_itself(conn, tmp_path):
    from fa.cli import _resolve_ticker
    from tests.conftest import make_app

    with pytest.raises(FinancialAnalyzerError, match="use TICKER"):
        _resolve_ticker(make_app(conn, tmp_path), None)
