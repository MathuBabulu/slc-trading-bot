# Sector: operate

Read the running bot and present clean summaries. Assumes `BOT_DIR` / `BASE_URL` from SKILL.md.
Prefer the helper `python3 scripts/slc.py <cmd>` for quick reads; fall back to raw curl if you need
fields it doesn't surface (see `references/api.md`).

## Status — open trades, equity, PnL
```bash
python3 scripts/slc.py status
# raw equivalent:
curl -s "$BASE_URL/api/state"; curl -s "$BASE_URL/api/performance"
```
From `/api/state`: `open_trades[]` (each with `symbol, side, trade_mode, grade, entry, sl, tp1, tp2,
lots, risk_pct, risk_amount, upnl, tp1_done, setup`), `open_pnl`, `shadow_open`, `paper_balance`,
`ea_connected`. From `/api/performance`: `n, win_pct, expectancy_R, total_R, pf` and a `shadow` block.

Present: a short Account block (balance/equity + open PnL), an Open Trades list (one line each; mark
`TP1 banked` when `tp1_done`; show the TV confluence line only if `setup` JSON has `tv_score`), then a
Performance block (closed + shadow). If `upnl` is null show `—`. If no open trades, say so and show only
performance. Never dump raw JSON.

## Signals — what fired and what was skipped (and why)
```bash
python3 scripts/slc.py signals
# raw: curl -s "$BASE_URL/api/signals"
```
Each row: `t, symbol, trade_mode, side, grade, entry, sl, tp, rr, status (executed|skipped|shadow),
reason, setup`. Group by `status`; for skipped, surface the `reason` (e.g. "max concurrent", "spread > …",
"RR at fill < …", "trading mode is OFF"). This is the fastest way to explain "why didn't it take X".
External (TradingView) signals show `grade=ext` with `setup.source=tradingview`.

## TradingView market context — bias, trends, top setups
```bash
cd "$BOT_DIR" && python3 tv_context.py --force 2>&1     # --force bypasses the hourly cache (~5s)
# add --notify to push the summary to Telegram/Discord; drop --force to use cache
```
Output columns: `SYMBOL PRICE RSI ADX DIR REGIME EMA BB% CHG%` plus TOP OPPORTUNITIES + a USD vote.
Present three sections: Macro Bias (USD vote → favoured direction), Trending Pairs (ADX ≥ 25, grouped
long/short; flag ADX ≥ 40), and Top Setups (confluence 0–4: ADX dir + regime + EMA align + RSI room).
Always say whether data is fresh or cached. This is read-only confluence; it does not place trades.

## Health & integrity
```bash
python3 scripts/slc.py health                 # server reachable? EA connected? DB readable?
cd "$BOT_DIR" && python3 hallucination_check.py 2>&1   # READ-ONLY DB integrity scan
./watchdog-install.sh status                  # launchd auto-restart status
python3 tests/test_strategy_registry.py && python3 tests/test_tv_webhook.py   # logic/rails tests
```
Report: is the server up, is the EA pushing a fresh feed (`ea_connected` / feed age < 30s), is the DB
clean, are the tests green. If `hallucination_check.py` flags issues, do NOT act on the bot's numbers —
recommend `recover-db.sh` and stop. Never start/stop services or flip mode from here.
