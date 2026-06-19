#!/usr/bin/env python3
"""Visual pattern validator.

Pulls real MT5 bars from the running server, replays the SAME detectors the
engine uses (sliding window, so it surfaces historical detections too), and
renders each detected setup as a candlestick chart with entry/SL/TP marked.
You eyeball the images to judge whether the bot is finding real patterns, then
we tune the thresholds.

Read-only: it does NOT trade or change any config.

    pip install matplotlib pyyaml            # one-time
    python3 validate_patterns.py                         # all configured pairs/timeframes
    python3 validate_patterns.py --symbols EURUSD,XAUUSD --timeframes 1h,4h
    python3 validate_patterns.py --max 40                # cap how many charts

Output: strategy-study/pattern-validation/*.png + index.html
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from marketdata.base import Bar          # noqa: E402
from strategy import patterns            # noqa: E402

OUT_DIR = ROOT.parent / "strategy-study" / "pattern-validation"


def load_cfg():
    import yaml
    c = yaml.safe_load((ROOT / "config.yaml").open())
    return c


def fetch_bars(server, symbol, tf):
    url = f"{server.rstrip('/')}/api/chart/{urllib.parse.quote(symbol)}?tf={tf}"
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            data = json.loads(r.read())
    except Exception as exc:  # noqa: BLE001
        print(f"  fetch failed {symbol} {tf}: {exc}")
        return []
    out = []
    for b in data.get("bars", []):
        try:
            out.append(Bar(symbol=symbol, timeframe=tf, time=b["t"],
                           open=float(b["o"]), high=float(b["h"]), low=float(b["l"]),
                           close=float(b["c"]), volume=float(b.get("v", 0) or 0)))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def detect_history(bars, flags, min_bars=40):
    """Replay detectors as each bar completes; return [(detect_index, signal)]."""
    seen, found = set(), []
    for i in range(min_bars, len(bars) + 1):
        for s in patterns.run_all(bars[:i], flags):
            key = (s.setup, s.side, s.detected_at)
            if key in seen:
                continue
            seen.add(key)
            found.append((i - 1, s))
    return found


def find_exit(bars, det_idx, sig):
    """Walk forward from the detection bar to the first candle that hits TP or SL.

    Entry is taken on the bar AFTER detection. Returns (exit_idx, result) where
    result is 'TP', 'SL', or 'OPEN' (never resolved in available bars). If one
    candle straddles both TP and SL we resolve conservatively to 'SL'.
    """
    for j in range(det_idx + 1, len(bars)):
        b = bars[j]
        if sig.side == "sell":
            hit_sl, hit_tp = b.high >= sig.sl, b.low <= sig.tp
        else:
            hit_sl, hit_tp = b.low <= sig.sl, b.high >= sig.tp
        if hit_sl:
            return j, "SL"          # SL first (also covers straddle = worst case)
        if hit_tp:
            return j, "TP"
    return None, "OPEN"


def render(bars, det_idx, sig, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    exit_idx, result = find_exit(bars, det_idx, sig)
    entry_idx = min(det_idx + 1, len(bars) - 1)          # entry on bar after detection
    lo = max(0, det_idx - 45)
    right_anchor = exit_idx if exit_idx is not None else det_idx + 6
    hi = min(len(bars), right_anchor + 4)
    win = bars[lo:hi]
    fig, ax = plt.subplots(figsize=(11, 5.5))
    for k, b in enumerate(win):
        col = "#10b981" if b.close >= b.open else "#ef4444"
        ax.plot([k, k], [b.low, b.high], color=col, linewidth=0.8, zorder=1)
        h = abs(b.close - b.open) or (max(b.high - b.low, 1e-9) * 0.02)
        ax.add_patch(plt.Rectangle((k - 0.3, min(b.open, b.close)), 0.6, h, color=col, zorder=2))
    for level, lab, c in [(sig.entry, "entry", "#3b82f6"), (sig.sl, "SL", "#ef4444"), (sig.tp, "TP", "#10b981")]:
        ax.axhline(level, color=c, linestyle="--", linewidth=1, zorder=0)
        ax.text(0, level, f" {lab} {level:g}", color=c, fontsize=8, va="bottom")

    yspan = (max(b.high for b in win) - min(b.low for b in win)) or 1e-6
    pad = yspan * 0.12

    # ENTRY arrow -> the fill candle, at the entry price
    if lo <= entry_idx < hi:
        ex = entry_idx - lo
        ay = sig.entry + (pad if sig.side == "sell" else -pad)
        ax.annotate("ENTRY", xy=(ex, sig.entry), xytext=(ex, ay), ha="center",
                    fontsize=9, fontweight="bold", color="#3b82f6",
                    arrowprops=dict(arrowstyle="->", color="#3b82f6", lw=1.8))

    # EXIT arrow -> the candle that hit TP or SL
    if exit_idx is not None and lo <= exit_idx < hi:
        xx = exit_idx - lo
        xprice = sig.tp if result == "TP" else sig.sl
        xc = "#10b981" if result == "TP" else "#ef4444"
        ay = xprice + (pad if xprice == max(xprice, sig.entry) else -pad)
        ax.annotate(f"EXIT {result}", xy=(xx, xprice), xytext=(xx, ay), ha="center",
                    fontsize=9, fontweight="bold", color=xc,
                    arrowprops=dict(arrowstyle="->", color=xc, lw=1.8))

    ax.axvline(det_idx - lo, color="#94a3b8", linestyle=":", linewidth=1, zorder=0)
    tag = {"TP": "WIN (TP hit)", "SL": "LOSS (SL hit)", "OPEN": "unresolved in data"}[result]
    ax.set_title(f"{sig.symbol}  {sig.timeframe}  {sig.setup}  {sig.side.upper()}  (rr {sig.rr})  —  {tag}")
    ax.set_xlabel("dotted = detection · ENTRY arrow = fill candle · EXIT arrow = TP/SL candle")
    ax.margins(x=0.01)
    fig.tight_layout()
    fig.savefig(path, dpi=90)
    plt.close(fig)
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="http://localhost:8765")
    ap.add_argument("--symbols", default="config", help="comma list, or 'config' for config.instruments")
    ap.add_argument("--timeframes", default="config", help="comma list, or 'config'")
    ap.add_argument("--max", type=int, default=60, help="max charts to render")
    args = ap.parse_args()

    cfg = load_cfg()
    flags = cfg["strategy"]["patterns"]
    symbols = ([i["display"] for i in cfg["instruments"]] if args.symbols == "config"
               else [s.strip().upper() for s in args.symbols.split(",") if s.strip()])
    tfs = (cfg["timeframes"] if args.timeframes == "config"
           else [t.strip() for t in args.timeframes.split(",") if t.strip()])

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rendered = []
    counts = {}
    results = {"TP": 0, "SL": 0, "OPEN": 0}
    for sym in symbols:
        for tf in tfs:
            bars = fetch_bars(args.server, sym, tf)
            if len(bars) < 45:
                continue
            for det_idx, sig in detect_history(bars, flags):
                counts[sig.setup] = counts.get(sig.setup, 0) + 1
                if len(rendered) >= args.max:
                    continue
                name = f"{sym}_{tf}_{sig.setup}_{len(rendered):03d}.png"
                try:
                    res = render(bars, det_idx, sig, OUT_DIR / name)
                    results[res] = results.get(res, 0) + 1
                    rendered.append((name, sig, res))
                except Exception as exc:  # noqa: BLE001
                    print(f"  render failed {name}: {exc}")

    # index.html
    badge = {"TP": "#10b981", "SL": "#ef4444", "OPEN": "#94a3b8"}
    rows = "\n".join(
        f'<div style="display:inline-block;margin:6px;text-align:center;font:12px sans-serif">'
        f'<img src="{n}" width="460"><br>{s.symbol} {s.timeframe} {s.setup} {s.side} rr{s.rr} '
        f'<b style="color:{badge.get(res, "#000")}">[{res}]</b></div>'
        for n, s, res in rendered)
    resolved = results["TP"] + results["SL"]
    wr = (100 * results["TP"] / resolved) if resolved else 0
    summary = (f"{len(rendered)} charts &middot; TP {results['TP']} / SL {results['SL']} / "
               f"open {results['OPEN']} &middot; win-rate {wr:.0f}% of resolved")
    (OUT_DIR / "index.html").write_text(
        f"<html><body><h2>Pattern validation — {summary}</h2>{rows}</body></html>")

    print(f"\nDetected by setup: {counts or '(none)'}")
    print(f"Outcomes (on rendered): TP={results['TP']} SL={results['SL']} open={results['OPEN']}"
          + (f"  win-rate {wr:.0f}% of resolved" if resolved else ""))
    print(f"Rendered {len(rendered)} chart(s) → strategy-study/pattern-validation/index.html")
    if not rendered:
        print("No detections (or server unreachable / not enough bars). "
              "Is server.py running and feeding bars?")
    return 0


if __name__ == "__main__":
    sys.exit(main())
