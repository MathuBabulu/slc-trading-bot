"""Cross-pair correlation, choppiness, and direction filter.

This adds a strategy gate (used by the engine after confirmation) that:

  1. CHOPPINESS — skips a signal when its market is choppy/range-bound,
     measured with the Choppiness Index on the signal's timeframe.

  2. DIRECTION — requires strongly-correlated pairs to *confirm* the trade
     direction: a positively-correlated pair should be moving the same way
     as the trade, a negatively-correlated pair the opposite way. If a
     strong peer contradicts, the trade is blocked ("trade in the right
     direction").

  3. CORRELATED DE-DUPLICATION — when several correlated pairs fire the same
     directional bet at once, keep only the cleanest one; and skip a signal
     if a correlated position is already open (no doubling the same bet).

Every function here is pure and works on plain ``Bar`` lists, so the logic is
unit-testable without the async engine.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional

from marketdata.base import Bar


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
@dataclass
class CorrelationConfig:
    enabled: bool = True
    lookback_bars: int = 100          # bars of returns used for correlation
    strong_threshold: float = 0.7     # |r| >= this counts as strongly correlated
    direction_lookback: int = 10      # bars used to gauge a pair's recent direction
    block_on_conflict: bool = True    # block (True) or just warn (False) on a direction conflict
    ci_period: int = 14               # Choppiness Index lookback
    ci_choppy_threshold: float = 61.8  # skip if CI > this; set <= 0 to disable the skip
    dedupe_correlated: bool = True    # keep only the cleanest of correlated co-firing signals


# --------------------------------------------------------------------------- #
# Primitives
# --------------------------------------------------------------------------- #
def returns(bars: List[Bar]) -> List[float]:
    """Bar-to-bar simple returns from close prices."""
    out: List[float] = []
    for i in range(1, len(bars)):
        p0 = bars[i - 1].close
        out.append((bars[i].close - p0) / p0 if p0 else 0.0)
    return out


def pearson(a: List[float], b: List[float]) -> Optional[float]:
    """Pearson correlation of two equal-tail-aligned series. None if undefined."""
    n = min(len(a), len(b))
    if n < 3:
        return None
    a = a[-n:]
    b = b[-n:]
    ma = sum(a) / n
    mb = sum(b) / n
    cov = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((x - mb) ** 2 for x in b)
    if va <= 0 or vb <= 0:
        return None
    return cov / math.sqrt(va * vb)


def correlation(bars_a: List[Bar], bars_b: List[Bar], lookback: int) -> Optional[float]:
    """Return correlation of returns over the last ``lookback`` bars, or None."""
    ra = returns(bars_a[-(lookback + 1):])
    rb = returns(bars_b[-(lookback + 1):])
    n = min(len(ra), len(rb))
    if n < 3:
        return None
    return pearson(ra[-n:], rb[-n:])


def choppiness_index(bars: List[Bar], period: int = 14) -> Optional[float]:
    """Choppiness Index over ``period`` bars.

    CI = 100 * log10( sum(TR, period) / (maxHigh - minLow) ) / log10(period)

    ~100 = very choppy/sideways, ~0 = strongly trending. Returns None if
    there isn't enough history or the range is degenerate.
    """
    if period < 2 or len(bars) < period + 1:
        return None
    seq = bars[-(period + 1):]          # period TRs need one extra prior bar
    window = bars[-period:]
    tr_sum = 0.0
    for i in range(1, len(seq)):
        pc = seq[i - 1].close
        tr = max(
            seq[i].high - seq[i].low,
            abs(seq[i].high - pc),
            abs(seq[i].low - pc),
        )
        tr_sum += tr
    hi = max(b.high for b in window)
    lo = min(b.low for b in window)
    rng = hi - lo
    if rng <= 0 or tr_sum <= 0:
        return None
    return 100.0 * math.log10(tr_sum / rng) / math.log10(period)


def net_direction(bars: List[Bar], lookback: int) -> int:
    """+1 if price rose over the window, -1 if fell, 0 if essentially flat."""
    if len(bars) < 2:
        return 0
    seg = bars[-(lookback + 1):] if len(bars) > lookback else bars
    change = seg[-1].close - seg[0].close
    base = abs(seg[0].close) or 1.0
    if abs(change) / base < 1e-5:
        return 0
    return 1 if change > 0 else -1


def side_dir(side: str) -> int:
    """Trade side as a direction: buy -> +1, sell -> -1."""
    return 1 if side == "buy" else -1


def expected_peer_dir(signal_side: str, r: float) -> int:
    """Direction a correlated peer should be moving to *confirm* the trade.

    Positive correlation -> peer moves with the trade; negative -> against it.
    """
    sd = side_dir(signal_side)
    return sd if r > 0 else -sd


def same_directional_bet(side_a: str, side_b: str, r: float) -> bool:
    """True if two signals on correlated pairs express the SAME underlying bet.

    Positively-correlated pairs traded the same side = same bet. Negatively-
    correlated pairs traded on opposite sides = same bet (e.g. long EURUSD and
    short USDCHF are both 'short USD').
    """
    da = side_dir(side_a)
    db = side_dir(side_b)
    return (da == db) if r > 0 else (da == -db)


def quality_key(ci: Optional[float], rr: float):
    """Sort key for picking the *cleanest* of correlated signals.

    Lower Choppiness Index is better; ties broken by higher reward:risk.
    A missing CI is treated as worst.
    """
    return (ci if ci is not None else 999.0, -rr)
