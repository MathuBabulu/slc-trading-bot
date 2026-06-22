# Sector: analyze

Performance and parameter work. All read-only except the bounded `--apply` path of the sanity check
(which can only ever change `min_rr`/`atr_buffer`, within bounds, after a 2-run confirmation — never
risk, stops, mode, or concurrency). Assumes `BOT_DIR` from SKILL.md.

## Backtest — replay stored bars through the live strategy code
```bash
cd "$BOT_DIR"
python3 backtest.py --modes swing 2>&1                          # all enabled pairs, swing
python3 backtest.py --symbols EURUSD GBPUSD XAUUSD --modes swing 2>&1   # specific pairs
python3 backtest.py --modes swing intraday 2>&1                 # add intraday (slower)
python3 backtest.py --modes swing --spread-mult 2.0 2>&1        # spread-stress test
```
Uses the *exact* live strategy logic + current DB settings, so results compare directly to live (no
look-ahead). Per-symbol/mode line stats: `n, win%, expectancy_R, total_R, pf, maxDD_R, avg_win_R,
avg_loss_R`; results export to `state/backtest_trades.csv`.

Present: Overall block (n, win%, expectancy, total R, PF, max DD, $10k compounded), Symbol breakdown
ranked by total R (flag strong ≥ +2R 🏆 / marginal ⚠️ / weak ≤ −2R 🔴), and 2–3 takeaways (which pairs to
prioritise, which to move to watch-only, whether settings look optimal). Note "small sample" if n < 5.
Intraday is currently disabled in live (negative expectancy) — if still negative, recommend keeping it off.

## Sanity check — parameter sweep + recommendations
```bash
cd "$BOT_DIR"
python3 sanity_check.py --quick --modes swing --notify 2>&1     # ~2–4 min quick grid
python3 sanity_check.py --modes swing --notify 2>&1             # full grid (10+ min)
python3 sanity_check.py --quick --modes swing --apply --notify 2>&1   # bounded auto-tune (see safety)
```
Prints per-variant grid results (`min_rr` variants, `atr_buffer` variants, spread stress), a
Recommendations section, the TV market context, and live + shadow stats. Report saved to
`state/sanity_report.md`.

Present: the grid as a table (flag the winner: highest total swing R with n ≥ 30), the recommendations
as concrete actions, the TV context summary, and the live-vs-shadow stats side by side.

**Auto-apply safety:** `--apply` only applies a winning variant after the SAME variant wins on two
consecutive runs; only `min_rr` (1.8–3.0) and `atr_buffer` (0.25–0.60) can be auto-tuned. Risk %, stops,
mode, and concurrency are never auto-changed. Never recommend disabling swing — it's the consistently
profitable mode.

## Study — live vs shadow, by symbol / grade / regime
```bash
curl -s "$BASE_URL/api/trades?status=closed&mode=paper"      # closed paper trades
curl -s "$BASE_URL/api/trades?status=open&mode=shadow"       # open shadow (watch-pair) samples
curl -s "$BASE_URL/api/performance"                          # closed + shadow expectancy
```
`/api/trades` filters: `mode, trade_mode, symbol, grade, status, result=win|loss, days=N`. Use shadow
outcomes to judge whether a filter (that *skipped* a live trade) cost or saved R, and to decide gate
calibration and symbol culling (defer culling until ~15+ trades/pair). Keep paper/live and shadow strictly
separate; shadow is silent sample collection and is never "tuned on" as if live.
