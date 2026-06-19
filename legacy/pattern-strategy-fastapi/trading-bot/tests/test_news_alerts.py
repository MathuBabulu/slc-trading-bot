"""Unit tests for the news-agent market alerts (11 Jun 2026).

Run from trading-bot/ :  python3 -m unittest tests.test_news_alerts -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from news_agent import (RSSFetcher, _strip_html, analyze_headline_impact)
from telegram_notifier import TelegramNotifier

SAMPLE_RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item>
    <title>Fed signals surprise rate hike as inflation surges</title>
    <link>https://example.com/fed-hike</link>
    <source url="https://example.com">Example Wire</source>
    <description>&lt;a href="x"&gt;The Federal Reserve&lt;/a&gt; signalled a surprise
      hike after &lt;b&gt;inflation&lt;/b&gt; data came in hot.</description>
  </item>
  <item>
    <title>Local bakery wins award</title>
    <link>https://example.com/bakery</link>
  </item>
</channel></rss>"""

WATCH = ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDJPY"]


class TestRSSParse(unittest.TestCase):
    def test_rich_items(self):
        f = RSSFetcher(max_age_hours=999)
        items = f._parse(SAMPLE_RSS)
        self.assertEqual(len(items), 2)
        it = items[0]
        self.assertIn("rate hike", it["title"])
        self.assertEqual(it["link"], "https://example.com/fed-hike")
        self.assertEqual(it["source"], "Example Wire")
        self.assertIn("Federal Reserve", it["summary"])
        self.assertNotIn("<", it["summary"], "summary must be tag-free")

    def test_strip_html(self):
        self.assertEqual(_strip_html("<b>a</b>&amp; b\n  c"), "a & b c")


class TestImpactAnalysis(unittest.TestCase):
    def test_usd_bullish_headline_maps_to_pairs(self):
        item = {"title": "Fed signals surprise rate hike as dollar strengthens"}
        imp = analyze_headline_impact(item, WATCH, min_score=0.3)
        self.assertIsNotNone(imp)
        self.assertEqual(imp["driver"], "USD")
        self.assertGreater(imp["score"], 0)
        d = {i["pair"]: i["arrow"] for i in imp["pair_impacts"]}
        self.assertEqual(d.get("USDJPY"), "↑")   # USD base, bullish → up
        self.assertEqual(d.get("EURUSD"), "↓")   # USD quote, bullish → down
        self.assertNotIn("AUDJPY", d)            # USD not in this pair

    def test_irrelevant_headline_returns_none(self):
        self.assertIsNone(analyze_headline_impact(
            {"title": "Local bakery wins award"}, WATCH, 0.3))

    def test_threshold_respected(self):
        item = {"title": "Some concern about the dollar"}   # weak word (0.5 raw)
        self.assertIsNone(analyze_headline_impact(item, WATCH, min_score=0.5))


class TestNewsAlertMessage(unittest.TestCase):
    def _notifier_capture(self):
        tg = TelegramNotifier("t", "c")
        sent = []
        tg._send = lambda text: (sent.append(text), True)[1]
        return tg, sent

    def test_message_has_headline_expandable_and_impacts(self):
        tg, sent = self._notifier_capture()
        ok = tg.news_alert(
            headline="Fed signals surprise <rate> hike", source="Example Wire",
            published="Thu, 11 Jun 2026 08:00", link="https://example.com/x",
            driver="USD", score=0.75, direction="bullish",
            pair_impacts=[{"pair": "USDJPY", "arrow": "↑", "why": "USD strength"},
                          {"pair": "EURUSD", "arrow": "↓", "why": "USD strength"}],
            details="Long details here", live_mode=False,
        )
        self.assertTrue(ok)
        self.assertEqual(len(sent), 1)
        msg = sent[0]
        self.assertIn("NEWS ALERT", msg)
        self.assertIn("Fed signals surprise &lt;rate&gt; hike", msg)  # escaped
        self.assertIn("<blockquote expandable>", msg)                  # tap to expand
        self.assertIn("USDJPY</b> ↑", msg)
        self.assertIn("EURUSD</b> ↓", msg)
        self.assertIn('href="https://example.com/x"', msg)

    def test_fallback_when_expandable_rejected(self):
        tg = TelegramNotifier("t", "c")
        sent = []
        def fake_send(text):
            sent.append(text)
            return "expandable" not in text     # reject 1st form only
        tg._send = fake_send
        ok = tg.news_alert(headline="h", source="s", published="", link="",
                           driver="USD", score=0.5, direction="bullish",
                           pair_impacts=[{"pair": "USDJPY", "arrow": "↑", "why": "w"}],
                           details="d", live_mode=True)
        self.assertTrue(ok)
        self.assertEqual(len(sent), 2)
        self.assertIn("<blockquote>", sent[1])


class TestGeopoliticalLexicon(unittest.TestCase):
    """A presidential tariff statement scored 0.00 before 11 Jun — these
    pin the fix."""

    def test_trump_tariff_headline_is_usd_relevant_and_scored(self):
        from news_evaluator import score_currency_sentiment
        res = score_currency_sentiment(
            "USD", ["Trump threatens sweeping tariffs on European goods"])
        self.assertEqual(res.headline_count, 1, "must be recognised as USD news")
        self.assertLess(res.score, 0, "tariff threat must score bearish")

    def test_trade_war_alert_fires(self):
        imp = analyze_headline_impact(
            {"title": "White House escalates trade war, markets in turmoil"},
            ["EURUSD", "USDJPY"], min_score=0.5)
        self.assertIsNotNone(imp)
        self.assertEqual(imp["driver"], "USD")


class TestPaperSLCommand(unittest.TestCase):
    """News-agent SL commands must reach PAPER positions (they never did)."""

    def _router_with_position(self, td, side="buy"):
        import tempfile  # noqa: F401  (parity with other tests)
        from execution.base import OrderRequest
        from execution.paper import PaperRouter
        r = PaperRouter(
            starting_equity=10000.0,
            instruments={"EURUSD": {"pip_size": 0.0001, "pip_value": 10.0}},
            ledger_path=str(Path(td) / "ledger.json"),
            slippage_pips=0.0, commission_per_lot=0.0, scale_out=True,
        )
        r.submit(OrderRequest(
            ticket=99, symbol="EURUSD", side=side, lots=0.1,
            entry=1.1000, sl=1.0950 if side == "buy" else 1.1050,
            tp=1.1100 if side == "buy" else 1.0900,
            setup="DB", timeframe="1h", detected_at="2026-06-11T10:00:00Z",
            tick_value=1.0, tick_size=0.0001))
        return r

    def test_move_to_breakeven_applies(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            r = self._router_with_position(td)
            self.assertTrue(r.modify_sl(99, 1.1000, "news against — BE"))
            self.assertAlmostEqual(r.open_positions()[0].sl, 1.1000, places=5)

    def test_widening_risk_is_refused(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            r = self._router_with_position(td)
            self.assertFalse(r.modify_sl(99, 1.0900, "bad cmd"))   # below original SL
            self.assertAlmostEqual(r.open_positions()[0].sl, 1.0950, places=5)

    def test_unknown_ticket_returns_false(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            r = self._router_with_position(td)
            self.assertFalse(r.modify_sl(12345, 1.1000))

    def test_sell_side_protective_direction(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            r = self._router_with_position(td, side="sell")
            self.assertTrue(r.modify_sl(99, 1.1000, "BE"))         # down = protective
            self.assertFalse(r.modify_sl(99, 1.1100, "widen"))     # up = refused


class TestCutLoss(unittest.TestCase):
    """Strongly adverse news + underwater trade → close_position; the gate is
    STRICTER (−0.5) than the BE move (−0.25)."""

    # USD-bullish disaster for a EURUSD buy: net ≈ −1.0
    STRONG_ADVERSE = ["Fed signals surprise rate hike, dollar surges to record high"]
    # One moderate EUR-negative word: net = −0.25 (hits BE gate, NOT the −0.5 cut gate)
    MILD_ADVERSE = ["Euro lower against major currencies"]

    def _pos(self, current: float) -> dict:
        return {"ticket": 5, "symbol": "EURUSD", "side": "buy",
                "entry": 1.1000, "current": current, "sl": 1.0950, "tp": 1.1100}

    def test_strong_adverse_underwater_cuts(self):
        from news_evaluator import evaluate_trade
        d = evaluate_trade(self._pos(current=1.0970), self.STRONG_ADVERSE,
                           sentiment_threshold=0.25, cut_loss_threshold=0.5)
        self.assertEqual(d.action, "close_position", d.reason)
        self.assertIsNone(d.new_sl)
        self.assertLessEqual(d.net_score, -0.5)

    def test_mild_adverse_underwater_holds(self):
        from news_evaluator import evaluate_trade
        d = evaluate_trade(self._pos(current=1.0970), self.MILD_ADVERSE,
                           sentiment_threshold=0.25, cut_loss_threshold=0.5)
        self.assertEqual(d.action, "hold", d.reason)   # passes BE gate but BE impossible; below cut gate

    def test_strong_adverse_in_profit_moves_to_be_not_cut(self):
        from news_evaluator import evaluate_trade
        d = evaluate_trade(self._pos(current=1.1040), self.STRONG_ADVERSE,
                           sentiment_threshold=0.25, cut_loss_threshold=0.5)
        self.assertEqual(d.action, "move_sl_be", d.reason)

    def test_disabled_gate_never_cuts(self):
        from news_evaluator import evaluate_trade
        d = evaluate_trade(self._pos(current=1.0970), self.STRONG_ADVERSE,
                           sentiment_threshold=0.25, cut_loss_threshold=0.0)
        self.assertEqual(d.action, "hold")

    def test_paper_close_at_market_sell_pays_spread(self):
        import tempfile
        from execution.base import OrderRequest
        from execution.paper import PaperRouter
        with tempfile.TemporaryDirectory() as td:
            r = PaperRouter(10000.0, {"EURUSD": {"pip_size": 0.0001, "pip_value": 10.0}},
                            ledger_path=str(Path(td) / "l.json"), slippage_pips=0.0,
                            commission_per_lot=0.0, scale_out=True)
            r.submit(OrderRequest(ticket=8, symbol="EURUSD", side="sell", lots=0.1,
                                  entry=1.1000, sl=1.1050, tp=1.0900, setup="DT",
                                  timeframe="1h", detected_at="x",
                                  tick_value=1.0, tick_size=0.0001, spread=0.0004))
            ups = r.close_at_market(8, bid_price=1.1010, reason="news_cut_loss")
            self.assertEqual(len(ups), 1)
            self.assertAlmostEqual(ups[0].exit, 1.1010 + 0.0004, places=6)  # ask
            self.assertEqual(ups[0].close_reason, "news_cut_loss")
            self.assertLess(ups[0].pnl, 0)
            self.assertEqual(r.open_positions(), [])


class TestPresidentialPriorityLane(unittest.TestCase):
    TERMS = ["trump", "white house", "truth social", "potus",
             "president", "tweet", "tweets", "executive order"]

    def _is_priority(self, title):
        t = title.lower()
        return any(term in t for term in self.TERMS)

    def test_tweet_headline_detected_as_priority(self):
        for t in ["Trump posts on Truth Social about Fed",
                  "President's tweet rattles currency markets",
                  "White House executive order on trade"]:
            self.assertTrue(self._is_priority(t), t)
        self.assertFalse(self._is_priority("ECB holds rates steady"))

    def test_relaxed_gate_catches_moderate_tweet_move(self):
        """'Trump slams Fed' scores ±0.375 — below the normal 0.5 gate but
        above the priority gate (0.5 × 0.7 = 0.35). The priority lane exists
        exactly for this corridor."""
        item = {"title": "Trump slams Fed in fiery post, dollar concern grows"}
        normal = analyze_headline_impact(item, ["EURUSD", "USDJPY"], 0.5)
        relaxed = analyze_headline_impact(item, ["EURUSD", "USDJPY"], 0.5 * 0.7)
        self.assertIsNone(normal, "should be under the normal gate")
        self.assertIsNotNone(relaxed, "priority gate must catch it")
        self.assertEqual(relaxed["driver"], "USD")
        self.assertEqual(relaxed["direction"], "bearish")
        d = {i["pair"]: i["arrow"] for i in relaxed["pair_impacts"]}
        self.assertEqual(d.get("EURUSD"), "↑")   # USD weakness → EURUSD up
        self.assertEqual(d.get("USDJPY"), "↓")

    def test_trade_deal_post_scores_bullish(self):
        from news_evaluator import score_currency_sentiment
        res = score_currency_sentiment(
            "USD", ["Trump announces trade deal reached with EU"])
        self.assertEqual(res.headline_count, 1)
        self.assertGreater(res.score, 0)

    def test_priority_label_in_telegram_message(self):
        tg = TelegramNotifier("t", "c")
        sent = []
        tg._send = lambda text: (sent.append(text), True)[1]
        tg.news_alert(headline="Trump tweet moves markets", source="Wire",
                      published="", link="", driver="USD", score=-0.4,
                      direction="bearish",
                      pair_impacts=[{"pair": "EURUSD", "arrow": "↑", "why": "USD weakness"}],
                      details="d", live_mode=True, priority=True)
        self.assertIn("PRIORITY — PRESIDENTIAL / SOCIAL POST", sent[0])


class TestAlertFreshness(unittest.TestCase):
    """Yesterday's presidential post kept getting re-alerted via fresh wire
    rehashes. Alerts now require a PROVEN fresh timestamp and reject
    near-duplicate wording of already-sent stories."""

    def _agent(self):
        from news_agent import NewsAgent
        a = NewsAgent.__new__(NewsAgent)          # no config / network
        a.alert_max_age_min = 90.0
        a.alert_dedupe_hours = 48.0
        a._alerts_sent = {}
        return a

    def test_no_timestamp_is_not_fresh(self):
        a = self._agent()
        self.assertFalse(a._is_fresh({"title": "x", "published_iso": ""}))
        self.assertFalse(a._is_fresh({"title": "x"}))

    def test_recent_vs_old_timestamps(self):
        from datetime import datetime, timedelta, timezone
        a = self._agent()
        now = datetime.now(timezone.utc)
        fresh = (now - timedelta(minutes=10)).isoformat()
        stale = (now - timedelta(hours=20)).isoformat()    # yesterday's post
        self.assertTrue(a._is_fresh({"published_iso": fresh}))
        self.assertFalse(a._is_fresh({"published_iso": stale}))

    def test_rehash_coverage_is_deduped(self):
        a = self._agent()
        a._alerts_sent["k1"] = {"ts": "2026-06-12T06:00:00Z",
                                "title": "Nasdaq futures extend gains after "
                                         "Trump claims peace deal is near"}
        # Same story, different outlet wording → similar → suppressed
        self.assertTrue(a._too_similar_to_sent(
            "Trump claims peace deal near — Nasdaq futures extend gains"))
        # Genuinely different story → allowed
        self.assertFalse(a._too_similar_to_sent(
            "Bank of Japan surprises with emergency rate decision"))

    def test_old_store_format_migrates(self):
        import tempfile
        from news_agent import NewsAgent
        from pathlib import Path as P
        a = NewsAgent.__new__(NewsAgent)
        with tempfile.TemporaryDirectory() as td:
            p = P(td) / "sent.json"
            p.write_text('{"abc": "2026-06-12T06:00:00Z"}')   # legacy format
            a._alerts_sent_path = p
            store = a._load_alerts_sent()
            self.assertEqual(store["abc"]["ts"], "2026-06-12T06:00:00Z")
            self.assertIn("title", store["abc"])


class TestXPostFetcher(unittest.TestCase):
    def _fetcher(self, tweets):
        from news_agent import XPostFetcher
        f = XPostFetcher("token", ["realDonaldTrump"], max_age_minutes=90)
        class R:
            status_code = 200
            def __init__(self, payload): self._p = payload
            def json(self): return self._p
        class S:
            def __init__(self): self.calls = []
            def get(self, url, params=None, timeout=0):
                self.calls.append(url)
                if "/users/by/username/" in url:
                    return R({"data": {"id": "42"}})
                return R({"data": tweets})
        f.session = S()
        return f

    def test_fresh_post_becomes_priority_item(self):
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        tweets = [
            {"id": "1", "text": "Tariffs on all imports starting Monday. Markets will love it!",
             "created_at": (now - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S.000Z")},
            {"id": "2", "text": "Old post from yesterday about the economy and many other things",
             "created_at": (now - timedelta(hours=22)).strftime("%Y-%m-%dT%H:%M:%S.000Z")},
        ]
        items = self._fetcher(tweets).fetch_posts()
        self.assertEqual(len(items), 1, "only the fresh post survives the age gate")
        it = items[0]
        self.assertTrue(it["is_post"])
        self.assertEqual(it["source"], "@realDonaldTrump (X)")
        self.assertIn("x.com/realDonaldTrump/status/1", it["link"])
        self.assertTrue(it["published_iso"])
        # And it flows through impact analysis like any headline:
        imp = analyze_headline_impact(it, ["EURUSD", "USDJPY"], 0.35)
        self.assertIsNotNone(imp)

    def test_auth_failure_disables_quietly(self):
        from news_agent import XPostFetcher
        f = XPostFetcher("bad", ["x"])
        class R401:
            status_code = 401
            def json(self): return {}
        f.session = type("S", (), {"get": lambda s, *a, **k: R401()})()
        self.assertEqual(f.fetch_posts(), [])
        self.assertTrue(f._auth_failed)
        self.assertEqual(f.fetch_posts(), [])   # no repeated hammering


if __name__ == "__main__":
    unittest.main(verbosity=2)
