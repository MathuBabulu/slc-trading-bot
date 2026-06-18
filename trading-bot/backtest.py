"""SLC strategy backtester.

Replays the bar history stored in data/trading.db through the EXACT same
strategy code the live engine uses (strategy.analyze), and simulates the
engine's full trade management: spread-aware entries, TP1 at +1R with 50%
banked + break-even, MTF structure trailing, SL/TP2 exits.

    python3 backtest.py                 # all enabled pairs, both modes
    python3 backtest.py --symbols EURUSD,XAGUSD --modes intraday
    python3 backtest.py --spread-mult 1.5   # stress-test wider spreads

Results are signal-level (every dedup-distinct setup is taken; the live
max-concurrent / daily-stop caps are NOT applied, so live results will be
a filtered subset). All performance is reported in R multiples — money
results depend only on the % you risk per R.

Honest limitations: bar granularity is the trigger TF (15m/1h), so
intrabar SL-vs-TP races resolve pessimistically (SL first); spreads are
estimates unless the live server is running; ~2 months of history is an
indication, not proof.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import json
import time
from typing import Any, Dict, List, Optional

import storage
import strategy
from strategy import MODE_TFS

TF_SECONDS = {"15m": 900, "30m": 1800, "1h": 3600,
              "2h": 7200, "4h": 14400, "1d": 86400}

# Fallback spread estimates (price units) when the live server isn't up.
DEFAULT_SPREADS = {
    "EURUSD": 0.00010, "GBPUSD": 0.00012, "AUDUSD": 0.00012, "NZDUSD": 0.00015,
    "USDCAD": 0.00014, "USDCHF": 0.00013, "EURGBP": 0.00013, "USDJPY": 0.012,
    "EURJPY": 0.016, "GBPJPY": 0.022,
    "XAUUSD": 0.35, "XAGUSD": 0.035, "XPTUSD": 1.2,
    "BTCUSD": 30.0, "ETHUSD": 2.5, "SOLUSD": 0.08, "XRPUSD": 0.002,
    "US30": 3.0, "NAS100": 2.0, "US500": 0.8, "USOIL": 0.03,
}


def live_spread(symbol: str) -> Optional[float]:
    """Use the running server's real spread if available."""
    try:
        import requests
        r = requests.get("http://127.0.0.1:8766/api/state", timeout=2).json()
        p = (r.get("feed", {}).get("prices") or {}).get(symbol)
        if p and p.get("ask") and p.get("bid"):
            s = abs(p["ask"] - p["bid"])
            if s > 0:
                return s
    except Exception:
        pass
    return None


def est_spread(symbol: str) -> float:
    s = live_spread(symbol)
    if s:
        return s
    if symbol in DEFAULT_SPREADS:
        return DEFAULT_SPREADS[symbol]
    bars = storage.get_bars(symbol, "1h", 1)
    return (bars[0]["c"] * 0.0002) if bars else 0.0


# ----------------------------------------------------------------- sim
class Trade:
    def __init__(self, sig: Dict, t: int):
        self.symbol = sig["symbol"]; self.mode = sig["trade_mode"]
        self.side = sig["side"]; self.grade = sig["grade"]
        self.entry_t = t; self.entry = sig["entry"]
        self.sl = sig["sl"]; self.initial_sl = sig["sl"]
        self.tp1 = sig["tp1"]; self.tp2 = sig["tp"]
        self.rr = sig["rr"]
        self.tp1_done = False
        self.mfe = 0.0; self.mae = 0.0
        self.exit_t = 0; self.exit_price = 0.0; self.r = 0.0; self.reason = ""

    @property
    def risk(self) -> float:
        return abs(self.entry - self.initial_sl)

    def close(self, t: int, price: float, reason: str):
        d = 1 if self.side == "buy" else -1
        r = (price - self.entry) * d / self.risk
        if self.tp1_done:
            r = 0.5 * 1.0 + 0.5 * r          # half banked at +1R
        self.exit_t = t; self.exit_price = price
        self.r = round(r, 3); self.reason = reason


def mtf_trail_level(mtf_bars: List[Dict], side: str) -> Optional[float]:
    if len(mtf_bars) < 10:
        return None
    pv = strategy.pivots(mtf_bars[-80:], k=2)
    if side == "buy":
        lows = [x["price"] for x in pv if x["kind"] == "L"]
        return lows[-1] if lows else None
    highs = [x["price"] for x in pv if x["kind"] == "H"]
    return highs[-1] if highs else None


def manage(tr: Trade, bar: Dict, t_close: int, spread: float,
           mtf_bars: List[Dict]) -> bool:
    """Advance one LTF bar. Returns True if the trade closed.
    Bars are bid prices; the ask side is bid+spread. Pessimistic: when SL
    and TP could both hit inside one bar, SL wins."""
    hi, lo = bar["h"], bar["l"]
    if tr.side == "buy":
        cur_hi, cur_lo = hi, lo                        # exits at bid
        r_hi = (cur_hi - tr.entry) / tr.risk
        r_lo = (cur_lo - tr.entry) / tr.risk
        hit_sl = cur_lo <= tr.sl
        hit_tp2 = cur_hi >= tr.tp2
        hit_tp1 = cur_hi >= tr.tp1
    else:
        ask_hi, ask_lo = hi + spread, lo + spread      # exits at ask
        r_hi = (tr.entry - ask_lo) / tr.risk
        r_lo = (tr.entry - ask_hi) / tr.risk
        hit_sl = ask_hi >= tr.sl
        hit_tp2 = ask_lo <= tr.tp2
        hit_tp1 = ask_lo <= tr.tp1

    tr.mfe = max(tr.mfe, r_hi)
    tr.mae = min(tr.mae, r_lo)

    if hit_sl:                                          # pessimistic order
        tr.close(t_close, tr.sl, "stop loss" if not tr.tp1_done else "trailing stop")
        return True
    if hit_tp2:
        tr.close(t_close, tr.tp2, "take profit (TP2)")
        return True
    if not tr.tp1_done and hit_tp1:
        tr.tp1_done = True
        tr.sl = tr.entry                                # break-even
    if tr.tp1_done:
        lvl = mtf_trail_level(mtf_bars, tr.side)
        if lvl is not None:
            better = (lvl > tr.sl) if tr.side == "buy" else (lvl < tr.sl)
            in_profit = (lvl > tr.entry) if tr.side == "buy" else (lvl < tr.entry)
            if better and in_profit:
                tr.sl = lvl
    return False


def run_symbol_mode(symbol: str, mode: str, params: Dict,
                    spread: float) -> List[Trade]:
    tfs = MODE_TFS[mode]
    all_bars = {tf: storage.get_bars(symbol, tf, 5000)
                for tf in set(list(tfs.values()) + ["1d"])}
    ltf = all_bars[tfs["ltf"]]
    if len(ltf) < 80:
        return []
    closes = {tf: [b["t"] + TF_SECONDS[tf] for b in bs]   # bar close times
              for tf, bs in all_bars.items()}

    def closed(tf: str, now: int) -> List[Dict]:
        i = bisect.bisect_right(closes[tf], now)
        return all_bars[tf][:i]

    ltf_sec = TF_SECONDS[tfs["ltf"]]
    trades: List[Trade] = []
    open_tr: Optional[Trade] = None
    recent_keys: Dict[str, int] = {}

    for i in range(60, len(ltf)):
        bar = ltf[i]
        now = bar["t"] + ltf_sec

        if open_tr is not None:
            if manage(open_tr, bar, now, spread, closed(tfs["mtf"], now)):
                trades.append(open_tr)
                open_tr = None

        slices = {tf: closed(tf, now) for tf in
                  ("15m", "30m", "1h", "2h", "4h", "1d") if tf in all_bars}
        for tf in ("15m", "30m", "1h", "2h", "4h", "1d"):
            slices.setdefault(tf, [])
        res = strategy.analyze(symbol, mode, slices, params,
                               spread=spread, live_price=bar["c"])
        sig = res["signal"]
        if not sig or open_tr is not None:
            continue
        if recent_keys.get(sig["key"], 0) > now - 12 * 3600:
            continue
        recent_keys[sig["key"]] = now

        # engine-level execution checks at the actual (LTF close) price
        entry = bar["c"] + (spread if sig["side"] == "buy" else 0.0)
        stop_dist = abs(entry - sig["sl"])
        if stop_dist <= 0:
            continue
        if spread > params["max_spread_frac"] * stop_dist:
            continue
        if abs(entry - sig["entry"]) > 0.25 * stop_dist:
            continue
        rr_actual = abs(sig["tp"] - entry) / stop_dist
        if rr_actual < params["min_rr"] * 0.9:
            continue
        sig = dict(sig, entry=entry)
        open_tr = Trade(sig, now)

    if open_tr is not None:                 # mark remaining open at last close
        open_tr.close(ltf[-1]["t"] + ltf_sec, ltf[-1]["c"], "end of data (open)")
        trades.append(open_tr)
    return trades


# --------------------------------------------------------------- stats
def stats(trades: List[Trade]) -> Dict[str, Any]:
    n = len(trades)
    if n == 0:
        return {"n": 0}
    rs = [t.r for t in trades]
    wins = [r for r in rs if r > 0]
    g = sum(r for r in rs if r > 0); l = -sum(r for r in rs if r < 0)
    eq = 0.0; peak = 0.0; mdd = 0.0
    for r in rs:
        eq += r; peak = max(peak, eq); mdd = min(mdd, eq - peak)
    return {"n": n,
            "win%": round(100 * len(wins) / n, 1),
            "expectancy_R": round(sum(rs) / n, 3),
            "total_R": round(sum(rs), 2),
            "pf": round(g / l, 2) if l > 0 else None,
            "maxDD_R": round(mdd, 2),
            "avg_win_R": round(sum(wins) / len(wins), 2) if wins else 0,
            "avg_loss_R": round(-l / (n - len(wins)), 2) if n > len(wins) else 0}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="")
    ap.add_argument("--modes", default="intraday,swing")
    ap.add_argument("--spread-mult", type=float, default=1.0,
                    help="multiply spreads to stress-test costs")
    args = ap.parse_args()

    storage.init()
    s = storage.all_settings()
    params = {
        "min_rr": float(s.get("min_rr", 2.0)),
        "atr_buffer": float(s.get("atr_buffer", 0.35)),
        "min_grade": s.get("min_grade", "B"),
        "regime_max": float(s.get("regime_max", 2.5)),
        "regime_b_ban": float(s.get("regime_b_ban", 1.5)),
        "max_spread_frac": float(s.get("max_spread_frac", 0.10)),
    }
    enabled = s.get("enabled_pairs", [])
    watch = [w for w in s.get("watch_pairs", []) if w not in enabled]
    symbols = ([x.strip().upper() for x in args.symbols.split(",") if x.strip()]
               or enabled + watch)
    modes = [m.strip() for m in args.modes.split(",") if m.strip()]

    all_trades: List[Trade] = []
    print("=" * 88)
    print(" SLC BACKTEST  (settings: min_rr=%.1f buffer=%.2f grade>=%s, spreads x%.1f)"
          % (params["min_rr"], params["atr_buffer"], params["min_grade"], args.spread_mult))
    print("=" * 88)
    for sym in symbols:
        spread = est_spread(sym) * args.spread_mult
        b1 = storage.get_bars(sym, "1h", 5000)
        if not b1:
            print("%-8s no data" % sym); continue
        days = (b1[-1]["t"] - b1[0]["t"]) / 86400
        for mode in modes:
            trs = run_symbol_mode(sym, mode, params, spread)
            all_trades += trs
            st = stats(trs)
            line = "  ".join("%s=%s" % (k, v) for k, v in st.items())
            print("%-8s %-9s spread=%-9.5g %s" % (sym, mode, spread, line))
        print("%-8s data span: %.0f days" % (sym, days))

    print("-" * 88)
    for label, fn in [("OVERALL", lambda t: True),
                      ("intraday", lambda t: t.mode == "intraday"),
                      ("swing", lambda t: t.mode == "swing"),
                      ("grade A", lambda t: t.grade == "A"),
                      ("grade B", lambda t: t.grade == "B"),
                      ("buys", lambda t: t.side == "buy"),
                      ("sells", lambda t: t.side == "sell")]:
        st = stats([t for t in all_trades if fn(t)])
        print("%-9s %s" % (label, "  ".join("%s=%s" % (k, v) for k, v in st.items())))
    print("-" * 88)

    # money translation at 1% risk per R (B setups at half risk)
    bal = 10000.0
    for t in sorted(all_trades, key=lambda x: x.exit_t):
        risk_frac = 0.01 * (0.5 if t.grade == "B" else 1.0)
        bal *= (1 + risk_frac * t.r)
    print(" $10,000 at 1%% risk/R (B at 0.5%%), compounded: $%.2f" % bal)

    out = "state/backtest_trades.csv"
    import os
    os.makedirs("state", exist_ok=True)
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["symbol", "mode", "side", "grade", "entry_t", "exit_t",
                    "entry", "exit", "r", "mfe", "mae", "reason"])
        for t in sorted(all_trades, key=lambda x: x.entry_t):
            w.writerow([t.symbol, t.mode, t.side, t.grade,
                        time.strftime("%Y-%m-%d %H:%M", time.gmtime(t.entry_t)),
                        time.strftime("%Y-%m-%d %H:%M", time.gmtime(t.exit_t)),
                        "%.5f" % t.entry, "%.5f" % t.exit_price,
                        t.r, round(t.mfe, 2), round(t.mae, 2), t.reason])
    print(" trade list -> %s" % out)


if __name__ == "__main__":
    main()
