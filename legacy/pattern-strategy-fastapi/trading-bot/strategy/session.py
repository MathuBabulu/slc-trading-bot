"""Forex market session hours guard.

The MT5 EA can push bars at any time, including during the weekend when the
broker feeds synthetic/hold prices and no real liquidity exists. This module
provides a single gate that prevents the engine from opening new positions
outside genuine market hours.

Forex market schedule (UTC):
  Opens:  Sunday 22:00 UTC  (NZ/Sydney open)
  Closes: Friday  22:00 UTC (New York close)
  Weekend: Saturday 00:00 → Sunday 22:00 UTC is fully closed

Special cases handled:
  - No position is opened on Saturday (ever).
  - No position is opened on Sunday before 22:00 UTC.
  - Friday after 21:30 UTC: market thins rapidly; we gate new entries from
    21:30 onwards (30-min buffer before NY close) to avoid being caught
    holding into the weekend.

The gate is a WARN-and-reject, not a hard crash. Rejections are logged at
stage "session" so their cost is measurable in the signals log.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, time as dt_time
from typing import Optional

log = logging.getLogger(__name__)

# Friday NY-close buffer: stop new entries this many minutes before 22:00 UTC
_FRIDAY_CUTOFF_MINUTES: int = 30
_FRIDAY_CUTOFF_UTC: dt_time = dt_time(21, 30)  # 22:00 − 30 min

# Sunday: market re-opens at 22:00 UTC
_SUNDAY_OPEN_UTC: dt_time = dt_time(22, 0)


def is_forex_open(bar_time: Optional[str] = None, now: Optional[datetime] = None) -> bool:
    """Return True if Forex is open for NEW position entries.

    Parameters
    ----------
    bar_time : ISO-8601 bar timestamp from MT5 (e.g. "2026-06-12T06:00:00Z").
               Used to validate that the BAR itself is not a weekend/stale bar.
               If None, only wall-clock time is checked.
    now      : Current UTC datetime. Defaults to datetime.now(timezone.utc).
               Injected in tests; leave None in production.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    # Check wall-clock time first (what actually matters for live execution)
    if not _is_open_at(now):
        log.debug("Session gate: market closed at %s (wall clock)", now.isoformat())
        return False

    # Validate bar timestamp if provided — reject stale weekend bars that
    # can arrive when MT5 reconnects after the weekend
    if bar_time:
        try:
            bt = datetime.fromisoformat(bar_time.replace("Z", "+00:00"))
            if not _is_open_at(bt):
                log.debug("Session gate: bar timestamp %s is off-market hours", bar_time)
                return False
        except (ValueError, AttributeError):
            pass  # can't parse — allow (let other gates handle bad data)

    return True


def _is_open_at(dt: datetime) -> bool:
    """Core open/closed logic for a given UTC datetime."""
    dt = dt.astimezone(timezone.utc)
    wd = dt.weekday()      # Mon=0 … Fri=4, Sat=5, Sun=6
    t = dt.time()

    # Saturday: always closed
    if wd == 5:
        return False

    # Sunday: closed until 22:00 UTC
    if wd == 6 and t < _SUNDAY_OPEN_UTC:
        return False

    # Friday: close new entries at 21:30 UTC (30-min buffer before NY close)
    if wd == 4 and t >= _FRIDAY_CUTOFF_UTC:
        return False

    return True


def market_session(dt: Optional[datetime] = None) -> str:
    """Human-readable name of the current or overlapping Forex session(s).

    Useful for logging and study material — session context correlates with
    volatility and pattern reliability.
    """
    if dt is None:
        dt = datetime.now(timezone.utc)
    dt = dt.astimezone(timezone.utc)
    hour = dt.hour

    sessions = []
    # Sydney:  22:00–07:00 UTC
    if hour >= 22 or hour < 7:
        sessions.append("Sydney")
    # Tokyo:   00:00–09:00 UTC
    if 0 <= hour < 9:
        sessions.append("Tokyo")
    # London:  08:00–17:00 UTC
    if 8 <= hour < 17:
        sessions.append("London")
    # New York: 13:00–22:00 UTC
    if 13 <= hour < 22:
        sessions.append("NewYork")
    # London/NewYork overlap: 13:00–17:00 UTC (highest liquidity)
    if 13 <= hour < 17:
        sessions.append("★overlap")

    return "+".join(sessions) if sessions else "inter-session"
