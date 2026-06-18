# Dynamic-Spread Stop-Loss for Paper Trades

**Date:** 2026-06-15
**Goal:** Make paper trades feel the broker's *live, changing* spread for the whole life of the trade — not just at entry — so a spread blowout during news or session rollover can hit the stop exactly as it would in live trading.

## What was already there

The paper engine already exits on the correct side of the book — a long is stopped when the **bid** ≤ SL, a short when the **ask** ≥ SL — so spread was charged on entry and exit. But the stop was only tested at each **20-second management poll**, using that instant's quote. The EA pushes prices every **5 seconds**, so up to three of every four spread snapshots are never examined, and a brief blowout that lands between polls (or has normalized by the poll instant) is missed. Nothing recorded the spread behaviour, so you couldn't see *why* a stop fired.

## What changed (`dynamic-spread-SL.diff`, edits to `engine.py`)

1. **Capture the whole window.** On every 5s feed push, `_accum_px_window()` tracks, per symbol, the worst exit-side price (lowest bid, highest ask) and the max spread seen *since the last management cycle*.
2. **Stop against the worst case.** The paper SL test now uses that accumulated worst bid (longs) / worst ask (shorts), so a spread spike anywhere in the 20s window triggers the stop at the SL price — matching what the broker would do.
3. **Flag and log it.** When a stop fires, the engine decides whether the **mid** ever reached the SL or whether the **spread** alone did it. Spread-induced stops are tagged in the exit reason (`stop loss [spread-induced, max 28.0p]`) and written to `state/spread_trace.jsonl` so every stop's spread story is visible.
4. **Reset each cycle** so each interval is measured fresh.

Nothing touches risk %, stops sizing, TP logic, or trade mode. Live mode is unchanged (it already reconciles against real MT5 fills).

## The real scenario this reproduces

A EURUSD long, entry 1.16170 (ask), SL 1.16030. The mid drifts down to ~1.16080 — 5 pips clear of the stop — and stays there. At 21:00 rollover the spread blows out to ~28 pips for ~10 seconds:

| t (s) | mid | bid | ask | spread |
|---|---|---|---|---|
| 30 | 1.16080 | 1.16075 | 1.16085 | 1.0p |
| 55 | 1.16086 | 1.16056 | 1.16116 | 6.0p |
| **60** | 1.16088 | **1.15948** | 1.16228 | **28.0p** |
| 65 | 1.16089 | 1.15979 | 1.16199 | 22.0p |
| 70 | 1.16092 | 1.16082 | 1.16102 | 2.0p |

- **Old logic (20s instantaneous):** polls at ~0/20/40/75s all see a bid above the SL — the 10s blowout falls between polls — so the paper trade is **NOT stopped**. Unrealistic: a real broker's bid hit 1.15948, well through the 1.16030 stop.
- **New logic (worst bid over the window):** worst bid 1.15948 ≤ SL → **stopped at 1.16030**, and flagged spread-induced because the lowest *mid* (1.16088) never reached the stop.

`spread_trace.jsonl`:
```
{"symbol":"EURUSD","side":"buy","sl":1.1603,"max_spread_pts":28.0,"spread_induced":true,"exit_reason":"stop loss [spread-induced, max 28.0p]"}
```

## Limits / honesty

- Faithful to **5-second granularity** — the EA's push rate. A sub-5s spike that the EA samples between its own pushes still isn't captured. To go tighter, the EA (`SLCDataBridge.mq5`) would need to report the **max spread since its last push**, accumulated in `OnTick()` — an MQL5 change you'd compile in MT5. Happy to draft it.
- This can only be validated **forward in live paper** — there is no stored historical spread series, so it can't be backtested.
- TP detection still uses the current snapshot (conservative — it won't over-credit a fleeting favourable spike).

## To apply

Apply `dynamic-spread-SL.diff` to `trading-bot/engine.py`, `python3 -m py_compile engine.py`, then restart the bot's server service. Watch `state/spread_trace.jsonl` and the exit reasons to see spread-induced stops as they happen.
