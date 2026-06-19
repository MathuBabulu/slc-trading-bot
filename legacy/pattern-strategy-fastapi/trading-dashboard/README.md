# Price-Action Trading Dashboard (Mac)

A single-file, standalone HTML dashboard that encodes the chart-pattern trading strategy from your source video and visualises your real MT5 performance against it.

**Designed to run entirely on macOS** — no Windows VPS, no Wine, no exotic Python packages required. You export your account history from MT5, drop the file onto the dashboard, and you're done.

The dashboard is intentionally separate from the existing `mt5-trade-journal` Streamlit project in this workspace — that one stays as-is and remains available if you later want live Windows-only polling.

---

## Quick start (60 seconds)

1. **Open the dashboard.** Double-click `index.html` (or run `open index.html` in Terminal). You'll see it populated with sample data so you can browse the UI.

2. **Export your MT5 history.**
   - Open MetaTrader 5 on your Mac.
   - Press `Ctrl+T` to show the Toolbox and click the **History** tab.
   - Right-click anywhere in the history list → choose a period (`All History` or a custom range).
   - Right-click again → **Report** → **Open** (or **HTML**). MT5 saves an `.html` file (default location is your Downloads or `~/Library/Application Support/...` depending on broker — MT5 will show you where).

3. **Drop the file on the dashboard.** Go to the **MT5 Data** tab, drag your `ReportHistory-*.html` onto the dropzone. The dashboard reloads with your real trades. The data is cached in `localStorage` so you don't have to re-import every time.

That's it.

---

## File map

```
trading-dashboard/
├── index.html              the dashboard (open this)
├── app.js                  rendering + report parsers
├── data.js                 bundled sample data
├── parse_mt5_report.py     optional CLI parser (Python 3, no extra deps)
└── README.md               this file
```

---

## Supported file types in the drop zone

| Extension | When MT5 produces it |
|-----------|---------------------|
| `.html` / `.htm` | History → Report → **Open / HTML** (recommended — richest data) |
| `.csv` | History → Report → **CSV** (or some brokers' "Save As") |
| `.json` | Output of the CLI parser, or a custom export |

The parser handles the standard MT5 closed-positions table layout. Slight column-order variations between brokers are handled automatically.

---

## Optional: command-line parser

If you want to automate the import (cron job, shell alias, etc.), use the bundled Python script:

```bash
cd "Trading Strategy/trading-dashboard"

# Convert an MT5 HTML report to trades.json:
python3 parse_mt5_report.py ~/Downloads/ReportHistory-12345.html

# Or write both trades.json AND data.js (so the dashboard works via file://):
python3 parse_mt5_report.py ~/Downloads/ReportHistory-12345.html --datajs
```

The script uses only the Python 3 standard library — **no `MetaTrader5` package, no pip installs**. It prints a quick summary (trade count, win rate, avg R:R, net P&L) so you can sanity-check the import.

After running with `--datajs`, just reload `index.html` in your browser.

---

## Tagging convention

The dashboard knows what setup a trade was (and therefore which rules to score it against) by reading the MT5 **order comment** field. When you place an order, fill the Comment field with:

```
{SETUP}-{TF}                e.g.  DB-H1
{SETUP}-{TF}|note           e.g.  HS-M15|right shoulder confirmed
{SETUP}-{TF}|TG             marks a "Telegram quality" trade
{SETUP}-{TF}|RF             marks a "Red Flag" pending-confirmation trade
```

Recognised setup codes:

| Code | Setup |
|------|-------|
| `DT` | Double Top |
| `DB` | Double Bottom |
| `HS` | Head & Shoulders |
| `IHS` | Inverse Head & Shoulders |
| `CHS` | Complex Head & Shoulders |
| `TT` | Triple Top |
| `TB` | Triple Bottom |
| `TRI` | Triangle |
| `REC` | Rectangle |
| `TL` | Trendline break |
| `FK` | Fakeout |

Untagged trades still import — they just won't get a setup-specific adherence score.

---

## What the dashboard shows

| Tab | Content |
|-----|---------|
| **Overview** | KPIs (win rate, P&L, R:R, profit factor, adherence), equity curve, setup mix, strategy snapshot, open positions |
| **Strategy & Rules** | Visual reference for each pattern + momentum + candle + risk + trade-management rules from the source video |
| **Pre-Trade Checklist** | Tickable checklist that turns GREEN only when all required items are met. Use before every entry. |
| **Trade Log** | Sortable, filterable table of every closed trade |
| **Performance** | Win-rate by week, R:R histogram, P&L by symbol/setup, category performance, hour-of-day win rate |
| **Rule Adherence** | Per-rule pass-rate bars, most-broken-rules chart, violations table |
| **MT5 Data** | Drop-zone for HTML/CSV/JSON, export instructions, optional CLI usage |
| **Improve This Dashboard** | Twelve concrete data points to add next, ranked by signal-per-effort |

---

## Adherence scoring (Mac, from the report alone)

The browser-side parser checks the rules that can be verified from history alone:

| Rule | How it's checked |
|------|------------------|
| Direction matches setup | DB/IHS/TB → buy; DT/HS/TT → sell |
| Setup tagged in comment | comment matches `^[A-Z]{1,3}-(M\d\|H\d\|D\d)(\|note)?$` |
| TF ≥ M15 | parsed timeframe is not M1/M5 |
| Min 1:2 R:R | `|tp - entry| / |entry - sl| ≥ 1.99` |

Rules that need OHLC bars (candle body, wick ratios, approach momentum) are **not** scored here — they need price data the report doesn't include. If you want those, the sibling `mt5-trade-journal` project does that scoring (Windows-only).

---

## Suggested next additions (ranked)

Documented in detail inside the dashboard under **Improve This Dashboard**. Highest-leverage adds:

1. **Entry-context candle snapshots** — save ~50 candles around each entry so you can re-score adherence as rules evolve.
2. **Slippage & fill quality** — requested vs actual fill in pips. Separates strategy edge from execution decay.
3. **Time-of-day / session breakdown** — tag every trade Asia / London / NY.
4. **Economic-event proximity** — log nearest CPI/FOMC/NFP and minutes-to/from each trade.
5. **ATR + spread at entry** — verifies sizing was correct and surfaces setups where spread ate the R:R.
6. **Higher-TF context tag** — record H4 trend direction for every M15/H1 trade.
7. **Adherence-vs-PnL scatter** — the cleanest visual proof that the rules are edge.
8. **MAE / MFE per trade** — Max Adverse / Favourable Excursion.
9. **Trade-cap discipline** — win-rate cliff between trades #1–12 vs #13+ each week.
10. **Replay viewer** — click any trade → see candles N bars before & after, with SL/TP/entry overlaid.
11. **Currency-exposure heat map** — current net long/short by currency.
12. **Streak detector** — pause-and-review banner after N consecutive wins or losses.

---

## Recommendations for the auto-trading agent

- **Half-position sizing** on new symbols until ≥ 30 trades show positive expectancy.
- **Hard daily-loss circuit breaker** at 2× per-trade risk (i.e. 2% daily cap).
- **Pattern detector should propose, not auto-execute** — Phase 1 alert-only, Phase 2 paper-trade, Phase 3 live with kill switch.
- **Adherence-score gate** — auto-skip any trade scoring < 80% on the rule checklist.
- **News blackout window** — no new entries within 30 minutes of any high-impact event on the symbol's currencies.

---

## Optional: live polling (advanced)

The drop-file workflow above is the recommended Mac path. If you'd later like the dashboard to update against a running MT5 terminal automatically, two options exist:

- **Windows VPS** — run MT5 there and use the sibling `mt5-trade-journal` project (already documented in its own README) to expose a Streamlit dashboard you browse from your Mac.
- **MT5 via Wine + `mt5linux`** — runs the Windows MT5 binary under Wine on Mac, exposes the Python API via RPC. Requires Wine + a Windows Python under Wine. Heavier setup than the report-export path.

Neither is required for this dashboard to work.

---

## Troubleshooting

**"No closed trades found in the file"**
Most likely you exported an Account Summary report instead of an Account History report. Make sure you right-clicked inside the **History** tab, not the Trade tab or Account Summary.

**Some trades have no setup tag**
You forgot the `SETUP-TF` comment when placing the order. You can right-click the position in MT5 → Modify → fill in the Comment, then re-export.

**The dashboard still shows sample data after I drop a file**
Open the browser console (Cmd-Option-J in Chrome / Cmd-Option-C in Safari with Developer enabled) — the parser logs any errors there. The most common cause is a non-standard broker template; if so, send me the raw HTML and I'll teach the parser to handle it.

**I want to reset back to sample data**
On the MT5 Data tab, click "Reset to sample data". This clears the `localStorage` cache and reloads.
