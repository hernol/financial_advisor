"""Interactive review of the alert suggestions returned by the AI report."""
from __future__ import annotations

from typing import Sequence

from fa import actions
from fa.app import App
from fa.errors import FinancialAnalyzerError
from fa.models import Suggestion
from fa.store import suggestions as suggestions_store
from fa.ui.prompts import ask, ask_yes_no

PRIORITY_MARK = {"high": "🔴", "medium": "🟡", "low": "🟢"}


def render_suggestion(suggestion: Suggestion, index: int, total: int) -> None:
    mark = PRIORITY_MARK.get(suggestion.priority, "•")
    tag = "ALERTA" if suggestion.category == "alert" else "ACCIÓN"
    print(f"\n[{index}/{total}] {mark} {tag} — {suggestion.ticker}: {suggestion.headline}")
    if suggestion.rationale:
        print(f"      motivo: {suggestion.rationale}")


def review(app: App, suggestions: Sequence[Suggestion]) -> tuple[int, int]:
    """Walk the suggestions one by one. Returns (creadas, descartadas)."""
    if not suggestions:
        print("\n(No hay sugerencias pendientes)")
        return 0, 0

    print("\n--- 🤖 SUGERENCIAS DE LA IA ---")
    print("Para cada una: (s) crear, (n) descartar, (e) editar parámetros, (ENTER) dejar pendiente, (q) salir")
    created = discarded = 0
    total = len(suggestions)
    for index, suggestion in enumerate(suggestions, start=1):
        render_suggestion(suggestion, index, total)
        if suggestion.category != "alert":
            # Actions are advice for the user, not something the app can automate.
            choice = ask("      (n) descartar / (ENTER) dejar pendiente", "")
            if choice.lower().startswith("n"):
                actions.reject_suggestion(app, suggestion)
                discarded += 1
            continue
        choice = ask("      ¿Crear esta alerta? [s/n/e/ENTER/q]", "").lower()
        if choice.startswith("q"):
            break
        if choice.startswith("n"):
            actions.reject_suggestion(app, suggestion)
            discarded += 1
            continue
        if not choice.startswith(("s", "e", "y")):
            continue
        overrides = _edit(suggestion) if choice.startswith("e") else None
        try:
            alert = actions.accept_suggestion(app, suggestion, overrides)
        except FinancialAnalyzerError as exc:
            print(f"      ⚠️  {exc}")
            if ask_yes_no("      ¿Descartarla?", default=False):
                actions.reject_suggestion(app, suggestion, status=suggestions_store.SKIPPED)
                discarded += 1
            continue
        created += 1
        print(f"      ✅ alerta #{alert.id} creada: {alert.kind} {dict(alert.params)}")
    print(f"\nResumen: {created} alerta(s) creada(s), {discarded} descartada(s).")
    return created, discarded


def _edit(suggestion: Suggestion) -> dict[str, str]:
    """Let the user tweak each parameter before the alert is created."""
    overrides: dict[str, str] = {}
    for name, value in suggestion.params.items():
        raw = ask(f"      {name}", str(value))
        if raw != str(value):
            overrides[name] = raw
    return overrides
