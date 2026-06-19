"""High-impact news filter.

Two sources, picked by config.news.source:

- forexfactory : daily fetch of https://www.forexfactory.com/calendar
                 Parses the HTML table, extracts events tagged "High Impact"
                 (red folder icon). Caches to state/news_cache.json.

- manual       : reads state/manual_news.json (you maintain by hand).

Either way, the public API is the same:

    NewsFilter(cfg).is_blocked(now_utc: datetime) -> (blocked: bool, why: str)

The bot calls this *just before* sending an order, and also runs a periodic
sweep to flatten open positions before a blackout window opens.

NOTE: ForexFactory does not have an official public API and may block bots.
The scraper tries to be polite (User-Agent, single fetch per day, cached),
but if it breaks the bot falls back to the manual file with a warning.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)


@dataclass
class NewsEvent:
    time_utc: str         # ISO 8601
    currency: str         # "USD" | "EUR" | ...
    impact: str           # "high" | "medium" | "low"
    title: str

    def to_dict(self) -> dict:
        return {
            "time_utc": self.time_utc,
            "currency": self.currency,
            "impact": self.impact,
            "title": self.title,
        }


@dataclass
class NewsConfig:
    source: str = "forexfactory"
    forexfactory_url: str = "https://www.forexfactory.com/calendar"
    manual_events_file: str = "state/manual_news.json"
    cache_file: str = "state/news_cache.json"
    block_minutes_before: int = 30
    block_minutes_after: int = 30
    high_impact_only: bool = True
    cache_ttl_hours: int = 6
    request_timeout: int = 15
    user_agent: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    )


class NewsFilter:
    def __init__(self, cfg: NewsConfig) -> None:
        self.cfg = cfg
        self._events: List[NewsEvent] = []
        self._loaded_at: Optional[datetime] = None

    # --------------------------------------------------------------------- #
    # Public API
    # --------------------------------------------------------------------- #
    def refresh(self, force: bool = False) -> None:
        """Reload events if the cache is stale."""
        now = datetime.now(timezone.utc)
        cache_valid = (
            self._loaded_at is not None
            and (now - self._loaded_at) < timedelta(hours=self.cfg.cache_ttl_hours)
        )
        if cache_valid and not force:
            return

        if self.cfg.source == "manual":
            self._events = self._load_manual()
        elif self.cfg.source == "forexfactory":
            scraped = self._scrape_forexfactory()
            if scraped:
                self._events = scraped
                self._save_cache()
            else:
                log.warning(
                    "ForexFactory scrape returned nothing; using cached file "
                    "and the manual fallback."
                )
                self._events = self._load_cache() + self._load_manual()
        else:
            raise ValueError(f"Unknown news source: {self.cfg.source}")

        self._loaded_at = now
        log.info("Loaded %d news events", len(self._events))

    def is_blocked(
        self, now: Optional[datetime] = None, currencies: Optional[List[str]] = None
    ) -> Tuple[bool, str]:
        """Is `now` inside a high-impact news blackout window?"""
        now = now or datetime.now(timezone.utc)
        before = timedelta(minutes=self.cfg.block_minutes_before)
        after  = timedelta(minutes=self.cfg.block_minutes_after)

        for ev in self._events:
            if self.cfg.high_impact_only and ev.impact != "high":
                continue
            if currencies and ev.currency not in currencies:
                continue
            try:
                t = datetime.fromisoformat(ev.time_utc.replace("Z", "+00:00"))
            except ValueError:
                continue
            if (t - before) <= now <= (t + after):
                return True, f"Blocked by {ev.currency} {ev.title} at {ev.time_utc}"
        return False, "Clear"

    def upcoming(self, within_hours: int = 24) -> List[NewsEvent]:
        now = datetime.now(timezone.utc)
        horizon = now + timedelta(hours=within_hours)
        out = []
        for ev in self._events:
            if self.cfg.high_impact_only and ev.impact != "high":
                continue
            try:
                t = datetime.fromisoformat(ev.time_utc.replace("Z", "+00:00"))
            except ValueError:
                continue
            if now <= t <= horizon:
                out.append(ev)
        return sorted(out, key=lambda e: e.time_utc)

    # --------------------------------------------------------------------- #
    # ForexFactory scrape
    # --------------------------------------------------------------------- #
    def _scrape_forexfactory(self) -> List[NewsEvent]:
        try:
            resp = requests.get(
                self.cfg.forexfactory_url,
                headers={"User-Agent": self.cfg.user_agent},
                timeout=self.cfg.request_timeout,
            )
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            log.warning("ForexFactory fetch failed: %s", exc)
            return []

        try:
            return _parse_forexfactory_html(resp.text)
        except Exception as exc:  # noqa: BLE001
            log.warning("ForexFactory parse failed: %s", exc)
            return []

    # --------------------------------------------------------------------- #
    # Persistence
    # --------------------------------------------------------------------- #
    def _save_cache(self) -> None:
        path = Path(self.cfg.cache_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps([e.to_dict() for e in self._events], indent=2))

    def _load_cache(self) -> List[NewsEvent]:
        path = Path(self.cfg.cache_file)
        if not path.exists():
            return []
        try:
            raw = json.loads(path.read_text())
            return [NewsEvent(**r) for r in raw]
        except Exception as exc:  # noqa: BLE001
            log.warning("News cache unreadable: %s", exc)
            return []

    def _load_manual(self) -> List[NewsEvent]:
        path = Path(self.cfg.manual_events_file)
        if not path.exists():
            return []
        try:
            raw = json.loads(path.read_text())
            return [NewsEvent(**r) for r in raw]
        except Exception as exc:  # noqa: BLE001
            log.warning("Manual news file unreadable: %s", exc)
            return []


# --------------------------------------------------------------------------- #
# ForexFactory HTML parser
# --------------------------------------------------------------------------- #
# ForexFactory uses a table where each row represents an event. Impact is
# shown as a coloured folder icon: red = high, orange = medium, yellow = low.
# Times are in the user's selected timezone via JS; the embedded `data-time`
# attribute on the row is a UTC epoch second.
#
# This parser targets the layout as of 2024-2026. If ForexFactory changes
# their HTML, you'll see "ForexFactory parse failed" in the logs and the bot
# will fall back to the manual file.
def _parse_forexfactory_html(html: str) -> List[NewsEvent]:
    soup = BeautifulSoup(html, "html.parser")
    events: List[NewsEvent] = []

    rows = soup.select("tr.calendar__row, tr.calendar_row")
    for row in rows:
        # Impact icon
        impact_cell = row.select_one("td.calendar__impact, td.impact")
        impact = "low"
        if impact_cell:
            cls = " ".join(impact_cell.get("class", []))
            icon = impact_cell.find(attrs={"class": re.compile(r"icon")})
            icon_cls = " ".join(icon.get("class", [])) if icon else ""
            blob = (cls + " " + icon_cls).lower()
            if "high" in blob or "red" in blob:
                impact = "high"
            elif "med" in blob or "orange" in blob:
                impact = "medium"
            elif "low" in blob or "yellow" in blob:
                impact = "low"
            else:
                impact = "low"

        currency_cell = row.select_one("td.calendar__currency, td.currency")
        currency = currency_cell.get_text(strip=True) if currency_cell else ""
        if not currency:
            continue

        title_cell = row.select_one("td.calendar__event, td.event")
        title = title_cell.get_text(strip=True) if title_cell else ""
        if not title:
            continue

        # Epoch in data-event-datetime or data-time
        epoch = (
            row.get("data-event-datetime")
            or row.get("data-time")
            or row.get("data-timestamp")
        )
        if not epoch:
            # Try the time cell text
            continue
        try:
            t = datetime.fromtimestamp(int(epoch), tz=timezone.utc)
        except (TypeError, ValueError):
            continue

        events.append(NewsEvent(
            time_utc=t.isoformat().replace("+00:00", "Z"),
            currency=currency,
            impact=impact,
            title=title,
        ))

    return events
