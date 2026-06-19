"""Negative / failure-path validation (12 Jun 2026).

Every test here feeds the system something WRONG — bad data, breached caps,
corrupt files, missing feeds, hostile commands — and asserts it fails SAFELY
(rejects, holds, falls back) instead of trading on garbage or crashing.
Failures of these guards are exactly the events that destroy live accounts.

Run:  python3 -m unittest tests.test_negative_cases -v
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from marketdata.base import Bar
from execution.base import OrderRequest
from execution.paper import PaperRouter
from strategy.confirmation import ConfirmationConfig, check_candle_anatomy, confirm
from strategy.patterns import Signal, detect_double_top_bottom, score_pattern, _DTConfig
from strategy.risk import Instrument, RiskConfig, RiskState, evaluate_signal
from strategy.shadow import ShadowTracker
from news_agent import RSSFetcher, analyze_headline_impact


def mk_bar(t, o, h, l, c, symbol="EURUSD", tf="1h"):
    return Bar(symbol=symbol, timeframe=tf, time=t, open=o, high=h, low=l,
               close=c, volume=100.0)


def mk_signal(**kw):
    base = dict(symbol="EURUSD", timeframe="1h", setup="DB", side="buy",
                entry=1.1000, sl=1.0950, tp=1.1100, pattern_level=1.1000,
                detected_at="2026-06-12T10:00:00Z", bars_in_pattern=20, rr=2.0)
    base.update(kw)
    return Signal(**base)


def mk_inst(**kw):
    base = dict(symbol="EURUSD", pip_size=0.0001, pip_value=10.0,
                tick_value=1.0, tick_size=0.0001)
    base.update(kw)
    return Instrument(**base)


def mk_state(equity=100000.0, **kw):
    from datetime import datetime, timezone
    s = RiskState(starting_equity=equity, current_equity=equity)
    for k, v in kw.items():
        setattr(s, k, v)
    # Pin to the CURRENT day/week so rollover_if_needed() doesn't wipe the
    # counters we just set (a mismatched date triggers the daily reset).
    now = datetime.now(timezone.utc)
    s.last_day = now.strftime("%Y-%m-%d")
    s.last_week = f"{now.isocalendar().year}-W{now.isocalendar().week:02d}"
    return s


CFG = RiskConfig(per_trade_pct=2.0, min_rr=2.0, daily_trade_cap=3,
                 weekly_trade_cap=12, daily_loss_pct=3.0, max_drawdown_pct=10.0,
                 kill_switch_file="state/__no_such_file__")


# --------------------------------------------------------------------------- #
# Risk rails — every breach must REJECT
# --------------------------------------------------------------------------- #
class TestRiskRails(unittest.TestCase):
    def test_drawdown_breach_halts_and_stays_halted(self):
        st = mk_state()
        st.current_equity = 89999.0          # >10% down
        ok, why, lots = evaluate_signal(mk_signal(), mk_inst(), CFG, st)
        self.assertFalse(ok)
        self.assertIn("drawdown", why.lower())
        self.assertTrue(st.halted_for_dd, "halt flag must latch")
        ok2, why2, _ = evaluate_signal(mk_signal(), mk_inst(), CFG, st)
        self.assertFalse(ok2)
        self.assertIn("halt", why2.lower())

    def test_daily_loss_cap_blocks(self):
        st = mk_state(realized_today=-3500.0)   # > 3% of 100k
        ok, why, _ = evaluate_signal(mk_signal(), mk_inst(), CFG, st)
        self.assertFalse(ok)
        self.assertIn("daily loss", why.lower())

    def test_daily_and_weekly_trade_caps(self):
        st = mk_state(trades_today=3)
        ok, why, _ = evaluate_signal(mk_signal(), mk_inst(), CFG, st)
        self.assertFalse(ok and "cap" not in why.lower())
        st2 = mk_state(trades_this_week=12)
        ok2, why2, _ = evaluate_signal(mk_signal(), mk_inst(), CFG, st2)
        self.assertFalse(ok2)
        self.assertIn("cap", why2.lower())

    def test_rr_below_minimum_rejected(self):
        ok, why, _ = evaluate_signal(mk_signal(rr=1.4), mk_inst(), CFG, mk_state())
        self.assertFalse(ok)
        self.assertIn("RR", why)

    def test_zero_risk_distance_rejected(self):
        ok, why, _ = evaluate_signal(mk_signal(sl=1.1000), mk_inst(), CFG, mk_state())
        self.assertFalse(ok)
        self.assertIn("risk distance", why.lower())

    def test_kill_switch_file_blocks_everything(self):
        with tempfile.NamedTemporaryFile() as f:
            cfg = RiskConfig(kill_switch_file=f.name)
            ok, why, _ = evaluate_signal(mk_signal(), mk_inst(), cfg, mk_state())
            self.assertFalse(ok)
            self.assertIn("kill switch", why.lower())

    def test_missing_tick_data_falls_back_to_config(self):
        st = mk_state()
        sizing = {}
        ok, why, lots = evaluate_signal(
            mk_signal(), mk_inst(tick_value=None, tick_size=None), CFG, st, sizing)
        self.assertTrue(ok)
        self.assertEqual(sizing["source"], "config_fallback",
                         "fallback sizing must be flagged for audit")


# --------------------------------------------------------------------------- #
# Detectors / confirmation on garbage data — must not signal, must not crash
# --------------------------------------------------------------------------- #
class TestBadMarketData(unittest.TestCase):
    def test_too_few_bars_no_signal(self):
        bars = [mk_bar(f"2026-06-12T{i:02d}:00:00Z", 1.1, 1.101, 1.099, 1.1005)
                for i in range(10)]
        self.assertEqual(detect_double_top_bottom(bars), [])

    def test_flat_dead_market_no_signal(self):
        bars = [mk_bar(f"2026-06-1{i // 24}T{i % 24:02d}:00:00Z", 1.1, 1.1, 1.1, 1.1)
                for i in range(60)]
        self.assertEqual(detect_double_top_bottom(bars), [])   # zero ATR path

    def test_zero_range_candle_fails_confirmation(self):
        res = check_candle_anatomy(
            mk_bar("t", 1.1, 1.1, 1.1, 1.1), "buy", ConfirmationConfig())
        self.assertFalse(res.passed)
        self.assertIn("Zero-range", res.detail)

    def test_clarity_score_zero_atr_safe(self):
        self.assertEqual(score_pattern(touch_diff=0, depth=0, gap_bars=10,
                                       violations=0, atr=0.0, cfg=_DTConfig()), 0.0)


# --------------------------------------------------------------------------- #
# Paper router — hostile inputs
# --------------------------------------------------------------------------- #
class TestRouterNegative(unittest.TestCase):
    def _router(self, td):
        return PaperRouter(100000.0,
                           {"EURUSD": {"pip_size": 0.0001, "pip_value": 10.0}},
                           ledger_path=str(Path(td) / "l.json"),
                           slippage_pips=0.0, commission_per_lot=0.0)

    def test_unknown_instrument_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            r = self._router(td)
            fill = r.submit(OrderRequest(ticket=1, symbol="GHOST", side="buy",
                                         lots=0.1, entry=1, sl=0.9, tp=1.2,
                                         setup="DB", timeframe="1h", detected_at="x"))
            self.assertIsNone(fill)

    def test_corrupt_ledger_does_not_crash(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "l.json"
            p.write_text("{this is not json")
            r = PaperRouter(100000.0, {}, ledger_path=str(p))
            self.assertEqual(r.equity(), 100000.0)   # falls back to clean start

    def test_close_at_market_invalid_inputs(self):
        with tempfile.TemporaryDirectory() as td:
            r = self._router(td)
            self.assertEqual(r.close_at_market(123, 0.0), [])     # no price
            self.assertEqual(r.close_at_market(123, 1.1), [])     # no such ticket

    def test_modify_sl_never_widens_risk(self):
        with tempfile.TemporaryDirectory() as td:
            r = self._router(td)
            r.submit(OrderRequest(ticket=2, symbol="EURUSD", side="buy", lots=0.1,
                                  entry=1.1000, sl=1.0950, tp=1.1100, setup="DB",
                                  timeframe="1h", detected_at="x",
                                  tick_value=1.0, tick_size=0.0001))
            self.assertFalse(r.modify_sl(2, 1.0800, "hostile widen"))
            self.assertFalse(r.modify_sl(2, 0.0, "zero"))
            self.assertAlmostEqual(r.open_positions()[0].sl, 1.0950, places=5)

    def test_cross_timeframe_bar_does_not_manage_position(self):
        """A 1d bar must NOT trigger exits on a 1h position (old bug)."""
        with tempfile.TemporaryDirectory() as td:
            r = self._router(td)
            r.submit(OrderRequest(ticket=3, symbol="EURUSD", side="buy", lots=0.1,
                                  entry=1.1000, sl=1.0950, tp=1.1100, setup="DB",
                                  timeframe="1h", detected_at="x",
                                  tick_value=1.0, tick_size=0.0001))
            daily = mk_bar("2026-06-12T00:00:00Z", 1.2, 1.2, 0.9, 1.0, tf="1d")
            self.assertEqual(r.on_bar("EURUSD", daily), [])
            self.assertEqual(len(r.open_positions()), 1)


# --------------------------------------------------------------------------- #
# Shadow tracker / news pipeline — malformed inputs
# --------------------------------------------------------------------------- #
class TestNewsAndShadowNegative(unittest.TestCase):
    def test_shadow_malformed_signal_ignored(self):
        with tempfile.TemporaryDirectory() as td:
            sh = ShadowTracker(outcomes_path=str(Path(td) / "o.jsonl"),
                               pending_path=str(Path(td) / "p.json"))
            class Junk:                    # no numeric entry
                entry = "not-a-price"; symbol = "X"; timeframe = "1h"
                setup = "DB"; side = "buy"; sl = 1; tp = 2; rr = 2
            sh.register(Junk(), "confirmation", "x")
            self.assertEqual(sh.pending_count(), 0)

    def test_shadow_corrupt_pending_file_recovers(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "p.json"
            p.write_text("][ garbage")
            sh = ShadowTracker(outcomes_path=str(Path(td) / "o.jsonl"),
                               pending_path=str(p))
            self.assertEqual(sh.pending_count(), 0)

    def test_rss_garbage_xml_returns_empty(self):
        f = RSSFetcher()
        self.assertEqual(f._parse(b"<<<<not xml>>>>"), [])
        self.assertEqual(f._parse(b""), [])

    def test_impact_empty_title_or_pairs(self):
        self.assertIsNone(analyze_headline_impact({"title": ""}, ["EURUSD"], 0.3))
        self.assertIsNone(analyze_headline_impact(
            {"title": "Fed signals surprise rate hike as dollar surges"}, [], 0.3))

    def test_impact_pair_without_driver_excluded(self):
        imp = analyze_headline_impact(
            {"title": "Fed signals surprise rate hike as dollar surges"},
            ["AUDJPY"], 0.3)      # USD not in AUDJPY → nothing to map
        self.assertIsNone(imp)


if __name__ == "__main__":
    unittest.main(verbosity=2)
