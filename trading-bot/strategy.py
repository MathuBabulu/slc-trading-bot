"""SLC (Structure - Liquidity - Confirmation) pure price action logic.

Implements the playbook (SLC-Price-Action-Playbook.md) mechanically:
  Pillar 1  HTF structure -> directional bias (closes, not wicks)
  Pillar 2  MTF POI (order block / FVG) + liquidity pools + sweep
  Pillar 3  LTF confirmation (CHoCH or engulfing) inside the POI
  Volatility: everything denominated in ATR(14) of the MTF; regime
  filter ATR(14)/ATR(100).

All functions take bars as lists of dicts {t,o,h,l,c,v}, oldest first,
closed bars only (the EA already skips the forming bar).
"""
from typing import Any, Dict, List, Optional, Tuple

Bar = Dict[str, Any]

MODE_TFS = {
    "intraday": {"htf": "4h", "mtf": "1h", "ltf": "15m"},
    "swing":    {"htf": "1d", "mtf": "4h", "ltf": "1h"},
}


# ---------------------------------------------------------------- utils
def atr(bars: List[Bar], n: int = 14) -> float:
    if len(bars) < n + 1:
        return 0.0
    trs = []
    for i in range(len(bars) - n, len(bars)):
        h, l, pc = bars[i]["h"], bars[i]["l"], bars[i - 1]["c"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs) / n


def pivots(bars: List[Bar], k: int = 2) -> List[Dict[str, Any]]:
    """3-candle (k=1) .. 5-candle (k=2) swing pivots. Returns oldest-first
    [{i, t, price, kind:'H'|'L'}]."""
    out = []
    for i in range(k, len(bars) - k):
        hi = bars[i]["h"]
        lo = bars[i]["l"]
        if all(bars[i - j]["h"] < hi for j in range(1, k + 1)) and \
           all(bars[i + j]["h"] < hi for j in range(1, k + 1)):
            out.append({"i": i, "t": bars[i]["t"], "price": hi, "kind": "H"})
        if all(bars[i - j]["l"] > lo for j in range(1, k + 1)) and \
           all(bars[i + j]["l"] > lo for j in range(1, k + 1)):
            out.append({"i": i, "t": bars[i]["t"], "price": lo, "kind": "L"})
    return out


# ------------------------------------------------------ Pillar 1: bias
def structure_bias(bars: List[Bar]) -> Dict[str, Any]:
    """Read HTF structure from pivot sequence + closes.
    Returns {bias:'long'|'short'|None, trend, last_high, last_low}."""
    pv = pivots(bars, k=2)
    highs = [p for p in pv if p["kind"] == "H"][-3:]
    lows = [p for p in pv if p["kind"] == "L"][-3:]
    if len(highs) < 2 or len(lows) < 2:
        return {"bias": None, "trend": "undefined"}

    hh = highs[-1]["price"] > highs[-2]["price"]
    hl = lows[-1]["price"] > lows[-2]["price"]
    ll = lows[-1]["price"] < lows[-2]["price"]
    lh = highs[-1]["price"] < highs[-2]["price"]
    close = bars[-1]["c"]

    if hh and hl:
        trend = "up"
    elif ll and lh:
        trend = "down"
    else:
        trend = "range"

    # CHoCH check by CLOSE beyond the most recent opposite swing
    if trend == "up" and close < lows[-1]["price"]:
        trend = "range"   # first CHoCH = caution, stand down to range rules
    if trend == "down" and close > highs[-1]["price"]:
        trend = "range"

    bias = "long" if trend == "up" else "short" if trend == "down" else None
    return {"bias": bias, "trend": trend,
            "last_high": highs[-1]["price"], "last_low": lows[-1]["price"]}


# --------------------------------------------- Pillar 2: POIs + pools
def find_pois(bars: List[Bar], direction: str, a: float) -> List[Dict[str, Any]]:
    """Order blocks: last opposite candle before an impulsive leg that broke
    structure (close beyond the prior pivot). Zone refined to candle body.
    Returns newest-last [{lo,hi,t,kind:'OB'|'FVG'}], unmitigated only."""
    pois: List[Dict[str, Any]] = []
    pv = pivots(bars, k=2)
    n = len(bars)

    for i in range(max(2, n - 60), n - 1):
        b = bars[i]
        # impulsive bar = range > 1.2 * ATR and strong body
        rng = b["h"] - b["l"]
        body = abs(b["c"] - b["o"])
        if a <= 0 or rng < 1.2 * a or body < 0.5 * rng:
            continue
        if direction == "long" and b["c"] > b["o"]:
            # did this leg break a prior swing high by close?
            prior_highs = [p["price"] for p in pv if p["kind"] == "H" and p["i"] < i]
            if not prior_highs or b["c"] <= prior_highs[-1]:
                continue
            # last down candle before it = demand OB (body zone)
            for j in range(i - 1, max(i - 6, -1), -1):
                if bars[j]["c"] < bars[j]["o"]:
                    lo = min(bars[j]["o"], bars[j]["c"], bars[j]["l"])
                    hi = max(bars[j]["o"], bars[j]["c"])
                    pois.append({"lo": lo, "hi": hi, "t": bars[j]["t"], "kind": "OB"})
                    break
        elif direction == "short" and b["c"] < b["o"]:
            prior_lows = [p["price"] for p in pv if p["kind"] == "L" and p["i"] < i]
            if not prior_lows or b["c"] >= prior_lows[-1]:
                continue
            for j in range(i - 1, max(i - 6, -1), -1):
                if bars[j]["c"] > bars[j]["o"]:
                    lo = min(bars[j]["o"], bars[j]["c"])
                    hi = max(bars[j]["o"], bars[j]["c"], bars[j]["h"])
                    pois.append({"lo": lo, "hi": hi, "t": bars[j]["t"], "kind": "OB"})
                    break

    # Fair value gaps (3-candle imbalance), same direction
    for i in range(max(2, n - 40), n):
        if direction == "long" and bars[i]["l"] > bars[i - 2]["h"]:
            pois.append({"lo": bars[i - 2]["h"], "hi": bars[i]["l"],
                         "t": bars[i - 1]["t"], "kind": "FVG"})
        elif direction == "short" and bars[i]["h"] < bars[i - 2]["l"]:
            pois.append({"lo": bars[i]["h"], "hi": bars[i - 2]["l"],
                         "t": bars[i - 1]["t"], "kind": "FVG"})

    # drop mitigated zones (price closed through the far side afterwards)
    out = []
    for z in pois:
        idx = next((i for i, b in enumerate(bars) if b["t"] == z["t"]), None)
        if idx is None:
            continue
        violated = False
        for b in bars[idx + 1:]:
            if direction == "long" and b["c"] < z["lo"]:
                violated = True
                break
            if direction == "short" and b["c"] > z["hi"]:
                violated = True
                break
        if not violated:
            out.append(z)
    return out[-5:]


def liquidity_pools(bars: List[Bar], daily: List[Bar], a: float) -> Dict[str, List[float]]:
    """Pools below (sell-side) and above (buy-side): equal lows/highs within
    0.15*ATR tolerance + prior day high/low."""
    tol = 0.15 * a if a > 0 else 0
    pv = pivots(bars, k=2)
    lows = [p["price"] for p in pv if p["kind"] == "L"][-8:]
    highs = [p["price"] for p in pv if p["kind"] == "H"][-8:]

    below, above = [], []
    for i in range(len(lows)):
        for j in range(i + 1, len(lows)):
            if abs(lows[i] - lows[j]) <= tol:
                below.append(min(lows[i], lows[j]))
    for i in range(len(highs)):
        for j in range(i + 1, len(highs)):
            if abs(highs[i] - highs[j]) <= tol:
                above.append(max(highs[i], highs[j]))
    below += lows[-2:]          # recent swing lows are pools too
    above += highs[-2:]
    if len(daily) >= 2:
        below.append(daily[-1]["l"])   # prior day low (last CLOSED daily bar)
        above.append(daily[-1]["h"])
    return {"below": sorted(set(below)), "above": sorted(set(above))}


def detect_sweep(bars: List[Bar], pools: Dict[str, List[float]],
                 direction: str, lookback: int = 6) -> Optional[Dict[str, Any]]:
    """Wick through a pool, close back on the right side, within the last
    `lookback` closed bars. Returns {extreme, pool, t} or None."""
    recent = bars[-lookback:]
    best = None
    for b in recent:
        if direction == "long":
            for pool in pools["below"]:
                if b["l"] < pool and b["c"] > pool:
                    if best is None or b["l"] < best["extreme"]:
                        best = {"extreme": b["l"], "pool": pool, "t": b["t"]}
        else:
            for pool in pools["above"]:
                if b["h"] > pool and b["c"] < pool:
                    if best is None or b["h"] > best["extreme"]:
                        best = {"extreme": b["h"], "pool": pool, "t": b["t"]}
    return best


# ------------------------------------------- Pillar 3: LTF confirmation
def confirmation(ltf: List[Bar], direction: str, zone: Tuple[float, float],
                 after_t: int) -> Optional[Dict[str, Any]]:
    """After time `after_t`, inside/at the zone: LTF CHoCH (close beyond the
    most recent opposite pivot) or engulfing close. Confirmation bar must
    touch the zone (lo..hi) or be within half its height of it."""
    zlo, zhi = zone
    pad = (zhi - zlo) * 0.5
    bars = [b for b in ltf if b["t"] >= after_t]
    if len(bars) < 4:
        return None
    pv = pivots(bars, k=1)

    for i in range(2, len(bars)):
        b = bars[i]
        near = (b["l"] <= zhi + pad and b["h"] >= zlo - pad)
        if not near:
            continue
        if direction == "long":
            lhs = [p["price"] for p in pv if p["kind"] == "H" and p["i"] < i]
            if lhs and b["c"] > lhs[-1]:
                return {"type": "choch", "t": b["t"], "price": b["c"]}
            p = bars[i - 1]
            if b["l"] < p["l"] and b["c"] > max(p["o"], p["c"]):
                return {"type": "engulf", "t": b["t"], "price": b["c"]}
        else:
            lls = [p["price"] for p in pv if p["kind"] == "L" and p["i"] < i]
            if lls and b["c"] < lls[-1]:
                return {"type": "choch", "t": b["t"], "price": b["c"]}
            p = bars[i - 1]
            if b["h"] > p["h"] and b["c"] < min(p["o"], p["c"]):
                return {"type": "engulf", "t": b["t"], "price": b["c"]}
    return None


# ------------------------------------------------------------ analyze
def analyze(symbol: str, trade_mode: str,
            bars_by_tf: Dict[str, List[Bar]],
            params: Dict[str, Any],
            spread: float = 0.0,
            live_price: Optional[float] = None) -> Dict[str, Any]:
    """Run the full SLC checklist for one symbol+mode.
    `spread` (price units) and `live_price` (mid) come from the broker feed:
    spread is charged against RR / padded onto stops; live_price guards
    against analyzing bar data that disagrees with the live market.
    Returns {signal: None|{...}, info: {...}} — info always populated for
    dashboard transparency."""
    tfs = MODE_TFS[trade_mode]
    htf = bars_by_tf.get(tfs["htf"], [])
    mtf = bars_by_tf.get(tfs["mtf"], [])
    ltf = bars_by_tf.get(tfs["ltf"], [])
    daily = bars_by_tf.get("1d", [])
    info: Dict[str, Any] = {"symbol": symbol, "trade_mode": trade_mode}

    if len(htf) < 30 or len(mtf) < 60 or len(ltf) < 30:
        info["note"] = "insufficient history"
        return {"signal": None, "info": info}

    a14 = atr(mtf, 14)
    a100 = atr(mtf, 100)
    regime = (a14 / a100) if a100 > 0 else 1.0
    info["atr"] = a14
    info["regime"] = round(regime, 2)
    if a14 <= 0:
        info["note"] = "ATR unavailable"
        return {"signal": None, "info": info}
    # bar data must agree with the live market (stale / mixed-broker bars)
    if live_price is not None and abs(live_price - mtf[-1]["c"]) > 3 * a14:
        info["note"] = ("bar data out of sync with live price "
                        "(%.5f vs %.5f)" % (mtf[-1]["c"], live_price))
        return {"signal": None, "info": info}

    st = structure_bias(htf)
    info["bias"] = st["bias"]
    info["trend"] = st["trend"]
    if st["bias"] is None:
        info["note"] = "no HTF bias (range/undefined)"
        return {"signal": None, "info": info}
    direction = st["bias"]

    if regime > params["regime_max"]:
        info["note"] = "regime shock (ATR ratio %.2f) — standing aside" % regime
        return {"signal": None, "info": info}

    pois = find_pois(mtf, direction, a14)
    info["pois"] = len(pois)
    if not pois:
        info["note"] = "no unmitigated POI in bias direction"
        return {"signal": None, "info": info}

    pools = liquidity_pools(mtf, daily, a14)
    price = mtf[-1]["c"]

    # nearest POI that price has actually reached recently
    active = None
    for z in reversed(pois):
        touched = any(b["l"] <= z["hi"] and b["h"] >= z["lo"] for b in mtf[-6:])
        if touched:
            active = z
            break
    if active is None:
        info["note"] = "price not at any POI"
        return {"signal": None, "info": info}
    info["poi"] = {"lo": active["lo"], "hi": active["hi"], "kind": active["kind"]}

    sweep = detect_sweep(mtf, pools, direction)
    grade = "A" if sweep else "B"
    info["sweep"] = bool(sweep)

    if grade == "B":
        if params["min_grade"] == "A":
            info["note"] = "B setup (no sweep) — filtered by min_grade=A"
            return {"signal": None, "info": info}
        if regime > params["regime_b_ban"]:
            info["note"] = "B setup banned in expanded regime (%.2f)" % regime
            return {"signal": None, "info": info}
        if st["trend"] not in ("up", "down"):
            info["note"] = "B setups only in clear trends"
            return {"signal": None, "info": info}

    conf_after = sweep["t"] if sweep else active["t"]
    conf = confirmation(ltf, direction, (active["lo"], active["hi"]), conf_after)
    if conf is None:
        info["note"] = "waiting for LTF confirmation at POI"
        return {"signal": None, "info": info}
    info["confirmation"] = conf["type"]

    # --- relative-volume confirmation gate (7th checklist item) ---
    # Participation behind the LTF confirmation: confirmation-bar tick volume
    # vs the trailing 20-bar average on the trigger TF. Gated by params["vol_mult"].
    _vm = float(params.get("vol_mult", 0.0) or 0.0)
    _ci = next((i for i, b in enumerate(ltf) if b["t"] == conf["t"]), None)
    if _ci is not None:
        _win = [b["v"] for b in ltf[max(0, _ci - 20):_ci] if b.get("v")]
        _avg = (sum(_win) / len(_win)) if _win else 0.0
        _relv = (ltf[_ci]["v"] / _avg) if _avg > 0 else 1.0
        info["relvol"] = round(_relv, 2)
        if _vm > 0 and _relv < _vm:
            info["note"] = "confirmation volume %.2fx < gate %.2fx" % (_relv, _vm)
            return {"signal": None, "info": info}

    # ----- build the trade ------------------------------------------------
    # Spread handling: longs fill at ask (entry = price + spread, exits at
    # bid hit the levels as charted); shorts fill at bid but get stopped by
    # the ASK, so the stop is padded one spread beyond the structural level.
    spread = max(0.0, spread or 0.0)
    buffer_ = params["atr_buffer"] * a14
    if direction == "long":
        sl_struct = (sweep["extreme"] if sweep else active["lo"]) - buffer_
        entry = price + spread                     # expected fill (ask)
        sl = sl_struct
        risk = entry - sl
        targets = [p for p in pools["above"] if p > entry + params["min_rr"] * risk]
        tp = min(targets) if targets else entry + params["min_rr"] * risk
        tp1 = entry + risk
    else:
        sl_struct = (sweep["extreme"] if sweep else active["hi"]) + buffer_
        entry = price                              # expected fill (bid)
        sl = sl_struct + spread                    # ask triggers the stop
        risk = sl - entry
        targets = [p for p in pools["below"] if p < entry - params["min_rr"] * risk]
        tp = max(targets) if targets else entry - params["min_rr"] * risk
        tp1 = entry - risk

    if risk <= 0:
        info["note"] = "degenerate risk distance"
        return {"signal": None, "info": info}
    rr = abs(tp - entry) / risk                    # net of spread on both legs
    if rr < params["min_rr"]:
        info["note"] = "RR %.2f below minimum %.1f (after spread)" % (rr, params["min_rr"])
        return {"signal": None, "info": info}

    signal = {
        "symbol": symbol, "trade_mode": trade_mode,
        "side": "buy" if direction == "long" else "sell",
        "grade": grade, "entry": entry, "sl": sl, "tp1": tp1, "tp": tp,
        "rr": round(rr, 2), "atr": a14, "regime": round(regime, 2),
        "spread": spread,
        "setup": {
            "poi": {"lo": active["lo"], "hi": active["hi"], "kind": active["kind"]},
            "sweep": sweep, "confirmation": conf, "trend": st["trend"],
        },
        # key built from the structural stop (pre-spread) so a fluctuating
        # spread can't defeat the dedup
        "key": "%s|%s|%s|%.6f" % (symbol, trade_mode, direction, sl_struct),
    }
    info["note"] = "SIGNAL %s %s (grade %s, RR %.1f)" % (signal["side"], symbol, grade, rr)
    return {"signal": signal, "info": info}
