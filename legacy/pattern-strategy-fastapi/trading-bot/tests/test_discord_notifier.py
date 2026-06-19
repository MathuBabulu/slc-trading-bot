"""Tests for the Discord notifier and the multi-channel fan-out group."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from discord_notifier import DiscordNotifier, build_discord_notifier
from notifications import NotifierGroup, build_notifier


def _capturing():
    n = DiscordNotifier(webhook_url="https://discord.com/api/webhooks/1/abc")
    sent = []
    n._send = lambda text: (sent.append(text) or True)
    return n, sent


def test_discord_escapes_markdown_in_data():
    n, sent = _capturing()
    nasty = "EUR *star* _under_ `code` |pipe|"
    n.news_against(symbol="EURUSD", side="sell", ticket=42, net_score=-0.7,
                   score_base=-0.3, score_quote=0.4, new_sl=1.1, entry=1.1,
                   reason=nasty, live_mode=False)
    msg = sent[0]
    # raw markdown specials from DATA must be backslash-escaped
    assert "\\*star\\*" in msg and "\\_under\\_" in msg and "\\`code\\`" in msg
    # template formatting (the bold header) is still real markdown
    assert "**NEWS AGAINST" in msg


def test_discord_signal_and_order():
    n, sent = _capturing()
    n.signal_detected({"setup": "DT", "symbol": "CADJPY", "side": "sell",
                       "timeframe": "2h", "entry": 114.6, "sl": 114.7, "tp": 114.4})
    n.order_filled({"symbol": "CADJPY", "side": "sell", "lots": 0.28,
                    "fill_price": 114.6, "sl": 114.7, "tp": 114.4, "setup": "DT",
                    "timeframe": "2h", "rr": 2.0, "risk_pct": 1.0})
    assert "SIGNAL DETECTED" in sent[0] and "Double Top" in sent[0]
    assert "ORDER FILLED" in sent[1] and "1:2.0" in sent[1]


def test_group_fans_out_to_all_channels():
    calls = {"a": [], "b": []}

    class Fake:
        def __init__(self, key): self.key = key
        def signal_detected(self, sig): calls[self.key].append(sig); return True
        def _send(self, text): calls[self.key].append(text); return True

    g = NotifierGroup([Fake("a"), Fake("b")])
    assert g.signal_detected({"symbol": "X"}) is True
    assert g._send("hi") is True
    assert calls["a"] and calls["b"], "both channels must receive the message"


def test_group_one_channel_failure_does_not_block_other():
    ok = []

    class Boom:
        def _send(self, t): raise RuntimeError("network down")

    class Good:
        def _send(self, t): ok.append(t); return True

    g = NotifierGroup([Boom(), Good()])
    assert g._send("x") is True       # Good still delivered despite Boom raising
    assert ok == ["x"]


def test_build_discord_disabled_or_placeholder_returns_none():
    assert build_discord_notifier({}) is None
    assert build_discord_notifier({"discord": {"enabled": False}}) is None
    assert build_discord_notifier({"discord": {"enabled": True,
                                   "webhook_url": "YOUR_DISCORD_WEBHOOK_URL"}}) is None
    assert build_discord_notifier({"discord": {"enabled": True,
                                   "webhook_url": "https://example.com/not-a-webhook"}}) is None


def test_discord_header_prepended_on_every_message():
    n = DiscordNotifier(webhook_url="https://discord.com/api/webhooks/1/abc",
                        header="Pattern Strategy")
    captured = []

    class R:
        status_code = 204

    def fake_post(url, json=None, timeout=None):
        captured.append(json["content"])
        return R()

    n._session.post = fake_post
    n._send("BODY")
    assert captured[0].startswith("**Pattern Strategy**\nBODY")


def test_build_notifier_none_when_all_disabled():
    cfg = {"telegram": {"enabled": False}, "discord": {"enabled": False}}
    assert build_notifier(cfg) is None


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); print(f"  PASS  {name}"); passed += 1
    print(f"\n{passed} tests passed.")
