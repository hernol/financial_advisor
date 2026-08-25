"""Interactive console: a general menu plus a per-ticker workspace."""
from __future__ import annotations

from fa import actions
from fa.app import App
from fa.errors import FinancialAnalyzerError
from fa.portfolio import build_portfolio
from fa.store import alerts as alerts_store
from fa.store import events as events_store
from fa.store import meta as meta_store
from fa.store import positions as positions_store
from fa.store import suggestions as suggestions_store
from fa.ui import ticker_menu
from fa.ui.charts import clear_screen
from fa.ui.prompts import ask, ask_date, ask_float, ask_int, ask_ticker, ask_yes_no
from fa.ui.suggestions_ui import review
from fa.ui.views import (
    SEPARATOR,
    render_alerts,
    render_check_report,
    render_events,
    render_portfolio,
    render_positions,
)

GENERAL_MENU = """
--- MENÚ GENERAL ---
 1. Trabajar sobre un ticker        5. Chequear todas las alertas ahora
 2. Portfolio (P&L en vivo)         6. Historial de alertas disparadas
 3. Ver / cerrar posiciones         7. Digest del portfolio (IA local)
 4. Ver / borrar alertas            8. Sugerencias pendientes de la IA
 0. Salir
"""


def run(app: App) -> None:
    """Main loop. Resumes on the ticker that was active in the last session."""
    clear_screen()
    _banner(app)
    _show_pending_events(app)

    while True:
        try:
            ticker = meta_store.get_current_ticker(app.conn)
            keep_going = _ticker_loop(app, ticker) if ticker else _general_step(app)
            if not keep_going:
                print("\nSaliendo. ¡Suerte con el portfolio!")
                return
        except FinancialAnalyzerError as exc:
            print(f"\n⚠️  {exc}")
        except (KeyboardInterrupt, EOFError):
            print("\nSaliendo.")
            return


def _banner(app: App) -> None:
    print(SEPARATOR)
    print("📈 FINANCIAL ANALYZER — datos en vivo + alertas persistentes 📉")
    print(SEPARATOR)
    print(f"Proveedores activos: {', '.join(app.market.providers) or 'ninguno'}")
    print(f"Canales de aviso:    {', '.join(app.dispatcher.names)}")
    local = app.settings.local_ai_model or "no configurada"
    print(f"IA local:            {local} ({app.settings.local_ai_url})")
    print(f"Base de datos:       {app.settings.db_path}")
    pending = suggestions_store.pending_count(app.conn)
    if pending:
        print(f"🤖 Tenés {pending} sugerencia(s) de la IA sin revisar (opción 8).")


def _show_pending_events(app: App) -> None:
    pending = events_store.recent_events(app.conn, limit=10, unacknowledged_only=True)
    if not pending:
        return
    render_events(pending)
    if ask_yes_no("¿Marcar como vistas?", default=True):
        events_store.acknowledge_all(app.conn)


def _ticker_loop(app: App, ticker: str) -> bool:
    """Workspace for one symbol. Returns False when the user wants to quit."""
    ticker_menu.header(app, ticker)
    print(ticker_menu.quote_line(app, ticker))
    print(ticker_menu.MENU)
    signal = ticker_menu.dispatch(app, ticker, ask("Elegí una opción", "x"))
    if signal == "quit":
        return False
    if signal == "leave":
        meta_store.clear_current_ticker(app.conn)
        print(f"\n↩️  Saliste de {ticker}. Volvés al menú general.")
    elif signal == "change":
        _select_ticker(app)
    return True


def _general_step(app: App) -> bool:
    print(GENERAL_MENU)
    choice = ask("Elegí una opción", "0").strip()
    if choice == "0":
        return False
    handlers = {
        "1": _select_ticker,
        "2": _portfolio,
        "3": _manage_positions,
        "4": _manage_alerts,
        "5": _check_all,
        "6": _history,
        "7": _digest,
        "8": _suggestions,
    }
    handler = handlers.get(choice)
    if handler is None:
        print("\n⚠️  Opción inválida.")
        return True
    handler(app)
    return True


def _select_ticker(app: App) -> None:
    """Set the working ticker; it persists across runs until cleared."""
    known = positions_store.tracked_tickers(app.conn)
    if known:
        print(f"\nConocidos: {', '.join(known)}")
    ticker = meta_store.set_current_ticker(app.conn, ask_ticker("Ticker a trabajar"))
    print(f"\n📌 Ticker activo: {ticker} (salís con 'x' desde su menú)")


def _portfolio(app: App) -> None:
    print("\n📥 Consultando precios en vivo...")
    render_portfolio(build_portfolio(app.conn, app.market))


def _manage_positions(app: App) -> None:
    render_positions(positions_store.list_positions(app.conn))
    action = ask("(c)errar posición / (b)orrar / ENTER para volver", "")
    if not action:
        return
    position_id = ask_int("ID")
    if action.startswith("c"):
        closed = _close_with_sale(app, position_id)
        ok = closed is not None
        if closed is not None and closed.realized_pnl is not None:
            print(f"   P&L realizado: {closed.realized_pnl:+.2f} {closed.currency}")
    elif action.startswith("b"):
        ok = positions_store.delete_position(app.conn, position_id)
    else:
        return
    print("✅ Listo." if ok else "⚠️  No encontrada.")


def _close_with_sale(app: App, position_id: int):
    """Ask for the sale so the ledger records a real exit, not just a flag.

    Leaving the price empty archives the position without inventing a number:
    the realised P&L stays unknown instead of wrong.
    """
    raw = ask("Precio de venta (ENTER para cerrar sin precio)", "")
    if not raw:
        return positions_store.close_position(app.conn, position_id)
    try:
        price = float(raw)
    except ValueError:
        print("⚠️  Precio inválido, cierro sin registrar la venta.")
        return positions_store.close_position(app.conn, position_id)
    sold_on = ask_date("Fecha de venta")
    fees = ask_float("Comisiones", 0.0)
    return positions_store.close_position(
        app.conn, position_id, price=price, close_date=sold_on, fees=fees
    )


def _manage_alerts(app: App) -> None:
    render_alerts(app.conn, alerts_store.list_alerts(app.conn))
    action = ask("(d)esactivar / (a)ctivar / (b)orrar / ENTER para volver", "")
    if not action:
        return
    alert_id = ask_int("ID de la alerta")
    if action.startswith("b"):
        ok = alerts_store.delete_alert(app.conn, alert_id)
    else:
        ok = alerts_store.set_active(app.conn, alert_id, active=action.startswith("a"))
    print("✅ Listo." if ok else "⚠️  No encontrada.")


def _check_all(app: App) -> None:
    print("\n🔎 Evaluando alertas contra datos en vivo...")
    render_check_report(actions.check_alerts(app))


def _history(app: App) -> None:
    render_events(events_store.recent_events(app.conn, limit=20))


def _digest(app: App) -> None:
    print("\n📝 Juntando datos y pidiéndole el resumen al modelo local...")
    facts, prose, error = actions.run_digest(app)
    clear_screen()
    if prose:
        print(f"{SEPARATOR}\n🧾 DIGEST DEL PORTFOLIO\n{SEPARATOR}")
        print(prose)
    else:
        print(f"⚠️  El modelo local no redactó el resumen: {error}")
        print("   (la primera llamada también carga el modelo: puede tardar más que el timeout)")
    print(f"\n--- DATOS USADOS ---\n{facts}")


def _suggestions(app: App) -> None:
    review(app, actions.pending_suggestions(app))
