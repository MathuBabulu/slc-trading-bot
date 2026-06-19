# Strategy Analysis & Way Forward — 10 Jun 2026

Based on: paper ledger (17 closed legs / 12 trades), signals.log (2,241 events, ~24h), performance reviews, detector/gate source code.

---

## 1. What the data actually says

### The -18.2% is a bug, not the strategy

| Slice | Net P&L |
|---|---:|
| NAS100 4h TL (pre-fix index oversizing bug) | **-₹2,070.85** |
| Everything else (16 legs) | **+₹246.87** |
| …of which ETHUSD TL alone | +₹218.41 |

One legacy trade, sized at ~20% risk instead of 2% by the since-fixed index-sizing bug, accounts for the entire drawdown. It also tripped the **10% max-drawdown halt — the bot has been refusing every signal since** (42 "Max drawdown halt" rejections in the log). The bot is not currently trading.

### Position sizing is still inconsistent by ~100x

With 2% risk on ₹10,000, a full 2R winner should pay ~₹300–400. Actual FX winners at 2R paid **₹1.2–₹4.2** — implied risk per trade ≈ ₹1.5–2 (0.02%, not 2%). Meanwhile NAS100 risked ~₹2,000. Rupee P&L is therefore meaningless right now; only R-multiples are valid. Until risk-per-trade is uniform, no equity-curve conclusion can be drawn.

### In R terms, the core patterns look promising — but the sample proves nothing

12 trades, 83% win rate, avg R +1.35. By setup:

| Setup | Legs | Win% | Avg R | Verdict |
|---|---:|---:|---:|---|
| DT | 6 | 100% | 1.82 | Core edge candidate |
| DB | 5 | 100% | 1.87 | Core edge candidate |
| TL | 6 | 50% | 0.46 | Unproven; produced the NAS100 disaster |

n=12 is far too small. 100% win rates on 5–6 legs are noise until ~50+ trades per setup.

### The signal funnel is opaque and detector-noise-heavy

Last ~24h: **2,239 detected → 2 accepted (0.09%)**.

- Confirmation gate rejects 93% (2,076). Of those, **1,241 are logged with reason "✓ Slow approach OK"** — a logging bug: the engine emits only the *last* check's message, so candle-anatomy failures (the real killer) are logged as a passing momentum check. You currently cannot see why most signals die.
- True split ≈ 1,241 candle-anatomy fails, 835 "approach too fast" fails.
- DT (825) + DB (801) generate 73% of all rejected signals — detectors re-fire on every tick a level is touched; dedupe is only by `detected_at`. 15m+30m alone are 50% of the noise.
- Choppiness blocked 70, correlation 46 — these gates barely matter by volume.

### Trade journal is empty

`state/trade_journal/` has 0 files despite 17 closed legs — all trades predate the journal, and nothing has traded since (halt). The post-hoc pattern-quality review the journal was built for has no data yet.

---

## 2. Way forward — prioritized

### Phase 0 — Reset to a clean baseline (do first, ~minutes)

1. **Reset the paper ledger to ₹10,000** and clear the drawdown halt. Everything in the current ledger is contaminated by the sizing bug and the cross-timeframe bug; it can't be tuned against.
2. **Rotate the Telegram token** in config.yaml (still plaintext).
3. Run on the Mac: `check_feed.py`, `validate_patterns.py`, `validate_trades.py` to confirm feed + detectors healthy post-restart.

### Phase 1 — Make the numbers trustworthy (before any tuning)

4. **Sizing invariant check.** At fill, log `risked_money = lots × money_per_lot_at_SL`. At close, assert `|pnl| ≈ |R| × risked_money` (±10%); alert on violation. This would have caught both the NAS100 bug and the 100x-too-small FX P&L automatically. Also verify tick_value arrives in INR (account currency), not broker-quote currency.
5. **Fix rejection logging.** Emit *all* check results (or at least the failing one) as structured fields (`stage`, `check`, `pass`, `value`, `threshold`) instead of the last reason string. Rejection analytics are your main tuning instrument — right now the instrument lies.
6. **Confirm the journal wiring** so the next trade actually writes to `state/trade_journal/`.

### Phase 2 — Shrink scope until the sample means something

7. **Trade DT/DB only; disable TL** until it's validated offline (worst PF, caused the worst trade, and break-and-retest variant isn't built yet).
8. **Drop 15m/30m initially; keep 1h/2h/4h/1d.** Half the detector noise is sub-hourly, and your only confirmed-bad outcomes cluster there. Patterns are cleaner and confirmation candles more meaningful on higher TFs.
9. **Cut the universe from 42 to ~12–15 liquid pairs** (majors + the JPY crosses that are already working). 42×6×8 = 2,016 scan slots makes every per-cell sample microscopic. Goal: reach 50–100 trades per setup×TF cell as fast as possible — that's the prerequisite for every threshold decision.

### Phase 3 — Pattern identification clarity (the detector roadmap)

10. **Replace binary detection with a clarity score.** Score each candidate 0–100 on: touch precision (|p1−p2|/ATR), structure depth (valley/crest vs `min_drop_atr`), peak spacing (bars between touches vs ideal), valley cleanliness (no intermediate violations), and confirmation-candle quality. Log the score on every signal, trade only above a threshold. This converts "why did/didn't it fire?" from archaeology into a number you can tune, and the journal can correlate score vs outcome.
11. **Level-based cooldown/dedupe.** One signal per symbol×TF×level per N bars (e.g. suppress re-fires within 0.5×ATR of a recently signalled level for 10 bars). Kills the 800+/day DT/DB re-fire spam and makes the funnel readable.
12. **Higher-timeframe context filter.** Patterns are currently evaluated in isolation. Require the TF-above trend to not oppose the trade (e.g. DB only if 4h structure isn't making lower-lows, or price above the 4h/1d 50-EMA). This is the single most common reason textbook-perfect double bottoms fail.
13. **Shadow mode for gate tuning.** For every signal that passes detection but is rejected by a gate, track it forward to its hypothetical TP/SL and log the outcome. After 2–3 weeks you'll know, with data: does the 0.70 body-ratio rule earn its 1,241 rejections/day? Is ATR-ratio 1.20 too strict? Tune gates against measured expectancy, not intuition. Run an A/B variant in shadow: entry-at-touch (current) vs entry-on-neckline-break.
14. **Weekly labeled review loop.** Run `validate_patterns.py` weekly, hand-label each rendered detection good/bad pattern, append to `setups_dataset.json`. Feed disagreements back into the clarity-score weights. (Apply the asymmetric tolerance to the triple detector while there, per the open item.)

### Phase 4 — Only after ~50+ clean trades

15. Re-run `performance_review.py` per setup×TF cell on expectancy (not win rate). Re-admit TL / lower TFs / wider universe only where shadow-mode data supports it. Then consider live routing (`execution/mt5_router.py`).

---

## 3. The two headline answers

**How to improve the strategy:** stop tuning on contaminated data. Reset the ledger, make sizing provably uniform (invariant check), shrink to DT/DB on 1h+ across ~12 pairs, and let shadow mode measure what each gate is actually worth. The strategy's R-multiples are healthy; the money plumbing and the sample size are the problems.

**How to make pattern identification clear:** score patterns instead of pass/fail (clarity score), dedupe re-fires per level, add a higher-TF trend filter, and fix the rejection logging so every dead signal tells you exactly which check killed it and by how much. Clarity comes from measurement, not tighter thresholds.
