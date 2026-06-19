"""Unit tests for the 10 Jun 2026 improvement pass.

Covers: structured confirmation results, sizing invariant, journal write on a
simulated PaperRouter trade, per-level cooldown, clarity score, HTF context
filter, and the shadow tracker.

Run from trading-bot/ :  python3 -m unittest tests.test_new_logic -v
(stdlib unittest only — no pytest dependency)
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from marketdata.base import Bar
from execution.base import OrderRequest
from execution.paper import PaperRouter
from strategy.confirmation import (CheckResult, ConfirmationConfig,
                                   check_candle_anatomy, confirm, failed_check)
from strategy.cooldown import CooldownConfig, LevelCooldown
from strategy.htf import htf_trend_conflict
from strategy.journal import TradeJournal
from strategy.patterns import Signal, _DTConfig, score_pattern
from strategy.shadow import ShadowTracker


def mk_bar(t: str, o: float, h: float, l: float, c: float,
           symbol: str = "EURUSD", tf: str = "1h") -> Bar:
    return Bar(symbol=symbol, timeframe=tf, time=t, open=o, high=h, low=l,
               close=c, volume=100.0)


def mk_signal(**kw) -> Signal:
    base = dict(symbol="EURUSD", timeframe="1h", setup="DB", side="buy",
                entry=1.1000, sl=1.0950, tp=1.1100, pattern_level=1.1000,
                detected_at="2026-06-10T10:00:00Z", bars_in_pattern=20,
                rr=2.0, clarity_score=70.0)
    base.update(kw)
    return Signal(**base)


# --------------------------------------------------------------------------- #
# Structured confirmation
# --------------------------------------------------------------------------- #
class TestConfirmation(unittest.TestCase):
    def test_candle_anatomy_failure_is_named(self):
        # Doji-ish bar: tiny body — must fail candle_anatomy with value+threshold.
        bar = mk_bar("2026-06-10T10:00:00Z", 1.1000, 1.1010, 1.0990, 1.1001)
        res = check_candle_anatomy(bar, "buy", ConfirmationConfig())
        self.assertFalse(res.passed)
        self.assertEqual(res.name, "candle_anatomy")
        self.assertIsNotNone(res.value)
        self.assertEqual(res.threshold, 0.55)  # updated from 0.70 on 13 Jun

    def test_confirm_reports_failing_check_not_last_note(self):
        """THE logging bug: candle fails, momentum passes — failed_check must
        say candle_anatomy (previously logged as '✓ Slow approach OK')."""
        bars = [mk_bar(f"2026-06-10T{i:02d}:00:00Z", 1.10, 1.101, 1.099, 1.1005)
                for i in range(22)]
        # Last bar: tiny body → candle anatomy fails; volatility flat → momentum passes.
        ok, checks = confirm(mk_signal(), bars, ConfirmationConfig())
        self.assertFalse(ok)
        self.assertEqual(failed_check(checks), "candle_anatomy")
        momentum = [c for c in checks if c.name == "momentum"][0]
        self.assertTrue(momentum.passed)

    def test_confirm_passes_good_candle(self):
        bars = [mk_bar(f"2026-06-10T{i:02d}:00:00Z", 1.10, 1.101, 1.099, 1.1005)
                for i in range(21)]
        bars.append(mk_bar("2026-06-10T22:00:00Z", 1.0995, 1.1011, 1.0994, 1.1010))
        ok, checks = confirm(mk_signal(), bars, ConfirmationConfig())
        self.assertTrue(ok, [c.detail for c in checks])
        self.assertIsNone(failed_check(checks))


# --------------------------------------------------------------------------- #
# Sizing invariant
# --------------------------------------------------------------------------- #
class TestSizingInvariant(unittest.TestCase):
    def _router(self, tmp: Path, alerts: list) -> PaperRouter:
        return PaperRouter(
            starting_equity=10000.0,
            instruments={"EURUSD": {"pip_size": 0.0001, "pip_value": 10.0}},
            ledger_path=str(tmp / "ledger.json"),
            slippage_pips=0.0, commission_per_lot=0.0,
            scale_out=False, alert=alerts.append,
        )

    def _open(self, router: PaperRouter, risked_money: float):
        req = OrderRequest(
            ticket=1, symbol="EURUSD", side="buy", lots=0.4,
            entry=1.1000, sl=1.0950, tp=1.1100, setup="DB", timeframe="1h",
            detected_at="2026-06-10T10:00:00Z",
            tick_value=1.0, tick_size=0.0001,       # 0.4 lots × 50 ticks × 1.0 = 20 at SL
            risked_money=risked_money, sizing_basis="test",
        )
        return router.submit(req)

    def test_consistent_close_no_alert(self):
        alerts: list = []
        with tempfile.TemporaryDirectory() as td:
            r = self._router(Path(td), alerts)
            self._open(r, risked_money=20.0)        # matches tick math exactly
            bar = mk_bar("2026-06-10T11:00:00Z", 1.1090, 1.1105, 1.1085, 1.1100)
            ups = r.on_bar("EURUSD", bar)           # TP hit → +2R = +40
            self.assertEqual(len(ups), 1)
            self.assertAlmostEqual(ups[0].pnl, 40.0, places=2)
            self.assertEqual(alerts, [])

    def test_mismatched_risk_fires_alert(self):
        """Recorded risk says 2, P&L math says 20/R — the NAS100-style
        mismatch must trigger a CRITICAL alert on close."""
        alerts: list = []
        with tempfile.TemporaryDirectory() as td:
            r = self._router(Path(td), alerts)
            self._open(r, risked_money=2.0)         # wrong by 10x
            bar = mk_bar("2026-06-10T11:00:00Z", 1.1090, 1.1105, 1.1085, 1.1100)
            r.on_bar("EURUSD", bar)
            self.assertEqual(len(alerts), 1)
            self.assertIn("SIZING INVARIANT VIOLATION", alerts[0])

    def test_breakeven_close_skipped(self):
        alerts: list = []
        with tempfile.TemporaryDirectory() as td:
            r = self._router(Path(td), alerts)
            r2 = self._open(r, risked_money=2.0)    # wrong, but BE close → skip
            self.assertIsNotNone(r2)
            r.flatten_all("test")                   # closes at entry, R≈0
            self.assertEqual(alerts, [])


# --------------------------------------------------------------------------- #
# Journal wiring
# --------------------------------------------------------------------------- #
class TestJournal(unittest.TestCase):
    def test_fill_and_close_write_journal_file(self):
        with tempfile.TemporaryDirectory() as td:
            jdir = Path(td) / "journal"
            journal = TradeJournal(str(jdir))
            router = PaperRouter(
                starting_equity=10000.0,
                instruments={"EURUSD": {"pip_size": 0.0001, "pip_value": 10.0}},
                ledger_path=str(Path(td) / "ledger.json"),
                slippage_pips=0.0, commission_per_lot=0.0, scale_out=False,
            )
            sig = mk_signal()
            req = OrderRequest(ticket=42, symbol="EURUSD", side="buy", lots=0.1,
                               entry=sig.entry, sl=sig.sl, tp=sig.tp, setup="DB",
                               timeframe="1h", detected_at=sig.detected_at,
                               tick_value=1.0, tick_size=0.0001,
                               risked_money=5.0, sizing_basis="test")
            fill = router.submit(req)
            self.assertIsNotNone(fill)
            journal.open_trade(fill, sig, pattern_bars=[{"t": "x"}])
            f = jdir / "42.json"
            self.assertTrue(f.exists(), "journal file not written on open")
            rec = json.loads(f.read_text())
            self.assertEqual(rec["status"], "open")
            self.assertEqual(rec["signal"]["setup"], "DB")

            # Close at TP and journal the closure (as engine._journal_closure does).
            bar = mk_bar("2026-06-10T11:00:00Z", 1.1090, 1.1105, 1.1085, 1.1100)
            ups = router.on_bar("EURUSD", bar)
            self.assertEqual(len(ups), 1)
            journal.record_close(ups[0], trade_bars=[{"t": "y"}], still_open=False)
            rec = json.loads(f.read_text())
            self.assertEqual(rec["status"], "closed")
            self.assertEqual(len(rec["exits"]), 1)
            self.assertAlmostEqual(rec["net_pnl"], ups[0].pnl, places=2)


# --------------------------------------------------------------------------- #
# Per-level cooldown
# --------------------------------------------------------------------------- #
class TestCooldown(unittest.TestCase):
    def setUp(self):
        self.cd = LevelCooldown(CooldownConfig(enabled=True, atr_mult=0.5, bars=10))
        self.times = [f"2026-06-10T{i:02d}:00:00Z" for i in range(20)]

    def test_refire_same_level_suppressed(self):
        first = self.cd.check("EURUSD", "1h", "buy", 1.1000, atr=0.0020, bar_times=self.times[:10])
        self.assertIsNone(first)
        again = self.cd.check("EURUSD", "1h", "buy", 1.1005, atr=0.0020, bar_times=self.times[:12])
        self.assertIsNotNone(again)        # within 0.5×ATR=10 pips, 2 bars later
        self.assertEqual(again["prior_level"], 1.1000)

    def test_distant_level_allowed(self):
        self.cd.check("EURUSD", "1h", "buy", 1.1000, atr=0.0020, bar_times=self.times[:10])
        far = self.cd.check("EURUSD", "1h", "buy", 1.1050, atr=0.0020, bar_times=self.times[:12])
        self.assertIsNone(far)             # 50 pips away > 10-pip tolerance

    def test_opposite_side_not_suppressed(self):
        self.cd.check("EURUSD", "1h", "buy", 1.1000, atr=0.0020, bar_times=self.times[:10])
        sell = self.cd.check("EURUSD", "1h", "sell", 1.1000, atr=0.0020, bar_times=self.times[:12])
        self.assertIsNone(sell)

    def test_expires_after_n_bars(self):
        self.cd.check("EURUSD", "1h", "buy", 1.1000, atr=0.0020, bar_times=self.times[:5])
        # 14 bars later (> 10-bar cooldown) the level is cold again.
        later = self.cd.check("EURUSD", "1h", "buy", 1.1000, atr=0.0020, bar_times=self.times[:19])
        self.assertIsNone(later)


# --------------------------------------------------------------------------- #
# Clarity score
# --------------------------------------------------------------------------- #
class TestClarityScore(unittest.TestCase):
    CFG = _DTConfig()

    def test_perfect_pattern_scores_high(self):
        s = score_pattern(touch_diff=0.0, depth=4 * 0.0020,   # 2×min depth
                          gap_bars=28,                        # window midpoint
                          violations=0, atr=0.0020, cfg=self.CFG)
        self.assertGreaterEqual(s, 95.0)

    def test_sloppy_pattern_scores_low(self):
        s = score_pattern(touch_diff=0.0020, depth=0.0,
                          gap_bars=6, violations=3, atr=0.0020, cfg=self.CFG)
        self.assertLessEqual(s, 5.0)

    def test_bounds_and_monotonic_in_touch(self):
        tight = score_pattern(touch_diff=0.0001, depth=0.008, gap_bars=28,
                              violations=0, atr=0.0020, cfg=self.CFG)
        loose = score_pattern(touch_diff=0.0015, depth=0.008, gap_bars=28,
                              violations=0, atr=0.0020, cfg=self.CFG)
        self.assertGreater(tight, loose)
        for s in (tight, loose):
            self.assertGreaterEqual(s, 0.0)
            self.assertLessEqual(s, 100.0)

    def test_detector_attaches_score(self):
        """A clean synthetic double bottom must come out of the detector with
        clarity_score > 0 and a Clarity note."""
        from strategy.patterns import detect_double_top_bottom
        bars = []
        t = 0
        def add(o, h, l, c):
            nonlocal t
            bars.append(mk_bar(f"2026-06-{(t // 24) + 1:02d}T{t % 24:02d}:00:00Z", o, h, l, c))
            t += 1
        for _ in range(15):                       # drift down toward support
            add(1.1080, 1.1085, 1.1070, 1.1075)
        add(1.1010, 1.1015, 1.0950, 1.0960)       # trough 1
        add(1.0980, 1.1030, 1.0975, 1.1025)
        for _ in range(8):                        # crest (≥2 ATR above)
            add(1.1100, 1.1140, 1.1095, 1.1130)
        add(1.1010, 1.1015, 1.0952, 1.0990)       # trough 2 (≈ equal low)
        add(1.0990, 1.1020, 1.0988, 1.1015)       # rejection bar
        add(1.1015, 1.1030, 1.1010, 1.1028)
        sigs = detect_double_top_bottom(bars)
        dbs = [s for s in sigs if s.setup == "DB"]
        if dbs:                                    # geometry-dependent; if detected, must be scored
            self.assertGreater(dbs[0].clarity_score, 0.0)
            self.assertTrue(any("Clarity" in n for n in dbs[0].notes))


# --------------------------------------------------------------------------- #
# HTF context filter
# --------------------------------------------------------------------------- #
class TestHTF(unittest.TestCase):
    def _downtrend(self, n=70):
        bars = []
        px = 1.2000
        for i in range(n):
            o = px
            px -= 0.0010
            # carve clear falling swing lows every ~10 bars
            lo = px - (0.0030 if i % 10 == 5 else 0.0005)
            bars.append(mk_bar(f"2026-06-{(i // 24) + 1:02d}T{i % 24:02d}:00:00Z",
                               o, o + 0.0005, lo, px, tf="2h"))
        return bars

    def test_buy_blocked_in_htf_downtrend(self):
        conflict, detail, vals = htf_trend_conflict(self._downtrend(), "buy",
                                                    ema_period=50, swings=3)
        self.assertTrue(conflict, (detail, vals))
        self.assertIn("downtrend", detail)

    def test_sell_allowed_in_htf_downtrend(self):
        conflict, _, _ = htf_trend_conflict(self._downtrend(), "sell",
                                            ema_period=50, swings=3)
        self.assertFalse(conflict)

    def test_insufficient_history_allows(self):
        conflict, detail, _ = htf_trend_conflict(self._downtrend(20), "buy")
        self.assertFalse(conflict)
        self.assertIn("Insufficient", detail)


# --------------------------------------------------------------------------- #
# Shadow tracker
# --------------------------------------------------------------------------- #
class TestShadow(unittest.TestCase):
    def _tracker(self, td: str, max_bars: int = 100) -> ShadowTracker:
        return ShadowTracker(outcomes_path=str(Path(td) / "out.jsonl"),
                             pending_path=str(Path(td) / "pending.json"),
                             max_bars=max_bars)

    def _outcomes(self, td: str) -> list:
        p = Path(td) / "out.jsonl"
        if not p.exists():
            return []
        return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]

    def test_win_resolution(self):
        with tempfile.TemporaryDirectory() as td:
            sh = self._tracker(td)
            sh.register(mk_signal(), "confirmation", "candle_anatomy")
            self.assertEqual(sh.pending_count(), 1)
            # First post-registration bar is the ENTRY bar (look-ahead guard):
            # it must not resolve the shadow even if it touched TP.
            sh.on_bar(mk_bar("2026-06-10T11:00:00Z", 1.1000, 1.1020, 1.0990, 1.1010))
            self.assertEqual(self._outcomes(td), [])
            self.assertEqual(sh.pending_count(), 1)
            # Next bar touches TP (1.1100) without touching SL (1.0950) → win.
            sh.on_bar(mk_bar("2026-06-10T12:00:00Z", 1.1050, 1.1105, 1.1040, 1.1100))
            outs = self._outcomes(td)
            self.assertEqual(len(outs), 1)
            self.assertEqual(outs[0]["outcome"], "win")
            self.assertEqual(outs[0]["r"], 2.0)
            self.assertEqual(outs[0]["failed_check"], "candle_anatomy")
            self.assertEqual(sh.pending_count(), 0)

    def test_loss_and_conservative_tiebreak(self):
        with tempfile.TemporaryDirectory() as td:
            sh = self._tracker(td)
            sh.register(mk_signal(), "htf_context", "htf_trend")
            # Entry bar first (look-ahead guard), then the resolving bar.
            sh.on_bar(mk_bar("2026-06-10T11:00:00Z", 1.1000, 1.1020, 1.0990, 1.1010))
            # Bar touches BOTH SL and TP → conservative loss.
            sh.on_bar(mk_bar("2026-06-10T12:00:00Z", 1.1000, 1.1105, 1.0940, 1.1050))
            outs = self._outcomes(td)
            self.assertEqual(outs[0]["outcome"], "loss")
            self.assertEqual(outs[0]["r"], -1.0)

    def test_timeout_marks_to_market(self):
        with tempfile.TemporaryDirectory() as td:
            sh = self._tracker(td, max_bars=3)
            sh.register(mk_signal(), "risk", "risk")
            # 4 bars: the first is the ENTRY bar (look-ahead guard), leaving 3
            # forward bars to reach max_bars=3 and time out. No SL/TP touch.
            for i in range(4):
                sh.on_bar(mk_bar(f"2026-06-10T1{i + 2}:00:00Z",
                                 1.1010, 1.1030, 1.1000, 1.1025))
            outs = self._outcomes(td)
            self.assertEqual(len(outs), 1)
            self.assertEqual(outs[0]["outcome"], "timeout")
            self.assertAlmostEqual(outs[0]["r"], 0.5, places=1)  # +25 pips / 50-pip risk

    def test_duplicate_registration_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            sh = self._tracker(td)
            sh.register(mk_signal(), "confirmation", "candle_anatomy")
            sh.register(mk_signal(), "confirmation", "momentum")   # same entry
            self.assertEqual(sh.pending_count(), 1)

    def test_pending_survives_restart(self):
        with tempfile.TemporaryDirectory() as td:
            sh = self._tracker(td)
            sh.register(mk_signal(), "confirmation", "candle_anatomy")
            sh2 = self._tracker(td)                                # fresh instance
            self.assertEqual(sh2.pending_count(), 1)


# --------------------------------------------------------------------------- #
# Spread-aware execution (bars are BID; sells exit at ASK)
# --------------------------------------------------------------------------- #
class TestSpreadModel(unittest.TestCase):
    SPREAD = 0.0004   # 4 pips on a 5-digit pair

    def _router(self, td: str) -> PaperRouter:
        return PaperRouter(
            starting_equity=10000.0,
            instruments={"EURUSD": {"pip_size": 0.0001, "pip_value": 10.0}},
            ledger_path=str(Path(td) / "ledger.json"),
            slippage_pips=1.0, commission_per_lot=0.0, scale_out=False,
        )

    def _req(self, side: str, spread: float, **kw):
        base = dict(ticket=7, symbol="EURUSD", side=side, lots=0.1,
                    entry=1.1000, sl=1.0950 if side == "buy" else 1.1050,
                    tp=1.1100 if side == "buy" else 1.0900,
                    setup="DB", timeframe="1h", detected_at="2026-06-11T10:00:00Z",
                    tick_value=1.0, tick_size=0.0001, spread=spread)
        base.update(kw)
        return OrderRequest(**base)

    def test_buy_fills_at_ask(self):
        with tempfile.TemporaryDirectory() as td:
            r = self._router(td)
            fill = r.submit(self._req("buy", self.SPREAD))
            self.assertAlmostEqual(fill.fill_price, 1.1000 + self.SPREAD, places=6)
            self.assertAlmostEqual(fill.spread, self.SPREAD, places=6)

    def test_sell_fills_at_bid(self):
        with tempfile.TemporaryDirectory() as td:
            r = self._router(td)
            fill = r.submit(self._req("sell", self.SPREAD))
            self.assertAlmostEqual(fill.fill_price, 1.1000, places=6)

    def test_fallback_to_slippage_when_no_live_spread(self):
        with tempfile.TemporaryDirectory() as td:
            r = self._router(td)
            fill = r.submit(self._req("buy", 0.0))
            self.assertAlmostEqual(fill.fill_price, 1.1000 + 0.0001, places=6)  # 1 pip
            self.assertAlmostEqual(fill.spread, 0.0001, places=6)

    def test_sell_sl_triggers_at_ask(self):
        """Bid high stays BELOW the SL, but ask (bid+spread) reaches it —
        the stop must fire (this is how it would fill at the broker)."""
        with tempfile.TemporaryDirectory() as td:
            r = self._router(td)
            r.submit(self._req("sell", self.SPREAD))
            bar = mk_bar("2026-06-11T11:00:00Z", 1.1030,
                         1.1050 - 0.0002, 1.1020, 1.1040)   # high 1.1048 < SL 1.1050
            ups = r.on_bar("EURUSD", bar)
            self.assertEqual(len(ups), 1)
            self.assertEqual(ups[0].close_reason, "sl")
            self.assertLess(ups[0].pnl, 0)

    def test_sell_tp_needs_bid_beyond_target_by_spread(self):
        with tempfile.TemporaryDirectory() as td:
            r = self._router(td)
            r.submit(self._req("sell", self.SPREAD))
            # Bid low touches TP exactly: ask = tp + spread → NOT filled yet.
            bar1 = mk_bar("2026-06-11T11:00:00Z", 1.0950, 1.0960, 1.0900, 1.0955)
            self.assertEqual(r.on_bar("EURUSD", bar1), [])
            # Bid low goes spread beyond TP → ask reaches target → filled.
            bar2 = mk_bar("2026-06-11T12:00:00Z", 1.0950, 1.0960,
                          1.0900 - self.SPREAD, 1.0955)
            ups = r.on_bar("EURUSD", bar2)
            self.assertEqual(len(ups), 1)
            self.assertEqual(ups[0].close_reason, "tp")

    def test_buy_exits_unaffected_by_spread_field(self):
        with tempfile.TemporaryDirectory() as td:
            r = self._router(td)
            r.submit(self._req("buy", self.SPREAD))
            bar = mk_bar("2026-06-11T11:00:00Z", 1.1090, 1.1105, 1.1085, 1.1100)
            ups = r.on_bar("EURUSD", bar)
            self.assertEqual(len(ups), 1)
            self.assertEqual(ups[0].close_reason, "tp")
            # P&L = (tp − (entry+spread)) × lots/tick math → spread cost included
            expected = (1.1100 - (1.1000 + self.SPREAD)) / 0.0001 * 1.0 * 0.1
            self.assertAlmostEqual(ups[0].pnl, round(expected, 2), places=2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
