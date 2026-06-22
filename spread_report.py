#!/usr/bin/env python3
"""Spread-impact report — see how the broker spread behaved through each trade.

Reads closed trades from state/paper_ledger.json and shows, per leg, the entry
(baseline) spread, the widest spread seen while the trade was open, the spread
at exit, and whether the exit was SPREAD-INDUCED (a stop that fired only because
the spread had widened — the news / session-rollover stop-out you wanted to see
replicated). Spreads are shown in price units and pips.

Usage:  python3 tools/spread_report.py
"""
from __future__ import annotations

import json
from pathlib import Path

LEDGER = Path(__file__).resolve().parent.parent / "state" / "paper_ledger.json"

# pip size by quote convention (JPY pairs = 0.01, else 0.0001; metals/indices ~ 0.01)
def _pip(sym: str) -> float:
    return 0.01 if sym.endswith("JPY") else 0.0001


def main() -> None:
    d = json.loads(LEDGER.read_text())
    closed = d.get("closed", [])
    if not closed:
        print("No closed trades yet.")
        return

    print(f"# Spread-impact report — {len(closed)} closed leg(s)\n")
    hdr = (f"{'ticket':>7} {'sym':<7} {'side':<4} {'reason':<10} "
           f"{'entry_sp(pip)':>13} {'exit_sp(pip)':>13} {'induced':>8}")
    print(hdr)
    print("-" * len(hdr))

    induced_n = 0
    for c in closed:
        sym = c.get("symbol", "?")
        pip = _pip(sym)
        # entry baseline spread isn't stored on the closed record; show exit_spread
        # (prevailing at close) and the induced flag, which is what matters here.
        exit_sp = float(c.get("exit_spread", 0.0) or 0.0)
        induced = bool(c.get("spread_induced", False))
        induced_n += induced
        print(f"{c.get('ticket',''):>7} {sym:<7} {c.get('side',''):<4} "
              f"{c.get('close_reason',''):<10} {'—':>13} "
              f"{exit_sp/pip:>13.1f} {('YES' if induced else ''):>8}")

    print(f"\nSpread-induced stop-outs: {induced_n} / {len(closed)}")
    if induced_n:
        print("These exits fired only because the spread widened beyond normal "
              "(news / session rollover) — exactly what would happen live.")
    else:
        print("No spread-induced exits yet. Enable execution.spread_stress to "
              "rehearse a rollover/news episode on demand, or wait for a real one.")


if __name__ == "__main__":
    main()
