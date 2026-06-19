#!/usr/bin/env python3
"""Shadow-outcome report — what did each gate's rejections actually do?

Reads state/shadow_outcomes.jsonl and aggregates hypothetical results by the
check that rejected each signal, by stage, and by clarity-score bucket.

Reading the table:
  - A check whose rejections have NEGATIVE expectancy is earning its keep
    (it blocked losers).
  - A check whose rejections have POSITIVE expectancy is costing money —
    candidate for loosening (e.g. the 0.70 body-ratio or 1.20 ATR-ratio).
Wait for a meaningful sample (50+ outcomes per row) before tuning anything.

Usage (on the Mac, from trading-bot/):
    python3 tools/shadow_report.py             # full report
    python3 tools/shadow_report.py --min 20    # hide rows with < 20 outcomes
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

BOT_ROOT = Path(__file__).resolve().parent.parent
OUTCOMES = BOT_ROOT / "state" / "shadow_outcomes.jsonl"


def load() -> list:
    if not OUTCOMES.exists():
        print(f"No shadow outcomes yet ({OUTCOMES}). "
              "Enable strategy.shadow_mode and let the engine run.")
        sys.exit(0)
    rows = []
    for line in OUTCOMES.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def bucket(rows: list, key) -> dict:
    groups = defaultdict(list)
    for r in rows:
        groups[key(r)].append(r)
    return groups


def stats(rows: list) -> dict:
    n = len(rows)
    wins = sum(1 for r in rows if r.get("outcome") == "win")
    losses = sum(1 for r in rows if r.get("outcome") == "loss")
    timeouts = sum(1 for r in rows if r.get("outcome") == "timeout")
    rs = [float(r.get("r", 0.0)) for r in rows]
    avg_r = sum(rs) / n if n else 0.0
    return {"n": n, "wins": wins, "losses": losses, "timeouts": timeouts,
            "win_pct": (100.0 * wins / n) if n else 0.0, "avg_r": avg_r}


def print_table(title: str, groups: dict, min_n: int) -> None:
    print(f"\n## {title}")
    header = f"{'group':<28} {'n':>5} {'win':>4} {'loss':>5} {'t/o':>4} {'win%':>6} {'avgR':>7}  verdict"
    print(header)
    print("-" * len(header))
    for g, rows in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        s = stats(rows)
        if s["n"] < min_n:
            continue
        if s["n"] < 50:
            verdict = "(small sample)"
        elif s["avg_r"] > 0.2:
            verdict = "REJECTIONS PROFITABLE — gate may be too strict"
        elif s["avg_r"] < -0.2:
            verdict = "gate earning its keep"
        else:
            verdict = "neutral"
        print(f"{str(g):<28} {s['n']:>5} {s['wins']:>4} {s['losses']:>5} "
              f"{s['timeouts']:>4} {s['win_pct']:>5.0f}% {s['avg_r']:>+7.2f}  {verdict}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min", type=int, default=1, help="hide groups with fewer outcomes")
    args = ap.parse_args()

    rows = load()
    print(f"# Shadow report — {len(rows)} resolved hypothetical signal(s)")
    s = stats(rows)
    print(f"Overall: {s['wins']}W / {s['losses']}L / {s['timeouts']}T  "
          f"win {s['win_pct']:.0f}%  avg R {s['avg_r']:+.2f}")

    print_table("By rejecting check (failed_check)", bucket(
        rows, lambda r: r.get("failed_check") or r.get("stage") or "?"), args.min)
    print_table("By stage", bucket(rows, lambda r: r.get("stage") or "?"), args.min)
    print_table("By setup", bucket(rows, lambda r: r.get("setup") or "?"), args.min)
    print_table("By timeframe", bucket(rows, lambda r: r.get("tf") or "?"), args.min)

    def cl_bucket(r):
        c = float(r.get("clarity_score") or 0.0)
        if c <= 0:
            return "unscored"
        lo = int(c // 20) * 20
        return f"{lo}-{lo + 19}"
    print_table("By clarity-score bucket", bucket(rows, cl_bucket), args.min)

    print("\nNote: outcomes are HYPOTHETICAL plain SL/TP fills (no scale-out, "
          "slippage, or commission). Use for gate comparison, not equity projection.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
