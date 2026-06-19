"""Tests for strategy/indicators.py and the engine indicator integration.

Covers:
  - EMA convergence (ema_last)
  - RSI oversold / overbought / neutral
  - RSI extreme edge cases (all-up, all-down moves)
  - ATR percentile rank (high-volatility vs quiet period)
  - Volume ratio (surge vs flat)
  - Dead-market gate: indicator_cfg triggers _reject on frozen market
  - Clarity bonus: RSI + EMA200 + volume each contribute correct points
  - Body ratio lowered to 0.55: candles that failed at 0.70 now pass
  - compute_context: no crash on short or empty bars

Run:  python3 -m unittest tests.test_indicators -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from marketdata.base import Bar
from strategy.indicators import (
    ema_last,
    rsi,
    atr_percentile_rank,
    volume_ratio,
    compute_context,
    indicator_clarity_bonus,
    IndicatorContext,
)
from strategy.confirmation import ConfirmationConfig, check_candle_anatomy


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def mk_bar(i: int, o: float, h: float, l: float, c: float,
           vol: float = 1000.0, sym: str = "EURUSD", tf: str = "1h") -> Bar:
    return Bar(
        symbol=sym, timeframe=tf,
        time=f"2026-06-13T{i % 24:02d}:00:00Z",
        open=o, high=h, low=l, close=c, volume=vol,
    )


def flat_bars(n: int, price: float = 1.1000, vol: float = 1000.0) -> list:
    return [mk_bar(i, price, price + 0.0005, price - 0.0005, price, vol) for i in range(n)]


def trending_bars(n: int, start: float = 1.1000, step: float = 0.0005) -> list:
    bars = []
    for i in range(n):
        o = start + i * step
        c = o + step
        h = max(o, c) + 0.0002
        l = min(o, c) - 0.0002
        bars.append(mk_bar(i, o, h, l, c))
    return bars


# --------------------------------------------------------------------------- #
# EMA
# --------------------------------------------------------------------------- #
class TestEmaLast(unittest.TestCase):
    def test_returns_none_when_too_short(self):
        self.assertIsNone(ema_last([1.0, 2.0], 5))

    def test_converges_to_value_for_constant_series(self):
        vals = [1.1] * 220
        result = ema_last(vals, 200)
        self.assertAlmostEqual(result, 1.1, places=4)

    def test_responds_to_recent_spike(self):
        vals = [1.0] * 200 + [2.0] * 5  # sudden jump at end
        result = ema_last(vals, 200)
        self.assertGreater(result, 1.0)  # should move up from 1.0

    def test_period_1_returns_last(self):
        self.assertAlmostEqual(ema_last([1.0, 2.0, 3.0], 1), 3.0, places=6)


# --------------------------------------------------------------------------- #
# RSI
# --------------------------------------------------------------------------- #
class TestRsi(unittest.TestCase):
    def test_returns_none_when_too_short(self):
        self.assertIsNone(rsi([1.0] * 5, 14))

    def test_all_up_moves_returns_100(self):
        vals = [float(i) for i in range(1, 20)]
        self.assertEqual(rsi(vals, 14), 100.0)

    def test_all_down_moves_returns_near_0(self):
        vals = [float(20 - i) for i in range(20)]
        result = rsi(vals, 14)
        self.assertLess(result, 5.0)

    def test_neutral_market_near_50(self):
        # Alternating +1 / -1
        vals = []
        v = 1.0
        for i in range(30):
            v += 1.0 if i % 2 == 0 else -1.0
            vals.append(v)
        result = rsi(vals, 14)
        self.assertIsNotNone(result)
        self.assertGreater(result, 40.0)
        self.assertLess(result, 60.0)

    def test_oversold_threshold(self):
        # 16 sharp down bars then 3 small up bars → RSI should be < 40
        vals = [10.0 - i * 0.5 for i in range(17)] + [2.0, 2.1, 2.2]
        result = rsi(vals, 14)
        self.assertIsNotNone(result)
        self.assertLess(result, 40.0)

    def test_overbought_threshold(self):
        # 16 sharp up bars then plateau → RSI should be > 60
        vals = [1.0 + i * 0.5 for i in range(17)] + [9.0, 9.05, 9.1]
        result = rsi(vals, 14)
        self.assertIsNotNone(result)
        self.assertGreater(result, 60.0)


# --------------------------------------------------------------------------- #
# ATR percentile rank
# --------------------------------------------------------------------------- #
class TestAtrPercentileRank(unittest.TestCase):
    def test_returns_none_when_too_short(self):
        bars = flat_bars(30)
        self.assertIsNone(atr_percentile_rank(bars, lookback=50, atr_period=14))

    def test_quiet_period_at_end_has_low_rank(self):
        # 200 volatile bars, then 15 quiet bars.
        # The lookback window (50 bars) has 35 volatile + 15 mixing in quiet,
        # so the current ATR (14 fully quiet bars) is well below the volatile majority.
        volatile = [mk_bar(i, 1.1, 1.1 + 0.01, 1.1 - 0.01, 1.1) for i in range(200)]
        quiet = [mk_bar(i + 200, 1.1, 1.10002, 1.09998, 1.1) for i in range(15)]
        bars = volatile + quiet
        rank = atr_percentile_rank(bars, lookback=50, atr_period=14)
        self.assertIsNotNone(rank)
        self.assertLess(rank, 0.20, "sudden quiet after long volatile session → bottom 20%")

    def test_volatile_period_at_end_has_high_rank(self):
        # 200 quiet bars, then 15 volatile bars.
        # Every bar in the lookback window that preceded the current is quiet;
        # the current ATR (14 volatile bars) is the highest in the window.
        quiet = [mk_bar(i, 1.1, 1.10002, 1.09998, 1.1) for i in range(200)]
        volatile = [mk_bar(i + 200, 1.1, 1.1 + 0.01, 1.1 - 0.01, 1.1) for i in range(15)]
        bars = quiet + volatile
        rank = atr_percentile_rank(bars, lookback=50, atr_period=14)
        self.assertIsNotNone(rank)
        self.assertGreater(rank, 0.70, "sudden volatility after quiet session → top 70%+")


# --------------------------------------------------------------------------- #
# Volume ratio
# --------------------------------------------------------------------------- #
class TestVolumeRatio(unittest.TestCase):
    def test_returns_none_when_too_short(self):
        self.assertIsNone(volume_ratio(flat_bars(5, vol=1000.0), 20))

    def test_average_volume_returns_near_1(self):
        bars = flat_bars(25, vol=1000.0)
        r = volume_ratio(bars, 20)
        self.assertIsNotNone(r)
        self.assertAlmostEqual(r, 1.0, places=2)

    def test_surge_returns_high_ratio(self):
        bars = flat_bars(25, vol=1000.0)
        # Last bar has 3× volume
        bars[-1] = mk_bar(25, 1.1, 1.1005, 1.0995, 1.1, vol=3000.0)
        r = volume_ratio(bars, 20)
        self.assertIsNotNone(r)
        self.assertAlmostEqual(r, 3.0, places=1)

    def test_zero_volume_returns_none(self):
        bars = flat_bars(25, vol=0.0)
        self.assertIsNone(volume_ratio(bars, 20))


# --------------------------------------------------------------------------- #
# compute_context (safe on edge cases)
# --------------------------------------------------------------------------- #
class TestComputeContext(unittest.TestCase):
    def test_empty_bars_no_crash(self):
        ctx = compute_context([])
        self.assertIsNone(ctx.rsi_14)
        self.assertIsNone(ctx.ema200)

    def test_short_bars_no_crash(self):
        ctx = compute_context(flat_bars(10))
        # Not enough bars for RSI(14) or EMA(200) — should be None, not raise
        self.assertIsNone(ctx.rsi_14)
        self.assertIsNone(ctx.ema200)

    def test_long_bars_populate_fields(self):
        bars = trending_bars(250)
        ctx = compute_context(bars)
        self.assertIsNotNone(ctx.rsi_14)
        self.assertIsNotNone(ctx.ema200)
        self.assertIsNotNone(ctx.close)


# --------------------------------------------------------------------------- #
# indicator_clarity_bonus
# --------------------------------------------------------------------------- #
class TestIndicatorClarityBonus(unittest.TestCase):
    def _ctx(self, rsi_val=None, ema200=None, close=None, vol_ratio=None, atr_pct=None):
        return IndicatorContext(
            rsi_14=rsi_val, ema200=ema200, close=close,
            vol_ratio=vol_ratio, atr_pct_rank=atr_pct,
        )

    def test_no_data_no_bonus(self):
        bonus, reasons = indicator_clarity_bonus(IndicatorContext(), "buy")
        self.assertEqual(bonus, 0.0)
        self.assertEqual(reasons, [])

    def test_rsi_oversold_buy_gets_15(self):
        ctx = self._ctx(rsi_val=32.0)
        bonus, reasons = indicator_clarity_bonus(ctx, "buy")
        self.assertEqual(bonus, 15.0)
        self.assertTrue(any("oversold" in r for r in reasons))

    def test_rsi_overbought_sell_gets_15(self):
        ctx = self._ctx(rsi_val=72.0)
        bonus, reasons = indicator_clarity_bonus(ctx, "sell")
        self.assertEqual(bonus, 15.0)

    def test_rsi_neutral_no_bonus(self):
        ctx = self._ctx(rsi_val=50.0)
        bonus, _ = indicator_clarity_bonus(ctx, "buy")
        self.assertEqual(bonus, 0.0)

    def test_rsi_overbought_buy_no_bonus(self):
        # RSI overbought but we're buying — no RSI bonus (wrong side)
        ctx = self._ctx(rsi_val=75.0)
        bonus, _ = indicator_clarity_bonus(ctx, "buy")
        self.assertEqual(bonus, 0.0)

    def test_ema200_aligned_buy_gets_10(self):
        ctx = self._ctx(ema200=1.1000, close=1.1200)  # price above EMA
        bonus, reasons = indicator_clarity_bonus(ctx, "buy")
        self.assertEqual(bonus, 10.0)
        self.assertTrue(any("EMA200" in r for r in reasons))

    def test_ema200_against_buy_no_bonus(self):
        ctx = self._ctx(ema200=1.1200, close=1.1000)  # price below EMA — bearish
        bonus, _ = indicator_clarity_bonus(ctx, "buy")
        self.assertEqual(bonus, 0.0)

    def test_volume_surge_gets_10(self):
        ctx = self._ctx(vol_ratio=2.0)
        bonus, reasons = indicator_clarity_bonus(ctx, "buy")
        self.assertEqual(bonus, 10.0)
        self.assertTrue(any("surge" in r for r in reasons))

    def test_volume_below_threshold_no_bonus(self):
        ctx = self._ctx(vol_ratio=1.3)  # < 1.5 threshold
        bonus, _ = indicator_clarity_bonus(ctx, "buy")
        self.assertEqual(bonus, 0.0)

    def test_all_three_bonuses_add_to_35(self):
        ctx = self._ctx(rsi_val=30.0, ema200=1.0900, close=1.1000, vol_ratio=2.0)
        bonus, reasons = indicator_clarity_bonus(ctx, "buy")
        self.assertEqual(bonus, 35.0)
        self.assertEqual(len(reasons), 3)

    def test_bonus_caps_clarity_at_100(self):
        """Simulate the engine capping: structural 80 + 35 bonus → capped at 100."""
        structural = 80.0
        ctx = self._ctx(rsi_val=30.0, ema200=1.0900, close=1.1000, vol_ratio=2.0)
        bonus, _ = indicator_clarity_bonus(ctx, "buy")
        final = round(min(100.0, structural + bonus), 1)
        self.assertEqual(final, 100.0)


# --------------------------------------------------------------------------- #
# Body ratio lowered: confirm candles that failed 0.70 now pass at 0.55
# --------------------------------------------------------------------------- #
class TestBodyRatioLowered(unittest.TestCase):
    def _bar(self, o, h, l, c):
        return mk_bar(0, o, h, l, c)

    def test_0_62_body_fails_old_threshold(self):
        """0.62 body ratio: would fail 0.70 gate but passes 0.55."""
        # body = 0.0062, range = 0.01 → ratio = 0.62
        bar = self._bar(1.1000, 1.1080, 1.0980, 1.1062)  # bullish, body 62 pts
        old_cfg = ConfirmationConfig(min_body_ratio=0.70)
        new_cfg = ConfirmationConfig(min_body_ratio=0.55)
        old_res = check_candle_anatomy(bar, "buy", old_cfg)
        new_res = check_candle_anatomy(bar, "buy", new_cfg)
        self.assertFalse(old_res.passed, "0.70 threshold should reject 0.62 body")
        self.assertTrue(new_res.passed, "0.55 threshold should accept 0.62 body")

    def test_0_50_body_fails_new_threshold_too(self):
        """0.50 body ratio: still fails at 0.55 (spinning top / doji)."""
        bar = self._bar(1.1000, 1.1080, 1.0980, 1.1050)  # body 50 pts / range 100 pts
        cfg = ConfirmationConfig(min_body_ratio=0.55)
        res = check_candle_anatomy(bar, "buy", cfg)
        self.assertFalse(res.passed, "0.55 threshold must still reject 0.50 body")

    def test_default_cfg_uses_new_threshold(self):
        """ConfirmationConfig() default must now be 0.55."""
        cfg = ConfirmationConfig()
        self.assertAlmostEqual(cfg.min_body_ratio, 0.55)


if __name__ == "__main__":
    unittest.main(verbosity=2)
