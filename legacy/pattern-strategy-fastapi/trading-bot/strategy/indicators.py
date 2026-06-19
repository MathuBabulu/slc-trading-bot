"""Technical indicator library — pure functions on OHLCV bar lists.

All functions accept bar history (oldest→newest) and return scalar values or
None when there isn't enough data. Three roles in the strategy:

  1. Clarity bonus   — RSI, EMA200, volume surge push borderline patterns higher
                       without acting as binary gates (non-blocking).
  2. Dead-market     — ATR percentile rank < threshold flags hibernating markets
                       where pattern moves are noise not signal.
  3. Study material  — every indicator reading is logged on each signal so the
                       shadow + journal data can correlate indicators with outcome.

Nothing here touches execution. All functions are safe (no raises on bad data).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from marketdata.base import Bar


# --------------------------------------------------------------------------- #
# Exponential Moving Average
# --------------------------------------------------------------------------- #
def ema_last(values: List[float], period: int) -> Optional[float]:
    """Standard EMA of `values` (oldest→newest); returns the LAST value only.

    Uses SMA over first `period` bars as the seed, then applies Wilder's
    multiplier k = 2/(period+1). Returns None if len(values) < period.
    """
    if len(values) < period or period <= 0:
        return None
    k = 2.0 / (period + 1.0)
    e = sum(values[:period]) / period          # SMA seed
    for v in values[period:]:
        e = v * k + e * (1.0 - k)
    return e


# --------------------------------------------------------------------------- #
# Relative Strength Index (Wilder smoothing)
# --------------------------------------------------------------------------- #
def rsi(closes: List[float], period: int = 14) -> Optional[float]:
    """Wilder RSI; returns 0-100. None if len(closes) < period + 1.

    Wilder smoothing: the first avg gain/loss is a plain SMA of the first
    `period` up/down moves; subsequent bars use the exponential formula
    avg = (prev_avg * (period-1) + current) / period.
    """
    if len(closes) < period + 1:
        return None
    gains: List[float] = []
    losses: List[float] = []
    for i in range(1, len(closes)):
        delta = closes[i] - closes[i - 1]
        gains.append(max(0.0, delta))
        losses.append(max(0.0, -delta))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for g, l in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + l) / period

    if avg_loss == 0.0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100.0 - 100.0 / (1.0 + rs), 2)


# --------------------------------------------------------------------------- #
# ATR percentile rank
# --------------------------------------------------------------------------- #
def _true_range_at(bars: List[Bar], i: int) -> float:
    if i <= 0:
        return bars[i].high - bars[i].low
    prev = bars[i - 1].close
    return max(
        bars[i].high - bars[i].low,
        abs(bars[i].high - prev),
        abs(bars[i].low - prev),
    )


def _atr_at(bars: List[Bar], end: int, period: int) -> float:
    """Simple average true range over `period` bars ending at index `end`."""
    if end < period:
        return 0.0
    return sum(_true_range_at(bars, i) for i in range(end - period + 1, end + 1)) / period


def atr_percentile_rank(
    bars: List[Bar], lookback: int = 50, atr_period: int = 14
) -> Optional[float]:
    """Percentile rank (0.0–1.0) of the current ATR vs its own recent history.

    0.0 = lowest ATR in the lookback window (dead/frozen market).
    1.0 = highest ATR in the lookback window (explosive move underway).

    Returns None if the bar list is too short. A reading < 0.15 is the
    'dead market' flag: the current candle is in the quietest 15% of the
    symbol's own recent session history — pattern moves may be noise.
    """
    required = lookback + atr_period
    if len(bars) < required:
        return None

    n = len(bars)
    atrs: List[float] = []
    for end in range(n - lookback, n):
        a = _atr_at(bars, end, atr_period)
        if a > 0:
            atrs.append(a)

    if len(atrs) < 2:
        return None

    current = atrs[-1]
    below = sum(1 for a in atrs[:-1] if a <= current)
    return round(below / (len(atrs) - 1), 3)


# --------------------------------------------------------------------------- #
# Volume ratio
# --------------------------------------------------------------------------- #
def volume_ratio(bars: List[Bar], ma_period: int = 20) -> Optional[float]:
    """Current bar's volume as a multiple of the n-bar simple average.

    Returns e.g. 1.5 (50% above average). None if:
      - insufficient history
      - all volumes are zero (some crypto/index feeds)
      - the current bar's volume is 0
    """
    if len(bars) < ma_period + 1:
        return None
    history_vols = [b.volume for b in bars[-(ma_period + 1):-1]]
    avg = sum(history_vols) / len(history_vols) if history_vols else 0.0
    if avg <= 0.0 or bars[-1].volume <= 0.0:
        return None
    return round(bars[-1].volume / avg, 3)


# --------------------------------------------------------------------------- #
# Composite indicator context
# --------------------------------------------------------------------------- #
@dataclass
class IndicatorContext:
    """Snapshot of all indicator readings for one bar.

    Consumed by the engine for clarity bonuses, the dead-market gate, and
    structured logging. Fields are None when data is insufficient.
    """
    rsi_14: Optional[float] = None        # 0-100
    ema200: Optional[float] = None        # price level of 200-bar EMA
    close: Optional[float] = None        # close at indicator snapshot time
    vol_ratio: Optional[float] = None    # current_vol / 20-bar avg volume
    atr_pct_rank: Optional[float] = None # 0-1 percentile of current ATR

    def to_dict(self) -> dict:
        return {
            "rsi_14": self.rsi_14,
            "ema200": self.ema200,
            "close": self.close,
            "vol_ratio": self.vol_ratio,
            "atr_pct_rank": self.atr_pct_rank,
        }

    def summary(self) -> str:
        parts: List[str] = []
        if self.rsi_14 is not None:
            parts.append(f"RSI={self.rsi_14:.0f}")
        if self.ema200 is not None and self.close is not None:
            rel = "above" if self.close > self.ema200 else "below"
            parts.append(f"EMA200={self.ema200:.5f}({rel})")
        if self.vol_ratio is not None:
            parts.append(f"Vol={self.vol_ratio:.2f}×")
        if self.atr_pct_rank is not None:
            parts.append(f"ATRpct={self.atr_pct_rank:.0%}")
        return ", ".join(parts) if parts else "no indicator data"


def compute_context(bars: List[Bar]) -> IndicatorContext:
    """Compute all indicators from a bar list. Never raises."""
    ctx = IndicatorContext()
    if not bars:
        return ctx
    try:
        closes = [b.close for b in bars]
        ctx.close = closes[-1]
        ctx.rsi_14 = rsi(closes, 14)
        ctx.ema200 = ema_last(closes, 200)
        ctx.vol_ratio = volume_ratio(bars, 20)
        ctx.atr_pct_rank = atr_percentile_rank(bars, 50, 14)
    except Exception:  # noqa: BLE001
        pass
    return ctx


# --------------------------------------------------------------------------- #
# Indicator clarity bonus
# --------------------------------------------------------------------------- #
def indicator_clarity_bonus(
    ctx: IndicatorContext, side: str
) -> Tuple[float, List[str]]:
    """Compute a non-binary clarity bonus (0–35) from indicator confluence.

    The bonus is ADDED to the structural clarity score (0-100) and the total
    is capped at 100. It does not gate trades — it helps borderline patterns
    cross a clarity threshold and makes study data richer.

    Components:
      RSI confluence  +15 — oversold RSI (< 40) for buys, overbought (> 60) sells
      EMA 200 aligned +10 — price on the trade-direction side of the 200-bar EMA
      Volume surge    +10 — current bar's volume > 1.5× its 20-bar average

    Returns (bonus_points, list_of_human_readable_reasons).
    """
    bonus = 0.0
    reasons: List[str] = []

    # RSI confluence (+15)
    if ctx.rsi_14 is not None:
        if side == "buy" and ctx.rsi_14 < 40.0:
            bonus += 15.0
            reasons.append(f"RSI {ctx.rsi_14:.0f} < 40 (oversold)")
        elif side == "sell" and ctx.rsi_14 > 60.0:
            bonus += 15.0
            reasons.append(f"RSI {ctx.rsi_14:.0f} > 60 (overbought)")

    # EMA200 alignment (+10)
    if ctx.ema200 is not None and ctx.close is not None:
        if side == "buy" and ctx.close > ctx.ema200:
            bonus += 10.0
            reasons.append(f"above EMA200 {ctx.ema200:.5f}")
        elif side == "sell" and ctx.close < ctx.ema200:
            bonus += 10.0
            reasons.append(f"below EMA200 {ctx.ema200:.5f}")

    # Volume surge (+10)
    if ctx.vol_ratio is not None and ctx.vol_ratio >= 1.5:
        bonus += 10.0
        reasons.append(f"volume {ctx.vol_ratio:.1f}× avg surge")

    return bonus, reasons
