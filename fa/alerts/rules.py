"""Pure alert rules: given an alert, a market context and a position, fire or not.

Every rule is side-effect free so it can be unit tested without network or DB.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Callable, Mapping

from fa.alerts import kinds
from fa.indicators import detect_cross, drawdown_pct, rsi
from fa.models import Alert, MarketContext, Position, Signal

RuleFn = Callable[[Alert, MarketContext, Position | None], Signal | None]

_MONTH_DAYS = 30.44


def _reference_price(alert: Alert, position: Position | None) -> float | None:
    if alert.params.get("reference") == "baseline" or position is None:
        return alert.params.get("baseline_price")
    return position.buy_price


def _signal(alert: Alert, title: str, message: str, payload: Mapping[str, object], severity: str = "info") -> Signal:
    return Signal(alert=alert, title=title, message=message, payload=payload, severity=severity)


def pct_move(alert: Alert, ctx: MarketContext, position: Position | None) -> Signal | None:
    reference = _reference_price(alert, position)
    if not reference:
        return None
    threshold = float(alert.params["pct"])
    change = (ctx.quote.price - reference) / reference * 100.0
    going_up = alert.kind == kinds.PCT_UP
    if (going_up and change < threshold) or (not going_up and change > -threshold):
        return None
    arrow = "📈" if going_up else "📉"
    return _signal(
        alert,
        f"{arrow} {ctx.ticker} {change:+.2f}% vs {reference:.2f}",
        f"{ctx.ticker} cotiza {ctx.quote.price:.2f} {ctx.quote.currency} "
        f"({change:+.2f}% contra la referencia {reference:.2f}; umbral {threshold:.2f}%).",
        {"price": ctx.quote.price, "reference": reference, "change_pct": round(change, 4)},
        severity="warning" if not going_up else "info",
    )


def price_target(alert: Alert, ctx: MarketContext, position: Position | None) -> Signal | None:
    target = float(alert.params["price"])
    above = alert.kind == kinds.PRICE_ABOVE
    price = ctx.quote.price
    if (above and price < target) or (not above and price > target):
        return None
    direction = "superó" if above else "perforó"
    return _signal(
        alert,
        f"🎯 {ctx.ticker} {direction} {target:.2f}",
        f"{ctx.ticker} {direction} el objetivo {target:.2f}: precio actual {price:.2f} {ctx.quote.currency}.",
        {"price": price, "target": target},
        severity="warning" if not above else "info",
    )


def period_elapsed(alert: Alert, ctx: MarketContext, position: Position | None) -> Signal | None:
    if position is None:
        return None
    months = alert.params.get("months")
    days = int(alert.params.get("days") or 0) or int(round(float(months or 0) * _MONTH_DAYS))
    if days <= 0:
        return None
    due = position.buy_date + timedelta(days=days)
    today = ctx.evaluated_at.date() if ctx.evaluated_at else date.today()
    if today < due:
        return None
    pnl_abs, pnl_pct = position.unrealized(ctx.quote.price)
    span = f"{months:g} meses" if months else f"{days} días"
    return _signal(
        alert,
        f"⏳ {ctx.ticker}: se cumplieron {span} desde la compra",
        f"Compraste {ctx.ticker} el {position.buy_date} a {position.buy_price:.2f}. "
        f"Hoy vale {ctx.quote.price:.2f} ({pnl_pct:+.2f}%, {pnl_abs:+.2f} {position.currency}). "
        "Toca revisar la tesis.",
        {"due_date": due.isoformat(), "pnl_pct": round(pnl_pct, 4), "pnl_abs": round(pnl_abs, 4)},
    )


def earnings_near(alert: Alert, ctx: MarketContext, position: Position | None) -> Signal | None:
    if ctx.next_earnings is None:
        return None
    window = int(alert.params.get("days", 7))
    today = ctx.evaluated_at.date() if ctx.evaluated_at else date.today()
    remaining = (ctx.next_earnings - today).days
    if remaining < 0 or remaining > window:
        return None
    return _signal(
        alert,
        f"📅 {ctx.ticker}: earnings en {remaining} día(s)",
        f"{ctx.ticker} reporta el {ctx.next_earnings} (faltan {remaining} días). "
        f"Precio actual {ctx.quote.price:.2f} {ctx.quote.currency}.",
        {"earnings_date": ctx.next_earnings.isoformat(), "days_ahead": remaining},
        severity="warning",
    )


def trailing_stop(alert: Alert, ctx: MarketContext, position: Position | None) -> Signal | None:
    if position is None:
        return None
    peak = ctx.max_close_since(position.buy_date) or alert.params.get("peak")
    peak = max(float(peak or 0.0), position.buy_price, ctx.quote.price)
    threshold = float(alert.params["pct"])
    drop = drawdown_pct(ctx.quote.price, peak)
    if drop < threshold:
        return None
    pnl_abs, pnl_pct = position.unrealized(ctx.quote.price)
    return _signal(
        alert,
        f"🛑 {ctx.ticker} cayó {drop:.2f}% desde el máximo",
        f"{ctx.ticker} está {drop:.2f}% abajo del máximo {peak:.2f} alcanzado desde la compra "
        f"(umbral {threshold:.2f}%). Precio {ctx.quote.price:.2f}, P&L {pnl_pct:+.2f}%.",
        {"peak": peak, "drop_pct": round(drop, 4), "pnl_pct": round(pnl_pct, 4)},
        severity="critical",
    )


def sma_cross(alert: Alert, ctx: MarketContext, position: Position | None) -> Signal | None:
    fast = int(alert.params.get("fast", 50))
    slow = int(alert.params.get("slow", 200))
    wanted = str(alert.params.get("direction", "any"))
    cross = detect_cross(ctx.closes, fast, slow)
    if cross is None or (wanted != "any" and cross != wanted):
        return None
    label = "Golden cross ✨" if cross == "golden" else "Death cross ☠️"
    return _signal(
        alert,
        f"{label} en {ctx.ticker} (SMA{fast}/SMA{slow})",
        f"{ctx.ticker}: la SMA{fast} cruzó {'arriba' if cross == 'golden' else 'abajo'} de la SMA{slow} "
        f"en la última rueda. Precio {ctx.quote.price:.2f} {ctx.quote.currency}.",
        {"cross": cross, "fast": fast, "slow": slow},
        severity="warning" if cross == "death" else "info",
    )


def rsi_bounds(alert: Alert, ctx: MarketContext, position: Position | None) -> Signal | None:
    period = int(alert.params.get("period", 14))
    value = rsi(ctx.closes, period)
    if value is None:
        return None
    overbought = float(alert.params.get("overbought", 70))
    oversold = float(alert.params.get("oversold", 30))
    if value >= overbought:
        state, severity = "sobrecompra", "warning"
    elif value <= oversold:
        state, severity = "sobreventa", "info"
    else:
        return None
    return _signal(
        alert,
        f"📊 {ctx.ticker} RSI {value:.1f} ({state})",
        f"{ctx.ticker} tiene RSI({period}) = {value:.1f} → {state}. "
        f"Precio {ctx.quote.price:.2f} {ctx.quote.currency}.",
        {"rsi": round(value, 2), "period": period, "state": state},
        severity=severity,
    )


def dividend_ex_near(alert: Alert, ctx: MarketContext, position: Position | None) -> Signal | None:
    if ctx.next_ex_dividend is None:
        return None
    window = int(alert.params.get("days", 7))
    today = ctx.evaluated_at.date() if ctx.evaluated_at else date.today()
    remaining = (ctx.next_ex_dividend - today).days
    if remaining < 0 or remaining > window:
        return None
    return _signal(
        alert,
        f"💰 {ctx.ticker}: ex-dividend en {remaining} día(s)",
        f"{ctx.ticker} corta dividendo el {ctx.next_ex_dividend} (faltan {remaining} días).",
        {"ex_dividend_date": ctx.next_ex_dividend.isoformat(), "days_ahead": remaining},
    )


def split_detected(alert: Alert, ctx: MarketContext, position: Position | None) -> Signal | None:
    if position is None or not ctx.recent_splits:
        return None
    after_buy = [s for s in ctx.recent_splits if s.event_date >= position.buy_date]
    if not after_buy:
        return None
    latest = max(after_buy, key=lambda s: s.event_date)
    adjusted_price = position.buy_price / latest.value if latest.value else position.buy_price
    adjusted_qty = position.quantity * latest.value
    return _signal(
        alert,
        f"✂️ {ctx.ticker}: split {latest.value:g}:1 el {latest.event_date}",
        f"{ctx.ticker} hizo un split {latest.value:g}:1 el {latest.event_date}, posterior a tu compra. "
        f"Costo ajustado sugerido: {adjusted_price:.2f} x {adjusted_qty:g} acciones "
        "(usá 'ajustar por split' para corregir la posición).",
        {
            "ratio": latest.value,
            "split_date": latest.event_date.isoformat(),
            "adjusted_buy_price": round(adjusted_price, 4),
            "adjusted_quantity": adjusted_qty,
        },
        severity="critical",
    )


REGISTRY: dict[str, RuleFn] = {
    kinds.PCT_UP: pct_move,
    kinds.PCT_DOWN: pct_move,
    kinds.PRICE_ABOVE: price_target,
    kinds.PRICE_BELOW: price_target,
    kinds.PERIOD_ELAPSED: period_elapsed,
    kinds.EARNINGS_NEAR: earnings_near,
    kinds.TRAILING_STOP: trailing_stop,
    kinds.SMA_CROSS: sma_cross,
    kinds.RSI: rsi_bounds,
    kinds.DIVIDEND_EX_NEAR: dividend_ex_near,
    kinds.SPLIT_DETECTED: split_detected,
}


def evaluate(alert: Alert, ctx: MarketContext, position: Position | None) -> Signal | None:
    """Run the rule matching ``alert.kind``; unknown kinds never fire."""
    rule = REGISTRY.get(alert.kind)
    if rule is None:
        return None
    return rule(alert, ctx, position)
