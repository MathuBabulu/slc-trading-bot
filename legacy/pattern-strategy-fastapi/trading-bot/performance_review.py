#!/usr/bin/env python3
"""Performance review (read-only).

Reads the paper ledger and produces a performance report + tuning hints.
Touches NO trading code — it only reads state/paper_ledger.json and writes a
markdown report. Safe to run any time, including on a schedule.

    python3 performance_review.py
"""
from __future__ import annotations

import json
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LEDGER = ROOT / "state" / "paper_ledger.json"
SIGNALS = ROOT / "state" / "signals.log"
OUT_DIR = (ROOT.parent / "strategy-study" / "performance")


def _fmt(n, d=2):
    try:
        return f"{float(n):,.{d}f}"
    except (TypeError, ValueError):
        return "—"


def load_ledger():
    if not LEDGER.exists():
        return None
    try:
        return json.loads(LEDGER.read_text())
    except Exception as exc:  # noqa: BLE001
        return {"_error": str(exc)}


def group_metrics(records):
    """Metrics over a list of closed-trade records (legs)."""
    n = len(records)
    pnl = sum(r.get("pnl", 0) for r in records)
    wins = [r for r in records if r.get("pnl", 0) > 0]
    losses = [r for r in records if r.get("pnl", 0) < 0]
    gross_w = sum(r["pnl"] for r in wins)
    gross_l = abs(sum(r["pnl"] for r in losses))
    return {
        "legs": n,
        "net": pnl,
        "win_rate": (len(wins) / n * 100) if n else 0,
        "profit_factor": (gross_w / gross_l) if gross_l > 0 else (float("inf") if gross_w > 0 else 0),
        "avg_rr": statistics.mean([r.get("rr", 0) for r in records]) if n else 0,
        "wins": len(wins), "losses": len(losses),
    }


def by_key(records, key):
    buckets = defaultdict(list)
    for r in records:
        buckets[r.get(key) or "—"].append(r)
    return {k: group_metrics(v) for k, v in sorted(buckets.items())}


def per_ticket(records):
    """Group legs by ticket → one outcome per actual trade (handles scale-outs)."""
    t = defaultdict(float)
    for r in records:
        t[r.get("ticket")] += r.get("pnl", 0)
    vals = list(t.values())
    wins = sum(1 for v in vals if v > 0)
    return {"trades": len(vals), "wins": wins,
            "win_rate": (wins / len(vals) * 100) if vals else 0}


def max_drawdown(records, start_eq):
    eq = start_eq
    peak = start_eq
    mdd = 0.0
    for r in sorted(records, key=lambda x: x.get("close_time", "")):
        eq += r.get("pnl", 0)
        peak = max(peak, eq)
        mdd = max(mdd, peak - eq)
    return mdd


def signal_funnel(hours: float = 26.0):
    """Summarize the engine's signal decisions over the recent window from
    state/signals.log: how many setups were detected, accepted, and rejected
    by which gate. Answers 'why were/weren't trades taken?'."""
    if not SIGNALS.exists():
        return None
    cutoff = datetime.now(timezone.utc).timestamp() - hours * 3600
    detected = accepted = filled = 0
    by_stage = defaultdict(int)
    last_rejections = []
    try:
        for line in SIGNALS.read_text().splitlines():
            try:
                r = json.loads(line)
            except Exception:
                continue
            ts = r.get("ts", "")
            try:
                t = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
            except Exception:
                t = cutoff  # keep if unparseable
            if t < cutoff:
                continue
            ev = r.get("event", "")
            if ev == "signal:accepted":
                accepted += 1; detected += 1
            elif ev == "signal:rejected":
                detected += 1
                by_stage[r.get("stage") or "?"] += 1
                last_rejections.append(r)
            elif ev == "order:filled":
                filled += 1
    except Exception:
        return None
    return {"detected": detected, "accepted": accepted, "filled": filled,
            "by_stage": dict(by_stage), "recent": last_rejections[-6:]}


def build_report() -> str:
    led = load_ledger()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    if led is None:
        return f"# Performance review — {now}\n\nNo paper ledger yet (no trades closed)."
    if "_error" in led:
        return f"# Performance review — {now}\n\nCould not read ledger: {led['_error']}"

    start_eq = led.get("starting_equity", 0)
    equity = led.get("equity", start_eq)
    closed = led.get("closed", []) or []
    openp = led.get("open", []) or []

    L = []
    L.append(f"# Performance review — {now}\n")
    L.append(f"- Starting equity: **{_fmt(start_eq)}**  |  Current equity: **{_fmt(equity)}**  "
             f"(**{_fmt(equity-start_eq)}**, {_fmt((equity-start_eq)/start_eq*100 if start_eq else 0)}%)")
    L.append(f"- Open positions: **{len(openp)}**  |  Closed legs: **{len(closed)}**")

    # Signal funnel — explains activity (detected → accepted / rejected by gate).
    fn = signal_funnel()
    if fn is not None:
        L.append("\n## Signal funnel (last ~24h)")
        L.append(f"- Setups detected: **{fn['detected']}**  |  accepted: **{fn['accepted']}**  |  "
                 f"orders filled: **{fn['filled']}**")
        if fn["by_stage"]:
            L.append("- Rejected by gate: " +
                     ", ".join(f"{k}: {v}" for k, v in sorted(fn["by_stage"].items(), key=lambda x: -x[1])))
        if fn["detected"] == 0:
            L.append("- **No setups detected at all** in the window → the engine isn't seeing bars "
                     "(restart server.py for the bar-persistence fix, and confirm the EA is pushing).")
        elif fn["accepted"] == 0:
            L.append("- Setups were found but **all were filtered** by the gates above — the strategy is "
                     "being selective, not idle.")
    else:
        L.append("\n_(No signal log yet — restart server.py to start recording the signal funnel.)_")

    if not closed:
        L.append("\n_No closed trades yet — nothing to analyse._")
        return "\n".join(L)

    m = group_metrics(closed)
    pt = per_ticket(closed)
    mdd = max_drawdown(closed, start_eq)
    pf = "∞" if m["profit_factor"] == float("inf") else _fmt(m["profit_factor"])
    L.append("\n## Overall")
    L.append(f"- Trades (by ticket): **{pt['trades']}**  |  Win rate: **{_fmt(pt['win_rate'],0)}%** "
             f"({pt['wins']}W)")
    L.append(f"- Net P&L: **{_fmt(m['net'])}**  |  Profit factor: **{pf}**  |  "
             f"Avg R: **{_fmt(m['avg_rr'])}**  |  Max drawdown: **{_fmt(mdd)}**")
    L.append(f"- Closed legs: {m['legs']} ({m['wins']}W / {m['losses']}L) "
             f"— note: scale-outs produce 2 legs per trade")

    def table(title, d):
        L.append(f"\n## By {title}")
        L.append("| " + title + " | Legs | Net | Win% | PF | Avg R |")
        L.append("|---|---:|---:|---:|---:|---:|")
        for k, g in sorted(d.items(), key=lambda kv: kv[1]["net"]):
            pf = "∞" if g["profit_factor"] == float("inf") else _fmt(g["profit_factor"])
            L.append(f"| {k} | {g['legs']} | {_fmt(g['net'])} | {_fmt(g['win_rate'],0)}% | {pf} | {_fmt(g['avg_rr'])} |")

    table("setup", by_key(closed, "setup"))
    table("pair", by_key(closed, "symbol"))
    table("timeframe", by_key(closed, "timeframe"))

    # exit-reason mix (validates the scale-out behaviour)
    rmix = defaultdict(int)
    for r in closed:
        rmix[r.get("close_reason", "?")] += 1
    L.append("\n## Exit reasons")
    L.append(", ".join(f"{k}: {v}" for k, v in sorted(rmix.items(), key=lambda x: -x[1])))

    # ---- tuning hints (suggestions only — applied only on your approval) ----
    hints = []
    MIN_N = 8   # need a meaningful sample before suggesting changes
    for label, d in (("setup", by_key(closed, "setup")),
                     ("pair", by_key(closed, "symbol")),
                     ("timeframe", by_key(closed, "timeframe"))):
        for k, g in d.items():
            if g["legs"] >= MIN_N and g["win_rate"] < 35 and g["net"] < 0:
                hints.append(f"- **{label} `{k}`** is a drag: {g['legs']} legs, "
                             f"{_fmt(g['win_rate'],0)}% win, net {_fmt(g['net'])}. "
                             f"Consider disabling it in the dashboard.")
    if m["profit_factor"] != float("inf") and m["profit_factor"] < 1 and pt["trades"] >= 10:
        hints.append("- Overall **profit factor < 1** over a meaningful sample — review the "
                     "choppiness/correlation thresholds before risking more.")
    if mdd > 0.15 * start_eq:
        hints.append(f"- **Drawdown {_fmt(mdd)}** exceeds 15% of starting capital — consider "
                     f"lowering `per_trade_pct` or the daily/weekly caps.")
    L.append("\n## Tuning suggestions")
    L.append("\n".join(hints) if hints else "_Nothing flagged — sample still small or metrics healthy. "
             "Keep collecting paper trades._")

    L.append("\n---\n_Read-only review. No settings were changed. "
             "Tuning is applied only after you approve a suggestion._")
    return "\n".join(L)


def main():
    report = build_report()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    (OUT_DIR / f"review_{stamp}.md").write_text(report)
    (OUT_DIR / "latest.md").write_text(report)
    print(report)
    print(f"\n[saved to strategy-study/performance/review_{stamp}.md]")


if __name__ == "__main__":
    main()
