"""Telegram notifier for the News Monitoring Sub-Agent.

Implements the interface news_agent.py expects:
    build_notifier(raw_cfg) -> Optional[TelegramNotifier]
    TelegramNotifier.news_favourable(...)
    TelegramNotifier.news_against(...)
    TelegramNotifier.news_alert(...)

Credentials resolve in this order:
  1. config.yaml  telegram: bot_token / chat_id
  2. the trading bot's settings DB (what you saved in the dashboard)

Every message is prefixed with a [SLC NEWS] header so notifications are
distinguishable from other strategies sharing the same Telegram bot.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import requests

log = logging.getLogger(__name__)

HEADER = "📰 <b>[SLC NEWS]</b>\n"


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str, timeout: int = 10,
                 discord_url: str = "") -> None:
        self.token = token
        self.chat_id = chat_id
        self.timeout = timeout
        self.discord_url = discord_url

    # ----------------------------------------------------------- core
    @staticmethod
    def _to_markdown(msg: str) -> str:
        import html as _html
        import re as _re
        msg = (msg.replace("<b>", "**").replace("</b>", "**")
                  .replace("<i>", "*").replace("</i>", "*")
                  .replace("<code>", "`").replace("</code>", "`")
                  .replace("<blockquote expandable>", "\n> ")
                  .replace("</blockquote>", ""))
        msg = _re.sub(r'<a href="([^"]+)">[^<]*</a>', r"\1", msg)
        msg = _re.sub(r"<[^>]+>", "", msg)
        return _html.unescape(msg)

    def send(self, text: str) -> bool:
        ok = True
        if self.token and self.chat_id:
            try:
                r = requests.post(
                    "https://api.telegram.org/bot%s/sendMessage" % self.token,
                    json={"chat_id": self.chat_id, "text": HEADER + text,
                          "parse_mode": "HTML", "disable_web_page_preview": True},
                    timeout=self.timeout,
                )
                ok = bool(r.json().get("ok"))
                if not ok:
                    log.warning("Telegram error: %s", r.text[:200])
            except Exception as exc:  # noqa: BLE001
                log.warning("Telegram send failed: %s", exc)
                ok = False
        if self.discord_url:
            try:
                requests.post(self.discord_url, json={
                    "content": self._to_markdown(HEADER + text)[:1900]},
                    timeout=self.timeout)
            except Exception as exc:  # noqa: BLE001
                log.warning("Discord send failed: %s", exc)
        return ok

    @staticmethod
    def _mode_tag(live_mode: bool) -> str:
        return "" if live_mode else "\n<i>(dry-run — no command sent to MT5)</i>"

    # ------------------------------------------- position management
    def news_favourable(self, symbol: str, side: str, ticket: int,
                        net_score: float, score_base: float, score_quote: float,
                        new_sl: float, old_sl: float, reason: str,
                        live_mode: bool = False) -> bool:
        return self.send(
            "📈 <b>News favours trade — trailing SL</b>\n"
            "<b>%s %s</b> (ticket %s)\n"
            "Sentiment: net %+0.2f (base %+0.2f / quote %+0.2f)\n"
            "SL: <code>%.5f</code> → <code>%.5f</code>\n<i>%s</i>%s"
            % (side.upper(), symbol, ticket, net_score, score_base, score_quote,
               old_sl, new_sl, reason[:300], self._mode_tag(live_mode)))

    def news_against(self, symbol: str, side: str, ticket: int,
                     net_score: float, score_base: float, score_quote: float,
                     new_sl: float, entry: float, reason: str,
                     live_mode: bool = False) -> bool:
        return self.send(
            "🛡 <b>News against trade — SL to break-even</b>\n"
            "<b>%s %s</b> (ticket %s)\n"
            "Sentiment: net %+0.2f (base %+0.2f / quote %+0.2f)\n"
            "Entry: <code>%.5f</code> | new SL: <code>%.5f</code>\n<i>%s</i>%s"
            % (side.upper(), symbol, ticket, net_score, score_base, score_quote,
               entry, new_sl, reason[:300], self._mode_tag(live_mode)))

    # ------------------------------------------------- market alerts
    def news_alert(self, headline: str, source: str, published: str, link: str,
                   driver: str, score: float, direction: str,
                   pair_impacts: List[Dict[str, Any]], details: str,
                   live_mode: bool = False) -> bool:
        pair_line = "  ".join("%s %s" % (i["pair"], i["arrow"]) for i in pair_impacts)
        src = (" — %s" % source) if source else ""
        lnk = ('\n<a href="%s">source</a>' % link) if link else ""
        return self.send(
            "🗞 <b>Market alert</b> | %s %s %+0.2f\n"
            "<b>%s</b>%s\n%s\n"
            "<blockquote expandable>%s</blockquote>%s"
            % (driver, direction.upper(), score, headline[:200], src,
               pair_line, details[:900], lnk))


def build_notifier(raw_cfg: Dict[str, Any]) -> Optional[TelegramNotifier]:
    """Build a notifier from config.yaml's telegram section; fall back to the
    trading bot's settings DB (dashboard values). Returns None if disabled
    or unconfigured."""
    tg = (raw_cfg or {}).get("telegram", {}) or {}
    token = tg.get("bot_token") or ""
    chat_id = str(tg.get("chat_id") or "")
    enabled = tg.get("enabled", None)
    discord_url = ""

    try:
        import storage
        token = token or storage.get_setting("telegram_bot_token", "")
        chat_id = chat_id or str(storage.get_setting("telegram_chat_id", ""))
        if enabled is None:
            enabled = storage.get_setting("telegram_enabled", False)
        if storage.get_setting("discord_enabled", False):
            discord_url = storage.get_setting("discord_webhook_url", "")
    except Exception as exc:  # noqa: BLE001
        log.debug("Could not read bot settings DB: %s", exc)

    tg_on = bool(enabled and token and chat_id)
    if not tg_on and not discord_url:
        log.info("Notifier disabled (no Telegram token/chat_id, no Discord webhook).")
        return None
    log.info("Notifier active (telegram=%s discord=%s).",
             "on" if tg_on else "off", "on" if discord_url else "off")
    return TelegramNotifier(token if tg_on else "", chat_id if tg_on else "",
                            discord_url=discord_url)
