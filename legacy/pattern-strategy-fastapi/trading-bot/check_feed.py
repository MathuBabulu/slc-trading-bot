#!/usr/bin/env python3
"""Feed-coverage checker.

Answers: "Is the server receiving data from MT5 for all my configured pairs?"

Run this on the machine where server.py is running:

    cd trading-bot && python check_feed.py
    # or point at another host/port:
    python check_feed.py --url http://127.0.0.1:8765

It queries the server's live endpoints and reports, for every pair in
config.yaml, whether a live price snapshot and OHLCV bars (per timeframe) are
arriving, plus how stale the data is. Nothing here touches MT5 directly — it
just reads what the EA has pushed into the server.

NOTE: the feed is a ~5s price snapshot + ~60s bar push, NOT tick-by-tick.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

try:
    import yaml
except ImportError:
    print("pyyaml not installed — run inside your venv (pip install pyyaml).")
    sys.exit(1)

ROOT = Path(__file__).resolve().parent


def _get(url: str, timeout: float = 5.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _age(iso: str | None) -> str:
    if not iso:
        return "never"
    try:
        ts = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        secs = (datetime.now(timezone.utc) - ts).total_seconds()
        if secs < 90:
            return f"{secs:.0f}s ago"
        if secs < 5400:
            return f"{secs/60:.0f}m ago"
        return f"{secs/3600:.1f}h ago"
    except Exception:
        return iso


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8765", help="server base URL")
    args = ap.parse_args()
    base = args.url.rstrip("/")

    cfg = yaml.safe_load((ROOT / "config.yaml").open())
    pairs = [i["display"] for i in cfg["instruments"]]
    tfs = cfg["timeframes"]

    # ---- reach the server ----
    try:
        status = _get(f"{base}/api/status")
    except Exception as exc:
        print(f"✗ Cannot reach server at {base}: {exc}")
        print("  Is server.py running? Try:  cd trading-bot && python server.py")
        return 2

    print(f"✓ Server reachable at {base}")
    print(f"  running={status.get('running')} mode={status.get('mode')} "
          f"source={status.get('data_source')}")

    # ---- prices feed ----
    prices = _get(f"{base}/api/mt5/prices")
    price_syms = {str(p.get("symbol", "")).upper().split('.')[0].split('_')[0]
                  for p in prices.get("prices", [])}
    print(f"\nLIVE PRICES: {len(price_syms)} symbol(s) arriving "
          f"(updated {_age(prices.get('ts'))})")
    has_tick = sum(1 for p in prices.get("prices", []) if p.get("tick_value"))
    print(f"  of which {has_tick} carry tick_value (broker-exact sizing)")

    # ---- bars feed ----
    bars = _get(f"{base}/api/mt5_bars")
    bar_syms = {k.upper().split('.')[0].split('_')[0]: v
                for k, v in (bars.get("symbols") or {}).items()}
    print(f"\nBARS: received {_age(bars.get('received_at'))}, "
          f"tz_offset={bars.get('tz_offset_sec')}s")

    # ---- coverage table ----
    print(f"\nCOVERAGE for {len(pairs)} configured pairs "
          f"(P=price, then bar counts per timeframe {tfs}):\n")
    hdr = f"  {'PAIR':<8} {'P':>2}  " + " ".join(f"{t:>5}" for t in tfs)
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    missing_price, missing_bars = [], []
    for sym in pairs:
        s = sym.upper().split('.')[0].split('_')[0]
        p_ok = "✓" if s in price_syms else "·"
        if s not in price_syms:
            missing_price.append(sym)
        tf_counts = bar_syms.get(s, {})
        cells = []
        any_bar = False
        for t in tfs:
            c = tf_counts.get(t, 0)
            any_bar = any_bar or c > 0
            cells.append(f"{c:>5}" if c else f"{'·':>5}")
        if not any_bar:
            missing_bars.append(sym)
        print(f"  {sym:<8} {p_ok:>2}  " + " ".join(cells))

    # ---- summary ----
    print("\nSUMMARY")
    print(f"  prices arriving : {len(pairs) - len(missing_price)}/{len(pairs)}")
    print(f"  bars arriving   : {len(pairs) - len(missing_bars)}/{len(pairs)}")
    if missing_price:
        print(f"  NO price for    : {', '.join(missing_price)}")
    if missing_bars:
        print(f"  NO bars for     : {', '.join(missing_bars)}")
    if missing_price or missing_bars:
        print("\n  A pair is missing when: it's not enabled in the dashboard Pairs")
        print("  Manager, OR your broker doesn't offer that symbol name, OR the name")
        print("  differs (add it to the EA's SymbolAliases). Turn on the EA's")
        print("  VerboseLog to see which symbols it skipped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
