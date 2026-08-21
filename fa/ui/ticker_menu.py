"""Ticker workspace: every action here is scoped to the selected symbol."""
from __future__ import annotations

from datetime import date

from fa import actions
from fa.alerts import kinds
from fa.app import App
from fa.errors import DataUnavailableError
from fa.store import alerts as alerts_store
from fa.store import events as events_store
from fa.store import positions as positions_store
from fa.store import suggestions as suggestions_store
from fa.ui.charts import clear_screen
from fa.ui.prompts import ask, ask_date, ask_float, ask_int, ask_yes_no
from fa.ui.suggestions_ui import review
from fa.ui.views import SEPARATOR, render_alerts, render_check_report, render_events, render_positions

MENU = """
 1. Análisis anual (YtoY)          6. Cargar compra
 2. Análisis trimestral (QtoQ)     7. Chequear sus alertas ahora
 3. Evaluación estratégica con IA  8. Historial disparado
 4. Ver / borrar sus alertas       9. Ajustar posición por split
 5. Crear alerta                  10. Sugerencias pendientes de la IA
 c. Cambiar de ticker              x. Salir del ticker (menú general)
 0. Salir del programa
"""


def header(app: App, ticker: str) -> None:
    """One-line status of the ticker being worked on."""
    positions = positions_store.positions_for_ticker(app.conn, ticker)
    alerts = alerts_store.list_alerts(app.conn, ticker=ticker, only_active=True)
    pending = suggestions_store.pending_count(app.conn, ticker)
    bits = [f"alertas activas: {len(alerts)}"]
    if positions:
        total = sum(p.quantity for p in positions)
        cost = sum(p.cost_basis for p in positions)
        bits.insert(0, f"posición: {total:g} acciones (costo {cost:,.2f})")
    else:
        bits.insert(0, "sin posición (watchlist)")
    if pending:
        bits.append(f"sugerencias pendientes: {pending}")
    print(f"\n{SEPARATOR}")
    print(f"📌 TRABAJANDO SOBRE {ticker} — {' | '.join(bits)}")
    print(SEPARATOR)


def dispatch(app: App, ticker: str, choice: str) -> str:
    """Run one ticker-scoped action. Returns the control flow signal.

    ``"stay"`` keeps the workspace, ``"leave"`` returns to the general menu and
    ``"quit"`` exits the program.
    """
    handlers = {
        "1": _annual,
        "2": _quarterly,
        "3": _ai_report,
        "4": _manage_alerts,
        "5": _add_alert,
        "6": _add_position,
        "7": _check,
        "8": _history,
        "9": _split_adjust,
        "10": _suggestions,
    }
    normalized = choice.strip().lower()
    if normalized == "0":
        return "quit"
    if normalized == "x":
        return "leave"
    if normalized == "c":
        return "change"
    handler = handlers.get(normalized)
    if handler is None:
        print("\n⚠️  Opción inválida.")
        return "stay"
    handler(app, ticker)
    return "stay"


def _annual(app: App, ticker: str) -> None:
    annual, _, context, source = actions.load_analysis(app, ticker)
    clear_screen()
    print(actions.snapshot_text(context.quote))
    actions.show_metrics(annual, ticker, f"COMPARATIVA ANUAL (fuente: {source})", "anual")


def _quarterly(app: App, ticker: str) -> None:
    _, quarterly, context, source = actions.load_analysis(app, ticker)
    clear_screen()
    print(actions.snapshot_text(context.quote))
    actions.show_metrics(quarterly, ticker, f"COMPARATIVA TRIMESTRAL (fuente: {source})", "trimestral")


def _ai_report(app: App, ticker: str) -> None:
    from fa.ui.prompts import read_multiline  # noqa: PLC0415 - keeps the import graph flat

    external = read_multiline(
        "\n📥 [OPCIONAL] Pegá el texto/recomendación de tu app de inversión "
        "(ENTER en línea vacía para terminar):"
    )
    print("\n🤖 Recolectando datos en vivo y consultando al modelo...")
    report = actions.run_ai_report(app, ticker, external)
    clear_screen()
    print(SEPARATOR)
    print(f"🧠 REPORTE ESTRATÉGICO — {ticker}")
    print(f"📊 {report.provenance}")
    print(SEPARATOR)
    print(report.text)
    print(SEPARATOR)
    if report.suggestions and ask_yes_no(
        f"\n¿Revisar las {len(report.suggestions)} sugerencias una por una?", default=True
    ):
        review(app, report.suggestions)
    elif report.suggestions:
        print("(Quedaron pendientes: opción 10 del menú del ticker)")


def _manage_alerts(app: App, ticker: str) -> None:
    render_alerts(app.conn, alerts_store.list_alerts(app.conn, ticker=ticker))
    action = ask("(d)esactivar / (a)ctivar / (b)orrar / ENTER para volver", "")
    if not action:
        return
    alert_id = ask_int("ID de la alerta")
    if action.startswith("b"):
        ok = alerts_store.delete_alert(app.conn, alert_id)
    else:
        ok = alerts_store.set_active(app.conn, alert_id, active=action.startswith("a"))
    print("✅ Listo." if ok else "⚠️  No encontrada.")


def _add_alert(app: App, ticker: str) -> None:
    print(f"\nTipos de alerta disponibles para {ticker}:")
    catalogue = list(kinds.CATALOGUE.values())
    for index, kind in enumerate(catalogue, start=1):
        print(f" {index:>2}. {kind.key:<16} {kind.label} — {kind.description}")
    selection = ask_int("Número de alerta", 1)
    if not 1 <= selection <= len(catalogue):
        print("⚠️  Selección inválida.")
        return
    definition = catalogue[selection - 1]
    params = {name: ask(f"  {name}", str(default)) for name, default in definition.defaults.items()}
    cooldown = ask_int("Cooldown en horas (0 = sin límite)", app.settings.default_cooldown_hours)
    alert = actions.add_alert(app, ticker, definition.key, params, cooldown_hours=cooldown)
    print(f"\n✅ Alerta #{alert.id} creada: {alert.kind} {dict(alert.params)}")


def _add_position(app: App, ticker: str) -> None:
    quantity = ask_float("Cantidad de acciones")
    buy_price = ask_float("Precio de compra por acción")
    buy_date = ask_date("Fecha de compra", date.today())
    currency = ask("Moneda", "USD")
    notes = ask("Notas", "")
    position = actions.add_position(
        app, ticker, quantity, buy_price, buy_date, currency=currency, notes=notes
    )
    print(f"\n✅ Posición #{position.id}: {position.quantity:g} {ticker} @ {position.buy_price:.2f}")
    if ask_yes_no("¿Crear alertas sugeridas (±10%, trailing 15%, earnings 7d, revisión 6 meses)?", default=True):
        for kind, params in (
            (kinds.PCT_UP, {"pct": 10, "reference": "buy"}),
            (kinds.PCT_DOWN, {"pct": 10, "reference": "buy"}),
            (kinds.TRAILING_STOP, {"pct": 15}),
            (kinds.EARNINGS_NEAR, {"days": app.settings.earnings_warning_days}),
            (kinds.PERIOD_ELAPSED, {"months": 6}),
            (kinds.SPLIT_DETECTED, {}),
        ):
            alert = actions.add_alert(app, ticker, kind, params, position_id=position.id)
            print(f"   ➕ alerta #{alert.id}: {kind} {dict(alert.params)}")


def _check(app: App, ticker: str) -> None:
    print(f"\n🔎 Evaluando las alertas de {ticker} contra datos en vivo...")
    render_check_report(actions.check_alerts(app, ticker))


def _history(app: App, ticker: str) -> None:
    events = [e for e in events_store.recent_events(app.conn, limit=50) if e["ticker"] == ticker]
    render_events(events[:20])


def _split_adjust(app: App, ticker: str) -> None:
    render_positions(positions_store.positions_for_ticker(app.conn, ticker))
    position_id = ask_int("ID de la posición")
    ratio = ask_float("Ratio del split (4 = 4-for-1)")
    position = actions.adjust_for_split(app, position_id, ratio)
    print(f"\n✅ Ajustada: {position.quantity:g} acciones @ {position.buy_price:.2f}")


def _suggestions(app: App, ticker: str) -> None:
    review(app, actions.pending_suggestions(app, ticker))


def quote_line(app: App, ticker: str) -> str:
    """Live price for the header; a data outage must not block the workspace."""
    try:
        return actions.snapshot_text(app.market.quote(ticker))
    except DataUnavailableError as exc:
        return f"⚠️  precio no disponible: {exc}"
