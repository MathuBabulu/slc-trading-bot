"""Discord notifier — mirrors the Telegram notifications to a Discord channel.

Uses a Discord *incoming webhook* (no bot token / OAuth needed): you create a
webhook URL in the channel settings and the bot POSTs JSON to it. Every method
here matches TelegramNotifier's signature so a NotifierGroup can fan the same
calls out to both channels with no changes at the call sites.

Discord uses Markdown (**bold**, `code`, *italic*, [text](url)) rather than the
HTML Telegram uses, and caps a message at 2000 chars. Dynamic values are escaped
so a stray markdown character in data can't mangle the message, and @mentions
are disabled for safety.

Setup (config.yaml):
    discord:
      enabled: true
      webhook_url: "https://discord.com/api/webhooks/XXXX/YYYY"
      username: "SLC Trading Bot"
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import requests

log = logging.getLogger(__name__)

SETUP_NAMES: Dict[str, str] = {
    "DT": "Double Top", "DB": "Double Bottom", "HS": "Head & Shoulders",
    "IHS": "Inverse H&S", "TT": "Triple Top", "TB": "Triple Bottom",
    "RECT": "Rectangle", "TL": "Trendline Break",
}
DIRECTION_EMOJI = {"buy": "📈", "sell": "📉", "BUY": "📈", "SELL": "📉"}

_MD_SPECIAL = ("\\", "`", "*", "_", "~", "|", ">")


def _md(value) -> str:
    """Escape Discord markdown specials in dynamic content."""
    s = "" if value is None else str(value)
    for ch in _MD_SPECIAL:
        s = s.replace(ch, "\\" + ch)
    return s


def _now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


class DiscordNotifier:
    """Posts Markdown messages to a Discord channel via an incoming webhook."""

    mode = "discord"

    def __init__(self, webhook_url: str, username: str = "SLC Trading Bot",
                 timeout: int = 10, header: str = "") -> None:
        self.webhook_url = webhook_url.strip()
        self.username = username
        self.timeout = timeout
        self.header = (header or "").strip()   # title line prepended to every message
        self._lock = threading.Lock()
        self._last_send = 0.0
        self._session = requests.Session()

    # ── Public notification methods (mirror TelegramNotifier) ──────────────

    def signal_detected(self, signal: dict) -> bool:
        setup = signal.get("setup", "?")
        side = signal.get("direction") or signal.get("side", "?")
        emoji = DIRECTION_EMOJI.get(side, "↔️")
        text = (
            f"🔍 **SIGNAL DETECTED**\n\n"
            f"**{_md(signal.get('symbol', '?'))}** — {_md(SETUP_NAMES.get(setup, setup))}\n"
            f"{emoji} Direction: **{_md((side or '?').upper())}** | "
            f"Timeframe: **{_md(signal.get('timeframe', '?'))}**\n"
            f"📍 Entry: `{_md(signal.get('entry', '?'))}`\n"
            f"🛑 SL: `{_md(signal.get('sl', '?'))}`\n"
            f"🎯 TP: `{_md(signal.get('tp', '?'))}`\n"
            f"_Awaiting confirmation filter…_"
        )
        return self._send(text)

    def order_filled(self, payload: dict) -> bool:
        setup = payload.get("setup", "?")
        side = payload.get("side", "?")
        emoji = DIRECTION_EMOJI.get(side, "↔️")
        rr = payload.get("rr", None)
        risk = payload.get("risk_pct", None)
        rr_str = f"1:{float(rr):.1f}" if rr is not None else "—"
        risk_str = f"{float(risk):.1f}%" if risk is not None else "—"
        text = (
            f"✅ **ORDER FILLED**\n\n"
            f"**{_md(payload.get('symbol', '?'))}** — {_md(SETUP_NAMES.get(setup, setup))} "
            f"({_md(payload.get('timeframe', '?'))})\n"
            f"{emoji} **{_md((side or '?').upper())}** | Lots: **{_md(payload.get('lots', '?'))}**\n"
            f"📍 Fill: `{_md(payload.get('fill_price', '?'))}`\n"
            f"🛑 SL: `{_md(payload.get('sl', '?'))}`\n"
            f"🎯 TP: `{_md(payload.get('tp', '?'))}`\n"
            f"⚖️ Risk: {risk_str} | RR: {rr_str}\n"
            f"⏰ {_now_str()}"
        )
        return self._send(text)

    def news_favourable(self, symbol, side, ticket, net_score, score_base,
                        score_quote, new_sl, old_sl, reason, live_mode) -> bool:
        emoji = DIRECTION_EMOJI.get(str(side).lower(), "↔️")
        mode = "🟢 LIVE — command sent to MT5" if live_mode else "🟡 DRY-RUN — not executed"
        text = (
            f"📈 **NEWS FAVOURABLE — TRAILING SL**\n\n"
            f"{emoji} **{_md(symbol)}** {_md(str(side).upper())} | Ticket: `{_md(ticket)}`\n"
            f"💡 Net sentiment: **+{net_score:.2f}**\n"
            f"📊 Base: {score_base:+.2f} | Quote: {score_quote:+.2f}\n"
            f"🔄 SL moved: `{old_sl:.5f}` → `{new_sl:.5f}`\n"
            f"📝 _{_md(str(reason)[:200])}_\n"
            f"{mode}\n⏰ {_now_str()}"
        )
        return self._send(text)

    def news_against(self, symbol, side, ticket, net_score, score_base,
                     score_quote, new_sl, entry, reason, live_mode) -> bool:
        emoji = DIRECTION_EMOJI.get(str(side).lower(), "↔️")
        mode = "🟢 LIVE — command sent to MT5" if live_mode else "🟡 DRY-RUN — not executed"
        text = (
            f"🛡 **NEWS AGAINST — MOVING TO BREAK-EVEN**\n\n"
            f"{emoji} **{_md(symbol)}** {_md(str(side).upper())} | Ticket: `{_md(ticket)}`\n"
            f"💡 Net sentiment: **{net_score:.2f}**\n"
            f"📊 Base: {score_base:+.2f} | Quote: {score_quote:+.2f}\n"
            f"🔄 SL → Break-even: `{new_sl:.5f}` (entry was `{entry:.5f}`)\n"
            f"📝 _{_md(str(reason)[:200])}_\n"
            f"{mode}\n⏰ {_now_str()}"
        )
        return self._send(text)

    def news_cut_loss(self, symbol, side, ticket, net_score, score_base,
                      score_quote, entry, reason, live_mode) -> bool:
        emoji = DIRECTION_EMOJI.get(str(side).lower(), "↔️")
        mode = "🟢 LIVE — position closed" if live_mode else "🟡 DRY-RUN — not executed"
        text = (
            f"✂️ **NEWS CUT-LOSS — CLOSING AT MARKET**\n\n"
            f"{emoji} **{_md(symbol)}** {_md(str(side).upper())} | Ticket: `{_md(ticket)}`\n"
            f"💡 Net sentiment: **{net_score:.2f}** (cut gate −0.50)\n"
            f"📊 Base: {score_base:+.2f} | Quote: {score_quote:+.2f}\n"
            f"Trade underwater (entry `{entry:.5f}`) — break-even impossible, strong "
            f"news against. Cutting the loss now beats riding it to the stop.\n"
            f"📝 _{_md(str(reason)[:200])}_\n"
            f"{mode}\n⏰ {_now_str()}"
        )
        return self._send(text)

    def news_alert(self, headline, source, published, link, driver, score,
                   direction, pair_impacts, details, live_mode,
                   priority: bool = False) -> bool:
        dir_emoji = "🟢" if score > 0 else "🔴"
        arrows = " · ".join(f"**{_md(i['pair'])}** {i.get('arrow','')}"
                            for i in pair_impacts[:8]) or "—"
        why = pair_impacts[0]["why"] if pair_impacts else f"{driver} move"
        mode = "🟢 agent LIVE" if live_mode else "🟡 agent DRY-RUN"
        label = "⚡ **PRIORITY — PRESIDENTIAL / SOCIAL POST**\n" if priority else ""
        src = _md(source) or "news"
        when = f" · {_md(published)}" if published else ""
        link_line = f"🔗 [Read full story]({link})\n" if link else ""
        block = "\n".join("> " + ln for ln in _md(str(details)).strip().splitlines()[:20])
        text = (
            f"{label}🗞 **NEWS ALERT** — **{_md(driver)}** {dir_emoji} "
            f"{_md(direction)} ({score:+.2f})\n\n"
            f"**{_md(headline)}**\n_{src}{when}_\n\n"
            f"🎯 **Likely impact** ({_md(why)}):\n{arrows}\n\n"
            f"{block}\n\n{link_line}{mode} — SL actions only on open positions\n⏰ {_now_str()}"
        )
        return self._send(text[:1990])

    def test_message(self) -> bool:
        return self._send(
            "✅ **Trading Bot — Discord connected!**\n\n"
            "Notifications are active for:\n"
            "• Signal detected (Buy / Sell)\n• Order filled\n• News agent outcomes\n"
            f"⏰ {_now_str()}"
        )

    # ── Internal ───────────────────────────────────────────────────────────

    def _send(self, text: str) -> bool:
        if not self.webhook_url:
            log.warning("Discord: webhook_url not set — skipping notification")
            return False
        # Prepend the configured header (e.g. "Pattern Strategy") to every message.
        if self.header:
            text = f"**{_md(self.header)}**\n{text}"
        with self._lock:
            elapsed = time.monotonic() - self._last_send
            if elapsed < 0.5:                     # gentle pacing (Discord ~30/min)
                time.sleep(0.5 - elapsed)
            payload = {
                "content": text[:2000],
                "username": self.username,
                "allowed_mentions": {"parse": []},  # never ping @everyone/@here
            }
            try:
                resp = self._session.post(self.webhook_url, json=payload, timeout=self.timeout)
                self._last_send = time.monotonic()
                if resp.status_code in (200, 204):
                    return True
                log.warning("Discord send failed: %s — %s", resp.status_code, resp.text[:200])
            except Exception as exc:  # noqa: BLE001
                log.warning("Discord send error: %s", exc)
        return False


def build_discord_notifier(cfg: Dict[str, Any]) -> Optional[DiscordNotifier]:
    """Build a DiscordNotifier from config, or None if disabled/unconfigured."""
    dc = cfg.get("discord", {}) or {}
    if not dc.get("enabled", False):
        log.info("Discord notifications disabled (discord.enabled: false)")
        return None
    url = str(dc.get("webhook_url", "") or "").strip()
    if not url or url.startswith("YOUR_") or "discord.com/api/webhooks/" not in url:
        log.warning("Discord: webhook_url not set or invalid — notifications disabled")
        return None
    header = (cfg.get("notifications", {}) or {}).get("header", "")
    n = DiscordNotifier(webhook_url=url, username=dc.get("username", "SLC Trading Bot"),
                        header=header)
    threading.Thread(target=n.test_message, daemon=True).start()   # confirm connection
    log.info("Discord notifier initialised")
    return n
