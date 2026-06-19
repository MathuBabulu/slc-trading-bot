# Price-Action Strategy — Knowledge Base

*Distilled from the "All Setups From Last Week Live Session" frames (14 PDFs, ~106
frames). Purpose: a reusable reference for the strategy's rules and a guide for future
trades and bot tuning.*

> **Read this first — survivorship bias.** These PDFs are a curated **winners-only**
> highlight reel. Every example is a win. So this material is excellent for learning
> *what the strategy trades and how it's framed*, but it says **nothing** about the true
> win-rate or how losers behave. Never loosen risk based on it.

---

## 1. The core setup (what every example has in common)

Every single setup follows the same skeleton:

1. **A tested horizontal level.** Price reaches a level it has visited before — a
   resistance that capped it, or a support that held — i.e. a **double top / double
   bottom** at that level. Setups never trigger mid-range.
2. **Entry on the *second* test of the level** (the "R2" entry), not on a breakout.
3. **Trade away from the level:** short the second rejection of resistance, long the
   second hold of support.
4. **Stop just beyond the level**, **target the prior swing**, giving a fixed **~1:2**
   reward:risk (the green target box is ~2× the red stop box in every frame).
5. **Confluence boosts it:** a trendline meeting the level (GBPUSD held support sitting
   on a rising trendline; GBPAUD bounced support *and* broke a descending trendline).

## 2. What it's applied to

| Dimension | Observed in the setups |
|-----------|------------------------|
| Timeframes | **15m, 30m, 1h, 2h** (all treated identically) |
| FX | NZDCAD, NZDUSD, AUDNZD (×2), GBPUSD, GBPAUD, CADCHF — **NZD / AUD / GBP crosses dominate** |
| Commodities | USOIL (WTI) — short the double-top of a rally (×2) |
| Crypto | BNBUSDT — long off rising-trendline support |
| Direction | Both long and short; direction is dictated by the level, not a bias |

## 3. Worked logic (representative examples)

- **AUDNZD 1h short @ 1.2237** — price tagged 1.2237 twice, rejected, fell to target.
  *Classic resistance-retest short.*
- **GBPUSD 15m long @ 1.3620** — support held while sitting on a rising trendline
  (two reasons to be long), then rallied. *Confluence long.*
- **GBPAUD long @ ~2.0000** — double bottom that also broke a descending trendline;
  confirmed on M30. *Reversal with trendline-break confirmation.*
- **USOIL 1h short @ ~78.5** — double top at the top of an extended rally, dropped to
  target. *Exhaustion short at resistance.*
- **AUDNZD 2h long** — buy a pullback to support *inside* an uptrend (trend continuation),
  showing the same pair can be long or short depending on which level it's at.

## 4. How this maps to the bot (and the gaps)

| Strategy element | Bot status |
|------------------|-----------|
| Entry on 2nd test of level (R2) | ✅ engine enters at the level, not the break |
| Double top / double bottom | ✅ implemented + enabled |
| Resistance/support retest | ✅ effectively the same as DT/DB detection |
| ~1:2 R:R, stop beyond level | ✅ `risk.min_rr = 2.0` |
| Timeframes 15m/30m/1h/2h | ✅ all in `config.timeframes` |
| Confirmation candle / slow approach | ✅ confirmation module |
| Correlation / "right direction" / not-choppy | ✅ correlation module |
| **Trendline support/break** | ⚠️ **GAP** — used in several setups (GBPUSD, GBPAUD, BNB) but the trendline detector is a **v2 stub**. Biggest missing piece. |
| **Crypto + commodity symbols** | ⚠️ ensure these are in the EA watch/bar list with correct broker names (USOIL→USOUSD alias already added; BNBUSDT only if your broker offers it). |

## 5. Practical guidance for future trades

- Favour the pairs this strategy clearly works on here: **NZD/AUD/GBP crosses**, plus
  **WTI**. These are also where the **correlation filter** matters most (NZD and AUD
  crosses move together — don't stack them; the filter now prevents that).
- Only act on the **second** touch of a level, with a confirmation candle, and only when
  the approach isn't choppy — exactly the gates now in the engine.
- Because we only have winners here, treat the **risk caps as sacred** and let paper
  mode reveal the real win-rate before trusting it.

---

*Companion files: `setups_dataset.json` (labeled catalog) and `parameter_tuning.md`
(the concrete config changes derived from this study).*
