"""Text-based charts and tables for the Linux console."""
from __future__ import annotations

import os
from typing import Sequence

import pandas as pd

MAX_BAR_WIDTH = 40


def clear_screen() -> None:
    os.system("cls" if os.name == "nt" else "clear")  # noqa: S605 - fixed literal command


def draw_bar_chart(values: Sequence[float | None], labels: Sequence[str], title: str, unit: str = "M") -> None:
    """Draw a horizontal bar chart, skipping periods without data."""
    print(f"\n--- 📈 {title} ---")
    pairs = [(lbl, val) for lbl, val in zip(labels, values) if val is not None and val == val]
    if not pairs:
        print("[Sin datos disponibles para graficar]")
        return
    peak = max(abs(v) for _, v in pairs) or 1.0
    for label, value in pairs:
        bar = "█" * int(abs(value) / peak * MAX_BAR_WIDTH)
        sign = "-" if value < 0 else " "
        print(f" {label:<8} | {sign}{bar} ({value:,.2f}{unit})")
    print("-" * (MAX_BAR_WIDTH + 15))


def _as_numeric(series: pd.Series) -> pd.Series:
    """Coerce a column to floats when every present value is numeric.

    Missing metrics are stored as ``None``, which pandas keeps in an object
    column and prints literally as "None"; converting to NaN lets ``na_rep``
    render them as ``n/a`` while real numbers keep their formatting.
    """
    converted = pd.to_numeric(series, errors="coerce")
    present = series.notna()
    if not present.any() or converted[present].notna().all():
        return converted
    return series


def print_table(frame: pd.DataFrame, columns: Sequence[str] | None = None) -> None:
    """Print a DataFrame with missing values shown as ``n/a``."""
    if frame.empty:
        print("[Sin datos]")
        return
    view = frame[list(columns)] if columns else frame
    view = view.apply(_as_numeric)
    print(view.to_string(index=False, na_rep="n/a", float_format=lambda v: f"{v:,.2f}"))
