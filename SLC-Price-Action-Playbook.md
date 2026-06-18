# The SLC System — Structure · Liquidity · Confirmation
### An adaptive pure price action playbook for Forex, Metals & Crypto

**Risk model:** 0.5–1% per trade · **Timeframes:** scalp, intraday, swing (same rules, different triplets) · **Audience:** advanced trader

---

## 1. Philosophy

Price action is the final output of all market information — news, wars, rate decisions, sentiment — already digested by participants. This system never asks *why* price moved; it only asks *where structure broke, where liquidity sits, and whether the market confirmed intent*. That is what makes it regime-agnostic: a war headline doesn't change the rules, it only changes the ATR, and the ATR is built into every stop and position size.

Three pillars:

1. **Structure** tells you direction (who controls the market).
2. **Liquidity** tells you location (where the trade should happen).
3. **Confirmation** tells you timing (when the market has shown its hand).

You only ever trade where all three agree. Most of your edge comes from *not* trading the other 95% of the time.

---

## 2. Timeframe triplets

One system, three speeds. Each mode uses three timeframes: **HTF (bias) → MTF (setup) → LTF (trigger)**.

| Mode | HTF bias | MTF setup | LTF trigger | Typical hold |
|---|---|---|---|---|
| Scalp | 1H | 15m | 1–3m | minutes–hours |
| Intraday | 4H | 1H | 5–15m | hours–1 day |
| Swing | Daily/Weekly | 4H | 1H | days–weeks |

The rules below are identical in all three modes. "HTF/MTF/LTF" always refers to your chosen triplet.

---

## 3. Pillar 1 — Structure (direction)

### 3.1 Reading structure
Mark swing highs/lows on the HTF (a swing point = a candle with lower highs on both sides, or higher lows for swing lows — use 3-candle pivots).

- **Uptrend:** higher highs (HH) + higher lows (HL) → you only look for longs.
- **Downtrend:** lower lows (LL) + lower highs (LH) → you only look for shorts.
- **Range:** no clean sequence → trade the range edges only, or stand aside.

### 3.2 Structure events
- **BOS (Break of Structure):** close beyond the most recent swing point *in trend direction*. Continuation signal.
- **CHoCH (Change of Character):** close beyond the most recent swing point *against* the trend. First warning the trend may be flipping. One CHoCH = caution; CHoCH + BOS in the new direction = new trend confirmed.

**Rule: use candle closes, not wicks, to define breaks.** Wicks through a level are liquidity events (Pillar 2), not structure events.

### 3.3 The bias rule
You may only take trades on MTF/LTF in the direction of HTF structure. If HTF is ranging, halve your risk (0.5% max) and only trade from range extremes back toward the middle.

---

## 4. Pillar 2 — Liquidity (location)

Markets move from liquidity pool to liquidity pool. Your job is to enter *after* a pool has been consumed, not before.

### 4.1 Where liquidity sits
- **Equal highs / equal lows** — clustered stops; magnets for price.
- **Prior day/week high & low** (PDH/PDL, PWH/PWL) — in forex/metals; **prior daily/weekly open** matters in crypto too.
- **Trendline & range boundaries** — breakout traders' entries = their stops on failure.
- **Session highs/lows** — Asia range high/low is routinely swept in London (forex/gold).

### 4.2 Points of Interest (POI)
A POI is a zone where you are *willing* to trade, marked on the MTF:

- **Demand/supply zone (order block):** the last opposite candle (or small cluster) before the impulsive move that caused a BOS. Refine it on the LTF to the candle body, not the full wick range.
- **Imbalance (Fair Value Gap):** a 3-candle gap left by an impulsive move; price frequently rebalances into it before continuing.
- The best POIs sit at the *origin* of a BOS leg and overlap a liquidity pool (e.g., a demand zone resting just below equal lows).

### 4.3 The sweep — your core setup condition
The single highest-quality event in this system: **price wicks through a liquidity pool into your POI and closes back inside.** That is engineered stop-hunting completing its job. Trapped breakout traders + filled institutional orders = fuel for the reversal in your HTF direction.

**No sweep into the POI = no A+ setup.** A simple tap of the POI without a sweep is a B-setup: allowed only in strong trends, at half risk.

---

## 5. Pillar 3 — Confirmation (timing)

Never enter on the touch alone. After price reaches the POI (ideally via a sweep), drop to the LTF and demand one of:

1. **LTF CHoCH:** price breaks its most recent LTF swing point in your intended direction. The strongest confirmation. Entry: on the retest of the LTF break, or the first LTF demand/supply zone created by the CHoCH leg.
2. **Engulfing close:** an MTF candle that sweeps the prior candle's extreme and *closes* beyond its body, in your direction, inside the POI.
3. **Rejection close:** an MTF candle closing back inside the POI with ≥50% of its range as wick rejecting the level.

Confirmation must occur **inside or immediately at the POI**. A CHoCH that happens 2× ATR away from the zone is chasing — skip it.

---

## 6. The A+ checklist (all six or no trade)

1. ☐ HTF structure gives clear bias (trending, or price at a range extreme).
2. ☐ MTF POI in the direction of bias (order block / FVG at origin of a BOS leg).
3. ☐ Identifiable liquidity pool at or just beyond the POI.
4. ☐ Liquidity **sweep** — wick through, close back (B-setup if absent: half risk, trend only).
5. ☐ LTF confirmation (CHoCH / engulfing / rejection close) *at the zone*.
6. ☐ Minimum **2R** available to the first opposing liquidity pool/structure (3R+ for swing).

Score every trade 1–6 in your journal. Over time you'll see your real edge lives in 6/6 trades.

---

## 7. Volatility adaptation (why this works in any regime)

Never use fixed pips/dollars. Everything is denominated in **ATR(14) of your MTF**.

- **Stop-loss:** beyond the sweep extreme (the wick low/high) **plus a buffer of 0.25–0.5 × ATR**. The stop is placed where the setup is *invalidated*, then volatility-padded so noise can't tag it.
- **Position size:** `size = (account × risk%) ÷ stop distance`. High volatility → wider stop → automatically smaller position. Same dollar risk in calm and chaotic markets. This is the entire "war-proof" mechanism: you don't predict volatility, you *price* it.
- **Regime filter:** compute `ATR(14) ÷ ATR(100)`:
  - **< 0.7 (compressed):** expect sweeps and fake breakouts; favor range-edge setups; expansion is coming.
  - **0.7–1.5 (normal):** full system, full risk.
  - **> 1.5 (expanded):** trend setups only, B-setups banned, consider 0.5% risk.
  - **> 2.5 (shock):** stand aside until the ratio normalizes. No setup quality survives a market gapping through stops. Not trading *is* the strategy here.
- **Spread filter (scalping):** if spread > 10% of your stop distance, skip. This alone disqualifies most exotic pairs and small-cap alts from scalping.

---

## 8. Trade management

- **Entry:** limit order at the LTF confirmation retest, or market on the confirmation close.
- **Stop:** as in §7. **Never widen a stop.**
- **TP1 at +1R:** close 50%, move stop to breakeven. The trade can no longer lose.
- **TP2 at +2R:** close 25% (scalp/intraday) or hold (swing).
- **Runner:** trail the remainder behind each new MTF swing point (structure trailing, not ATR trailing — exit when structure breaks against you, BOS by close).
- **Time stop:** scalp — exit if nothing happens in ~10 LTF candles; intraday — flat by session close; swing — reassess if no progress in 3 daily candles.
- **One trade per setup, max 2 concurrent positions per mode, max 3 correlated exposures** (EURUSD+GBPUSD+Gold long = ~one USD-short trade; count it as such).

### Expectancy math (why 2R minimum matters)
At 2R average winners, you're profitable above ~34% win rate. This setup class historically prints 40–55% for disciplined traders, but **your** numbers only come from **your** journal. With 1% risk and a 40% win rate at 2R: expectancy = (0.4 × 2) − (0.6 × 1) = **+0.2R per trade** — about +2% per 10 trades before costs. Compounding does the rest; chasing more per trade is how accounts die.

---

## 9. Market-specific notes

**Forex:** Trade the London and New York sessions; the Asia range is your liquidity reference, not your trading window. Majors only for scalping (spread filter). Expect the PDH/PDL sweep-and-reverse as a recurring A+ pattern.

**Gold/Silver:** The cleanest sweep behavior of any market — and the most violent. Always use the full ATR buffer (0.5×). Gold routinely runs 1.5× the "obvious" stop level before reversing; place stops beyond the *sweep wick*, never at round numbers ($2,400.00 is a liquidity pool, not protection).

**Crypto:** 24/7, so PDH/PDL matter less; **weekly open, monthly open, and prior weekly high/low** are the dominant liquidity references. Weekend liquidity is thin — sweeps are common but follow-through is poor: trade at half risk or skip weekends. BTC/ETH set the regime; an altcoin long against a BTC downtrend violates the bias rule by proxy.

---

## 10. Risk & survival rules (non-negotiable)

1. 0.5–1% risk per trade. 1% only for 6/6 A+ setups in normal regime.
2. **Daily stop: −2%** → flat, done for the day. **Weekly stop: −5%** → done for the week.
3. After 3 consecutive losses, halve risk until 2 consecutive wins.
4. No averaging down. No revenge trades. No moving stops away from price.
5. News events: you don't trade *because* of news, but don't hold a scalp through a known data release with a stop inside the expected spike range — that's donating. Swing trades sized at 1% with structure-based stops simply ride through; that's the design.
6. Withdraw or set aside profits quarterly. Realized money changes psychology for the better.

---

## 11. Execution routine

**Weekly (Sunday/Monday):** mark HTF structure, weekly open, PWH/PWL, major POIs on every instrument you trade (keep the watchlist ≤ 8).

**Daily:** update bias per instrument: *long-only / short-only / stand-aside*. Mark fresh MTF POIs and liquidity pools. Note ATR regime.

**Per trade:** checklist §6 → size per §7 → set orders → walk away. Screenshot entry and exit, score 1–6, log: instrument, mode, R result, regime ratio, setup grade.

**Monthly:** review the journal. Cut whatever loses (an instrument, a session, B-setups). Your journal — not this document — is the strategy after 100 trades.

---

## 12. Honest limits

No strategy guarantees returns, and anyone promising "good ROI" without showing you drawdowns is selling something. What this system controls is *risk per unit of volatility* and *trade selection quality*; what it cannot control is your discipline or the market's distribution of opportunities. Expect: losing weeks (often), losing months (sometimes), 40–55% win rate at 2R+ targets if executed well — and a real edge only after the journal proves it on **your** execution. Forward-test on a demo or tiny size for at least 50 trades before risking meaningful capital. This document is educational, not financial advice.
