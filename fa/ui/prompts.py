"""Input helpers for the interactive console."""
from __future__ import annotations

from datetime import date

from fa.errors import ValidationError


def ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{prompt}{suffix}: ").strip()
    return value or default


def ask_float(prompt: str, default: float | None = None) -> float:
    while True:
        raw = ask(prompt, "" if default is None else str(default))
        try:
            return float(raw)
        except ValueError:
            print("⚠️  Ingresá un número válido.")


def ask_int(prompt: str, default: int | None = None) -> int:
    while True:
        raw = ask(prompt, "" if default is None else str(default))
        try:
            return int(raw)
        except ValueError:
            print("⚠️  Ingresá un entero válido.")


def ask_date(prompt: str, default: date | None = None) -> date:
    fallback = (default or date.today()).isoformat()
    while True:
        raw = ask(f"{prompt} (YYYY-MM-DD)", fallback)
        try:
            return date.fromisoformat(raw)
        except ValueError:
            print("⚠️  Formato inválido, usá YYYY-MM-DD.")


def ask_yes_no(prompt: str, default: bool = False) -> bool:
    raw = ask(f"{prompt} (s/n)", "s" if default else "n").lower()
    return raw.startswith(("s", "y"))


def ask_ticker(prompt: str = "Ticker") -> str:
    value = ask(prompt).upper()
    if not value:
        raise ValidationError("El ticker no puede estar vacío.")
    return value


def read_multiline(prompt: str) -> str:
    """Read lines until an empty one is entered."""
    print(prompt)
    lines: list[str] = []
    while True:
        line = input()
        if line == "":
            break
        lines.append(line)
    return "\n".join(lines)
