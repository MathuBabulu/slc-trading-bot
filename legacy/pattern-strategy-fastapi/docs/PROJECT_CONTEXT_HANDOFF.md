# MT5 Price-Action Trading Bot — Project Context Handoff

> Portable context export. Hand this file to any model/session (Fable, Opus, a fresh
> chat, etc.) to continue work without losing context. Last updated: 10 Jun 2026.

---

## 1. What this project is

An automated **MetaTrader 5 (MT5) price-action trading bot** with a web dashboard.
It detects classic price-action patterns across 42 instruments and 6 timeframes,
filters them through confirmation / correlation / news / risk gates, and paper-trades
them with staged scale-out management. Account is **paper mode, INR, ₹10,000 starting
capital, 2% risk per trade**.

- Root folder: `/Users/shakeebs/Trading Strategy/`
- Language/stack: Python (FastAPI async server + StrategyEngine), MQL5 EA bridge,
  single-page HTML/JS dashboard (Chart.js).

## 2. Architecture & data flow

```
MT5 terminal ── EA (ea/MT5DataBridge.mq5) ──HTTP──> server.py /api/mt5_feed (prices, 5s)
                                                            /api/mt5_bars (OHLCV, 60s)
                                                                  │ stored in mt5_store
                                                                  ▼
                  StrategyEngine (_tick loop) ── per pair × timeframe:
                     fetch bars → patterns.run_all → confirmation → correlation
                     → news → risk/sizing → PaperRouter.submit (fill)
                     → PaperRouter.on_bar (manage SL/TP/scale-out)
                     → _check_ltf_reversal_exits (lower-TF reversal)
                     → TradeJournal (per-trade capture)
                                                                  │
                  dashboard (trading-dashboard/) ◀── /api/status, /api/chart,
                     /api/agent/trades, /api/pairs, /api/patterns, /api/watch_levels
```

- **Sandbox cannot reach the user's `localhost:8765` or MT5.** All HTTP-dependent
  checks (feed coverage, validators that fetch bars) must be RUN ON THE USER'S MAC.
- Path mapping: host `/Users/shakeebs/Trading Strategy/` ↔ sandbox
  `/sessions/.../mnt/Trading Strategy/`.

## 3. Key files and their roles

**trading-bot/**
- `config.yaml` — central config (account, risk, timeframes, patterns, correlation,
  news, instruments, `risk.scale_out`, `risk.ltf_reversal_exit`). Contains a live
  Telegram token (FLAGGED TO ROTATE).
- `server.py` — builds router + engine, persistence (pairs/patterns/levels/ledger),
  signal log, status snapshot, Telegram notify. Constructs StrategyEngine with
  `ltf_exit_cfg` and `journal_dir`.
- `server/api.py` — FastAPI endpoints incl `/api/chart/{symbol}` (MT5-only),
  `/api/agent/trades`, `/api/watch_levels` (GET/POST replaces full list).
- `strategy/engine.py` — orchestration: `_tick`, `_process`, confirmation/correlation
  gates, `_execute_signal`, `_watch_level_signals`, `_check_ltf_reversal_exits`,
  `_lower_timeframes`, `_journal_closure`, trade journal hooks.
- `strategy/patterns.py` — detectors: Double Top/Bottom, H&S, Inverse H&S, Triple,
  Rectangle, Trendline. `_DTConfig` thresholds (see §5). `run_all` dedupes.
- `strategy/confirmation.py` — candle anatomy (≥70% body, ≤30% opposing wick) +
  slow-approach (ATR ratio) check.
- `strategy/correlation.py` — Pearson on returns, Choppiness Index, conflict block,
  dedupe correlated.
- `strategy/risk.py` — sizing (per_trade_pct, broker-exact tick value), caps, drawdown.
- `strategy/journal.py` — **TradeJournal**: per-ticket JSON capturing entry_time,
  pattern bars, every in-between bar, all exit legs. Dir: `state/trade_journal/`.
- `execution/paper.py` — PaperRouter: fills, `on_bar` (timeframe-matched mgmt),
  `_manage_position` (scale-out 1:2→50%+BE, 1:3→+1R, 1:4→+2R), `reversal_exit`,
  `_close_portion`, ledger persistence.
- `execution/base.py` — OrderRequest / OrderFill / PositionUpdate dataclasses
  (PositionUpdate now carries `entry_time`).
- `marketdata/mt5_source.py` — builds Bars from mt5_store (stamps `timeframe`).
- `ea/MT5DataBridge.mq5` — EA v2.20: pushes 6 timeframes + tick_value/size,
  auto-resolves broker symbol names, watches dashboard-enabled pairs.

**Validators / tools (read-only, run on Mac):**
- `validate_patterns.py` — replays detectors over real bars, renders each detection
  as a candlestick PNG with ENTRY + EXIT(TP/SL) arrows; win-rate of resolved.
- `validate_trades.py` — renders the agent's ACTUAL executed trades (reads ledger;
  prefers `trade_journal/` so rolled-off trades still render). Entry/exit arrows.
- `validate_timeframes.py` — audits that each trade closed on its own timeframe
  (treats `ltf_rev_*` closes as intended).
- `check_feed.py` — per-pair live feed coverage (HTTP).
- `performance_review.py` — read-only ledger analyzer → `strategy-study/performance/`.
- `video_levels.py` — YouTube transcript → watch-levels extractor (`--channel` mode).
- `tools/install_autostart.sh` (11 Jun) — registers launchd services
  (`com.tradingbot.server|newsagent` with KeepAlive auto-restart-on-crash +
  `com.tradingbot.watchdog` every 2 min: reopens MetaTrader 5, kickstarts a
  hung server via /api/health). Uninstall: `install_autostart.sh uninstall`.
  Logs: `state/launchd_*.log`, `state/watchdog.log`. After config changes use
  `launchctl kickstart -k gui/$UID/com.tradingbot.server` (NOT plain python3 —
  duplicates).
- `news_agent.py` (11 Jun) — **market-wide news ALERTS**: rich RSS items
  (title/link/source/summary), `analyze_headline_impact()` maps one headline's
  currency sentiment onto the watched pairs (↑/↓ per pair); Telegram
  `news_alert()` with EXPANDABLE blockquote details (+ plain fallbacks); runs
  every cycle even with 0 positions (watches dashboard-enabled pairs);
  deduped 48h (`state/news_alerts_sent.json`), capped per cycle, audit log
  `state/news_alerts.jsonl`. Config: `news_agent.alerts_*`,
  `telegram.notify_news_alert`. Tests: `tests/test_news_alerts.py`.

**strategy-study/** — `setups_dataset.json`, `strategy_knowledge_base.md`,
`parameter_tuning.md` (DevilTrader PDF study + tuning decisions), `performance/`,
`pattern-validation/`.

**trading-dashboard/** — `index.html`, `app.js`, `data.js` (INR ₹, live positions,
agent trades, Video Levels tab). 10 Jun additions: custom candlestick renderer
(`drawCandleChart`) with entry/SL/TP/exit/pattern-level overlays + OHLC hover;
trade-chart modal (📈 buttons on Trade Log rows = journal-backed, and on open
positions = live bars); MT5 chart Candles/Line toggle (candles default, line kept);
Monitor tab cards: rejection-by-check (24h), clarity histogram, shadow outcomes
table; Risked ₹ column on open positions; Pairs tab badges pairs enabled in the
dashboard but missing from config.yaml instruments. New API:
`/api/funnel`, `/api/shadow`, `/api/journal`, `/api/journal/{ticket}` (server/api.py).

## 4. Current state (10 Jun 2026, post-improvement pass)

- **SCOPE REDUCED** (see strategy-study/STRATEGY_ANALYSIS_WAYFORWARD.md): 12 pairs
  (7 majors + 5 JPY crosses) × 4 TFs (1h/2h/4h/1d) × DT+DB only. Full catalog
  preserved as comments in config.yaml. `state/enabled_pairs.json` /
  `enabled_patterns.json` (dashboard overrides!) reset to match (.bak_20260610 kept).
- Paper ledger was ₹8,176.02 / 17 closed (contaminated by pre-fix sizing bugs;
  bot was HALTED on max drawdown). Run `python3 tools/reset_ledger.py` to reset
  to ₹10,000 (backs up first), then RESTART the server.
- New since this pass: sizing invariant (CRITICAL + Telegram on |pnl| ≠ |R|×risked),
  structured rejection logging (failed_check + per-check values), per-level
  cooldown, clarity score (log-only, `strategy.min_clarity_score: 0`), HTF context
  filter (stage `htf_context`), shadow mode (`state/shadow_outcomes.jsonl` +
  `tools/shadow_report.py`). Unit tests: `python3 -m unittest tests.test_new_logic`.
- Watch levels: XAUUSD 4310 buy (active). A CHFJPY 200.45 buy (1h, DB) was being
  added manually via `/api/watch_levels` (curl) at session end.

## 5. Strategy logic & key parameters (in config.yaml + patterns.py)

- Timeframes: 15m, 30m, 1h, 2h, 4h, 1d. Patterns: all 8 enabled.
- Risk: `per_trade_pct 2.0`, `min_rr 2.0`, daily cap 3, weekly 12, daily loss 3%,
  max drawdown 10%.
- Scale-out: 1:2 close 50% + SL→BE; 1:3 trail +1R; 1:4 trail +2R.
- **Lower-TF reversal exit** (`risk.ltf_reversal_exit`): while in a trade, watch the
  2 timeframes below for a CONFIRMED opposing DT/DB; on trigger close 50% + SL→BE,
  trail rest. Tagged `ltf_rev_<tf>_<setup>`.
- **Detector thresholds (`_DTConfig`)**:
  - `peak_tolerance_atr = 0.25` — counter-trend extreme drift (tight).
  - `trend_tol_atr = 1.0` — TREND-aligned drift: higher 2nd low (ascending DB) /
    lower 2nd high (descending DT) allowed generously (added 10 Jun — see
    parameter_tuning.md).
  - `min_drop_atr = 2.0` (real W/M depth), `head_prominence_atr = 1.0`.

## 6. Fixes & learnings this session (chronological)

1. Dashboard render crash (missing containers) — fixed.
2. MT5 feed/bars not reaching dashboard: `/api/mt5_feed` was wiping bars every 5s —
   fixed to preserve bars across `mt5_store.clear()`.
3. yfinance fully removed; charts are MT5-only.
4. Added 30m/2h/1d (all 6 TFs); EA pushes all 6.
5. Dashboard-driven pairs/patterns with server-side persistence across restarts.
6. EA broker-symbol auto-resolution (e.g. JPN225→JPN225ft).
7. Correlation + choppiness + direction-conflict gate added.
8. DevilTrader PDF study → knowledge base + labeled dataset + parameter tuning.
9. Broker-exact tick-value sizing; real trendline detector.
10. All 42 pairs + all 8 patterns enabled.
11. INR / ₹10,000 / 2% risk; cleaned mock history; agent-only trade view.
12. Staged scale-out + trailing in PaperRouter.
13. **Cross-timeframe management bug** fixed: `on_bar` only manages a position with
    bars of ITS OWN timeframe.
14. **Index oversizing bug** fixed: P&L now uses stored tick_value/tick_size (matches
    sizing basis).
15. News-agent parsing bug (ElementTree falsy element) fixed.
16. Gold price sanity range corrected (~$4,359 Jun 2026).
17. Visual validators built (`validate_patterns.py`, `validate_trades.py`,
    `validate_timeframes.py`).
18. Detectors tightened (0.25 peak tol, 2.0 depth, 1.0 head) to cut false positives.
19. Entry/exit arrows + win/loss labels on validator charts.
20. **Lower-timeframe reversal exit** implemented + tested.
21. **Trade journal** (entry_time, pattern bars, in-between bars, exits) implemented;
    validators prefer it so rolled-off trades stay validatable.
22. **Asymmetric DT/DB tolerance** (ascending DB / descending DT) added after a CHFJPY
    higher-low double bottom was being rejected by the symmetric tolerance.
23. **Improvement pass (10 Jun, session 2)** — all unit-tested (23 tests,
    `tests/test_new_logic.py`):
    - `tools/reset_ledger.py` — backup + reset ledger, clears HALT file.
    - Sizing invariant: `risked_money`/`sizing_basis` persisted on every fill
      (risk.py → OrderRequest/OrderFill → paper.py); every close asserts
      |pnl| ≈ |R|×risked ±10% → CRITICAL log + Telegram. Skips |R|<0.25 (BE noise).
    - Structured rejection logging: `confirm()` returns `CheckResult` list;
      ALL gates emit via `engine._reject()` with `failed_check` + `checks`;
      signals.log `reason` is now guaranteed to be the FAILING check (the old
      `notes[-1]` heuristic logged candle-anatomy fails as "✓ Slow approach OK").
    - Journal wiring: `journal_dir` now ROOT-anchored in build_engine (was
      CWD-relative — wrong dir if server started from elsewhere).
    - Per-level cooldown (`strategy/cooldown.py`): same symbol×TF×side within
      0.5×ATR of a level signalled in the last 10 bars → `signal:deduped`.
    - Clarity score 0–100 (`patterns.score_pattern`): touch precision, depth,
      spacing, cleanliness; on every DT/DB Signal + in signals.log; gate at
      `strategy.min_clarity_score` (0 = log-only).
    - HTF context filter (`strategy/htf.py`): buy blocked when next-TF-up has
      close<EMA50 AND falling swing-lows (mirrored for sells); stage `htf_context`.
    - Shadow mode (`strategy/shadow.py`): every gate-rejected signal tracked to
      hypothetical TP/SL → `state/shadow_outcomes.jsonl`; pending survives
      restarts; report: `tools/shadow_report.py` (expectancy by rejecting check
      — the data for tuning body-ratio 0.70 / ATR-ratio 1.20).
28. **Fresh-only alerts + direct X source (12 Jun).** Alerts now REQUIRE a
    provable publish timestamp ≤ `alert_max_age_minutes` (90) — items without
    pubDate or older are never alerted (wire rehashes of yesterday's posts
    were re-alerting). Fuzzy similarity dedupe (Jaccard ≥0.55 vs recently sent
    titles; store migrated to {ts,title}). `XPostFetcher` reads presidential
    posts directly from the X API v2 when `news_agent.x_api.{enabled,
    bearer_token}` is set (handles cached, auth failure disables quietly,
    posts always priority-lane, title prefixed with @handle for USD
    relevance).
27. **Presidential tweet/social-post priority lane (11 Jun).** Two tweet-focused
    Google News feeds; headlines containing `alert_priority_terms` (trump,
    white house, truth social, potus, president, tweet(s), executive order)
    use a RELAXED alert gate (`alert_min_score × alert_priority_factor`,
    0.5×0.7=0.35) and jump the per-cycle alert queue; Telegram message carries
    a "⚡ PRIORITY — PRESIDENTIAL / SOCIAL POST" tag; `priority` flag in
    news_alerts.jsonl. Lexicon += slams/attacks/blasts/tensions/bans/blocks
    (bearish), trade deal/deal reached/truce/suspends tariffs/tax cuts/
    stimulus/backs down (bullish).
26. **News cut-loss (11 Jun).** New `close_position` action: net sentiment
    ≤ −`cut_loss_threshold` (0.5, stricter than the −0.25 BE gate) AND trade
    underwater (BE impossible) → close full remaining size at market.
    `PaperRouter.close_at_market` (bid for buys, bid+spread for sells),
    `_apply_paper_sl` in server.py handles type close_position (records
    risk-state close, journals, emits position:closed), Telegram
    `news_cut_loss`. Config: `news_agent.cut_loss_enabled/threshold`.
25. **News SL-to-cost actually works now (11 Jun).** Root causes found: (a)
    `live_mode: false` — agent was dry-run; (b) SL commands only fed the MT5 EA
    queue, never the PaperRouter; (c) the agent evaluated BROKER positions, not
    the bot's paper trades; (d) lexicon had zero geopolitical vocabulary, so a
    presidential statement scored 0.00. Fixes: `PaperRouter.modify_sl`
    (protective-direction-only rail), `/api/commands` applies to paper tickets
    first (`apply_paper_sl` in server.py), agent reads `/api/agent/trades`
    first, lexicon += trump/white house/tariff/trade war/sanctions/etc., two
    broad feeds added, every fetched headline audited to
    `state/news_headlines.jsonl`, `live_mode: true`. Tests in
    tests/test_news_alerts.py (lexicon + paper SL) — 42 total green.
24. **Spread-aware execution (11 Jun).** Bars/quotes are BID. The engine captures
    the broker's LIVE ask−bid at signal time (`_live_tick_meta` → OrderRequest/
    OrderFill `.spread`; sanity-capped, falls back to `slippage_pips`×pip_size).
    PaperRouter: buys FILL at the ask (entry+spread); sells fill at bid but ALL
    sell-side exit checks (SL/TP/scale-out fav_R/reversal/flatten) trigger at
    ask = bid + spread — matching real MT5 execution. Tests: `TestSpreadModel`.

## 7. Timeframe audit result (still open)

7 of 17 ledger trades closed on a LOWER timeframe than opened — these are LEGACY
trades from BEFORE the cross-timeframe fix was live. The code is correct now.
**Recommended:** reset the paper ledger to clean ₹10,000 so future audits are clean.

## 8. Open / recommended next steps

- **IMMEDIATE (on the Mac):** `python3 tools/reset_ledger.py` then restart
  `server.py` (clears the max-drawdown halt; engine loads config at startup).
- ROTATE the Telegram bot token in config.yaml (it's committed in plaintext).
- Run on Mac after restart: `python3 check_feed.py`, `validate_patterns.py`,
  `validate_trades.py`, `validate_timeframes.py`; unit tests:
  `python3 -m unittest tests.test_new_logic`.
- Let shadow mode accumulate 2–3 weeks, then `python3 tools/shadow_report.py`:
  tune confirmation thresholds (0.70 body / 1.20 ATR) and pick a
  `min_clarity_score` cutoff from the clarity buckets — data, not intuition.
- Re-admit TL / lower TFs / wider universe only where shadow + journal data
  supports it (50+ trades per cell).
- Possible: `force` flag on watch levels to override correlation block; Whisper audio
  fallback for video_levels; finish live MT5 order routing (`execution/mt5_router.py`);
  apply asymmetric tolerance + clarity scoring to the triple detector.

## 9. How to run

```
cd "/Users/shakeebs/Trading Strategy/trading-bot"
python3 server.py            # starts FastAPI server on :8765 + engine
# dashboard served by the server; open the dashboard URL it prints
```
EA `MT5DataBridge.mq5` must be attached to a chart in MT5 (ServerHost 127.0.0.1,
Port 8765) and "Allow WebRequest" enabled for that URL.
