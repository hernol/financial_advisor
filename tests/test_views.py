"""Console rendering: the views must never crash on partial data."""
from __future__ import annotations

from datetime import date

import pandas as pd

from fa.alerts.engine import CheckReport
from fa.models import Alert, Position
from fa.portfolio import Holding, Portfolio
from fa.ui.charts import draw_bar_chart, print_table
from fa.ui.views import (
    portfolio_summary_for_ai,
    render_alerts,
    render_check_report,
    render_events,
    render_portfolio,
    render_positions,
)
from fa.store import alerts as alerts_store
from fa.store import positions as positions_store

POSITION = Position(id=1, ticker="PODD", quantity=10, buy_price=100.0, buy_date=date(2026, 1, 1))


def test_bar_chart_skips_missing_values(capsys):
    draw_bar_chart([10.0, None, -5.0], ["2024", "2025", "2026"], "FCF")
    out = capsys.readouterr().out
    assert "2024" in out and "2025" not in out and "-" in out


def test_bar_chart_handles_an_all_empty_series(capsys):
    draw_bar_chart([None], ["2025"], "FCF")
    assert "Sin datos" in capsys.readouterr().out


def test_print_table_renders_missing_cells_as_na(capsys):
    print_table(pd.DataFrame([{"Period": "2025", "FCF": None}, {"Period": "2026", "FCF": 1234.5}]))
    out = capsys.readouterr().out
    assert "n/a" in out and "1,234.50" in out


def test_print_table_keeps_text_columns_intact(capsys):
    print_table(pd.DataFrame([{"Period": "25-Q1", "FCF": 1.0}]))
    assert "25-Q1" in capsys.readouterr().out


def test_print_table_on_empty_frame(capsys):
    print_table(pd.DataFrame())
    assert "Sin datos" in capsys.readouterr().out


def test_render_positions_without_data(capsys):
    render_positions([])
    assert "No hay posiciones" in capsys.readouterr().out


def test_render_positions_lists_the_rows(capsys):
    render_positions([POSITION])
    assert "PODD" in capsys.readouterr().out


def test_render_portfolio_shows_totals_and_errors(capsys):
    portfolio = Portfolio(
        holdings=(
            Holding(position=POSITION, price=120.0, currency="USD"),
            Holding(position=POSITION, price=None, currency="USD", error="sin datos"),
        )
    )
    render_portfolio(portfolio)
    out = capsys.readouterr().out
    assert "TOTAL" in out and "sin datos" in out


def test_render_alerts_without_data(conn, capsys):
    render_alerts(conn, [])
    assert "No hay alertas" in capsys.readouterr().out


def test_render_alerts_lists_parameters(conn, capsys):
    render_alerts(conn, [Alert(id=1, ticker="PODD", kind="pct_up", params={"pct": 10})])
    assert "pct=10" in capsys.readouterr().out


def test_render_events(capsys):
    render_events([{"fired_at": "2026-08-20T12:00:00", "title": "t", "message": "m", "delivered": ["console"]}])
    assert "console" in capsys.readouterr().out


def test_render_check_report_lists_errors(capsys):
    report = CheckReport(checked=2, fired=(), skipped_cooldown=1, expired=0, errors=("PODD: sin datos",))
    render_check_report(report)
    out = capsys.readouterr().out
    assert "sin datos" in out and "cooldown: 1" in out


def test_render_check_report_when_all_quiet(capsys):
    render_check_report(CheckReport(checked=1, fired=(), skipped_cooldown=0, expired=0, errors=()))
    assert "Nada que reportar" in capsys.readouterr().out


def test_portfolio_summary_for_ai_lists_positions_and_alerts(conn):
    positions_store.add_position(conn, POSITION)
    alerts_store.add_alert(conn, Alert(ticker="PODD", kind="pct_up", params={"pct": 10}))
    summary = portfolio_summary_for_ai(conn, "PODD")
    assert "Position: 10 shares @ 100.00" in summary
    assert "Active alert: pct_up" in summary
