"""
Telegram Notifier
=================
Sends formatted trade notifications to a Telegram chat.

Notifications sent:
  • Signal detected   — new pattern found, awaiting confirmation
  • Order filled      — signal passed all filters, position opened
  • News favourable   — trailing SL command queued/executed
  • News against      — move-to-BE command queued/executed

Setup:
  1. Create a bot via Telegram BotFather → get BOT_TOKEN
  2. Get your CHAT_ID (see README / config comments)
  3. Fill in config.yaml → telegram section
  4. Set enabled: true

Rate limiting: Telegram allows 1 message/second per chat.
This module enforces a 1.1s minimum gap between sends.
"""
from __future__ import annotations

import html
import logging
import time
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import requests

log = logging.getLogger(__name__)


def _esc(value) -> str:
    """Escape dynamic content for Telegram's HTML parse mode.

    Messages are sent with parse_mode=HTML, so any '<', '>' or '&' inside DATA
    (a symbol, a news reason like 'EUR <= 1.10', a setup name…) would be parsed
    as markup and 400 the entire message ("can't parse entities"). Template tags
    (<b>, <code>, …) are added by the methods themselves and must NOT be passed
    through this — only the interpolated values are escaped.
    """
    return html.escape("" if value is None else str(value), quote=False)

# Setup code → friendly name
SETUP_NAMES: Dict[str, str] = {
    "DT":   "Double Top",
    "DB":   "Double Bottom",
    "HS":   "Head & Shoulders",
    "IHS":  "Inverse H&S",
    "TT":   "Triple Top",
    "TB":   "Triple Bottom",
    "RECT": "Rectangle",
    "TL":   "Trendline Break",
}

DIRECTION_EMOJI = {"buy": "📈", "sell": "📉", "BUY": "📈", "SELL": "📉"}


class TelegramNotifier:
    """
    Sends HTML-formatted messages to a Telegram bot chat.
    Thread-safe with a built-in 1.1s send rate limiter.
    """

    TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"

    def __init__(self, bot_token: str, chat_id="", timeout: int = 10,
                 header: str = "") -> None:
        self.bot_token = bot_token.strip()
        self.header = (header or "").strip()   # title line prepended to every message
        # Accept a single id, a comma-separated string, or a list — every
        # notification goes to ALL of them.
        if isinstance(chat_id, (list, tuple)):
            ids = [str(c).strip() for c in chat_id]
        else:
            ids = [c.strip() for c in str(chat_id).split(",")]
        self.chat_ids: list = [c for c in ids if c]
        self.chat_id = self.chat_ids[0] if self.chat_ids else ""   # back-compat
        self.timeout   = timeout
        self._lock     = threading.Lock()
        self._last_send: float = 0.0
        self._session  = requests.Session()
        self._session.headers.update({"Content-Type": "application/json"})

    # ── Public notification methods ───────────────────────────────────────────

    def signal_detected(self, signal: dict) -> bool:
        """
        Notify when a new pattern signal is detected (pre-confirmation).
        `signal` is the Signal.to_dict() payload from the bot engine.
        """
        setup  = signal.get("setup", "?")
        side   = signal.get("direction") or signal.get("side", "?")
        emoji  = DIRECTION_EMOJI.get(side, "↔️")   # emoji lookup needs raw side
        symbol = _esc(signal.get("symbol", "?"))
        tf     = _esc(signal.get("timeframe", "?"))
        entry  = _esc(signal.get("entry", "?"))
        sl     = _esc(signal.get("sl", "?"))
        tp     = _esc(signal.get("tp", "?"))
        name   = _esc(SETUP_NAMES.get(setup, setup))
        side_u = _esc(side.upper() if side else "?")

        text = (
            f"🔍 <b>SIGNAL DETECTED</b>\n\n"
            f"<b>{symbol}</b> — {name}\n"
            f"{emoji} Direction: <b>{side_u}</b> | Timeframe: <b>{tf}</b>\n\n"
            f"📍 Entry:  <code>{entry}</code>\n"
            f"🛑 SL:     <code>{sl}</code>\n"
            f"🎯 TP:     <code>{tp}</code>\n\n"
            f"<i>Awaiting confirmation filter…</i>"
        )
        return self._send(text)

    def order_filled(self, payload: dict) -> bool:
        """
        Notify when an order is filled (position opened).
        `payload` is the order:filled event payload from the bot engine.
        """
        side   = payload.get("side", "?")
        setup  = payload.get("setup", "?")
        emoji  = DIRECTION_EMOJI.get(side, "↔️")   # emoji lookup needs raw side
        symbol = _esc(payload.get("symbol", "?"))
        lots   = _esc(payload.get("lots", "?"))
        price  = _esc(payload.get("fill_price", "?"))
        sl     = _esc(payload.get("sl", "?"))
        tp     = _esc(payload.get("tp", "?"))
        tf     = _esc(payload.get("timeframe", "?"))
        rr     = payload.get("rr", None)
        risk   = payload.get("risk_pct", None)
        name   = _esc(SETUP_NAMES.get(setup, setup))
        side_u = _esc(side.upper() if side else "?")

        rr_str   = f"1:{float(rr):.1f}" if rr is not None else "—"
        risk_str = f"{float(risk):.1f}%" if risk is not None else "—"

        text = (
            f"✅ <b>ORDER FILLED</b>\n\n"
            f"<b>{symbol}</b> — {name} ({tf})\n"
            f"{emoji} <b>{side_u}</b>  |  Lots: <b>{lots}</b>\n\n"
            f"📍 Fill:  <code>{price}</code>\n"
            f"🛑 SL:    <code>{sl}</code>\n"
            f"🎯 TP:    <code>{tp}</code>\n"
            f"⚖️ Risk: {risk_str}  |  RR: {rr_str}\n\n"
            f"⏰ {_now_str()}"
        )
        return self._send(text)

    def news_favourable(
        self,
        symbol: str,
        side: str,
        ticket: int,
        net_score: float,
        score_base: float,
        score_quote: float,
        new_sl: float,
        old_sl: float,
        reason: str,
        live_mode: bool,
    ) -> bool:
        """
        Notify when news is favourable for an open trade → trailing SL.
        """
        emoji  = DIRECTION_EMOJI.get(side.lower(), "↔️")
        side_u = _esc(side.upper())
        symbol = _esc(symbol)
        reason = _esc(str(reason)[:200])
        mode   = "🟢 LIVE — command sent to MT5" if live_mode else "🟡 DRY-RUN — not executed"
        sl_change = f"<code>{old_sl:.5f}</code> → <code>{new_sl:.5f}</code>"

        text = (
            f"📈 <b>NEWS FAVOURABLE — TRAILING SL</b>\n\n"
            f"{emoji} <b>{symbol}</b> {side_u}  |  Ticket: <code>{ticket}</code>\n\n"
            f"💡 Net sentiment: <b>+{net_score:.2f}</b>\n"
            f"📊 Base: {score_base:+.2f}  |  Quote: {score_quote:+.2f}\n\n"
            f"🔄 SL moved: {sl_change}\n\n"
            f"📝 <i>{reason}</i>\n\n"
            f"{mode}\n"
            f"⏰ {_now_str()}"
        )
        return self._send(text)

    def news_against(
        self,
        symbol: str,
        side: str,
        ticket: int,
        net_score: float,
        score_base: float,
        score_quote: float,
        new_sl: float,
        entry: float,
        reason: str,
        live_mode: bool,
    ) -> bool:
        """
        Notify when news is against an open trade → move SL to break-even.
        """
        emoji  = DIRECTION_EMOJI.get(side.lower(), "↔️")
        side_u = _esc(side.upper())
        symbol = _esc(symbol)
        reason = _esc(str(reason)[:200])
        mode   = "🟢 LIVE — command sent to MT5" if live_mode else "🟡 DRY-RUN — not executed"

        text = (
            f"🛡 <b>NEWS AGAINST — MOVING TO BREAK-EVEN</b>\n\n"
            f"{emoji} <b>{symbol}</b> {side_u}  |  Ticket: <code>{ticket}</code>\n\n"
            f"💡 Net sentiment: <b>{net_score:.2f}</b>\n"
            f"📊 Base: {score_base:+.2f}  |  Quote: {score_quote:+.2f}\n\n"
            f"🔄 SL → Break-even: <code>{new_sl:.5f}</code>  "
            f"(entry was <code>{entry:.5f}</code>)\n\n"
            f"📝 <i>{reason}</i>\n\n"
            f"{mode}\n"
            f"⏰ {_now_str()}"
        )
        return self._send(text)

    def news_cut_loss(
        self,
        symbol: str,
        side: str,
        ticket: int,
        net_score: float,
        score_base: float,
        score_quote: float,
        entry: float,
        reason: str,
        live_mode: bool,
    ) -> bool:
        """Notify when strongly adverse news closes an underwater trade."""
        emoji  = DIRECTION_EMOJI.get(side.lower(), "↔️")
        side_u = _esc(side.upper())
        symbol = _esc(symbol)
        reason = _esc(str(reason)[:200])
        mode   = "🟢 LIVE — position closed" if live_mode else "🟡 DRY-RUN — not executed"

        text = (
            f"✂️ <b>NEWS CUT-LOSS — CLOSING AT MARKET</b>\n\n"
            f"{emoji} <b>{symbol}</b> {side_u}  |  Ticket: <code>{ticket}</code>\n\n"
            f"💡 Net sentiment: <b>{net_score:.2f}</b> (cut gate −0.50)\n"
            f"📊 Base: {score_base:+.2f}  |  Quote: {score_quote:+.2f}\n\n"
            f"Trade was underwater (entry <code>{entry:.5f}</code>) — break-even "
            f"impossible, strong news against. Cutting the loss now beats "
            f"riding it to the stop.\n\n"
            f"📝 <i>{reason}</i>\n\n"
            f"{mode}\n"
            f"⏰ {_now_str()}"
        )
        return self._send(text)

    def news_alert(
        self,
        headline: str,
        source: str,
        published: str,
        link: str,
        driver: str,
        score: float,
        direction: str,
        pair_impacts: list,      # [{"pair","arrow","why"}, ...]
        details: str,            # long text shown inside the expandable section
        live_mode: bool,
        priority: bool = False,  # presidential / social-post market mover
    ) -> bool:
        """Market-news alert: headline + likely per-pair impact, with the full
        details tucked into Telegram's EXPANDABLE blockquote (tap to expand).
        Falls back to a plain blockquote, then plain text, for old clients."""
        import html as _html
        esc = lambda s: _html.escape(str(s or ""), quote=False)

        dir_emoji = "🟢" if score > 0 else "🔴"
        arrow_line = "  ·  ".join(
            f"<b>{esc(i['pair'])}</b> {i['arrow']}" for i in pair_impacts[:8]
        ) or "—"
        why = pair_impacts[0]["why"] if pair_impacts else f"{driver} move"
        mode = "🟢 agent LIVE" if live_mode else "🟡 agent DRY-RUN"

        label = ("⚡ <b>PRIORITY — PRESIDENTIAL / SOCIAL POST</b>\n"
                 if priority else "")
        head = (
            f"{label}🗞 <b>NEWS ALERT</b> — <b>{esc(driver)}</b> {dir_emoji} "
            f"{esc(direction)} ({score:+.2f})\n\n"
            f"<b>{esc(headline)}</b>\n"
            f"<i>{esc(source) or 'news'}{(' · ' + esc(published)) if published else ''}</i>\n\n"
            f"🎯 <b>Likely impact</b> ({esc(why)}):\n{arrow_line}\n"
        )
        tail = (
            (f"\n🔗 <a href=\"{link}\">Read full story</a>\n" if link else "\n")
            + f"{mode} — SL actions are taken only on open positions\n⏰ {_now_str()}"
        )
        block = esc(details).strip()

        # 1st choice: expandable blockquote (collapsed by default, tap to expand)
        text = head + f"\n<blockquote expandable>{block}</blockquote>" + tail
        if self._send(text):
            return True
        # Fallbacks for clients/APIs that reject the expandable attribute.
        text = head + f"\n<blockquote>{block}</blockquote>" + tail
        if self._send(text):
            return True
        return self._send(head + "\n" + block + tail)

    def test_message(self) -> bool:
        """Send a test ping to verify the bot is configured correctly."""
        text = (
            f"✅ <b>Trading Bot — Telegram connected!</b>\n\n"
            f"Notifications are active for:\n"
            f"  • Signal detected (Buy / Sell)\n"
            f"  • Order filled\n"
            f"  • News agent outcomes\n\n"
            f"⏰ {_now_str()}"
        )
        return self._send(text)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _send(self, text: str) -> bool:
        """POST a message to every configured chat_id. Enforces a 1.1s rate
        limit between sends. Thread-safe. True if at least one delivery OK."""
        if not self.bot_token or not self.chat_ids:
            log.warning("Telegram: bot_token or chat_id not set — skipping notification")
            return False

        # Prepend the configured header (e.g. "Pattern Strategy") to every message.
        if self.header:
            text = f"<b>{html.escape(self.header)}</b>\n{text}"

        any_ok = False
        with self._lock:
            url = self.TELEGRAM_API.format(token=self.bot_token)
            for cid in self.chat_ids:
                # Rate limit: wait if last send was < 1.1s ago
                elapsed = time.monotonic() - self._last_send
                if elapsed < 1.1:
                    time.sleep(1.1 - elapsed)
                payload = {
                    "chat_id":    cid,
                    "text":       text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                }
                try:
                    resp = self._session.post(url, json=payload, timeout=self.timeout)
                    self._last_send = time.monotonic()
                    if resp.status_code == 200 and resp.json().get("ok"):
                        any_ok = True
                    else:
                        log.warning("Telegram send to %s failed: %s — %s",
                                    cid, resp.status_code, resp.text[:200])
                except Exception as exc:
                    log.warning("Telegram send error to %s: %s", cid, exc)
        return any_ok


# ── Factory ───────────────────────────────────────────────────────────────────

def build_notifier(cfg: Dict[str, Any]) -> Optional[TelegramNotifier]:
    """
    Build a TelegramNotifier from the config dict.
    Returns None if Telegram is disabled or credentials are missing.

    Reads from config.yaml:
        telegram:
          enabled: true
          bot_token: "123456:ABC..."
          chat_id: "987654321"
    """
    tg = cfg.get("telegram", {})
    if not tg.get("enabled", False):
        log.info("Telegram notifications disabled (telegram.enabled: false in config.yaml)")
        return None

    token = tg.get("bot_token", "").strip()
    # chat_id may be a single id, a comma-separated string, or a YAML list —
    # all recipients receive every notification.
    chat = tg.get("chat_id", "")
    if not isinstance(chat, (list, tuple)):
        chat = str(chat).strip()

    if not token or token.startswith("YOUR_"):
        log.warning("Telegram: bot_token not set — notifications disabled")
        return None
    if not chat or (isinstance(chat, str) and chat.startswith("YOUR_")):
        log.warning("Telegram: chat_id not set — notifications disabled")
        return None

    header = (cfg.get("notifications", {}) or {}).get("header", "")
    notifier = TelegramNotifier(bot_token=token, chat_id=chat, header=header)
    if not notifier.chat_ids:
        log.warning("Telegram: no valid chat_id — notifications disabled")
        return None
    # Send a test message to confirm connection
    threading.Thread(target=notifier.test_message, daemon=True).start()
    log.info("Telegram notifier initialised — %d recipient(s): %s",
             len(notifier.chat_ids), ", ".join(notifier.chat_ids))
    return notifier


def _now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
