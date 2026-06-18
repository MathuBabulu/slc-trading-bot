# Relative-Volume Confirmation Gate — Validation Report

**Date:** 2026-06-15
**Scope:** Add a volume-participation check on top of the existing SLC price-action confirmation, using the **tick volume already in the MT5 feed** (the `v` column) — *not* TradingView footprint.

## Why tick volume, not TradingView footprint

Footprint (bid/ask split per price level) only reflects real order flow on **centralized venues**. Spot FX is decentralized, so both MT5 and TradingView "volume" on `FX:` pairs is **tick volume** (a synthetic proxy), and metals on `TVC:` tickers are index feeds with no real volume. Footprint would therefore give false confidence on most of the watchlist. It is genuine only for crypto (Binance) and exchange futures. TradingView's footprint is also only accessible *inside* Pine via `request.footprint()` — there is no REST endpoint to pipe it into the bot, and many feeds are delayed 10–30 min.

The achievable, honest version: gate the LTF confirmation candle on its tick volume relative to the trailing 20-bar average on the trigger timeframe — i.e. require genuine participation behind the confirmation. Zero new infrastructure.

## The gate

In `strategy.py:analyze()`, immediately after a confirmation is found, compute:

```
relvol = confirmation_bar.volume / mean(volume of previous 20 trigger-TF bars)
```

If `relvol < vol_mult`, reject the signal. `vol_mult = 0` disables the gate (current behaviour).

## Backtest results (8 enabled pairs, ~21-day window)

### Intraday

| Config | n | Win% | Exp R | Total R | PF | MaxDD R |
|---|---|---|---|---|---|---|
| baseline (buf0.35, gate off) | 85 | 49.4 | −0.02 | −1.5 | 0.96 | −8.9 |
| buf0.35 + gate 1.0× | 58 | 56.9 | +0.16 | +9.0 | 1.38 | −6.9 |
| buf0.35 + gate 1.2× | 42 | 52.4 | +0.15 | +6.4 | 1.37 | −5.1 |
| buf0.35 + gate 1.5× | 26 | 46.2 | +0.06 | +1.4 | 1.11 | −5.6 |
| buf0.50 + gate off | 75 | 56.0 | +0.12 | +8.7 | 1.29 | −5.8 |
| **buf0.50 + gate 1.0×** ★ | 53 | **62.3** | **+0.36** | **+18.8** | **2.07** | **−3.1** |

The buffer fix (premature stop-outs) and the volume gate (low-conviction confirmations) address **different leaks**, so they stack: combined, win rate hits 62%, profit factor doubles to 2.07, and max drawdown falls to −3.1R.

### Swing

| Config | n | Win% | Exp R | Total R | PF | MaxDD R |
|---|---|---|---|---|---|---|
| baseline (buf0.35, gate off) | 73 | 50.7 | +0.08 | +5.6 | 1.16 | −9.8 |
| **buf0.35 + gate 1.0×** ★ | 49 | 51.0 | +0.11 | +5.4 | 1.24 | −5.7 |
| buf0.35 + gate 1.2× | 35 | 51.4 | +0.05 | +1.9 | 1.12 | −5.1 |

Swing is already profitable; the gate keeps total R roughly flat while improving PF and **halving drawdown** — a pure quality/efficiency gain.

## Recommendation

- **Add the gate at `vol_mult = 1.0`** — it helps both modes.
- **Intraday:** combine with `atr_buffer = 0.50` (the standout: +18.8R, PF 2.07).
- **Swing:** keep `atr_buffer = 0.35`, add the 1.0× gate (PF up, drawdown halved).
- Thresholds above ~1.2× over-filter and erode the edge — 1.0× is the sweet spot.

## Caveats (read before going live)

1. **Small, single-period sample** (~21 days, 53–85 trades). Consistent across configs, but not conclusive.
2. **Tick-volume proxy** — real for crypto, synthetic for FX/metals. The gate helped on the mixed basket, but per-instrument robustness is unproven on this sample.
3. **Backtests flatter reality** (no slippage realism, possible period fit).
4. `vol_mult` is **not** in the auto-tune agent's allowed bounds, so it's a manual config + code change, not something the agent will adjust.
5. **Validate via shadow trades first** — run it in shadow/paper for 50+ trades before trusting it with size, per the playbook.

## How to apply

See `volume-confirmation-gate.patch` in this folder — three small edits to `strategy.py`, `engine.py`, and `config.yaml`. Nothing in the live bot has been changed yet.
