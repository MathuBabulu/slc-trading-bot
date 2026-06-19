"""Regression test: Telegram messages must escape dynamic content.

Bug: notifications were sent with parse_mode=HTML but interpolated raw data.
A news reason like 'EUR <= 1.10' made Telegram read '<=' as a tag and reject
the whole message (400 "can't parse entities: Unsupported start tag"). The fix
escapes every interpolated value while keeping the template tags intact.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from telegram_notifier import TelegramNotifier

NASTY = "EUR <= 1.10 & GBP >= 1.30 <b>x</b>"   # the kind of text that 400'd before


def _capturing_notifier():
    n = TelegramNotifier(bot_token="dummy", chat_id="123")
    sent = []
    n._send = lambda text: (sent.append(text) or True)   # capture, don't POST
    return n, sent


def _assert_safe(text: str):
    # Raw data tokens that Telegram would mis-parse must NOT survive verbatim...
    assert "<=" not in text, f"unescaped '<=' leaked into message:\n{text}"
    assert ">=" not in text, f"unescaped '>=' leaked into message:\n{text}"
    assert "<b>x</b>" not in text, f"raw <b> from DATA leaked:\n{text}"
    # ...they must appear escaped instead...
    assert "&lt;=" in text and "&gt;=" in text, f"data not escaped:\n{text}"
    # ...while the real template tags are still present.
    assert "<b>" in text and "</b>" in text


def test_news_against_escapes_reason():
    n, sent = _capturing_notifier()
    n.news_against(symbol="EURUSD", side="sell", ticket=42, net_score=-0.7,
                   score_base=-0.3, score_quote=0.4, new_sl=1.1, entry=1.1,
                   reason=NASTY, live_mode=False)
    _assert_safe(sent[0])


def test_news_favourable_escapes_reason():
    n, sent = _capturing_notifier()
    n.news_favourable(symbol="GBPJPY", side="buy", ticket=7, net_score=0.6,
                      score_base=0.3, score_quote=-0.3, new_sl=214.5, old_sl=214.7,
                      reason=NASTY, live_mode=True)
    _assert_safe(sent[0])


def test_news_cut_loss_escapes_reason():
    n, sent = _capturing_notifier()
    n.news_cut_loss(symbol="USDJPY", side="sell", ticket=9, net_score=-0.9,
                    score_base=-0.5, score_quote=0.4, entry=160.2,
                    reason=NASTY, live_mode=False)
    _assert_safe(sent[0])


def test_signal_and_order_escape_symbol():
    n, sent = _capturing_notifier()
    n.signal_detected({"setup": "DT", "symbol": "X<=Y", "side": "sell",
                       "timeframe": "1h", "entry": 1.1, "sl": 1.2, "tp": 0.9})
    assert "<=" not in sent[0] and "&lt;=" in sent[0]
    n.order_filled({"symbol": "A>=B", "side": "buy", "lots": 0.1,
                    "fill_price": 1.1, "sl": 1.0, "tp": 1.3, "setup": "DB",
                    "timeframe": "1h", "rr": 2.0, "risk_pct": 1.0})
    assert ">=" not in sent[1] and "&gt;=" in sent[1]


def test_telegram_header_prepended_on_every_message():
    n = TelegramNotifier(bot_token="t", chat_id="c", header="Pattern Strategy")
    captured = []

    class R:
        status_code = 200
        def json(self): return {"ok": True}

    def fake_post(url, json=None, timeout=None):
        captured.append(json["text"])
        return R()

    n._session.post = fake_post
    n._send("BODY")
    assert captured[0].startswith("<b>Pattern Strategy</b>\nBODY")


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  PASS  {name}")
            passed += 1
    print(f"\n{passed} tests passed.")
