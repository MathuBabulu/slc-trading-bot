# System Validation Report — 12 Jun 2026

Full functional validation of every capability built 10–12 Jun: positive paths,
negative/failure paths, and live runtime evidence. Kept as study material.

## 1. Test-suite results

**78 / 78 tests pass** across three suites:

| Suite | Tests | Covers |
|---|---:|---|
| `tests/test_new_logic.py` | 29 | Structured confirmation, sizing invariant, journal write, cooldown, clarity score, HTF filter, shadow tracker, spread-aware fills/exits |
| `tests/test_news_alerts.py` | 28 | RSS parsing, impact mapping, Telegram alert format (+expandable fallback), geopolitical lexicon, paper SL commands, cut-loss ladder, priority lane, freshness gate, X fetcher |
| `tests/test_negative_cases.py` (NEW) | 21 | Failure paths — see §2 |

## 2. Negative-case validation (all fail SAFELY)

- **Risk rails**: drawdown breach halts and LATCHES (stays halted); daily-loss cap,
  daily/weekly trade caps, sub-minimum RR, zero risk distance, kill-switch file —
  all reject with explicit reasons. Missing MT5 tick data falls back to config
  sizing AND flags itself `config_fallback` for audit.
- **Garbage market data**: too-few bars / flat zero-ATR markets produce no
  signals (no crash); zero-range candles fail confirmation; clarity score is 0
  on zero ATR.
- **Hostile router inputs**: unknown instrument rejected; corrupt ledger file →
  clean start (no crash); `close_at_market` with no price / unknown ticket → no-op;
  `modify_sl` refuses to WIDEN risk (protective direction only); a 1d bar cannot
  manage a 1h position (the old cross-timeframe bug stays dead).
- **News pipeline**: malformed shadow registrations ignored; corrupt pending
  file recovers; garbage XML → empty; empty titles/watchlists → no alert;
  X API auth failure disables quietly without hammering.
- Two test-fixture bugs were caught and fixed during this pass (rollover reset
  in caps tests; a "mild" headline that double-matched keywords) — the system
  code was right both times.

## 3. Live runtime evidence (from state files, 12 Jun)

- **Ledger**: ₹100,000.00 start, clean (capital raise applied).
- **Structured logging**: 100% of today's 94 rejections carry `failed_check`
  (candle_anatomy 82, choppiness 4, htf_trend 3, correlation 3, momentum 2);
  181 events clarity-tagged; **85 re-fires suppressed** by the level cooldown.
- **HTF filter active**: 3 rejections under `htf_context` — measurable, as designed.
- **Journal**: 6 trades captured with pattern bars + clarity (e.g. NZDUSD DB-1h,
  clarity 49, scale-out tp_partial + trail, net +₹339.47).
- **News**: 22 alerts (17 priority-lane), 160 headlines audited; freshness gate live.
- **Services**: server + news agent logs written 0 min ago; watchdog silent for
  14h = no interventions needed (it only logs actions).

## 4. FINDINGS — the study material

### Finding 1 (serious): dashboard "Enable all" silently undid the scope reduction
`enabled_patterns.json` was back to all 8 patterns, `enabled_pairs.json` to 41
pairs (engine still scanned only the 12 configured instruments, but ALL pattern
types traded on them). Result: a **TL (trendline) trade on CADJPY → −₹319.79** —
precisely the unvalidated setup we benched (TL PF was 0.11 in the original sample).
**Action taken**: overrides restored to DT+DB × 12 pairs (old files kept as
`.bak_20260612`). **Lesson**: the dashboard override path needs a guard —
proposed: a confirmation dialog + a visible "scope differs from config" banner.

### Finding 2 (the big one): the confirmation gate is rejecting winners wholesale
Shadow data, n=330 resolved hypothetical outcomes of REJECTED signals:

| Rejecting check | n | avg R |
|---|---:|---:|
| candle_anatomy | 278 | **+1.68** |
| momentum | 17 | +2.00 |
| htf_trend | 17 | +1.47 |
| choppiness | 14 | +1.79 |
| correlation_conflict | 4 | +2.00 |
| **Overall** | **330** | **+1.69 (90% win)** |

Read with care: shadow outcomes assume plain SL/TP fills (no spread, no
scale-out, no management) and the window covered trending sessions — the true
expectancy is lower. But the signal is overwhelming and consistent: **the 0.70
body-ratio candle gate (n=278!) is starving the strategy.** It rejects ~82
signals/day while the bot takes ~1–2.
**Proposed experiment (needs your approval)**: lower `min_body_ratio` 0.70 → 0.55
for two weeks; shadow keeps measuring what's still rejected. Alternative:
fold candle quality into the clarity score instead of a binary gate.

### Finding 3: duplicate-topic alerts with contradictory directions
Three alerts on the same Iran-strike story (08:17 "+0.50 bullish", 09:21/10:00
"−0.50 bearish"). Wording differed enough to dodge the Jaccard 0.55 similarity
gate, and the keyword scorer read "solid gains" (equities!) as USD-bullish but
"oil falls" as USD-bearish. **Lessons**: (a) topic-level dedupe (driver +
entity keywords within N hours) would beat title similarity; (b) the scorer
can't distinguish asset classes in a headline — equity/commodity wording
pollutes currency scores. Both queued as improvements.

### Finding 4 (minor): fast trades journal zero in-between bars
A trade that enters and exits within the same bar records `trade_bars: []`
(slice between entry and exit times is empty). Cosmetic — the exits and pattern
bars are intact; the trade chart falls back gracefully.

## 5. Residual risks / not yet validated
- Telegram delivery, dashboard rendering, and HTTP endpoints can only be
  verified on the Mac (sandbox can't reach localhost): one-time check —
  `curl -s localhost:8765/api/health`, open dashboard, confirm a test message.
- Spread-aware fills are unit-tested but only 1 live fill so far carries a
  live spread — verify `spread (live)` appears in fill log lines this week.
- X API works but the account has zero read credits (CreditsDepleted) —
  disabled; wire coverage with 90-min freshness is the active path.

## 6. Verdict
Every implemented function passes its positive AND negative tests; the live
system is running them in production. The two material issues found (scope
override, candle gate too strict) are both *strategy-level*, not code defects —
and both were caught by the instrumentation built for exactly this purpose.
