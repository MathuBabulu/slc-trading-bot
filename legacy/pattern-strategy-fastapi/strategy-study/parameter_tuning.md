# Bot Parameter Tuning — derived from the setup study

*What the live-session setups imply for the bot's configuration. Changes already applied
are marked ✅; recommendations needing your input are marked ⚠️.*

> **Survivorship caveat (again):** the setups are a winners-only reel. I used them to set
> *what* the bot trades (pairs, timeframes, patterns, R:R) — **not** to relax risk. No
> risk cap, stop, or filter was loosened on the basis of this data.

---

## ✅ Applied

**0. FULL COVERAGE (latest).** Per request, the engine now monitors **all 42 catalog pairs
× all 6 timeframes × all 8 patterns** (252 scan slots per tick). All pattern detectors are
now real and enabled — Double Top/Bottom, Head & Shoulders, Inverse H&S, Triple Top/Bottom,
Rectangle, and Trendline (no more stubs). Each was unit-tested on synthetic data.
*Caveat:* non-FX symbols (indices, crypto, energy) only produce signals if your broker
offers them under the exact `display` name in config (and the EA's `SymbolAliases`); the
engine safely skips any symbol that has no bars.

**1. Instrument universe.** The session traded **NZD/AUD/GBP crosses + WTI** far more than
the majors. The list now spans the full catalog; `pip_value` entries are cold-start
fallbacks only — the live MT5 tick value drives real sizing. `symbol` fields use plain MT5
names (yfinance tickers removed).

**2. Confirmed already-correct settings (no change needed):**
- `risk.min_rr = 2.0` — matches the fixed ~1:2 box in every frame. ✅
- `timeframes: 15m, 30m, 1h, 2h, 4h, 1d` — the study showed 15m/30m/1h/2h; all present. ✅
- Double top / double bottom detectors enabled — these *are* the strategy's core. ✅
- Correlation filter on — especially important here because NZD and AUD crosses move
  together; the dedupe/direction logic stops you stacking the same bet. ✅

---

## ✅ Resolved (the two open items)

**1. pip_value is now broker-exact (no more hand-tuning).** The MT5 EA now pushes each
symbol's `tick_value` and `tick_size` (from `SYMBOL_TRADE_TICK_VALUE` / `TICK_SIZE`), and
the risk engine sizes positions directly from those — exact for your account currency and
any cross pair. The static `pip_value` in config is now only a **fallback** used before the
EA's first price push. So the approximate cross values no longer affect live sizing once
the EA is connected (verify-against-broker is automatic). *Files: EA prices payload,
`strategy/risk.py` (`Instrument` + `evaluate_signal`), `strategy/engine.py`, `server.py`.*

**2. Trendline detector built (was a v2 stub).** `strategy/patterns.detect_trendline` now
detects diagonal **ascending-support bounces (long)** and **descending-resistance
rejections (short)**: it fits a line through the two most recent swing lows/highs, requires
the current bar to test the projected line and close back through it (a rejection), and
enters at the line with a 1:2 target — the same "trade from a tested level" logic as the
horizontal patterns. Enabled via `strategy.patterns.trendline: true`. Verified with unit
tests (bounce→buy, rejection→sell at ~1:2, nothing on a flat range).
*Note:* this covers the trendline **bounce** (e.g. the GBPUSD rising-support long). The
trendline **break-and-retest** variant (e.g. GBPAUD breaking a descending line) is a
distinct future setup, not yet implemented.

**3. Symbol names must match your broker.** `USOIL→USOUSD` alias is set. If your broker
names the crosses differently (suffixes like `.r`, or `GBPAUD.pro`), add them to the EA's
`SymbolAliases`. BNBUSDT only works if your broker offers crypto.

**4. Possible future calibration (after paper data exists):**
- `correlation.strong_threshold` (0.70) could be tuned once you see how tightly your
  broker's NZD/AUD crosses actually move.
- `risk.daily_trade_cap` (3) / `weekly_trade_cap` (12) — the session showed plenty of
  setups per week; only raise these after the *real* (loss-inclusive) win-rate is known.

---

## How to validate

Run in paper mode with these pairs enabled and let it collect real signals. Compare what
the engine flags against this `setups_dataset.json` catalog — if it misses a setup type
(e.g. anything trendline-based), that points to the next dev task. The biggest open item
is the trendline detector.

---

## Update (10 Jun 2026) — asymmetric DT/DB tolerance (ascending/descending doubles)

**Trigger:** a CHFJPY 1h double bottom rode a *rising trendline* — the second low sat
~20 pips ABOVE the first (a bullish higher-low). After tightening the equal-extreme
tolerance to 0.25·ATR, the detector rejected it: `abs(low1 - low2) > 0.25·ATR` is
SYMMETRIC and treated the higher second low as "not equal enough."

**Insight:** the two sides are not equivalent. For a double BOTTOM, a *higher* second low
is trend-aligned (bullish structure / trendline bounce) and should be allowed generously;
only a *lower* second low (undercut → breakdown) deserves a tight tolerance. Mirror logic
for a double TOP (a lower second high is a bearish lower-high; a higher second high is
counter-trend).

**Change:** split the single tolerance into two (`strategy/patterns.py _DTConfig`):
- `peak_tolerance_atr = 0.25` — COUNTER-trend drift (2nd low undercut / 2nd high overshoot). Tight.
- `trend_tol_atr     = 1.0`  — TREND-aligned drift (higher 2nd low / lower 2nd high). Generous.

`min_drop_atr = 2.0` (real W/M depth) still applies, so this does NOT reintroduce the loose
false positives removed earlier — it only admits ascending DBs / descending DTs.

**Verified (synthetic):** flat / ascending-+0.6ATR DB and flat / descending-0.6ATR DT now
detected; +1.5ATR drift, breakdowns, and counter-trend drift still rejected.

**Next:** run `validate_patterns.py --symbols CHFJPY` on live bars to confirm the actual
structure is now flagged, and review whether `triple` should get the same asymmetric rule.
