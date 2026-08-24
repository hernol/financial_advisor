"""Console rendering for the technical snapshot."""
from __future__ import annotations

from fa.analytics import TechnicalSnapshot

_NA = "n/a"


def _fmt(value: float | None, suffix: str = "", digits: int = 2) -> str:
    if value is None:
        return _NA
    return f"{value:,.{digits}f}{suffix}"


def _signed(value: float | None, suffix: str = "%") -> str:
    if value is None:
        return _NA
    return f"{value:+,.2f}{suffix}"


def _mark(value: float | None) -> str:
    """Colourless up/down marker so the output stays readable when piped."""
    if value is None:
        return " "
    return "▲" if value >= 0 else "▼"


def render(snapshot: TechnicalSnapshot) -> None:
    """Print the whole indicator set, showing gaps instead of hiding them."""
    returns = snapshot.returns or {}
    strength = snapshot.relative_strength or {}
    print(f"\n📐 TÉCNICOS — {snapshot.ticker} ({snapshot.sessions} ruedas)")
    print("─" * 62)
    print(
        f"  Tendencia      {snapshot.trend:<10} "
        f"precio vs SMA200 {_signed(snapshot.vs_sma_slow_pct)} {_mark(snapshot.vs_sma_slow_pct)}"
    )
    print(f"  SMA50/SMA200   {_fmt(snapshot.sma_fast)} / {_fmt(snapshot.sma_slow)}")
    print(
        f"  RSI(14)        {_fmt(snapshot.rsi)}      "
        f"%B Bollinger {_fmt(snapshot.percent_b)}      "
        f"MACD {snapshot.macd_state or 'sin cruce'}"
    )
    print(
        f"  Volatilidad    {_fmt(snapshot.volatility_pct, '%')} anual   "
        f"ATR(14) {_fmt(snapshot.atr)} ({_fmt(snapshot.atr_pct, '%')})"
    )
    print(
        f"  Rango 52s      {_fmt(snapshot.low_52w)} – {_fmt(snapshot.high_52w)}   "
        f"desde máx {_signed(snapshot.from_high_pct)}   sobre mín {_signed(snapshot.from_low_pct)}"
    )
    print(
        f"  Drawdown máx   {_fmt(snapshot.max_drawdown_pct, '%')}    "
        f"volumen vs 20d {_fmt(snapshot.volume_ratio, 'x')}"
    )
    print("─" * 62)
    windows = ("1m", "3m", "6m", "12m")
    header = "  ".join(f"{w:>9}" for w in windows)
    print(f"  {'Retorno':<14}{header}")
    print(f"  {'':<14}" + "  ".join(f"{_signed(returns.get(w)):>9}" for w in windows))
    print(f"  {'vs ' + snapshot.benchmark:<14}" + "  ".join(f"{_signed(strength.get(w), 'p'):>9}" for w in windows))
    if snapshot.beats_benchmark is not None:
        verdict = "le gana a" if snapshot.beats_benchmark else "pierde contra"
        print(f"\n  En 12 meses {snapshot.ticker} {verdict} {snapshot.benchmark}.")
    gaps = snapshot.missing()
    if gaps:
        print("\n  Sin datos para: " + "; ".join(gaps))
