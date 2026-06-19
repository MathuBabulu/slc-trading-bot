# Implementation Prompt — Strategy Improvements (paste into a new session)

You are working in `/Users/shakeebs/Trading Strategy/`. First read these two files for full context, then implement the tasks below in order:

1. `PROJECT_CONTEXT_HANDOFF.md` — architecture, file map, known bugs/fixes
2. `strategy-study/STRATEGY_ANALYSIS_WAYFORWARD.md` — analysis findings and rationale

## Constraints

- The sandbox CANNOT reach my Mac's `localhost:8765` or MT5. Anything needing the live server (feed checks, validators) must be given to me as commands to run on my Mac.
- Do not loosen any risk cap, stop, or filter.
- The engine loads config at startup — remind me to restart `server.py` after changes.
- Make each task a separate, reviewable change. After each, state which files changed and how I verify it.

## Phase 0 — Clean baseline

1. **Reset the paper ledger**: write a small script (`tools/reset_ledger.py`) that backs up `trading-bot/state/paper_ledger.json` to a timestamped file, then resets it to starting_equity ₹10,000, empty open/closed, and clears the `halted_for_dd` drawdown-halt state so the engine trades again. Do NOT delete the backup.
2. Remind me to rotate the Telegram token in `config.yaml` manually (don't print the token anywhere).

## Phase 1 — Trustworthy numbers

3. **Sizing invariant**: in `strategy/risk.py` / `execution/paper.py`, store `risked_money = lots × money_per_lot` on every fill (persist on the position). On every close, compute implied R from price distance and assert `|pnl| ≈ |R| × risked_money` within ±10%; on violation log a CRITICAL line and send the existing Telegram notify. Also log the sizing basis (tick_value/tick_size used and their source) at fill time so a currency-unit mismatch (INR vs broker quote ccy) is visible.
4. **Fix rejection logging**: `strategy/engine.py` currently emits only the last reason string, so candle-anatomy failures get logged as "✓ Slow approach OK". Change `signal:rejected` events to include structured per-check results: `checks: [{name, passed, value, threshold, detail}]` plus a top-level `failed_check` field. Update `confirm()` in `strategy/confirmation.py` to return structured results instead of bare strings (keep strings for dashboard display). Verify by parsing `state/signals.log` and showing the new true breakdown of confirmation failures.
5. **Journal wiring**: `state/trade_journal/` is empty despite 17 closed trades. Trace `journal_dir` from `server.py` → `StrategyEngine` → `TradeJournal` and confirm a paper fill+close actually writes a journal file (add a unit test that simulates one trade through PaperRouter with journal attached).

## Phase 2 — Scope reduction (config only)

6. In `config.yaml`: enable only DT + DB patterns; timeframes 1h, 2h, 4h, 1d (drop 15m/30m); reduce instruments to ~12–15 liquid pairs (majors + JPY crosses: EURUSD, GBPUSD, USDJPY, USDCHF, AUDUSD, NZDUSD, USDCAD, EURJPY, GBPJPY, AUDJPY, CADJPY, CHFJPY). Keep all risk settings unchanged. Preserve the full lists as comments so they're easy to re-enable.

## Phase 3 — Pattern identification clarity

7. **Per-level cooldown**: in the engine or detectors, suppress a new signal for the same symbol×timeframe×side when its level is within 0.5×ATR of a level already signalled in the last 10 bars. Log suppressions as `signal:deduped` with the prior signal reference.
8. **Clarity score**: add `score_pattern(...)` producing 0–100 from: touch precision (|p1−p2|/ATR), structure depth (vs `min_drop_atr`), peak spacing (vs ideal mid-range of min/max bars), and valley/crest cleanliness (no intermediate violation of the level). Attach `clarity_score` to every Signal, log it on accept AND reject, and add `strategy.min_clarity_score` to config (start at 0 = log-only, no filtering yet).
9. **Higher-TF context filter**: before confirmation, check the next timeframe up (15m→30m... 4h→1d): reject a buy if HTF is in a downtrend (price below HTF 50-EMA AND making lower lows over last N swings), mirror for sells. Config-gated (`strategy.htf_filter.enabled`), log as its own stage `htf_context` so its rejection rate is measurable.
10. **Shadow mode**: for every signal that passes detection but is rejected by any gate, register it in a shadow tracker that follows subsequent bars to its hypothetical TP/SL and writes the outcome (`win/loss/timeout`, R achieved, rejecting stage/check, clarity_score) to `state/shadow_outcomes.jsonl`. Add `tools/shadow_report.py` that aggregates expectancy by rejecting check — this is what we'll use to tune the 0.70 body-ratio and 1.20 ATR-ratio thresholds with data.

## Final verification

- Unit-test new logic (confirmation structured results, clarity score, cooldown, shadow tracker, journal write).
- Give me the exact commands to run on my Mac afterwards: restart server, `check_feed.py`, `validate_patterns.py`, `validate_trades.py`, and a quick `signals.log` query showing the new structured rejections.
- Update `PROJECT_CONTEXT_HANDOFF.md` §4/§6/§8 to reflect what was done.
