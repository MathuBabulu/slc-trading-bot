# Performance review — 2026-06-16 03:47 UTC

- Starting equity: **100,000.00**  |  Current equity: **110,368.16**  (**10,368.16**, 10.37%)
- Open positions: **2**  |  Closed legs: **13**

## Signal funnel (last ~24h)
- Setups detected: **144**  |  accepted: **5**  |  orders filled: **5**
- Rejected by gate: confirmation: 66, dead_market: 55, htf_context: 12, choppiness: 2, correlation: 2, correlation_open: 2

## Overall
- Trades (by ticket): **8**  |  Win rate: **75%** (6W)
- Net P&L: **10,368.16**  |  Profit factor: **2.07**  |  Avg R: **1.34**  |  Max drawdown: **9,654.84**
- Closed legs: 13 (10W / 3L) — note: scale-outs produce 2 legs per trade

## By setup
| setup | Legs | Net | Win% | PF | Avg R |
|---|---:|---:|---:|---:|---:|
| DB | 5 | -3,987.08 | 60% | 0.59 | 0.68 |
| TL | 2 | 2,706.06 | 50% | 921.43 | 1.00 |
| DT | 6 | 11,649.18 | 100% | ∞ | 2.00 |

## By pair
| pair | Legs | Net | Win% | PF | Avg R |
|---|---:|---:|---:|---:|---:|
| USDCAD | 1 | -6,354.65 | 0% | 0.00 | -1.00 |
| GBPJPY | 1 | -3,300.19 | 0% | 0.00 | -1.00 |
| EURJPY | 3 | 5,042.10 | 67% | 1,716.00 | 1.33 |
| USDJPY | 4 | 7,091.50 | 100% | ∞ | 1.85 |
| CADJPY | 4 | 7,889.40 | 100% | ∞ | 2.00 |

## By timeframe
| timeframe | Legs | Net | Win% | PF | Avg R |
|---|---:|---:|---:|---:|---:|
| 4h | 4 | 2,367.57 | 75% | 1.72 | 1.10 |
| 1h | 9 | 8,000.59 | 78% | 2.26 | 1.44 |

## Exit reasons
tp_partial: 6, trail: 3, sl: 2, be: 1, ltf_rev_2h_dt: 1

## Tuning suggestions
_Nothing flagged — sample still small or metrics healthy. Keep collecting paper trades._

---
_Read-only review. No settings were changed. Tuning is applied only after you approve a suggestion._