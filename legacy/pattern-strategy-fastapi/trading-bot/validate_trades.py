#!/usr/bin/env python3
"""Visual validator for the agent's ACTUAL executed trades.

Unlike validate_patterns.py (which replays the detectors over history and shows
hypothetical setups), this reads the real paper-trading ledger and draws every
trade the bot actually took — entry candle, SL/TP, and each exit leg (TP, partial
TP, break-even, trailed stop, SL) on the real price bars. You eyeball each one to
verify the bot traded the pattern correctly and give feedback.

Read-only: it never trades or edits the ledger/config.

    pip install matplotlib pyyaml            # one-time
    python3 validate_trades.py                          # all closed trades in the ledger
    python3 validate_trades.py --symbols EURUSD,XAUUSD  # filter
    python3 validate_trades.py --max 60

Output: strategy-study/trade-validation/*.png + index.html
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from validate_patterns import fetch_bars      # noqa: E402  (reuses the chart fetcher)

LEDGER = ROOT / "state" / "paper_ledger.json"
JOURNAL_DIR = ROOT / "state" / "trade_journal"
OUT_DIR = ROOT.parent / "strategy-study" / "trade-validation"

# colour + label per close reason
REASON = {
    "tp":         ("#10b981", "TP"),
    "tp_partial": ("#10b981", "TP 50%"),
    "be":         ("#94a3b8", "Break-even"),
    "trail":      ("#f59e0b", "Trail stop"),
    "sl":         ("#ef4444", "SL"),
}


def parse_dt(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def load_cfg():
    import yaml
    return yaml.safe_load((ROOT / "config.yaml").open())


def load_trades():
    d = json.loads(LEDGER.read_text())
    return d.get("closed", []), d.get("open", []), d


def journal_bars(ticket):
    """If a trade journal exists for this ticket, return (record, [Bar,...]) built
    from its captured pattern + in-between bars. These survive even after the live
    chart window has rolled off, and carry the exact entry time. Else None."""
    from marketdata.base import Bar
    p = JOURNAL_DIR / f"{ticket}.json"
    if not p.exists():
        return None
    try:
        rec = json.loads(p.read_text())
    except Exception:  # noqa: BLE001
        return None
    raw = (rec.get("pattern_bars") or []) + (rec.get("trade_bars") or [])
    if not raw:
        return None
    by_t = {b["time"]: b for b in raw if "time" in b}
    bars = []
    for b in sorted(by_t.values(), key=lambda x: x["time"]):
        try:
            bars.append(Bar(symbol=rec["symbol"], timeframe=rec["timeframe"], time=b["time"],
                            open=float(b["open"]), high=float(b["high"]), low=float(b["low"]),
                            close=float(b["close"]), volume=float(b.get("volume", 0) or 0)))
        except (KeyError, TypeError, ValueError):
            continue
    return (rec, bars) if bars else None


def group_by_ticket(closed):
    """Group legs sharing a ticket into one trade lifecycle, ordered by close_time."""
    groups = {}
    for t in closed:
        groups.setdefault(t["ticket"], []).append(t)
    for legs in groups.values():
        legs.sort(key=lambda x: parse_dt(x.get("close_time")) or datetime.min.replace(tzinfo=timezone.utc))
    return groups


def idx_at_or_before(bar_dts, target):
    """Index of the last bar whose time <= target; else nearest."""
    best = None
    for i, bt in enumerate(bar_dts):
        if bt is None:
            continue
        if bt <= target:
            best = i
        else:
            break
    if best is not None:
        return best
    # target before first bar -> 0; after last handled above
    return 0 if bar_dts else None


def locate_entry(bars, first_exit_idx, entry_price):
    """Find the candle the trade most likely filled on: searching back from the
    first exit, the most recent bar whose range straddles the entry price."""
    tol = max(abs(entry_price) * 1e-4, 1e-9)
    for i in range(first_exit_idx, -1, -1):
        if bars[i].low - tol <= entry_price <= bars[i].high + tol:
            return i
    return max(0, first_exit_idx - 8)   # fallback: a bit before the first exit


def render(bars, entry_idx, legs, meta, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    bar_dts = [parse_dt(b.time) for b in bars]
    side, entry = meta["side"], meta["entry"]
    sl = next((l["sl"] for l in legs if l.get("sl")), 0.0)
    tp = next((l["tp"] for l in legs if l.get("tp")), 0.0)

    exit_pts = []
    for l in legs:
        ed = parse_dt(l.get("close_time"))
        ei = idx_at_or_before(bar_dts, ed) if ed else None
        if ei is not None:
            exit_pts.append((ei, l))

    lo = max(0, entry_idx - 40)
    right = max([ei for ei, _ in exit_pts] + [entry_idx + 6])
    hi = min(len(bars), right + 4)
    win = bars[lo:hi]
    if len(win) < 3:
        return False

    fig, ax = plt.subplots(figsize=(11, 5.5))
    for k, b in enumerate(win):
        col = "#10b981" if b.close >= b.open else "#ef4444"
        ax.plot([k, k], [b.low, b.high], color=col, linewidth=0.8, zorder=1)
        h = abs(b.close - b.open) or (max(b.high - b.low, 1e-9) * 0.02)
        ax.add_patch(plt.Rectangle((k - 0.3, min(b.open, b.close)), 0.6, h, color=col, zorder=2))

    levels = [(entry, "entry", "#3b82f6")]
    if sl:
        levels.append((sl, "SL", "#ef4444"))
    if tp:
        levels.append((tp, "TP", "#10b981"))
    for level, lab, c in levels:
        ax.axhline(level, color=c, linestyle="--", linewidth=1, zorder=0)
        ax.text(0, level, f" {lab} {level:g}", color=c, fontsize=8, va="bottom")

    yspan = (max(b.high for b in win) - min(b.low for b in win)) or 1e-6
    pad = yspan * 0.12

    # ENTRY arrow
    if lo <= entry_idx < hi:
        ex = entry_idx - lo
        ay = entry + (pad if side == "sell" else -pad)
        ax.annotate("ENTRY", xy=(ex, entry), xytext=(ex, ay), ha="center",
                    fontsize=9, fontweight="bold", color="#3b82f6",
                    arrowprops=dict(arrowstyle="->", color="#3b82f6", lw=1.8))

    # EXIT arrows — one per leg
    for ei, l in exit_pts:
        if not (lo <= ei < hi):
            continue
        c, lab = REASON.get(l.get("close_reason", ""), ("#6b7280", l.get("close_reason", "exit")))
        xx = ei - lo
        price = l.get("exit", entry)
        ay = price + (pad if price >= entry else -pad)
        ax.annotate(f"{lab}\n{l.get('pnl', 0):+.2f}", xy=(xx, price), xytext=(xx, ay),
                    ha="center", fontsize=8, fontweight="bold", color=c,
                    arrowprops=dict(arrowstyle="->", color=c, lw=1.8))

    total = sum(l.get("pnl", 0) for l in legs)
    reasons = " + ".join(REASON.get(l.get("close_reason", ""), ("", l.get("close_reason", "?")))[1] for l in legs)
    ax.set_title(f"{meta['symbol']}  {meta['timeframe']}  {meta['setup']}  {side.upper()}  "
                 f"#{meta['ticket']}  —  net {total:+.2f}  ({reasons})")
    ax.set_xlabel("ENTRY arrow = fill candle · exit arrows = actual closes (reason + P&L)")
    ax.margins(x=0.01)
    fig.tight_layout()
    fig.savefig(path, dpi=90)
    plt.close(fig)
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="http://localhost:8765")
    ap.add_argument("--symbols", default="all", help="comma list, or 'all'")
    ap.add_argument("--max", type=int, default=100)
    args = ap.parse_args()

    closed, open_, raw = load_trades()
    if not closed:
        print("No closed trades in the ledger yet.")
        return 0
    filt = None if args.symbols == "all" else {s.strip().upper() for s in args.symbols.split(",")}

    groups = group_by_ticket(closed)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    rendered, missing = [], []
    bars_cache = {}
    for ticket, legs in sorted(groups.items(),
                               key=lambda kv: parse_dt(kv[1][0].get("close_time")) or datetime.min.replace(tzinfo=timezone.utc)):
        first = legs[0]
        sym, tf = first["symbol"], first["timeframe"]
        if filt and sym not in filt:
            continue
        meta = {"symbol": sym, "timeframe": tf, "side": first["side"],
                "setup": first["setup"], "entry": first["entry"], "ticket": ticket}

        # Prefer the trade journal (exact entry time + bars that survive roll-off).
        src = "journal"
        jb = journal_bars(ticket)
        if jb:
            jrec, bars = jb
            bar_dts = [parse_dt(b.time) for b in bars]
            entry_dt = parse_dt(jrec.get("entry_time")) or parse_dt(first.get("close_time"))
            entry_idx = idx_at_or_before(bar_dts, entry_dt) if entry_dt else 0
        else:
            src = "server"
            key = (sym, tf)
            if key not in bars_cache:
                bars_cache[key] = fetch_bars(args.server, sym, tf)
            bars = bars_cache[key]
            if len(bars) < 5:
                missing.append((ticket, sym, tf, "no journal + no bars from server"))
                continue
            bar_dts = [parse_dt(b.time) for b in bars]
            first_exit_dt = parse_dt(first.get("close_time"))
            if first_exit_dt is None or bar_dts[-1] is None or first_exit_dt < bar_dts[0] or first_exit_dt > bar_dts[-1]:
                missing.append((ticket, sym, tf, "no journal + trade older than server's bar window (rolled off)"))
                continue
            first_exit_idx = idx_at_or_before(bar_dts, first_exit_dt)
            entry_idx = locate_entry(bars, first_exit_idx, first["entry"])

        name = f"{sym}_{tf}_{meta['setup']}_{ticket}.png"
        try:
            if render(bars, entry_idx, legs, meta, OUT_DIR / name):
                total = sum(l.get("pnl", 0) for l in legs)
                rendered.append((name, meta, total, legs))
            else:
                missing.append((ticket, sym, tf, "window too small"))
        except Exception as exc:  # noqa: BLE001
            missing.append((ticket, sym, tf, f"render error: {exc}"))
        if len(rendered) >= args.max:
            break

    # summary numbers
    net = sum(t for _, _, t, _ in rendered)
    wins = sum(1 for _, _, t, _ in rendered if t > 0)
    losses = sum(1 for _, _, t, _ in rendered if t <= 0)
    cards = "\n".join(
        f'<div style="display:inline-block;margin:6px;text-align:center;font:12px sans-serif">'
        f'<img src="{n}" width="480"><br>{m["symbol"]} {m["timeframe"]} {m["setup"]} {m["side"]} '
        f'<b style="color:{"#10b981" if tot>0 else "#ef4444"}">net {tot:+.2f}</b></div>'
        for n, m, tot, _ in rendered)
    miss_html = ""
    if missing:
        rows = "".join(f"<li>#{tk} {s} {tf} — {why}</li>" for tk, s, tf, why in missing)
        miss_html = (f"<h3>Not rendered ({len(missing)})</h3>"
                     f"<p style='color:#64748b;font:12px sans-serif'>Usually the trade is older than the bars the "
                     f"server currently holds for that timeframe.</p><ul style='font:12px sans-serif'>{rows}</ul>")
    head = (f"Executed-trade validation — {len(rendered)} trades · "
            f"{wins}W / {losses}L · net {net:+.2f} · ledger equity {raw.get('equity', 0):.2f}")
    (OUT_DIR / "index.html").write_text(
        f"<html><body style='font-family:sans-serif'><h2>{head}</h2>{cards}{miss_html}</body></html>")

    print(f"Rendered {len(rendered)} executed trade(s); {len(missing)} skipped.")
    print(f"Net P&L on rendered: {net:+.2f}  ({wins}W / {losses}L)")
    print(f"→ strategy-study/trade-validation/index.html")
    return 0


if __name__ == "__main__":
    sys.exit(main())
