"""Volume profile (VPVR) + tick-volume delta — price-action confirmation.

WHY THIS IS A PROXY, NOT TRUE FOOTPRINT
---------------------------------------
Real footprint / order-flow needs per-trade bid/ask volume, which only exists
for centralised markets (futures, exchange-traded crypto/equities). Spot FX is
OTC with no consolidated tape, so the `volume` on our MT5 bars is TICK volume
(number of price updates), not traded contracts, and it cannot be split into
real buy/sell flow. This module therefore builds:

  * a Volume Profile (volume-at-price) — robust and meaningful even on tick
    volume: it shows WHERE the market spent its activity (POC, value area,
    high-volume nodes), which is exactly what we want to confirm an R2 retest.
  * a delta PROXY — each bar's volume signed by its close-vs-open direction and
    summed. Not real order flow, but a useful momentum-of-participation read.

It is deliberately NON-BLOCKING: it only contributes a clarity-score bonus, so
we can shadow-test whether it actually improves outcomes before trusting it.

All functions are pure and safe (never raise on bad/empty/zero-volume data).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from marketdata.base import Bar


# --------------------------------------------------------------------------- #
# Profile
# --------------------------------------------------------------------------- #
@dataclass
class VolumeProfile:
    poc: Optional[float] = None          # point of control — price of max volume
    va_high: Optional[float] = None      # value-area high (≈70% of volume)
    va_low: Optional[float] = None       # value-area low
    hvn_levels: List[float] = field(default_factory=list)   # high-volume nodes
    bin_width: float = 0.0
    total_volume: float = 0.0
    bins: int = 0

    def to_dict(self) -> dict:
        return {
            "poc": self.poc, "va_high": self.va_high, "va_low": self.va_low,
            "hvn_levels": [round(x, 5) for x in self.hvn_levels],
            "bin_width": self.bin_width, "total_volume": self.total_volume,
        }


def _atr_proxy(bars: List[Bar], period: int = 14) -> float:
    """Average high-low range over the last `period` bars (a cheap ATR proxy)."""
    window = bars[-period:] if len(bars) >= period else bars
    if not window:
        return 0.0
    return sum((b.high - b.low) for b in window) / len(window)


def build_profile(bars: List[Bar], lookback: int = 120, bins: int = 50,
                  hvn_frac: float = 0.70, value_area_pct: float = 0.70
                  ) -> VolumeProfile:
    """Distribute each bar's volume across price bins it spanned (high→low),
    then derive POC, value area, and high-volume nodes.

    Volume is spread evenly across the bins a bar's range covers (a standard
    approximation when only OHLCV is available). Returns an empty profile when
    there is no usable price range or all volume is zero.
    """
    window = [b for b in bars[-lookback:]] if bars else []
    if len(window) < 2:
        return VolumeProfile()

    lo = min(b.low for b in window)
    hi = max(b.high for b in window)
    if hi <= lo:
        return VolumeProfile()

    bins = max(5, int(bins))
    width = (hi - lo) / bins
    hist = [0.0] * bins

    total_vol = 0.0
    for b in window:
        v = float(b.volume or 0.0)
        if v <= 0.0:
            continue
        total_vol += v
        b_lo = max(lo, min(b.low, b.high))
        b_hi = min(hi, max(b.low, b.high))
        i_lo = int((b_lo - lo) / width)
        i_hi = int((b_hi - lo) / width)
        i_lo = min(max(i_lo, 0), bins - 1)
        i_hi = min(max(i_hi, 0), bins - 1)
        span = (i_hi - i_lo) + 1
        share = v / span
        for i in range(i_lo, i_hi + 1):
            hist[i] += share

    if total_vol <= 0.0:
        return VolumeProfile(bin_width=width, total_volume=0.0, bins=bins)

    def center(i: int) -> float:
        return lo + (i + 0.5) * width

    poc_idx = max(range(bins), key=lambda i: hist[i])
    poc_vol = hist[poc_idx]

    # High-volume nodes: bins holding >= hvn_frac of the POC's volume.
    hvn = [round(center(i), 5) for i in range(bins) if poc_vol > 0
           and hist[i] >= hvn_frac * poc_vol]

    # Value area: expand outward from the POC, taking the richer neighbour each
    # step, until `value_area_pct` of total volume is enclosed.
    included = {poc_idx}
    acc = poc_vol
    lo_i = hi_i = poc_idx
    while acc < value_area_pct * total_vol and (lo_i > 0 or hi_i < bins - 1):
        below = hist[lo_i - 1] if lo_i > 0 else -1.0
        above = hist[hi_i + 1] if hi_i < bins - 1 else -1.0
        if above >= below:
            hi_i += 1
            acc += max(above, 0.0)
            included.add(hi_i)
        else:
            lo_i -= 1
            acc += max(below, 0.0)
            included.add(lo_i)

    return VolumeProfile(
        poc=round(center(poc_idx), 5),
        va_high=round(center(max(included)), 5),
        va_low=round(center(min(included)), 5),
        hvn_levels=hvn,
        bin_width=width,
        total_volume=round(total_vol, 2),
        bins=bins,
    )


# --------------------------------------------------------------------------- #
# Delta proxy
# --------------------------------------------------------------------------- #
def cumulative_delta(bars: List[Bar], lookback: int = 20) -> Tuple[float, float]:
    """Signed tick-volume delta proxy over the last `lookback` bars.

    Each bar's volume is signed by its close-vs-open direction (up bar = buy
    pressure, down bar = sell pressure, doji = neutral). Returns
    (net_delta, normalised) where `normalised` = net_delta / total_volume in
    [-1, 1] (0 when there's no volume).
    """
    window = bars[-lookback:] if bars else []
    net = 0.0
    total = 0.0
    for b in window:
        v = float(b.volume or 0.0)
        if v <= 0.0:
            continue
        total += v
        if b.close > b.open:
            net += v
        elif b.close < b.open:
            net -= v
    norm = (net / total) if total > 0 else 0.0
    return round(net, 2), round(norm, 3)


# --------------------------------------------------------------------------- #
# Clarity bonus
# --------------------------------------------------------------------------- #
def volume_clarity_bonus(
    entry: float, side: str, bars: List[Bar], cfg: Optional[Dict] = None
) -> Tuple[float, List[str], Dict]:
    """Non-blocking clarity bonus from volume-profile + delta confluence.

    Two components (both configurable, default cap +20):
      * node_bonus (+12) — the entry/retest level sits within `node_tol_atr`×ATR
        of the POC or a high-volume node: the R2 retest is into a price the
        market has actually defended, not a low-volume vacuum.
      * delta_bonus (+8) — the recent tick-volume delta proxy agrees with the
        trade direction (buy with positive delta, sell with negative).

    Returns (bonus_points, reasons, readings_for_logging). Awards nothing (and
    never raises) when volume is absent or the profile can't be built.
    """
    cfg = cfg or {}
    lookback = int(cfg.get("lookback", 120))
    bins = int(cfg.get("bins", 50))
    node_tol_atr = float(cfg.get("node_tol_atr", 0.25))
    node_bonus = float(cfg.get("node_bonus", 12.0))
    delta_bonus = float(cfg.get("delta_bonus", 8.0))
    delta_lookback = int(cfg.get("delta_lookback", 20))
    delta_min = float(cfg.get("delta_min", 0.15))

    bonus = 0.0
    reasons: List[str] = []
    readings: Dict = {}

    try:
        prof = build_profile(bars, lookback=lookback, bins=bins)
        net, norm = cumulative_delta(bars, lookback=delta_lookback)
        readings = {"profile": prof.to_dict(), "delta": net, "delta_norm": norm}
        if prof.total_volume <= 0.0:
            readings["note"] = "no volume data"
            return 0.0, [], readings

        atr = _atr_proxy(bars)
        tol = node_tol_atr * atr if atr > 0 else (prof.bin_width or 0.0)

        # Node confluence: nearest of POC + HVNs to the entry.
        nodes = [prof.poc] + list(prof.hvn_levels)
        nodes = [n for n in nodes if n is not None]
        if nodes and tol > 0:
            nearest = min(nodes, key=lambda n: abs(n - entry))
            dist = abs(nearest - entry)
            readings["dist_to_nearest_node"] = round(dist, 5)
            if dist <= tol:
                bonus += node_bonus
                tag = "POC" if nearest == prof.poc else "HVN"
                reasons.append(f"entry at {tag} {nearest:.5f} (±{tol:.5f})")

        # Delta confluence.
        if abs(norm) >= delta_min:
            if (side == "buy" and norm > 0) or (side == "sell" and norm < 0):
                bonus += delta_bonus
                reasons.append(f"delta {norm:+.2f} confirms {side}")
    except Exception:  # noqa: BLE001
        return 0.0, [], readings

    return round(bonus, 1), reasons, readings
