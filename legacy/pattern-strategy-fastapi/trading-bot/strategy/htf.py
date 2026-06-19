"""Higher-timeframe context filter.

Patterns were evaluated in isolation — a textbook double bottom on 1h gets
taken even while the 4h is in a clean downtrend, which is the single most
common way 'perfect' reversal patterns fail. This filter checks the next
timeframe UP and blocks:

  - a BUY  when the HTF is in a downtrend (close < EMA(n)  AND the last
    `swings` swing-lows are strictly falling)
  - a SELL when the HTF is in an uptrend  (close > EMA(n)  AND the last
    `swings` swing-highs are strictly rising)

Both conditions must hold — EMA side alone is too twitchy, structure alone is
too slow. If there isn't enough HTF history, the filter ALLOWS (it is a
guard, not a prophet). Rejections are emitted under their own stage
"htf_context" so the gate's cost is measurable in the signal log and shadow
outcomes before anyone trusts it.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from marketdata.base import Bar
from .patterns import _find_swings


def ema(values: List[float], period: int) -> Optional[float]:
    """Standard EMA of `values` (oldest→newest); None if not enough data."""
    if len(values) < period or period <= 0:
        return None
    k = 2.0 / (period + 1.0)
    e = sum(values[:period]) / period          # SMA seed
    for v in values[period:]:
        e = v * k + e * (1.0 - k)
    return e


def _falling(xs: List[float]) -> bool:
    return len(xs) >= 2 and all(xs[i + 1] < xs[i] for i in range(len(xs) - 1))


def _rising(xs: List[float]) -> bool:
    return len(xs) >= 2 and all(xs[i + 1] > xs[i] for i in range(len(xs) - 1))


def htf_trend_conflict(
    htf_bars: List[Bar],
    side: str,
    ema_period: int = 50,
    swings: int = 3,
) -> Tuple[bool, str, dict]:
    """Return (conflict, detail, values) for a prospective `side` trade given
    the bars of the NEXT TIMEFRAME UP.

    conflict=True means the HTF trend opposes the trade and it should be
    rejected at stage "htf_context". Insufficient history → no conflict.
    """
    values: dict = {"ema_period": ema_period, "swings": swings}
    if len(htf_bars) < ema_period + 5:
        return False, f"Insufficient HTF history ({len(htf_bars)} bars; allowing)", values

    closes = [b.close for b in htf_bars]
    e = ema(closes, ema_period)
    if e is None:
        return False, "EMA unavailable (allowing)", values
    last = closes[-1]
    values["close"] = round(last, 5)
    values["ema"] = round(e, 5)

    swing_list = _find_swings(htf_bars, k=2)
    lows = [htf_bars[i].low for i, kind in swing_list if kind == "L"][-swings:]
    highs = [htf_bars[i].high for i, kind in swing_list if kind == "H"][-swings:]

    if side == "buy":
        below = last < e
        ll = len(lows) >= swings and _falling(lows)
        values["below_ema"] = below
        values["lower_lows"] = ll
        if below and ll:
            return True, (f"HTF downtrend: close {last:.5f} < EMA{ema_period} {e:.5f} "
                          f"and {swings} falling swing-lows"), values
    else:  # sell
        above = last > e
        hh = len(highs) >= swings and _rising(highs)
        values["above_ema"] = above
        values["higher_highs"] = hh
        if above and hh:
            return True, (f"HTF uptrend: close {last:.5f} > EMA{ema_period} {e:.5f} "
                          f"and {swings} rising swing-highs"), values

    return False, "No HTF trend conflict", values
