"""Rules driven by the derived technical indicators.

Kept apart from the price/date rules so each file stays small, and because
every rule here needs data the providers may not supply (index history, OHLC
bars, volume). When that data is missing the rule returns ``None`` instead of
firing on an assumption.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, timedelta

from fa import indicators
from fa.alerts import kinds
from fa.indicators.risk import daily_returns, stdev
from fa.models import Alert, MarketContext, Position, Signal


def _signal(
    alert: Alert,
    title: str,
    message: str,
    payload: Mapping[str, object],
    severity: str = "info",
) -> Signal:
    return Signal(alert=alert, title=title, message=message, payload=payload, severity=severity)


def rel_strength(alert: Alert, ctx: MarketContext, position: Position | None) -> Signal | None:
    """Fire when the ticker lags its benchmark by more than ``pct`` points."""
    window = str(alert.params.get("window", "3m"))
    sessions = indicators.SESSIONS.get(window)
    if sessions is None:
        return None
    excess = indicators.relative_strength(ctx.closes, ctx.benchmark_closes, sessions)
    if excess is None:
        return None
    threshold = float(alert.params["pct"])
    if excess > -threshold:
        return None
    own = indicators.return_pct(ctx.closes, sessions)
    index = indicators.return_pct(ctx.benchmark_closes, sessions)
    benchmark = ctx.benchmark_ticker or "el índice"
    return _signal(
        alert,
        f"🐌 {ctx.ticker} pierde {abs(excess):.2f} pts contra {benchmark} ({window})",
        f"{ctx.ticker} rindió {own:+.2f}% en {window} contra {index:+.2f}% de {benchmark}: "
        f"{excess:+.2f} puntos de diferencia (umbral {threshold:.2f}). "
        f"Precio {ctx.quote.price:.2f} {ctx.quote.currency}.",
        {
            "window": window,
            "excess_pct": round(excess, 4),
            "ticker_return_pct": round(own, 4),
            "benchmark_return_pct": round(index, 4),
            "benchmark": benchmark,
        },
        severity="warning",
    )


def atr_stop(alert: Alert, ctx: MarketContext, position: Position | None) -> Signal | None:
    """Volatility-sized trailing stop: peak minus ``multiple`` x ATR."""
    if position is None:
        return None
    period = int(alert.params.get("period", 14))
    average_range = indicators.atr(ctx.highs, ctx.lows, ctx.closes, period)
    if average_range is None:
        return None
    multiple = float(alert.params["multiple"])
    # An ATR measured on today's volatility against a peak from years ago is not a
    # stop, it is just "you are far down". lookback_days keeps both on one timescale.
    lookback = int(alert.params.get("lookback_days", 0) or 0)
    today = ctx.evaluated_at.date() if ctx.evaluated_at else date.today()
    since = max(position.buy_date, today - timedelta(days=lookback)) if lookback else position.buy_date
    peak = ctx.max_close_since(since) or position.buy_price
    peak = max(float(peak), ctx.quote.price) if lookback else max(float(peak), position.buy_price, ctx.quote.price)
    stop = peak - multiple * average_range
    if ctx.quote.price > stop:
        return None
    pnl_abs, pnl_pct = position.unrealized(ctx.quote.price)
    return _signal(
        alert,
        f"🛑 {ctx.ticker} perforó el stop por ATR ({multiple:g}x)",
        f"{ctx.ticker} cotiza {ctx.quote.price:.2f} y el stop está en {stop:.2f} "
        f"(máximo {peak:.2f} menos {multiple:g} x ATR{period} de {average_range:.2f}). "
        f"P&L {pnl_pct:+.2f}% ({pnl_abs:+.2f} {position.currency}).",
        {
            "stop": round(stop, 4),
            "peak": round(peak, 4),
            "atr": round(average_range, 4),
            "multiple": multiple,
            "peak_since": since.isoformat(),
            "pnl_pct": round(pnl_pct, 4),
        },
        severity="critical",
    )


def volume_spike(alert: Alert, ctx: MarketContext, position: Position | None) -> Signal | None:
    period = int(alert.params.get("period", 20))
    ratio = indicators.volume_ratio(ctx.completed_volumes(), period)
    if ratio is None:
        return None
    threshold = float(alert.params["ratio"])
    if ratio < threshold:
        return None
    return _signal(
        alert,
        f"📢 {ctx.ticker}: volumen {ratio:.1f}x el promedio",
        f"{ctx.ticker} negoció {ratio:.2f} veces su volumen promedio de {period} ruedas "
        f"(umbral {threshold:.2f}x). Suele anticipar noticias. "
        f"Precio {ctx.quote.price:.2f} {ctx.quote.currency}.",
        {"volume_ratio": round(ratio, 4), "period": period},
        severity="warning",
    )


def fifty_two_week(alert: Alert, ctx: MarketContext, position: Position | None) -> Signal | None:
    """Fire on a new 52-week extreme, ignoring today's own bar."""
    band = indicators.window_extremes(ctx.closes[:-1], ctx.highs[:-1], ctx.lows[:-1])
    if band is None:
        return None
    tolerance = float(alert.params.get("tolerance_pct", 0.0)) / 100.0
    price = ctx.quote.price
    hitting_high = alert.kind == kinds.NEW_52W_HIGH
    if hitting_high:
        reference = band["high"] * (1.0 - tolerance)
        if price < reference:
            return None
        return _signal(
            alert,
            f"🚀 {ctx.ticker}: nuevo máximo de 52 semanas",
            f"{ctx.ticker} cotiza {price:.2f} {ctx.quote.currency} y supera el máximo "
            f"de 52 semanas ({band['high']:.2f}).",
            {"price": price, "previous_high": round(band["high"], 4)},
        )
    reference = band["low"] * (1.0 + tolerance)
    if price > reference:
        return None
    return _signal(
        alert,
        f"🕳️ {ctx.ticker}: nuevo mínimo de 52 semanas",
        f"{ctx.ticker} cotiza {price:.2f} {ctx.quote.currency} y perfora el mínimo "
        f"de 52 semanas ({band['low']:.2f}).",
        {"price": price, "previous_low": round(band["low"], 4)},
        severity="critical",
    )


def sma_break(alert: Alert, ctx: MarketContext, position: Position | None) -> Signal | None:
    """Trend filter: fire only on the session the price crosses its average."""
    period = int(alert.params.get("period", 200))
    average = indicators.sma(ctx.closes, period)
    previous_average = indicators.sma(ctx.closes[:-1], period)
    if average is None or previous_average is None or len(ctx.closes) < 2:
        return None
    wanted = str(alert.params.get("direction", "below"))
    previous_close = ctx.closes[-2]
    price = ctx.quote.price
    crossed_down = previous_close >= previous_average and price < average
    crossed_up = previous_close <= previous_average and price > average
    if wanted == "below" and not crossed_down:
        return None
    if wanted == "above" and not crossed_up:
        return None
    distance = indicators.distance_pct(price, average) or 0.0
    going_down = wanted == "below"
    arrow = "📉" if going_down else "📈"
    verb = "perforó" if going_down else "recuperó"
    return _signal(
        alert,
        f"{arrow} {ctx.ticker} {verb} la SMA{period}",
        f"{ctx.ticker} {verb} su media de {period} ruedas ({average:.2f}): "
        f"precio {price:.2f} {ctx.quote.currency} ({distance:+.2f}%).",
        {"sma": round(average, 4), "period": period, "distance_pct": round(distance, 4)},
        severity="warning" if going_down else "info",
    )


# Roughly the 25th percentile of 839 real crosses across ten tickers and five
# years, so the quarter that barely separated stops reporting itself as an
# event. Configurable per alert; 0 restores the old behaviour of firing on any
# cross at all.
DEFAULT_MACD_STRENGTH = 0.02


def _cross_strength(closes: Sequence[float], histogram: float) -> float | None:
    """How far the lines parted, in units of the ticker's own daily move.

    ``None`` when there is not enough history to know what a normal day looks
    like for this ticker — in which case the caller lets the cross through
    rather than silently swallowing it on a measurement it could not take.
    """
    sample = closes[-91:]
    deviation = stdev(daily_returns(sample))
    if not deviation or not closes:
        return None
    daily_move = deviation * closes[-1]
    if not daily_move:
        return None
    return abs(histogram) / daily_move


def macd_cross(alert: Alert, ctx: MarketContext, position: Position | None) -> Signal | None:
    fast = int(alert.params.get("fast", 12))
    slow = int(alert.params.get("slow", 26))
    signal_period = int(alert.params.get("signal", 9))
    cross = indicators.macd_cross(ctx.closes, fast, slow, signal_period)
    if cross is None:
        return None
    wanted = str(alert.params.get("direction", "any"))
    if wanted == "above" and cross != "bullish":
        return None
    if wanted == "below" and cross != "bearish":
        return None
    values = indicators.macd(ctx.closes, fast, slow, signal_period)
    histogram = values[2] if values else 0.0

    # A cross is only news when the lines actually separate. The histogram is
    # zero *at* the crossing by definition, so its size on the day says how
    # steeply they crossed — and a nearly-flat one is two lines grazing, which
    # can reverse tomorrow without anything having happened. PLTR fired on a
    # histogram of 0.0031 against a 185 price: the bottom 2% of five years of
    # crosses, delivered with the same wording as a decisive one.
    #
    # The size is measured against the ticker's own daily move, not against the
    # price. Across ten tickers over five years the median cross sits at 0.03%
    # of price for SPY and 0.24% for CDE — an eightfold spread that tracks
    # volatility, so one fixed percentage would gag the quiet names and wave
    # everything through on the loud ones. Divided by daily volatility instead,
    # the same medians land between 0.034 and 0.056 for every one of them.
    strength = _cross_strength(ctx.closes, histogram)
    floor = float(alert.params.get("min_strength", DEFAULT_MACD_STRENGTH))
    if floor > 0 and (strength is None or strength < floor):
        return None

    label = "alcista ✨" if cross == "bullish" else "bajista ☠️"
    return _signal(
        alert,
        f"📐 {ctx.ticker}: cruce MACD {label}",
        f"{ctx.ticker}: la línea MACD({fast},{slow},{signal_period}) cruzó "
        f"{'arriba' if cross == 'bullish' else 'abajo'} de su señal en la última rueda "
        f"(histograma {histogram:+.4f}). Precio {ctx.quote.price:.2f} {ctx.quote.currency}.",
        {"cross": cross, "histogram": round(histogram, 6), "fast": fast, "slow": slow,
         "strength": round(strength, 4) if strength is not None else None},
        severity="warning" if cross == "bearish" else "info",
    )


REGISTRY = {
    kinds.REL_STRENGTH: rel_strength,
    kinds.ATR_STOP: atr_stop,
    kinds.VOLUME_SPIKE: volume_spike,
    kinds.NEW_52W_HIGH: fifty_two_week,
    kinds.NEW_52W_LOW: fifty_two_week,
    kinds.SMA_BREAK: sma_break,
    kinds.MACD_CROSS: macd_cross,
}
