#!/usr/bin/env python3
"""Volume-Gate Shadow Evaluator  (SLC bot)

Forward shadow test for the relative-volume confirmation gate, WITHOUT
touching the live signal path. Each run:

  1. Snapshots the live DB read-only (no locks, no writes to live data).
  2. Re-runs the exact live strategy on all accumulated bars, twice:
       - gate OFF (vol_mult = 0.0)  -> current live behaviour
       - gate ON  (vol_mult = 1.0)  -> candidate behaviour
  3. Compares win rate (and expectancy / PF) per mode.
  4. Appends the reading to volume_gate_shadow_log.jsonl.
  5. Prints a verdict: IMPLEMENT = yes only if the gate's win rate is
     higher (overall) with a sufficient sample (n >= MIN_N).

Live code is never modified by this script. Applying the gate for real
is a separate, explicit step (volume-confirmation-gate.patch).
"""
import os, sys, json, time, tempfile, shutil, sqlite3, glob

MIN_N = 30          # minimum gate-on trades before a verdict is trusted
GATE_THRESHOLD = 1.0
HERE = os.path.dirname(os.path.abspath(__file__))
BOT  = os.path.join(HERE, "trading-bot")
LOG  = os.path.join(HERE, "volume_gate_shadow_log.jsonl")

GATE_BLOCK = '''
    # --- relative-volume confirmation gate (shadow-injected) ---
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
'''
ANCHOR = '    info["confirmation"] = conf["type"]\n'


def build_workdir():
    wd = tempfile.mkdtemp(prefix="volgate_")
    for f in glob.glob(os.path.join(BOT, "*.py")):
        shutil.copy(f, wd)
    # inject the gate into the copied strategy.py
    sp = os.path.join(wd, "strategy.py")
    src = open(sp).read()
    if ANCHOR not in src:
        raise SystemExit("anchor not found in strategy.py — bot code changed?")
    open(sp, "w").write(src.replace(ANCHOR, ANCHOR + GATE_BLOCK, 1))
    # Snapshot the live DB read-only. It is written continuously by the EA
    # feed, and on a network/fuse mount a raw read can catch a torn image, so
    # try several strategies and dedupe/repair until integrity_check == ok.
    os.makedirs(os.path.join(wd, "data"), exist_ok=True)
    dst = os.path.join(wd, "data", "trading.db")
    src = os.path.join(BOT, "data", "trading.db")
    last_err = None

    def _repair(d):
        d.execute("DELETE FROM bars WHERE rowid NOT IN "
                  "(SELECT MIN(rowid) FROM bars GROUP BY symbol, tf, t)")
        d.commit(); d.execute("REINDEX"); d.commit()
        return d.execute("PRAGMA integrity_check").fetchone()[0]

    def _online_backup(immutable):
        if os.path.exists(dst):
            os.remove(dst)
        uri = f"file:{src}?mode=ro" + ("&immutable=1" if immutable else "")
        s = sqlite3.connect(uri, uri=True, timeout=10)
        d = sqlite3.connect(dst)
        s.backup(d); s.close()
        ok = _repair(d); d.close()
        return ok

    def _raw_copy():
        if os.path.exists(dst):
            os.remove(dst)
        shutil.copy(src, dst)
        d = sqlite3.connect(dst)
        ok = _repair(d); d.close()
        return ok

    for attempt in range(6):
        for strat in (lambda: _online_backup(False),
                      lambda: _online_backup(True),
                      _raw_copy):
            try:
                if strat() == "ok":
                    return wd
            except sqlite3.Error as e:
                last_err = str(e)
        time.sleep(4)
    raise SystemExit("could not obtain a clean DB snapshot after retries: %s" % last_err)


def stats(trades):
    rs = [t.r for t in trades]
    n = len(rs)
    if n == 0:
        return {"n": 0, "win": 0, "exp": 0, "tot": 0, "pf": 0}
    w = [r for r in rs if r > 0]; l = [r for r in rs if r <= 0]
    pf = (sum(w) / abs(sum(l))) if l and sum(l) != 0 else 999
    return {"n": n, "win": round(100 * len(w) / n, 1),
            "exp": round(sum(rs) / n, 3), "tot": round(sum(rs), 1), "pf": round(pf, 2)}


def main():
    wd = build_workdir()
    sys.path.insert(0, wd)
    import storage, backtest
    storage.init()
    s = storage.all_settings()
    syms = s.get("enabled_pairs", []) or ["EURUSD","GBPUSD","USDJPY","AUDUSD","XAUUSD","XAGUSD","BTCUSD","ETHUSD"]
    base = dict(min_rr=float(s.get("min_rr", 2.5)),
                atr_buffer=float(s.get("atr_buffer", 0.35)),
                min_grade=s.get("min_grade", "B"),
                regime_max=float(s.get("regime_max", 2.5)),
                regime_b_ban=float(s.get("regime_b_ban", 1.5)),
                max_spread_frac=float(s.get("max_spread_frac", 0.10)))

    def run(vm):
        p = {**base, "vol_mult": vm}
        out = {"intraday": [], "swing": []}
        for sym in syms:
            if not storage.get_bars(sym, "1h", 5000):
                continue
            for mode in ("intraday", "swing"):
                out[mode] += backtest.run_symbol_mode(sym, mode, p, backtest.est_spread(sym))
        allt = out["intraday"] + out["swing"]
        return {"intraday": stats(out["intraday"]), "swing": stats(out["swing"]), "overall": stats(allt)}

    off = run(0.0)
    on  = run(GATE_THRESHOLD)

    improved = (on["overall"]["win"] > off["overall"]["win"]) and (on["overall"]["n"] >= MIN_N)
    reading = {"ts": int(time.time()), "date": time.strftime("%Y-%m-%d %H:%M", time.gmtime()),
               "settings": {"atr_buffer": base["atr_buffer"], "min_rr": base["min_rr"]},
               "gate_threshold": GATE_THRESHOLD, "off": off, "on": on,
               "win_delta_overall": round(on["overall"]["win"] - off["overall"]["win"], 1),
               "implement": improved}
    with open(LOG, "a") as f:
        f.write(json.dumps(reading) + "\n")

    print(json.dumps(reading, indent=2))
    print("\nVERDICT:",
          "IMPLEMENT — gate win rate is higher" if improved
          else "HOLD — gate has not (yet) improved win rate / sample too small")
    shutil.rmtree(wd, ignore_errors=True)


if __name__ == "__main__":
    main()
