"""Command line interface. No arguments launches the interactive menu."""
from __future__ import annotations

import argparse
import json
import pathlib
import secrets
import sys
from datetime import date

from fa import actions
from fa.alerts import kinds
from fa.analytics import build_snapshot
from fa.analytics import to_payload as analytics_payload
from fa.app import App, build_app, configure_logging
from fa.config import ANALYSIS_HISTORY_PERIOD
from fa.errors import FinancialAnalyzerError
from fa.localai import LocalAIError
from fa.portfolio import build_portfolio
from fa.store import alerts as alerts_store
from fa.store import events as events_store
from fa.store import meta as meta_store
from fa.store import positions as positions_store
from fa.ui import menu
from fa.ui.suggestions_ui import review
from fa.ui.technicals import render as render_technicals
from fa.ui.views import (
    render_alerts,
    render_check_report,
    render_events,
    render_portfolio,
    render_positions,
    render_suggestions,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="financial_analyzer",
        description="Analizador financiero con datos en vivo, posiciones y alertas persistentes.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="logging detallado")
    sub = parser.add_subparsers(dest="command")

    check = sub.add_parser("check-alerts", help="evalúa las alertas activas y notifica (para cron)")
    check.add_argument("--quiet", action="store_true", help="no imprime las alertas, sólo las envía")
    check.add_argument("--json", action="store_true", help="salida JSON del resumen")
    check.add_argument("--ticker", default=None, help="limita el chequeo a un ticker")
    check.add_argument(
        "--trigger",
        default="manual",
        help="quién disparó la corrida (timer, manual, api); queda en el historial",
    )

    use = sub.add_parser("use", help="fija el ticker activo para los próximos comandos")
    use.add_argument("ticker")
    sub.add_parser("unuse", help="borra el ticker activo")

    analyze = sub.add_parser("analyze", help="métricas reales de un ticker")
    analyze.add_argument("ticker", nargs="?", default=None)
    analyze.add_argument("--period", choices=["annual", "quarterly", "both"], default="both")
    analyze.add_argument("--ai", action="store_true", help="además pide el reporte estratégico a Gemini")
    analyze.add_argument("--context", default="", help="texto externo para cruzar con los datos duros")
    analyze.add_argument("--no-technicals", action="store_true", help="omite el bloque de indicadores")

    tech = sub.add_parser("technicals", help="indicadores técnicos y fuerza relativa vs el índice")
    tech.add_argument("ticker", nargs="?", default=None)
    tech.add_argument("--json", action="store_true", help="salida JSON del snapshot")

    add_pos = sub.add_parser("add-position", help="registra una compra")
    add_pos.add_argument("ticker", nargs="?", default=None)
    add_pos.add_argument("--qty", type=float, required=True)
    add_pos.add_argument("--price", type=float, required=True)
    add_pos.add_argument("--date", default=date.today().isoformat(), help="YYYY-MM-DD")
    add_pos.add_argument("--currency", default="USD")
    add_pos.add_argument("--notes", default="")
    add_pos.add_argument("--with-default-alerts", action="store_true")

    add_alert = sub.add_parser("add-alert", help="crea una alerta")
    add_alert.add_argument("ticker", nargs="?", default=None)
    add_alert.add_argument("--kind", required=True, choices=sorted(kinds.CATALOGUE))
    add_alert.add_argument(
        "--param",
        action="append",
        default=[],
        metavar="CLAVE=VALOR",
        help="parámetro de la alerta; repetible (ej: --param pct=10)",
    )
    add_alert.add_argument("--cooldown", type=int, default=None, help="horas entre repeticiones")
    add_alert.add_argument("--expires", default=None, help="YYYY-MM-DD")
    add_alert.add_argument("--note", default="")

    sub.add_parser("portfolio", help="valuación en vivo del portfolio")
    sub.add_parser("positions", help="lista las posiciones guardadas")

    alerts_cmd = sub.add_parser("alerts", help="lista las alertas guardadas")
    alerts_cmd.add_argument("--active", action="store_true")

    history = sub.add_parser("history", help="alertas disparadas")
    history.add_argument("--limit", type=int, default=20)

    kinds_cmd = sub.add_parser("kinds", help="lista los tipos de alerta disponibles")
    kinds_cmd.add_argument("--json", action="store_true")

    sugg = sub.add_parser("suggestions", help="sugerencias de alertas propuestas por la IA")
    sugg.add_argument("--ticker", default=None)
    sugg.add_argument("--review", action="store_true", help="revisarlas una por una y crearlas")
    sugg.add_argument("--status", default="pending", choices=["pending", "accepted", "rejected", "skipped"])

    dig = sub.add_parser("digest", help="resumen del portfolio escrito por el modelo local")
    dig.add_argument("--facts-only", action="store_true", help="sólo los datos, sin pasar por la IA")

    sub.add_parser("local-ai", help="diagnostica la conexión con el modelo local")

    move = sub.add_parser(
        "migrate-db", help="copia toda la base a otro motor (SQLite → Postgres)"
    )
    move.add_argument("--to", required=True, metavar="URL",
                      help="destino, p.ej. postgresql://usuario:clave@host:5432/base")
    move.add_argument("--force", action="store_true",
                      help="escribe aunque el destino ya tenga datos")

    serve = sub.add_parser("serve", help="levanta la API y el dashboard web")
    serve.add_argument("--host", default="127.0.0.1", help="127.0.0.1 no sale de la máquina")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true", help="recarga al editar (desarrollo)")
    serve.add_argument(
        "--lan",
        action="store_true",
        help="escucha en toda la red local y muestra la URL para el celular",
    )
    serve.add_argument(
        "--insecure",
        action="store_true",
        help="permite --lan sin token (no lo uses en una red compartida)",
    )
    return parser


def _parse_params(pairs: list[str]) -> dict[str, str]:
    params: dict[str, str] = {}
    for pair in pairs:
        if "=" not in pair:
            raise FinancialAnalyzerError(f"Parámetro inválido '{pair}', usá CLAVE=VALOR.")
        key, value = pair.split("=", 1)
        params[key.strip()] = value.strip()
    return params


def _lan_address() -> str:
    """The address this machine answers on in the local network.

    Opening a socket to a public address does not send anything; it just asks
    the routing table which interface would be used, which is the only reliable
    way to pick the right one on a machine with several.
    """
    import socket

    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.0.2.1", 53))  # TEST-NET-1, never routed anywhere
        return probe.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        probe.close()


def _in_container() -> bool:
    """True when this process is inside a container.

    Matters because binding 0.0.0.0 there is not exposure: it is the only way
    to be reachable at all, and what actually decides who can reach it is the
    port mapping on the host, which this process cannot see.
    """
    if pathlib.Path("/.dockerenv").exists():
        return True
    try:
        return "docker" in pathlib.Path("/proc/1/cgroup").read_text()
    except OSError:
        return False


def _cmd_migrate_db(args: argparse.Namespace) -> int:
    """Copy this installation's database into another engine.

    The ids come across unchanged, so every foreign key keeps pointing where it
    pointed. Nothing is deleted from the source: it stays exactly as it was,
    which is what makes this safe to try.
    """
    from fa.config import load_settings
    from fa.store.db import connect
    from fa.store.transfer import copy_all, verify

    settings = load_settings()
    source = connect(settings.database_target)
    print(f"📤 Origen : {settings.database_target}")
    print(f"📥 Destino: {args.to}")
    try:
        target = connect(args.to)
    except Exception as exc:  # noqa: BLE001 - a bad URL is a message, not a traceback
        print(f"\n🚫 No se pudo abrir el destino: {exc}")
        return 2

    def progress(table: str, copied: int, total: int) -> None:
        if total:
            print(f"   {table:24} {copied:>6} filas")

    print("\nCopiando (el origen no se toca):")
    try:
        copy_all(source, target, force=args.force, on_table=progress)
    except ValueError as exc:
        print(f"\n🚫 {exc}")
        return 2

    problems = verify(source, target)
    if problems:
        print("\n🚫 La verificación encontró diferencias:")
        for line in problems:
            print(f"   {line}")
        print("\n   El destino quedó incompleto. No lo uses hasta resolverlo.")
        return 1

    print("\n✅ Todas las tablas coinciden en cantidad de filas.")
    print("   Para usarla, poné en el .env:")
    print(f"     DATABASE_URL={args.to}")
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    """Run the API and the web client on one port.

    Bound to localhost unless asked otherwise. Leaving that requires a token,
    because everything the dashboard shows and everything it can change is
    reachable by anyone who can open the port.
    """
    try:
        import uvicorn
    except ImportError:
        print(
            "⚠️  Falta el servidor web. Instalá:\n"
            '   pip install "fastapi>=0.115" "uvicorn[standard]>=0.32"'
        )
        return 1

    from fa.api.auth import OPEN, mode_for
    from fa.config import load_settings

    settings = load_settings()
    mode = mode_for(settings)
    host = "0.0.0.0" if args.lan else args.host  # noqa: S104 - deliberate, guarded below
    contained = _in_container()
    # Inside a container the bind address says nothing about who can reach the
    # server: the port mapping on the host decides that, and this process
    # cannot see it. Outside, the bind address is exactly the exposure.
    exposed = host not in {"127.0.0.1", "localhost", "::1"} and not contained

    if exposed and mode == OPEN and not args.insecure:
        print(
            "🚫 Sin autenticación no se puede salir de esta máquina.\n"
            "   Cualquiera en la red vería la cartera y podría cargar movimientos.\n\n"
            "   Elegí una:\n"
            f"     1) Token compartido — agregá al .env:  FA_API_TOKEN={secrets.token_urlsafe(24)}\n"
            "     2) Supabase — configurá SUPABASE_URL y SUPABASE_JWT_SECRET\n"
            "     3) Red de confianza y bajo tu responsabilidad:  --lan --insecure"
        )
        return 2

    if exposed and mode == OPEN:
        print("⚠️  Escuchando en toda la red SIN autenticación (--insecure).")
    elif contained and mode == OPEN:
        print(
            "⚠️  Sin autenticación: quién llega depende de cómo compose publique el\n"
            "   puerto. Con 127.0.0.1:8000 sólo esta máquina; si lo abrís a la red,\n"
            "   poné FA_API_TOKEN en el .env primero."
        )

    if contained:
        # The container's own address is meaningless outside it.
        print(f"📊 Dashboard en http://localhost:{args.port} (según el mapeo de compose)")
    else:
        where = _lan_address() if exposed else host
        print(f"📊 Dashboard en http://{where}:{args.port}")
        if exposed:
            print(f"   Abrilo desde el celular en esa misma URL. Modo de acceso: {mode}.")
    print(f"   Acceso: {mode}. Ctrl+C para cortar.")
    # proxy_headers with a trusted-hosts wildcard: behind nginx every request
    # arrives from the proxy, and without this the app sees its address and its
    # scheme instead of the client's. The wildcard is safe only because nothing
    # but the proxy can reach the port — on the server it is not published at
    # all, and locally it is bound to loopback.
    uvicorn.run(
        "fa.api.app:app",
        host=host,
        port=args.port,
        reload=args.reload,
        proxy_headers=True,
        forwarded_allow_ips="*",
    )
    return 0


def _cmd_check(app: App, args: argparse.Namespace) -> int:
    report = actions.check_alerts(
        app,
        args.ticker.upper() if args.ticker else None,
        trigger=getattr(args, "trigger", "manual"),
    )
    if args.json:
        print(
            json.dumps(
                {
                    "run_id": report.run_id,
                    "checked": report.checked,
                    "fired": [
                        {"ticker": s.alert.ticker, "kind": s.alert.kind, "title": s.title, "message": s.message}
                        for s in report.fired
                    ],
                    "skipped_cooldown": report.skipped_cooldown,
                    "expired": report.expired,
                    "errors": list(report.errors),
                },
                ensure_ascii=False,
            )
        )
    elif not args.quiet:
        render_check_report(report)
    return 0 if report.ok else 2


def _resolve_ticker(app: App, given: str | None) -> str:
    """Explicit argument wins; otherwise fall back to the active ticker."""
    if given:
        return given.upper()
    current = meta_store.get_current_ticker(app.conn)
    if not current:
        raise FinancialAnalyzerError(
            "No indicaste ticker y no hay uno activo. Usá 'use TICKER' o pasalo como argumento."
        )
    return current


def _cmd_analyze(app: App, args: argparse.Namespace) -> int:
    ticker = _resolve_ticker(app, args.ticker)
    annual, quarterly, context, source = actions.load_analysis(app, ticker)
    print(actions.snapshot_text(context.quote))
    if not args.no_technicals:
        render_technicals(build_snapshot(context))
    if args.period in {"annual", "both"}:
        actions.show_metrics(annual, ticker, f"COMPARATIVA ANUAL (fuente: {source})", "anual")
    if args.period in {"quarterly", "both"}:
        actions.show_metrics(quarterly, ticker, f"COMPARATIVA TRIMESTRAL (fuente: {source})", "trimestral")
    if args.ai:
        print("\n🤖 Recolectando datos en vivo y consultando al modelo...\n")
        report = actions.run_ai_report(app, ticker, args.context)
        print(f"📊 {report.provenance}\n")
        print(report.text)
        if report.suggestions:
            print(
                f"\n🤖 {len(report.suggestions)} sugerencia(s) guardadas. "
                "Revisalas con: financial_analyzer.py suggestions --review"
            )
    return 0


def _cmd_technicals(app: App, args: argparse.Namespace) -> int:
    ticker = _resolve_ticker(app, args.ticker)
    snapshot = build_snapshot(app.market.context(ticker, period=ANALYSIS_HISTORY_PERIOD))
    if args.json:
        print(json.dumps(analytics_payload(snapshot), indent=2, ensure_ascii=False))
        return 0
    render_technicals(snapshot)
    return 0


def _cmd_add_position(app: App, args: argparse.Namespace) -> int:
    ticker = _resolve_ticker(app, args.ticker)
    position = actions.add_position(
        app,
        ticker,
        args.qty,
        args.price,
        date.fromisoformat(args.date),
        currency=args.currency,
        notes=args.notes,
    )
    print(f"✅ Posición #{position.id}: {position.quantity:g} {position.ticker} @ {position.buy_price:.2f}")
    if args.with_default_alerts:
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
    return 0


def _cmd_add_alert(app: App, args: argparse.Namespace) -> int:
    alert = actions.add_alert(
        app,
        _resolve_ticker(app, args.ticker),
        args.kind,
        _parse_params(args.param),
        cooldown_hours=args.cooldown,
        expires_at=date.fromisoformat(args.expires) if args.expires else None,
        note=args.note,
    )
    print(f"✅ Alerta #{alert.id} para {alert.ticker}: {alert.kind} {dict(alert.params)}")
    return 0


def _cmd_kinds(args: argparse.Namespace) -> int:
    catalogue = [
        {
            "kind": k.key,
            "label": k.label,
            "description": k.description,
            "defaults": dict(k.defaults),
            "requires_position": k.requires_position,
        }
        for k in kinds.CATALOGUE.values()
    ]
    if args.json:
        print(json.dumps(catalogue, ensure_ascii=False, indent=2))
        return 0
    for item in catalogue:
        needs = " (necesita posición)" if item["requires_position"] else ""
        print(f"{item['kind']:<16} {item['label']}{needs}")
        print(f"    {item['description']}")
        print(f"    defaults: {item['defaults']}")
    return 0


def _cmd_suggestions(app: App, args: argparse.Namespace) -> int:
    from fa.store import (
        suggestions as suggestions_store,  # noqa: PLC0415 - keeps startup light
    )

    ticker = args.ticker.upper() if args.ticker else None
    items = suggestions_store.list_suggestions(app.conn, ticker=ticker, status=args.status)
    if args.review:
        review(app, [s for s in items if s.status == suggestions_store.PENDING])
        return 0
    render_suggestions(items)
    return 0


def _cmd_digest(app: App, args: argparse.Namespace) -> int:
    if args.facts_only:
        print(actions.digest_facts(app))
        return 0
    facts, prose, error = actions.run_digest(app)
    if prose:
        print(prose)
        return 0
    print(f"⚠️  El modelo local no redactó el resumen: {error}", file=sys.stderr)
    print(facts)
    return 2


def _cmd_local_ai(app: App) -> int:
    client = app.local_ai
    print(f"URL:    {client.base_url}")
    print(f"Modelo: {client.model or '(no configurado: FA_LOCAL_AI_MODEL)'}")
    try:
        models = client.ping()
    except LocalAIError as exc:
        print(f"⚠️  {exc}", file=sys.stderr)
        return 2
    print(f"Modelos disponibles: {', '.join(models) or '(ninguno)'}")
    if client.model and client.model not in models:
        print(f"⚠️  El modelo configurado '{client.model}' no está en la lista.", file=sys.stderr)
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging(args.verbose)

    if args.command == "kinds":  # does not need providers or the database
        return _cmd_kinds(args)
    if args.command == "migrate-db":
        return _cmd_migrate_db(args)
    if args.command == "serve":
        # The server opens its own database when it starts, and it must not
        # inherit a connection that this process would close on the way out.
        return _cmd_serve(args)

    # JSON output must stay machine readable, so the console channel keeps quiet.
    echo = not (getattr(args, "quiet", False) or getattr(args, "json", False))
    try:
        with build_app(echo_notifications=echo) as app:
            if args.command is None:
                menu.run(app)
                return 0
            if args.command == "check-alerts":
                return _cmd_check(app, args)
            if args.command == "use":
                print(f"📌 Ticker activo: {meta_store.set_current_ticker(app.conn, args.ticker)}")
                return 0
            if args.command == "unuse":
                meta_store.clear_current_ticker(app.conn)
                print("📌 Ticker activo borrado.")
                return 0
            if args.command == "suggestions":
                return _cmd_suggestions(app, args)
            if args.command == "digest":
                return _cmd_digest(app, args)
            if args.command == "local-ai":
                return _cmd_local_ai(app)
            if args.command == "analyze":
                return _cmd_analyze(app, args)
            if args.command == "technicals":
                return _cmd_technicals(app, args)
            if args.command == "add-position":
                return _cmd_add_position(app, args)
            if args.command == "add-alert":
                return _cmd_add_alert(app, args)
            if args.command == "portfolio":
                render_portfolio(build_portfolio(app.conn, app.market))
                return 0
            if args.command == "positions":
                render_positions(positions_store.list_positions(app.conn))
                return 0
            if args.command == "alerts":
                render_alerts(app.conn, alerts_store.list_alerts(app.conn, only_active=args.active))
                return 0
            if args.command == "history":
                render_events(events_store.recent_events(app.conn, limit=args.limit))
                return 0
    except FinancialAnalyzerError as exc:
        print(f"⚠️  {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrumpido.")
        return 130
    return 0
