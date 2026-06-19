"""Notification fan-out — send every alert to all enabled channels.

`build_notifier(cfg)` returns a single object that the rest of the bot uses
exactly as it used the Telegram notifier before. Under the hood it may hold a
Telegram notifier, a Discord notifier, or both; every call is forwarded to each.
One channel failing never blocks the others.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from telegram_notifier import build_notifier as _build_telegram
from discord_notifier import build_discord_notifier as _build_discord

log = logging.getLogger(__name__)

# Every public method the bot calls on a notifier, plus the internal _send used
# for CRITICAL invariant alerts.
_FORWARD = ("signal_detected", "order_filled", "news_favourable", "news_against",
            "news_cut_loss", "news_alert", "test_message", "_send")


class NotifierGroup:
    """Fans each notification out to every wrapped notifier (Telegram, Discord…)."""

    mode = "group"

    def __init__(self, notifiers: List[Any]) -> None:
        self._notifiers = [n for n in notifiers if n is not None]

    def _fan(self, method: str, *args, **kwargs) -> bool:
        ok = False
        for n in self._notifiers:
            fn = getattr(n, method, None)
            if fn is None:
                continue
            try:
                ok = bool(fn(*args, **kwargs)) or ok
            except Exception as exc:  # noqa: BLE001 — one channel must not break another
                log.warning("Notifier %s.%s failed: %s", type(n).__name__, method, exc)
        return ok

    def signal_detected(self, *a, **k):  return self._fan("signal_detected", *a, **k)
    def order_filled(self, *a, **k):     return self._fan("order_filled", *a, **k)
    def news_favourable(self, *a, **k):  return self._fan("news_favourable", *a, **k)
    def news_against(self, *a, **k):     return self._fan("news_against", *a, **k)
    def news_cut_loss(self, *a, **k):    return self._fan("news_cut_loss", *a, **k)
    def news_alert(self, *a, **k):       return self._fan("news_alert", *a, **k)
    def test_message(self, *a, **k):     return self._fan("test_message", *a, **k)
    def _send(self, *a, **k):            return self._fan("_send", *a, **k)


def build_notifier(cfg: Dict[str, Any]) -> Optional[Any]:
    """Build all enabled notifiers. Returns a single notifier if only one channel
    is on, a NotifierGroup if several, or None if none are enabled."""
    notifiers = [_build_telegram(cfg), _build_discord(cfg)]
    notifiers = [n for n in notifiers if n is not None]
    if not notifiers:
        return None
    if len(notifiers) == 1:
        return notifiers[0]
    log.info("Notifications fanning out to %d channels: %s",
             len(notifiers), ", ".join(type(n).__name__ for n in notifiers))
    return NotifierGroup(notifiers)
