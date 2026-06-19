#!/usr/bin/env python3
"""Pattern-strategy hallucination / sanity check.

Verifies the bot is NOT "hallucinating" chart patterns — i.e. that every signal
it acted on is grounded in the actual OHLC bars that supposedly formed it. For
each trade journal it re-derives the pattern's claimed structure from the signal
notes and checks it against the stored `pattern_bars`:

  1. Claimed peaks/troughs (and valley/crest) actually exist as bar highs/lows.
  2. Entry equals the pattern level (the R2 retest level).
  3. Stop-loss is on the correct side and beyond the pattern extreme.
  4. Reward:risk matches the signal's stated RR.
  5. Trade side matches the pattern type (DT/HS/TT/TL → sell; DB/IHS/TB → buy).
  6. Clarity score is in range, and every referenced price sits inside the bar
     window (a level outside [min low, max high] = a fabricated level).

Deterministic: it reads only stored data and arithmetic — the audit itself
cannot hallucinate. Exit code 1 if any FAIL, 2 on WARN-only, 0 if all clean.

Usage:
    python3 tools/pattern_sanity_check.py            # last 15 trades
    python3 tools/pattern_sanity_check.py --n 50     # last 50
    python3 tools/pattern_sanity_check.py --symbol CADJPY
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JOURNAL = os.path.join(ROOT, "state", "trade_journal")

SELL_SETUPS = {"DT", "HS", "TT", "RECT_TOP", "TL_SELL"}
BUY_SETUPS = {"DB", "IHS", "TB", "RECT_BOT", "TL_BUY"}

FLOAT = r"(\d+\.\d+)"


def _nums(notes, pattern):
    for n in notes:
        m = re.search(pattern, n)
        if m:
            return [float(g) for g in m.groups()]
    return []


def _nearest(value, candidates):
    """Smallest absolute distance from `value` to any candidate price."""
    return min((abs(value - c) for c in candidates), default=float("inf"))


def check_trade(j: dict) -> dict:
    sig = j.get("signal", {})
    setup = (j.get("setup") or sig.get("setup") or "?").upper()
    side = (j.get("side") or sig.get("side") or "?").lower()
    entry = float(sig.get("entry", j.get("entry_price", 0)) or 0)
    sl = float(sig.get("sl", 0) or 0)
    tp = float(sig.get("tp", 0) or 0)
    rr = float(sig.get("rr", 0) or 0)
    level = float(sig.get("pattern_level", entry) or entry)
    clarity = float(sig.get("clarity_score", -1) or -1)
    notes = sig.get("notes", []) or []
    bars = j.get("pattern_bars", []) or []
    fails, warns = [], []

    highs = [float(b["high"]) for b in bars if isinstance(b, dict) and "high" in b]
    lows = [float(b["low"]) for b in bars if isinstance(b, dict) and "low" in b]
    if len(highs) < 3:
        return {"ticket": j.get("ticket"), "symbol": j.get("symbol"),
                "setup": setup, "status": "WARN",
                "issues": ["no pattern_bars stored — cannot verify"]}

    win_hi, win_lo = max(highs), min(lows)
    atr_list = _nums(notes, r"ATR\s+" + FLOAT)
    atr = atr_list[0] if atr_list else (win_hi - win_lo) * 0.1
    tol = max(atr * 0.75, (win_hi + win_lo) / 2 * 0.0008)   # generous match tolerance

    # 1. Claimed extremes exist in the bars.
    peaks = _nums(notes, r"peaks? at\s+" + FLOAT + r"\s+and\s+" + FLOAT)
    troughs = _nums(notes, r"troughs? at\s+" + FLOAT + r"\s+and\s+" + FLOAT)
    valley = _nums(notes, r"[Vv]alley low\s+" + FLOAT)
    crest = _nums(notes, r"[Cc]rest high\s+" + FLOAT)

    for p in peaks:
        if _nearest(p, highs) > tol:
            fails.append(f"claimed peak {p:.5f} not found among bar highs (nearest off by "
                         f"{_nearest(p, highs):.5f} > tol {tol:.5f})")
    for t in troughs:
        if _nearest(t, lows) > tol:
            fails.append(f"claimed trough {t:.5f} not found among bar lows")
    for v in valley:
        if _nearest(v, lows) > tol:
            warns.append(f"valley {v:.5f} not matched to a bar low")
    for c in crest:
        if _nearest(c, highs) > tol:
            warns.append(f"crest {c:.5f} not matched to a bar high")
    if peaks and abs(peaks[0] - peaks[1]) > 3 * tol:
        warns.append(f"DT peaks {peaks} not roughly equal (>{3*tol:.5f} apart)")
    if troughs and abs(troughs[0] - troughs[1]) > 3 * tol:
        warns.append(f"DB troughs {troughs} not roughly equal")

    # 2. Entry == pattern level.
    if level and entry and abs(level - entry) > tol:
        warns.append(f"entry {entry:.5f} ≠ pattern level {level:.5f}")

    # 3. Stop on the correct side and beyond the extreme.
    if side == "sell" and not (sl > entry):
        fails.append(f"sell SL {sl:.5f} not above entry {entry:.5f}")
    if side == "buy" and not (sl < entry):
        fails.append(f"buy SL {sl:.5f} not below entry {entry:.5f}")
    if side == "sell" and peaks and sl < max(peaks) - tol:
        warns.append(f"sell SL {sl:.5f} not beyond the peaks {max(peaks):.5f}")
    if side == "buy" and troughs and sl > min(troughs) + tol:
        warns.append(f"buy SL {sl:.5f} not beyond the troughs {min(troughs):.5f}")

    # 4. RR consistency.
    risk = abs(entry - sl)
    if risk > 0 and rr > 0:
        actual_rr = abs(tp - entry) / risk
        if abs(actual_rr - rr) > 0.15:
            fails.append(f"RR mismatch: stated {rr:.2f} but tp/sl geometry = {actual_rr:.2f}")

    # 5. Side matches the setup type.
    if setup in SELL_SETUPS and side != "sell":
        fails.append(f"{setup} should be a sell, but side={side}")
    if setup in BUY_SETUPS and side != "buy":
        fails.append(f"{setup} should be a buy, but side={side}")

    # 6. Clarity range + every level inside the bar window.
    if clarity >= 0 and not (0 <= clarity <= 100):
        fails.append(f"clarity {clarity} out of 0–100")
    pad = tol
    for name, val in (("entry", entry), ("sl", sl), ("tp", tp), ("level", level)):
        if val and not (win_lo - pad * 6 <= val <= win_hi + pad * 6):
            # entry/level must be within the window; sl/tp may sit just beyond it
            if name in ("entry", "level"):
                fails.append(f"{name} {val:.5f} outside the pattern window "
                             f"[{win_lo:.5f}, {win_hi:.5f}] — fabricated level")

    status = "FAIL" if fails else ("WARN" if warns else "PASS")
    return {"ticket": j.get("ticket"), "symbol": j.get("symbol"), "setup": setup,
            "side": side, "status": status, "issues": fails + [f"(warn) {w}" for w in warns]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=15)
    ap.add_argument("--symbol", default=None)
    args = ap.parse_args()

    files = sorted(glob.glob(os.path.join(JOURNAL, "*.json")), key=os.path.getmtime)
    files = files[-args.n:]
    results = []
    for f in files:
        try:
            j = json.load(open(f))
        except Exception:
            continue
        if args.symbol and (j.get("symbol", "").upper() != args.symbol.upper()):
            continue
        results.append(check_trade(j))

    print(f"# Pattern-strategy hallucination check — {len(results)} trade(s)\n")
    n_fail = n_warn = 0
    for r in results:
        n_fail += r["status"] == "FAIL"
        n_warn += r["status"] == "WARN"
        mark = {"PASS": "✓", "WARN": "▲", "FAIL": "✗"}[r["status"]]
        print(f"{mark} #{r['ticket']} {r['symbol']:7} {r['setup']:4} {r['status']}")
        for issue in r.get("issues", []):
            print(f"      - {issue}")

    print(f"\nSummary: {len(results)-n_fail-n_warn} clean, {n_warn} warn, {n_fail} FAIL")
    if n_fail:
        print("VERDICT: FAIL — the strategy logged pattern(s) not supported by the bars. "
              "Investigate the detector before trusting new signals.")
        return 1
    if n_warn:
        print("VERDICT: WARN — minor mismatches; review but not necessarily hallucination.")
        return 2
    print("VERDICT: PASS — every pattern is grounded in its bars.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
