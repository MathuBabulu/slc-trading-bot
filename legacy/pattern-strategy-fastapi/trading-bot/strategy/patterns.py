"""Price-action pattern detection.

v1 ships *only* Double Top and Double Bottom. The other detectors are stubbed
and clearly marked. Pattern detection from raw OHLC is genuinely hard — every
threshold here is conservative and will need tuning against your paper
results before HS/IHS/REC/TRI/TL get added.

The detectors return a `Signal` dataclass that downstream code (confirmation,
risk, execution) consumes without caring which pattern produced it.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional

from marketdata.base import Bar

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Signal dataclass — shared by every detector
# --------------------------------------------------------------------------- #
@dataclass
class Signal:
    symbol: str
    timeframe: str
    setup: str                # "DT" | "DB" | "HS" | "IHS" | ...
    side: str                 # "buy" | "sell"
    entry: float              # planned entry price (= R2 = the level)
    sl: float                 # stop loss
    tp: float                 # take profit (1:2 minimum)
    pattern_level: float      # the resistance/support that was tested twice
    detected_at: str          # ISO 8601 UTC, the close time of the trigger bar
    bars_in_pattern: int
    notes: List[str] = field(default_factory=list)
    rr: float = 0.0           # planned reward:risk
    clarity_score: float = 0.0  # 0-100 pattern quality (see score_pattern); 0 = not scored

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------- #
# Utilities
# --------------------------------------------------------------------------- #
def _swing_high_low(bars: List[Bar], i: int, k: int = 2) -> Optional[str]:
    """Return 'H' if bars[i] is a k-bar swing high, 'L' if swing low, else None.

    A swing high requires the high at i to be >= every high in [i-k, i+k]
    (and strictly > the immediate neighbours). Same logic for swing low.
    """
    if i - k < 0 or i + k >= len(bars):
        return None
    hi = bars[i].high
    lo = bars[i].low
    left  = bars[i - k : i]
    right = bars[i + 1 : i + 1 + k]

    is_high = all(b.high <= hi for b in left + right) and any(b.high < hi for b in [bars[i - 1], bars[i + 1]])
    is_low  = all(b.low  >= lo for b in left + right) and any(b.low  > lo for b in [bars[i - 1], bars[i + 1]])

    if is_high and is_low:        # tiebreak (rare): use body direction
        return "H" if bars[i].close < bars[i].open else "L"
    if is_high:
        return "H"
    if is_low:
        return "L"
    return None


def _find_swings(bars: List[Bar], k: int = 2) -> List[tuple[int, str]]:
    """Return list of (index, 'H'|'L') swings ordered by index."""
    swings = []
    for i in range(k, len(bars) - k):
        s = _swing_high_low(bars, i, k=k)
        if s:
            swings.append((i, s))
    return swings


def _atr(bars: List[Bar], i: int, n: int = 14) -> float:
    """Simple ATR over the n bars ending at index i (inclusive)."""
    if i - n < 0:
        return 0.0
    total = 0.0
    for j in range(i - n + 1, i + 1):
        prev_close = bars[j - 1].close if j > 0 else bars[j].open
        tr = max(
            bars[j].high - bars[j].low,
            abs(bars[j].high - prev_close),
            abs(bars[j].low - prev_close),
        )
        total += tr
    return total / n


# --------------------------------------------------------------------------- #
# Pattern clarity score
# --------------------------------------------------------------------------- #
def score_pattern(
    *,
    touch_diff: float,        # |p1 - p2| price distance between the two tests
    depth: float,             # valley/crest depth between the tests (price)
    gap_bars: int,            # bars between the two tests
    violations: int,          # bars between the tests whose CLOSE breaches the level
    atr: float,
    cfg: "_DTConfig",
) -> float:
    """Score a double-top/bottom-style pattern 0-100 on four components
    (25 points each). Logged on every signal so the journal + shadow data can
    correlate clarity with outcome; `strategy.min_clarity_score` (default 0 =
    log-only) turns it into a gate once the data says where the cutoff pays.

    - Touch precision : equal tests score high; score decays linearly to 0 at
                        1.0×ATR difference.
    - Depth           : a real W/M shape. At the detector minimum
                        (min_drop_atr) → 12.5; at 2× the minimum → 25.
    - Spacing         : peaks near the middle of [min,max]_bars_between_peaks
                        score 25; score decays toward the window edges.
    - Cleanliness     : closes breaching the level between the tests pollute
                        the pattern; 3+ violating closes → 0.
    """
    if atr <= 0:
        return 0.0

    # 1. Touch precision (0-25)
    touch = 25.0 * max(0.0, 1.0 - (touch_diff / atr))

    # 2. Structure depth (0-25): 0 below the minimum, 12.5 at it, 25 at 2× it.
    depth_atr = depth / atr
    depth_score = 25.0 * min(1.0, max(0.0, depth_atr / (2.0 * cfg.min_drop_atr)))

    # 3. Peak spacing (0-25): triangular peak at the window midpoint.
    mid = (cfg.min_bars_between_peaks + cfg.max_bars_between_peaks) / 2.0
    half = max(mid - cfg.min_bars_between_peaks, 1.0)
    spacing = 25.0 * max(0.0, 1.0 - abs(gap_bars - mid) / half)

    # 4. Cleanliness (0-25)
    clean = 25.0 * max(0.0, 1.0 - violations / 3.0)

    return round(min(100.0, max(0.0, touch + depth_score + spacing + clean)), 1)


# --------------------------------------------------------------------------- #
# Double Top / Bottom detector
# --------------------------------------------------------------------------- #
@dataclass
class _DTConfig:
    swing_k: int = 2                  # bars on each side for swing definition
    peak_tolerance_atr: float = 0.25  # COUNTER-trend tolerance: how far the 2nd low may UNDERCUT
                                      # the 1st (DB), or the 2nd high OVERSHOOT the 1st (DT). Tight.
    trend_tol_atr: float = 1.0        # TREND-aligned tolerance: a higher 2nd low (ascending DB) or
                                      # lower 2nd high (descending DT) is bullish/bearish structure,
                                      # so allow it up to this much * ATR. Catches trendline bounces.
    min_bars_between_peaks: int = 6
    max_bars_between_peaks: int = 50
    min_drop_atr: float = 2.0         # valley/crest between peaks must be >= 2*ATR deep (real structure)
    head_prominence_atr: float = 1.0  # H&S head must clear the shoulders by >= 1*ATR
    min_rr: float = 2.0


def detect_double_top_bottom(bars: List[Bar], cfg: Optional[_DTConfig] = None) -> List[Signal]:
    """Detect Double Top (sell) and Double Bottom (buy) setups.

    Entry rule: we enter at R2 (the second test of the level), not after a
    neckline break. So the signal trigger is when the most recent bar's high
    (for DT) or low (for DB) touches within tolerance of the prior swing
    peak/trough, and the bar closes against it (rejection candle).

    The actual fill / confirmation candle check happens in confirmation.py.
    """
    cfg = cfg or _DTConfig()
    if len(bars) < 30:
        return []

    swings = _find_swings(bars, k=cfg.swing_k)
    if len(swings) < 3:
        return []

    last = len(bars) - 1
    atr = _atr(bars, last, n=14)
    if atr <= 0:
        return []

    signals: List[Signal] = []

    # We look only at the most recent swing. If the latest swing is at or near
    # the current bar, and it matches a previous swing of the same type within
    # tolerance, this is a Double Top / Bottom.
    # Walk backwards through swings to find the latest one.
    for swing_idx, kind in reversed(swings):
        # The most recent qualifying swing must be at or within 2 bars of last.
        if last - swing_idx > cfg.swing_k + 1:
            break  # any earlier swing is stale for "live" detection

        # Look for a prior swing of the same kind within window
        for prev_idx, prev_kind in reversed(swings[:swings.index((swing_idx, kind))]):
            gap = swing_idx - prev_idx
            if gap < cfg.min_bars_between_peaks:
                continue
            if gap > cfg.max_bars_between_peaks:
                break
            if prev_kind != kind:
                continue

            if kind == "H":
                sig = _build_double_top(bars, prev_idx, swing_idx, atr, cfg)
            else:
                sig = _build_double_bottom(bars, prev_idx, swing_idx, atr, cfg)
            if sig:
                signals.append(sig)
        # Only consider the very latest swing — once we've processed it, stop
        break

    return signals


def _build_double_top(
    bars: List[Bar], i1: int, i2: int, atr: float, cfg: _DTConfig
) -> Optional[Signal]:
    p1 = bars[i1].high      # earlier high
    p2 = bars[i2].high      # later high (the 2nd test)
    # Descending double top (p2 lower than p1) is a bearish lower-high -> allow
    # generously. A higher 2nd high is counter-trend -> keep tight.
    drift = p1 - p2         # > 0 means lower 2nd high (trend-aligned for a sell)
    if drift >= 0:
        if drift > atr * cfg.trend_tol_atr:
            return None
    elif -drift > atr * cfg.peak_tolerance_atr:
        return None

    # Valley between peaks must be sufficiently deep
    valley = min(b.low for b in bars[i1 : i2 + 1])
    if (min(p1, p2) - valley) < atr * cfg.min_drop_atr:
        return None

    level = (p1 + p2) / 2
    sl = max(p1, p2) + atr * 0.25       # 0.25 ATR buffer above pattern high
    risk = sl - level
    if risk <= 0:
        return None
    tp = level - cfg.min_rr * risk      # 1:2 minimum
    rr = (level - tp) / risk

    # Clarity: closes ABOVE the level between the two peaks pollute a DT.
    violations = sum(1 for b in bars[i1 + 1 : i2] if b.close > level)
    clarity = score_pattern(
        touch_diff=abs(p1 - p2), depth=min(p1, p2) - valley,
        gap_bars=i2 - i1, violations=violations, atr=atr, cfg=cfg,
    )

    return Signal(
        symbol=bars[i2].symbol,
        timeframe=bars[i2].timeframe,
        setup="DT",
        side="sell",
        entry=round(level, 5),
        sl=round(sl, 5),
        tp=round(tp, 5),
        pattern_level=round(level, 5),
        detected_at=bars[-1].time,
        bars_in_pattern=i2 - i1 + 1,
        notes=[f"Double Top, peaks at {p1:.5f} and {p2:.5f}",
               f"Valley low {valley:.5f}, ATR {atr:.5f}",
               f"Clarity {clarity:.0f}/100"],
        rr=round(rr, 2),
        clarity_score=clarity,
    )


def _build_double_bottom(
    bars: List[Bar], i1: int, i2: int, atr: float, cfg: _DTConfig
) -> Optional[Signal]:
    t1 = bars[i1].low       # earlier low
    t2 = bars[i2].low       # later low (the 2nd test)
    # Ascending double bottom (t2 higher than t1) is a bullish higher-low riding
    # support -> allow generously. A lower 2nd low is counter-trend -> keep tight.
    drift = t2 - t1         # > 0 means higher 2nd low (trend-aligned for a buy)
    if drift >= 0:
        if drift > atr * cfg.trend_tol_atr:
            return None
    elif -drift > atr * cfg.peak_tolerance_atr:
        return None

    crest = max(b.high for b in bars[i1 : i2 + 1])
    if (crest - max(t1, t2)) < atr * cfg.min_drop_atr:
        return None

    level = (t1 + t2) / 2
    sl = min(t1, t2) - atr * 0.25
    risk = level - sl
    if risk <= 0:
        return None
    tp = level + cfg.min_rr * risk
    rr = (tp - level) / risk

    # Clarity: closes BELOW the level between the two troughs pollute a DB.
    violations = sum(1 for b in bars[i1 + 1 : i2] if b.close < level)
    clarity = score_pattern(
        touch_diff=abs(t1 - t2), depth=crest - max(t1, t2),
        gap_bars=i2 - i1, violations=violations, atr=atr, cfg=cfg,
    )

    return Signal(
        symbol=bars[i2].symbol,
        timeframe=bars[i2].timeframe,
        setup="DB",
        side="buy",
        entry=round(level, 5),
        sl=round(sl, 5),
        tp=round(tp, 5),
        pattern_level=round(level, 5),
        detected_at=bars[-1].time,
        bars_in_pattern=i2 - i1 + 1,
        notes=[f"Double Bottom, troughs at {t1:.5f} and {t2:.5f}",
               f"Crest high {crest:.5f}, ATR {atr:.5f}",
               f"Clarity {clarity:.0f}/100"],
        rr=round(rr, 2),
        clarity_score=clarity,
    )


# --------------------------------------------------------------------------- #
# Head & Shoulders / Inverse H&S
# --------------------------------------------------------------------------- #
def detect_head_shoulders(bars: List[Bar], cfg: Optional[_DTConfig] = None) -> List[Signal]:
    """Head & Shoulders top (sell). Three swing highs L-H-R where the middle
    (head) is clearly the highest and the two shoulders are ~equal. The shoulder
    level is the tested level; we trigger when the right shoulder just formed
    (R2). Stop sits above the head (pattern invalidation); target 1:2."""
    cfg = cfg or _DTConfig()
    if len(bars) < 40:
        return []
    sw = _find_swings(bars, k=cfg.swing_k)
    highs = [i for i, k in sw if k == "H"]
    if len(highs) < 3:
        return []
    last = len(bars) - 1
    atr = _atr(bars, last, n=14)
    if atr <= 0 or last - highs[-1] > cfg.swing_k + 1:
        return []
    ls, head, rs = highs[-3], highs[-2], highs[-1]
    if not (cfg.min_bars_between_peaks <= head - ls <= cfg.max_bars_between_peaks):
        return []
    if not (cfg.min_bars_between_peaks <= rs - head <= cfg.max_bars_between_peaks):
        return []
    h_ls, h_head, h_rs = bars[ls].high, bars[head].high, bars[rs].high
    if not (h_head > h_ls and h_head > h_rs):
        return []
    if abs(h_ls - h_rs) > atr * cfg.peak_tolerance_atr:
        return []
    if (h_head - max(h_ls, h_rs)) < atr * cfg.head_prominence_atr:   # head must clearly clear shoulders
        return []
    level = (h_ls + h_rs) / 2
    sl = h_head + atr * 0.25
    risk = sl - level
    if risk <= 0:
        return []
    tp = level - cfg.min_rr * risk
    return [Signal(symbol=bars[rs].symbol, timeframe=bars[rs].timeframe, setup="HS", side="sell",
                   entry=round(level, 5), sl=round(sl, 5), tp=round(tp, 5),
                   pattern_level=round(level, 5), detected_at=bars[-1].time,
                   bars_in_pattern=rs - ls + 1,
                   notes=[f"Head & Shoulders: shoulders {h_ls:.5f}/{h_rs:.5f}, head {h_head:.5f}"],
                   rr=round((level - tp) / risk, 2))]


def detect_inverse_hs(bars: List[Bar], cfg: Optional[_DTConfig] = None) -> List[Signal]:
    """Inverse Head & Shoulders bottom (buy). Mirror of detect_head_shoulders
    using swing lows; head is the lowest trough, shoulders ~equal."""
    cfg = cfg or _DTConfig()
    if len(bars) < 40:
        return []
    sw = _find_swings(bars, k=cfg.swing_k)
    lows = [i for i, k in sw if k == "L"]
    if len(lows) < 3:
        return []
    last = len(bars) - 1
    atr = _atr(bars, last, n=14)
    if atr <= 0 or last - lows[-1] > cfg.swing_k + 1:
        return []
    ls, head, rs = lows[-3], lows[-2], lows[-1]
    if not (cfg.min_bars_between_peaks <= head - ls <= cfg.max_bars_between_peaks):
        return []
    if not (cfg.min_bars_between_peaks <= rs - head <= cfg.max_bars_between_peaks):
        return []
    l_ls, l_head, l_rs = bars[ls].low, bars[head].low, bars[rs].low
    if not (l_head < l_ls and l_head < l_rs):
        return []
    if abs(l_ls - l_rs) > atr * cfg.peak_tolerance_atr:
        return []
    if (min(l_ls, l_rs) - l_head) < atr * 0.8:
        return []
    level = (l_ls + l_rs) / 2
    sl = l_head - atr * 0.25
    risk = level - sl
    if risk <= 0:
        return []
    tp = level + cfg.min_rr * risk
    return [Signal(symbol=bars[rs].symbol, timeframe=bars[rs].timeframe, setup="IHS", side="buy",
                   entry=round(level, 5), sl=round(sl, 5), tp=round(tp, 5),
                   pattern_level=round(level, 5), detected_at=bars[-1].time,
                   bars_in_pattern=rs - ls + 1,
                   notes=[f"Inverse H&S: shoulders {l_ls:.5f}/{l_rs:.5f}, head {l_head:.5f}"],
                   rr=round((tp - level) / risk, 2))]


# --------------------------------------------------------------------------- #
# Triple Top / Bottom
# --------------------------------------------------------------------------- #
def detect_triple(bars: List[Bar], cfg: Optional[_DTConfig] = None) -> List[Signal]:
    """Triple Top (sell) / Triple Bottom (buy): three swings at ~the same level.
    Triggers on the third test (the latest swing). Enter at the level, stop just
    beyond the extreme of the three, target 1:2."""
    cfg = cfg or _DTConfig()
    if len(bars) < 40:
        return []
    sw = _find_swings(bars, k=cfg.swing_k)
    if not sw:
        return []
    last = len(bars) - 1
    atr = _atr(bars, last, n=14)
    if atr <= 0:
        return []
    idx, kind = sw[-1]
    if last - idx > cfg.swing_k + 1:               # latest swing must be fresh
        return []
    same = [i for i, k in sw if k == kind]
    if len(same) < 3:
        return []
    i1, i2, i3 = same[-3], same[-2], same[-1]
    if not (cfg.min_bars_between_peaks <= i2 - i1 <= cfg.max_bars_between_peaks):
        return []
    if not (cfg.min_bars_between_peaks <= i3 - i2 <= cfg.max_bars_between_peaks):
        return []
    if kind == "H":
        pts = [bars[i1].high, bars[i2].high, bars[i3].high]
        if max(pts) - min(pts) > atr * cfg.peak_tolerance_atr:
            return []
        valley = min(b.low for b in bars[i1:i3 + 1])
        if (min(pts) - valley) < atr * cfg.min_drop_atr:
            return []
        level = sum(pts) / 3
        sl = max(pts) + atr * 0.25
        risk = sl - level
        if risk <= 0:
            return []
        tp = level - cfg.min_rr * risk
        return [Signal(symbol=bars[i3].symbol, timeframe=bars[i3].timeframe, setup="TT", side="sell",
                       entry=round(level, 5), sl=round(sl, 5), tp=round(tp, 5),
                       pattern_level=round(level, 5), detected_at=bars[-1].time,
                       bars_in_pattern=i3 - i1 + 1,
                       notes=[f"Triple Top at {level:.5f} ({pts[0]:.5f}/{pts[1]:.5f}/{pts[2]:.5f})"],
                       rr=round((level - tp) / risk, 2))]
    else:
        pts = [bars[i1].low, bars[i2].low, bars[i3].low]
        if max(pts) - min(pts) > atr * cfg.peak_tolerance_atr:
            return []
        crest = max(b.high for b in bars[i1:i3 + 1])
        if (crest - max(pts)) < atr * cfg.min_drop_atr:
            return []
        level = sum(pts) / 3
        sl = min(pts) - atr * 0.25
        risk = level - sl
        if risk <= 0:
            return []
        tp = level + cfg.min_rr * risk
        return [Signal(symbol=bars[i3].symbol, timeframe=bars[i3].timeframe, setup="TB", side="buy",
                       entry=round(level, 5), sl=round(sl, 5), tp=round(tp, 5),
                       pattern_level=round(level, 5), detected_at=bars[-1].time,
                       bars_in_pattern=i3 - i1 + 1,
                       notes=[f"Triple Bottom at {level:.5f} ({pts[0]:.5f}/{pts[1]:.5f}/{pts[2]:.5f})"],
                       rr=round((tp - level) / risk, 2))]


# --------------------------------------------------------------------------- #
# Rectangle / Range
# --------------------------------------------------------------------------- #
def detect_rectangle(bars: List[Bar], cfg: Optional[_DTConfig] = None) -> List[Signal]:
    """Rectangle / range: a horizontal resistance (two ~equal swing highs) and
    support (two ~equal swing lows) define a band. Trade the bounce off whichever
    edge the current bar tests and rejects — sell the top, buy the bottom."""
    cfg = cfg or _DTConfig()
    if len(bars) < 40:
        return []
    sw = _find_swings(bars, k=cfg.swing_k)
    highs = [i for i, k in sw if k == "H"]
    lows = [i for i, k in sw if k == "L"]
    if len(highs) < 2 or len(lows) < 2:
        return []
    last = len(bars) - 1
    atr = _atr(bars, last, n=14)
    if atr <= 0:
        return []
    if abs(bars[highs[-1]].high - bars[highs[-2]].high) > atr * cfg.peak_tolerance_atr:
        return []
    if abs(bars[lows[-1]].low - bars[lows[-2]].low) > atr * cfg.peak_tolerance_atr:
        return []
    R = (bars[highs[-1]].high + bars[highs[-2]].high) / 2
    S = (bars[lows[-1]].low + bars[lows[-2]].low) / 2
    if (R - S) < atr * cfg.min_drop_atr:               # band must be meaningful
        return []
    bar = bars[last]
    tol = atr * 0.5
    out: List[Signal] = []
    # Sell the top edge
    if abs(bar.high - R) <= tol and bar.close < R and bar.close < bar.open:
        sl = R + atr * 0.25
        risk = sl - R
        if risk > 0:
            tp = R - cfg.min_rr * risk
            out.append(Signal(symbol=bar.symbol, timeframe=bar.timeframe, setup="REC", side="sell",
                              entry=round(R, 5), sl=round(sl, 5), tp=round(tp, 5),
                              pattern_level=round(R, 5), detected_at=bars[-1].time,
                              bars_in_pattern=last - highs[-2] + 1,
                              notes=[f"Range top {R:.5f} / bottom {S:.5f}; sell the top"],
                              rr=round((R - tp) / risk, 2)))
    # Buy the bottom edge
    if abs(bar.low - S) <= tol and bar.close > S and bar.close > bar.open:
        sl = S - atr * 0.25
        risk = S - sl
        if risk > 0:
            tp = S + cfg.min_rr * risk
            out.append(Signal(symbol=bar.symbol, timeframe=bar.timeframe, setup="REC", side="buy",
                              entry=round(S, 5), sl=round(sl, 5), tp=round(tp, 5),
                              pattern_level=round(S, 5), detected_at=bars[-1].time,
                              bars_in_pattern=last - lows[-2] + 1,
                              notes=[f"Range top {R:.5f} / bottom {S:.5f}; buy the bottom"],
                              rr=round((tp - S) / risk, 2)))
    return out


@dataclass
class _TLConfig:
    swing_k: int = 2                 # bars each side for a swing
    min_span_bars: int = 8           # anchor swings must be >= this far apart
    max_span_bars: int = 80          # ... and <= this
    touch_tol_atr: float = 0.35      # current bar within this * ATR of the line
    sl_buffer_atr: float = 0.25      # stop placed this far beyond the line
    min_rr: float = 2.0


def _line_at(i1: int, v1: float, i2: int, v2: float, x: int) -> float:
    slope = (v2 - v1) / (i2 - i1)
    return v2 + slope * (x - i2), slope


def detect_trendline(bars: List[Bar], cfg: Optional[_TLConfig] = None) -> List[Signal]:
    """Diagonal trendline bounce setups (enter at the line = the tested level).

    BUY:  an ASCENDING support line drawn through the two most recent swing
          lows; the current bar dips to that line and closes back above it
          (bullish rejection) -> long, like buying the hold of rising support.
    SELL: a DESCENDING resistance line through the two most recent swing highs;
          the current bar pokes the line and closes back below it -> short.

    This is the 'trade from a tested level' rule applied to a diagonal level.
    (Trendline break-and-retest is a separate future setup.)
    """
    cfg = cfg or _TLConfig()
    if len(bars) < 30:
        return []
    swings = _find_swings(bars, k=cfg.swing_k)
    if len(swings) < 2:
        return []
    last = len(bars) - 1
    atr = _atr(bars, last, n=14)
    if atr <= 0:
        return []

    out: List[Signal] = []
    lows = [(i, bars[i].low) for i, k in swings if k == "L"]
    highs = [(i, bars[i].high) for i, k in swings if k == "H"]

    s = _build_trendline_support(bars, lows, last, atr, cfg)
    if s:
        out.append(s)
    s = _build_trendline_resistance(bars, highs, last, atr, cfg)
    if s:
        out.append(s)
    return out


def _build_trendline_support(bars, lows, last, atr, cfg) -> Optional[Signal]:
    if len(lows) < 2:
        return None
    (i1, l1), (i2, l2) = lows[-2], lows[-1]
    span = i2 - i1
    if span < cfg.min_span_bars or span > cfg.max_span_bars:
        return None
    line_last, slope = _line_at(i1, l1, i2, l2, last)
    if slope < 0:                       # support must be ascending (or flat)
        return None
    bar = bars[last]
    tol = cfg.touch_tol_atr * atr
    if not (line_last - tol <= bar.low <= line_last + tol):
        return None                     # current bar must actually test the line
    if bar.close <= line_last or bar.close <= bar.open:
        return None                     # must hold above the line on a bullish close
    # the line must not have been decisively broken between the anchors
    for j in range(i1, last):
        ln = l1 + slope * (j - i1)
        if bars[j].close < ln - tol:
            return None
    level = line_last
    sl = min(bar.low, line_last) - cfg.sl_buffer_atr * atr
    risk = level - sl
    if risk <= 0:
        return None
    tp = level + cfg.min_rr * risk
    return Signal(
        symbol=bars[last].symbol, timeframe=bars[last].timeframe, setup="TL", side="buy",
        entry=round(level, 5), sl=round(sl, 5), tp=round(tp, 5),
        pattern_level=round(level, 5), detected_at=bars[-1].time,
        bars_in_pattern=last - i1 + 1,
        notes=[f"Ascending trendline support; anchors {l1:.5f}@{i1}, {l2:.5f}@{i2}",
               f"Tested {line_last:.5f}, ATR {atr:.5f}"],
        rr=round((tp - level) / risk, 2),
    )


def _build_trendline_resistance(bars, highs, last, atr, cfg) -> Optional[Signal]:
    if len(highs) < 2:
        return None
    (i1, h1), (i2, h2) = highs[-2], highs[-1]
    span = i2 - i1
    if span < cfg.min_span_bars or span > cfg.max_span_bars:
        return None
    line_last, slope = _line_at(i1, h1, i2, h2, last)
    if slope > 0:                       # resistance must be descending (or flat)
        return None
    bar = bars[last]
    tol = cfg.touch_tol_atr * atr
    if not (line_last - tol <= bar.high <= line_last + tol):
        return None
    if bar.close >= line_last or bar.close >= bar.open:
        return None                     # must reject below the line on a bearish close
    for j in range(i1, last):
        ln = h1 + slope * (j - i1)
        if bars[j].close > ln + tol:
            return None
    level = line_last
    sl = max(bar.high, line_last) + cfg.sl_buffer_atr * atr
    risk = sl - level
    if risk <= 0:
        return None
    tp = level - cfg.min_rr * risk
    return Signal(
        symbol=bars[last].symbol, timeframe=bars[last].timeframe, setup="TL", side="sell",
        entry=round(level, 5), sl=round(sl, 5), tp=round(tp, 5),
        pattern_level=round(level, 5), detected_at=bars[-1].time,
        bars_in_pattern=last - i1 + 1,
        notes=[f"Descending trendline resistance; anchors {h1:.5f}@{i1}, {h2:.5f}@{i2}",
               f"Tested {line_last:.5f}, ATR {atr:.5f}"],
        rr=round((level - tp) / risk, 2),
    )


# --------------------------------------------------------------------------- #
# Dispatcher used by the engine
# --------------------------------------------------------------------------- #
ENABLED_DETECTORS = {
    "double_top":     detect_double_top_bottom,    # both DT and DB returned
    "double_bottom":  None,                        # handled by detect_double_top_bottom
    "head_shoulders": detect_head_shoulders,
    "inverse_hs":     detect_inverse_hs,
    "triple_top":     detect_triple,
    "triple_bottom":  None,
    "rectangle":      detect_rectangle,
    "trendline":      detect_trendline,
}


def run_all(bars: List[Bar], flags: dict) -> List[Signal]:
    """Run every detector whose flag is True, then de-duplicate.

    With many detectors enabled, several can fire on the same bar of the same
    pair in the same direction (e.g. a Double Top and a Triple Top). We collapse
    those to a single signal per (symbol, timeframe, side, detected_at), keeping
    the one with the best reward:risk, so the engine never opens overlapping
    trades on the same setup.
    """
    raw: List[Signal] = []

    if flags.get("double_top") or flags.get("double_bottom"):
        raw.extend(detect_double_top_bottom(bars))
    if flags.get("head_shoulders"):
        raw.extend(detect_head_shoulders(bars))
    if flags.get("inverse_hs"):
        raw.extend(detect_inverse_hs(bars))
    if flags.get("triple_top") or flags.get("triple_bottom"):
        raw.extend(detect_triple(bars))
    if flags.get("rectangle"):
        raw.extend(detect_rectangle(bars))
    if flags.get("trendline"):
        raw.extend(detect_trendline(bars))

    best: Dict[tuple, Signal] = {}
    for s in raw:
        key = (s.symbol, s.timeframe, s.side, s.detected_at)
        cur = best.get(key)
        if cur is None or s.rr > cur.rr:
            if cur is not None:
                # fold the dropped setup's name into the kept signal's notes
                s.notes.append(f"(also matched {cur.setup})")
            best[key] = s
    return list(best.values())
