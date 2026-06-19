#!/usr/bin/env python3
"""Corrected shadow report — strips look-ahead-contaminated outcomes.

The historical state/shadow_outcomes.jsonl was produced BEFORE the entry-bar
guard fix, so most rows "resolved" on the entry bar itself (instant TP). Those
rows are not real forward outcomes. This report re-scores the data the way the
fixed engine would have recorded it:

A resolved outcome is LOOK-AHEAD CONTAMINATED if either
  * bars_seen <= 1                     (resolved on its own entry bar), or
  * it resolved faster than one bar of its own timeframe
    (e.g. a 1d signal that "hit TP" in 23 seconds — physically impossible).

Only the clean remainder is a legitimate forward-tracked outcome. We print the
overall win rate before vs after, and the per-rejecting-check table for the
clean subset.

Usage:  python3 tools/shadow_report_corrected.py
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

OUTCOMES = Path(__file__).resolve().parent.parent / "state" / "shadow_outcomes.jsonl"

BAR_SECONDS = {"15m": 900, "30m": 1800, "1h": 3600, "2h": 7200,
               "4h": 14400, "1d": 86400}


def _parse(t):
    try:
        return datetime.fromisoformat(t.replace("Z", "+00:00"))
    except Exception:
        return None


def _contaminated(r) -> bool:
    if int(r.get("bars_seen", 0)) <= 1:
        return True
    a, b = _parse(r.get("registered_at", "")), _parse(r.get("resolved_at", ""))
    if a and b:
        interval = BAR_SECONDS.get(r.get("tf", ""), 3600)
        if (b - a).total_seconds() < interval:
            return True
    return False


def _winrate(rows):
    n = len(rows)
    w = sum(1 for r in rows if r.get("outcome") == "win")
    avg_r = sum(float(r.get("r", 0.0)) for r in rows) / n if n else 0.0
    return n, w, (100.0 * w / n if n else 0.0), avg_r


def main() -> None:
    rows = [json.loads(l) for l in OUTCOMES.read_text().splitlines() if l.strip()]
    clean = [r for r in rows if not _contaminated(r)]
    dirty = [r for r in rows if _contaminated(r)]

    n0, w0, wr0, ar0 = _winrate(rows)
    n1, w1, wr1, ar1 = _winrate(clean)

    print("# Corrected shadow report")
    print(f"\nRaw file               : {n0} outcomes, win {wr0:.0f}%, avgR {ar0:+.2f}")
    print(f"Look-ahead contaminated: {len(dirty)} outcomes  (dropped)")
    print(f"Legitimate (clean)     : {n1} outcomes, win {wr1:.0f}%, avgR {ar1:+.2f}")

    if not clean:
        print("\n>> No legitimately-resolved outcomes remain. The entire shadow")
        print(">> sample was look-ahead fills. The fixed engine must re-accumulate")
        print(">> clean forward-tracked data before any gate can be tuned.")
        return

    groups = defaultdict(list)
    for r in clean:
        groups[r.get("failed_check", "?")].append(r)
    print("\n## Clean outcomes by rejecting check")
    print(f"{'group':<22}{'n':>5}{'win':>5}{'win%':>7}{'avgR':>8}")
    print("-" * 47)
    for g, rs in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        n, w, wr, ar = _winrate(rs)
        print(f"{g:<22}{n:>5}{w:>5}{wr:>6.0f}%{ar:>8.2f}")


if __name__ == "__main__":
    main()
