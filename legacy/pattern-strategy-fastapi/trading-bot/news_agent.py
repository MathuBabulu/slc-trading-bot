"""
News Monitoring Sub-Agent
=========================
Continuously fetches financial news from free RSS feeds, scores sentiment
for currencies in each open position, and posts SL management commands to
the trading server for the MT5 EA to execute.

Dry-run mode (DEFAULT): every decision is logged but NO commands are sent.
  → Flip  news_agent.live_mode: true  in config.yaml to go live.

Usage:
    cd trading-bot
    python news_agent.py                        # uses config.yaml
    python news_agent.py --config custom.yaml   # explicit config path

What happens in one cycle:
  1. Fetch open positions from GET /api/status
  2. Pull recent headlines from Google News RSS (no API key needed)
  3. Score sentiment for each position's base + quote currencies
  4. If news strongly favours the trade  → POST trail_sl command
     If news strongly opposes the trade  → POST move_sl_be command
     Otherwise                           → log "hold" and skip
  5. Append full decision log to state/news_decisions.jsonl

Recommended first run: start with live_mode: false (default) and monitor
  state/news_agent.log for a few hours before enabling live execution.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
import yaml

from news_evaluator import (TradeDecision, _parse_symbol, evaluate_trade,
                            score_currency_sentiment)
from telegram_notifier import TelegramNotifier
from notifications import build_notifier   # fans out to Telegram + Discord

# ── Logging setup ────────────────────────────────────────────────────────────
Path("state").mkdir(exist_ok=True)

_fmt = "%(asctime)s [NEWS-AGENT] %(levelname)s %(message)s"
logging.basicConfig(
    level=logging.INFO,
    format=_fmt,
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("state/news_agent.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

# ── RSS feed templates ───────────────────────────────────────────────────────
# Pair-specific (filled with {base}/{quote} per position)
PAIR_FEED_TEMPLATES = [
    "https://news.google.com/rss/search?q={base}+{quote}+forex&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q={base}+currency+forecast&hl=en&gl=US&ceid=US:en",
]

# Always-fetched macro feeds
GENERAL_FEEDS = [
    "https://news.google.com/rss/search?q=forex+currency+central+bank&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=interest+rate+decision+inflation&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=gdp+employment+cpi+economic+data&hl=en&gl=US&ceid=US:en",
    # Broad catch-alls so geopolitical shocks (presidential statements,
    # tariffs, sanctions) are seen — the narrow queries above missed them.
    "https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=trump+OR+tariff+OR+sanctions+market&hl=en&gl=US&ceid=US:en",
    # Presidential tweets / social posts move markets ~instantly; news wires
    # write them up within minutes — catch those write-ups specifically.
    "https://news.google.com/rss/search?q=trump+tweet+OR+%22truth+social%22+markets&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=president+post+OR+statement+dollar+OR+forex&hl=en&gl=US&ceid=US:en",
]

# Per-currency feed for the market-wide alert scan (filled with the currency
# code). Fetched for every currency present in the dashboard-enabled pairs,
# so alerts fire even when no position is open.
CURRENCY_FEED_TEMPLATE = (
    "https://news.google.com/rss/search?q={cur}+currency+economy&hl=en&gl=US&ceid=US:en"
)

MAJORS = ["USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF"]

# Defaults (all overridable in config.yaml under news_agent:)
DEFAULT_CFG: Dict[str, Any] = {
    "live_mode": False,
    "poll_seconds": 120,
    "max_headline_age_hours": 2,
    "sentiment_threshold": 0.25,
    "trail_factor": 1.5,
    "be_buffer_pips": 2.0,
    "command_expire_seconds": 300,
    "min_headlines_required": 3,
    "server_url": "http://127.0.0.1:8765",
    "request_timeout": 10,
    # Market-wide news alerts → Telegram (headline + expandable details +
    # likely impact per watched pair). Runs every cycle, even with no
    # positions open — it watches the dashboard-enabled pairs.
    "alerts_enabled": True,
    "alert_min_score": 0.5,        # |sentiment| a single headline needs to alert
    "alert_max_per_cycle": 3,      # cap so Telegram isn't spammed
    "alert_dedupe_hours": 48,      # never re-send the same headline within this
    # Cut-loss: when news is STRONGLY against a trade that's underwater
    # (break-even impossible), close it at market. Stricter gate than the
    # BE move (−0.5 vs −0.25) because cutting is more aggressive.
    "cut_loss_enabled": True,
    "cut_loss_threshold": 0.5,
    # Priority lane: headlines from/about the US president or social-media
    # posts jump the alert queue and use a RELAXED gate (min_score × factor),
    # because a 1% move can be done before a normal-strength signal accrues.
    "alert_priority_terms": ["trump", "white house", "truth social", "potus",
                             "president", "tweet", "tweets", "executive order"],
    "alert_priority_factor": 0.7,
    # Alerts must be FRESH: skip items older than this, or with no parseable
    # timestamp (wire services keep re-covering yesterday's posts — those
    # rehashes were being alerted as if new).
    "alert_max_age_minutes": 90,
    # Optional: read presidential posts DIRECTLY from X (api.twitter.com v2).
    # Needs a bearer token from developer.x.com. Posts skip the wire delay
    # and always take the priority lane.
    "x_api": {
        "enabled": False,
        "bearer_token": "",
        "usernames": ["realDonaldTrump", "POTUS"],
    },
}


# ── Utilities ─────────────────────────────────────────────────────────────────

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _strip_html(text: str) -> str:
    """Drop tags + collapse whitespace (Google News descriptions are HTML)."""
    import html as _html
    no_tags = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", _html.unescape(no_tags)).strip()


def analyze_headline_impact(item: dict, watch_pairs: List[str],
                            min_score: float = 0.5) -> Optional[dict]:
    """Score ONE headline against the 8 major currencies and map the result
    onto the pairs being watched.

    Returns None when the headline isn't market-moving enough, else:
      {
        "driver": "USD", "score": -0.5, "direction": "bearish",
        "pair_impacts": [{"pair": "USDJPY", "arrow": "↓", "why": "USD weakness"}, ...],
        "currency_scores": {"USD": -0.5, "EUR": 0.25, ...},   # non-zero only
        "triggers": ["-recession(2)", ...],
      }
    """
    title = item.get("title") or ""
    if not title:
        return None

    scores: Dict[str, Any] = {}
    triggers: List[str] = []
    for cur in MAJORS:
        res = score_currency_sentiment(cur, [title])
        if res.headline_count and res.score != 0.0:
            scores[cur] = round(res.score, 2)
            triggers.extend(p for p in res.matched_phrases if p not in triggers)
    if not scores:
        return None

    driver = max(scores, key=lambda c: abs(scores[c]))
    s = scores[driver]
    if abs(s) < min_score:
        return None

    impacts: List[dict] = []
    for pair in watch_pairs:
        base, quote = _parse_symbol(pair)
        if driver == base:
            up = s > 0
            why = f"{driver} {'strength' if s > 0 else 'weakness'}"
        elif driver == quote:
            up = s < 0
            why = f"{driver} {'strength' if s > 0 else 'weakness'}"
        else:
            continue
        impacts.append({"pair": pair.upper(), "arrow": "↑" if up else "↓", "why": why})
    if not impacts:
        return None

    return {
        "driver": driver,
        "score": s,
        "direction": "bullish" if s > 0 else "bearish",
        "pair_impacts": impacts,
        "currency_scores": scores,
        "triggers": triggers[:10],
    }


def _now_iso() -> str:
    return _now_utc().isoformat().replace("+00:00", "Z")


def _parse_rss_date(text: str) -> Optional[datetime]:
    """Parse common RSS/Atom date formats into a UTC-aware datetime."""
    text = text.strip()
    formats = [
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S GMT",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f%z",
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(text, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None


# ── RSS Fetcher ───────────────────────────────────────────────────────────────

class RSSFetcher:
    """
    Fetches RSS/Atom feeds and returns recent headline strings.
    Uses only the standard `requests` library — no feedparser needed.
    """

    def __init__(self, timeout: int = 10, max_age_hours: float = 2.0) -> None:
        self.timeout = timeout
        self.max_age = timedelta(hours=max_age_hours)
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (compatible; ForexNewsBot/1.0; +https://github.com)",
            "Accept": "application/rss+xml, application/xml, text/xml, */*",
        })

    def fetch_headlines(self, url: str) -> List[str]:
        """Fetch one RSS feed; return list of recent headline strings.
        (Kept for the position-evaluation path, which scores plain titles.)"""
        return [it["title"] for it in self.fetch_items(url)]

    def fetch_items(self, url: str) -> List[dict]:
        """Fetch one RSS feed; return rich recent items:
        {title, link, source, published, summary} — everything the Telegram
        news alert needs (headline + expandable details + source link)."""
        try:
            resp = self.session.get(url, timeout=self.timeout)
            if resp.status_code != 200:
                log.debug("Feed %s → HTTP %s", url.split("?")[0], resp.status_code)
                return []
            return self._parse(resp.content)
        except Exception as exc:
            log.debug("Feed error %s: %s", url.split("?")[0], exc)
            return []

    def _parse(self, content: bytes) -> List[dict]:
        """Parse RSS or Atom XML into rich items, filtering by age."""
        try:
            root = ET.fromstring(content)
        except ET.ParseError:
            return []

        ns = {"atom": "http://www.w3.org/2005/Atom"}
        items = root.findall(".//item") or root.findall(".//atom:entry", ns)
        out: List[dict] = []

        for item in items:
            # NOTE: use explicit `is not None` — an ElementTree element with text
            # but no children is falsy, so `a or b` would wrongly discard it.
            title_el = item.find("title")
            if title_el is None:
                title_el = item.find("atom:title", ns)
            if title_el is None or not title_el.text:
                continue
            title = title_el.text.strip()

            # Optional age filter
            published = ""
            published_iso = ""
            pub_el = item.find("pubDate")
            if pub_el is None:
                pub_el = item.find("published")
            if pub_el is None:
                pub_el = item.find("atom:published", ns)
            if pub_el is not None and pub_el.text:
                published = pub_el.text.strip()
                dt = _parse_rss_date(pub_el.text)
                if dt:
                    if (_now_utc() - dt) > self.max_age:
                        continue   # Skip old headlines
                    published_iso = dt.isoformat()

            link = ""
            link_el = item.find("link")
            if link_el is not None and link_el.text:
                link = link_el.text.strip()
            elif link_el is not None and link_el.get("href"):     # Atom style
                link = link_el.get("href").strip()

            source = ""
            src_el = item.find("source")
            if src_el is not None and src_el.text:
                source = src_el.text.strip()

            summary = ""
            desc_el = item.find("description")
            if desc_el is None:
                desc_el = item.find("atom:summary", ns)
            if desc_el is not None and desc_el.text:
                summary = _strip_html(desc_el.text)[:600]

            out.append({"title": title, "link": link, "source": source,
                        "published": published, "published_iso": published_iso,
                        "summary": summary})

        return out


# ── X (Twitter) post fetcher — direct source for presidential posts ─────────

class XPostFetcher:
    """Fetch recent posts from configured X accounts via the official API v2.

    Requires `news_agent.x_api.bearer_token` in config (X developer account).
    Each post becomes a PRIORITY news item: the post text is analyzed for
    pair impact exactly like a headline, but with zero wire-service delay.
    Fails quietly (logged once) if the token is missing/invalid."""

    API = "https://api.twitter.com/2"

    def __init__(self, bearer_token: str, usernames: List[str],
                 timeout: int = 10, max_age_minutes: float = 90.0) -> None:
        self.usernames = [u.lstrip("@") for u in usernames if u]
        self.timeout = timeout
        self.max_age = timedelta(minutes=max_age_minutes)
        self._ids: Dict[str, str] = {}          # username -> user id (cached)
        self._auth_failed = False
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {bearer_token}"})

    def _user_id(self, username: str) -> Optional[str]:
        if username in self._ids:
            return self._ids[username]
        try:
            r = self.session.get(f"{self.API}/users/by/username/{username}",
                                 timeout=self.timeout)
            if r.status_code == 401:
                if not self._auth_failed:
                    log.warning("X API: bearer token rejected (401) — X posts disabled")
                self._auth_failed = True
                return None
            if r.status_code == 200:
                uid = (r.json().get("data") or {}).get("id")
                if uid:
                    self._ids[username] = uid
                    return uid
            log.debug("X API user lookup %s → HTTP %s", username, r.status_code)
        except Exception as exc:  # noqa: BLE001
            log.debug("X API user lookup failed: %s", exc)
        return None

    def fetch_posts(self) -> List[dict]:
        """Recent posts (age-filtered) as news items with is_post=True."""
        if self._auth_failed:
            return []
        out: List[dict] = []
        for user in self.usernames:
            uid = self._user_id(user)
            if not uid:
                continue
            try:
                r = self.session.get(
                    f"{self.API}/users/{uid}/tweets",
                    params={"max_results": 10, "tweet.fields": "created_at",
                            "exclude": "retweets,replies"},
                    timeout=self.timeout)
                if r.status_code != 200:
                    log.debug("X API timeline %s → HTTP %s", user, r.status_code)
                    continue
                for tw in (r.json().get("data") or []):
                    created = tw.get("created_at", "")
                    try:
                        dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                        if (_now_utc() - dt) > self.max_age:
                            continue
                        published_iso = dt.isoformat()
                    except (ValueError, AttributeError):
                        continue            # no timestamp → can't prove fresh → skip
                    text = (tw.get("text") or "").strip()
                    if len(text) < 12:
                        continue
                    out.append({
                        # Prefix the author: the WHO is the market signal — a
                        # bare post text rarely names a currency, but the
                        # handle makes it USD-relevant for scoring.
                        "title": f"@{user}: {text}"[:300],
                        "link": f"https://x.com/{user}/status/{tw.get('id', '')}",
                        "source": f"@{user} (X)",
                        "published": created,
                        "published_iso": published_iso,
                        "summary": text[:600],
                        "is_post": True,    # always priority-lane
                    })
            except Exception as exc:  # noqa: BLE001
                log.debug("X API timeline fetch failed: %s", exc)
        if out:
            log.info("X posts fetched: %d fresh post(s)", len(out))
        return out


# ── Command poster ────────────────────────────────────────────────────────────

class CommandClient:
    """Talks to the trading server to fetch positions and post commands."""

    def __init__(self, server_url: str, expire_seconds: int, timeout: int) -> None:
        self.base = server_url.rstrip("/")
        self.expire_seconds = expire_seconds
        self.timeout = timeout
        self.session = requests.Session()

    def get_open_positions(self) -> List[dict]:
        """Fetch open positions — the BOT'S OWN (paper) trades first.

        /api/agent/trades returns the paper router's positions (with ticket,
        entry, current, sl, tp), which is what the SL commands can actually
        modify. Falls back to the broker snapshot in /api/status only when
        the paper book is empty (live-account mode)."""
        try:
            resp = self.session.get(f"{self.base}/api/agent/trades", timeout=self.timeout)
            if resp.status_code == 200:
                paper = resp.json().get("open", []) or []
                if paper:
                    return paper
        except Exception as exc:
            log.debug("Could not fetch paper positions: %s", exc)
        try:
            resp = self.session.get(f"{self.base}/api/status", timeout=self.timeout)
            if resp.status_code == 200:
                data = resp.json()
                return data.get("open_positions", []) or []
        except Exception as exc:
            log.warning("Cannot reach server at %s: %s", self.base, exc)
        return []

    def get_enabled_pairs(self) -> Optional[set]:
        """
        Fetch the pairs currently enabled in the dashboard Pairs Manager.
        Returns None if server not reachable (caller treats all pairs as enabled).
        Returns empty set if server responded but no pairs are toggled on.
        """
        try:
            resp = self.session.get(f"{self.base}/api/pairs", timeout=self.timeout)
            if resp.status_code == 200:
                data = resp.json()
                pairs = data.get("enabled_pairs", [])
                # Empty list = not yet synced from dashboard → treat all as enabled
                if not pairs:
                    return None
                return {p.upper() for p in pairs}
        except Exception as exc:
            log.debug("Could not fetch enabled pairs: %s", exc)
        return None

    def post_command(self, decision: TradeDecision) -> bool:
        """Post an SL management command. Returns True on HTTP 200."""
        expires_at = (
            _now_utc() + timedelta(seconds=self.expire_seconds)
        ).isoformat().replace("+00:00", "Z")

        cmd = {
            "id": str(uuid.uuid4()),
            "type": decision.action,
            "ticket": decision.ticket,
            "symbol": decision.symbol,
            "side": decision.side,
            "new_sl": decision.new_sl,
            "reason": decision.reason[:400],
            "created_at": _now_iso(),
            "expires_at": expires_at,
        }
        try:
            resp = self.session.post(
                f"{self.base}/api/commands",
                json=cmd,
                timeout=self.timeout,
            )
            if resp.status_code == 200:
                return True
            log.warning("Command POST returned %s: %s", resp.status_code, resp.text[:200])
        except Exception as exc:
            log.warning("Command POST failed: %s", exc)
        return False


# ── Main Agent ────────────────────────────────────────────────────────────────

class NewsAgent:
    """
    Main monitoring agent.  Call .run() to start the polling loop.
    """

    def __init__(self, config_path: str = "config.yaml") -> None:
        raw_cfg = self._load_yaml(config_path)
        # Merge user config over defaults
        self.cfg: Dict[str, Any] = {
            **DEFAULT_CFG,
            **raw_cfg.get("news_agent", {}),
        }

        self.live_mode: bool = bool(self.cfg["live_mode"])
        self.poll_seconds: int = int(self.cfg["poll_seconds"])
        self.sentiment_threshold: float = float(self.cfg["sentiment_threshold"])
        self.trail_factor: float = float(self.cfg["trail_factor"])
        self.be_buffer_pips: float = float(self.cfg["be_buffer_pips"])
        self.min_headlines: int = int(self.cfg["min_headlines_required"])
        self.max_age_hours: float = float(self.cfg["max_headline_age_hours"])

        self.fetcher = RSSFetcher(
            timeout=int(self.cfg["request_timeout"]),
            max_age_hours=self.max_age_hours,
        )
        self.client = CommandClient(
            server_url=self.cfg["server_url"],
            expire_seconds=int(self.cfg["command_expire_seconds"]),
            timeout=int(self.cfg["request_timeout"]),
        )

        # Telegram notifier (None if telegram.enabled: false in config)
        self.telegram: Optional[TelegramNotifier] = build_notifier(raw_cfg)
        # Per-event toggle: read from telegram section of the full config
        tg_cfg = raw_cfg.get("telegram", {})
        self._tg_notify_news: bool = tg_cfg.get("notify_news_outcome", True)
        self._tg_notify_alert: bool = tg_cfg.get("notify_news_alert", True)

        # Market-wide alert settings + persistent "already sent" store.
        self.alerts_enabled: bool = bool(self.cfg["alerts_enabled"])
        self.alert_min_score: float = float(self.cfg["alert_min_score"])
        self.alert_max_per_cycle: int = int(self.cfg["alert_max_per_cycle"])
        self.alert_dedupe_hours: float = float(self.cfg["alert_dedupe_hours"])
        self.cut_loss_enabled: bool = bool(self.cfg["cut_loss_enabled"])
        self.cut_loss_threshold: float = float(self.cfg["cut_loss_threshold"])
        self.alert_priority_terms: List[str] = [
            str(t).lower() for t in self.cfg["alert_priority_terms"]]
        self.alert_priority_factor: float = float(self.cfg["alert_priority_factor"])
        self.alert_max_age_min: float = float(self.cfg["alert_max_age_minutes"])

        # Optional direct X source for presidential posts.
        x_cfg = self.cfg.get("x_api") or {}
        self.x_fetcher: Optional[XPostFetcher] = None
        if x_cfg.get("enabled") and x_cfg.get("bearer_token"):
            self.x_fetcher = XPostFetcher(
                bearer_token=str(x_cfg["bearer_token"]),
                usernames=list(x_cfg.get("usernames") or []),
                timeout=int(self.cfg["request_timeout"]),
                max_age_minutes=self.alert_max_age_min,
            )
        self._alerts_sent_path = Path("state/news_alerts_sent.json")
        self._alerts_sent: Dict[str, str] = self._load_alerts_sent()

        # Tickets already acted on in this cycle (avoid duplicate commands)
        self._acted: set = set()

        self._print_banner()

    # ── Setup ────────────────────────────────────────────────────────────────

    @staticmethod
    def _load_yaml(path: str) -> dict:
        p = Path(path)
        if not p.exists():
            log.warning("Config not found: %s — using defaults", path)
            return {}
        with open(p, encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}

    def _print_banner(self) -> None:
        mode = "LIVE  ⚡" if self.live_mode else "DRY-RUN  (read-only)"
        log.info("=" * 60)
        log.info("  News Monitoring Sub-Agent")
        log.info("  Mode           : %s", mode)
        log.info("  Server         : %s", self.cfg["server_url"])
        log.info("  Poll interval  : %ss", self.poll_seconds)
        log.info("  Sentiment gate : ±%.2f", self.sentiment_threshold)
        log.info("  Trail factor   : %.1fx original risk", self.trail_factor)
        log.info("  BE buffer      : %.1f pips", self.be_buffer_pips)
        log.info("  News age limit : %sh", self.max_age_hours)
        log.info("  Market alerts  : %s (min score %.2f, max %d/cycle)",
                 "ON" if self.alerts_enabled else "off",
                 self.alert_min_score, self.alert_max_per_cycle)
        log.info("  Cut-loss       : %s (gate −%.2f, underwater only)",
                 "ON" if self.cut_loss_enabled else "off", self.cut_loss_threshold)
        if not self.live_mode:
            log.info("")
            log.info("  ⚠  DRY-RUN: decisions logged, no MT5 commands sent.")
            log.info("     Set  news_agent.live_mode: true  in config.yaml to enable.")
        log.info("=" * 60)

    # ── Headline collection ──────────────────────────────────────────────────

    def _collect_items(self, positions: List[dict],
                       watch_currencies: List[str]) -> List[dict]:
        """Fetch all relevant RICH news items: general macro feeds, one feed
        per watched currency (for market-wide alerts), and pair-specific feeds
        for open positions. Deduplicated by title, order preserved."""
        all_items: List[dict] = []
        seen_urls: set = set()

        def _pull(url: str, tag: str) -> None:
            if url in seen_urls:
                return
            seen_urls.add(url)
            items = self.fetcher.fetch_items(url)
            all_items.extend(items)
            if items:
                log.debug("%s feed: %d items", tag, len(items))

        # General macro feeds
        for url in GENERAL_FEEDS:
            _pull(url, "general")

        # Currency feeds for the watched universe (alerts work with 0 positions)
        for cur in watch_currencies:
            _pull(CURRENCY_FEED_TEMPLATE.format(cur=cur), cur)

        # Pair-specific feeds for open positions
        pairs_done: set = set()
        for pos in positions:
            sym = pos.get("symbol", "").upper()
            sym_clean = sym.rstrip("._-Rr")
            base = sym_clean[:3] if len(sym_clean) >= 3 else sym_clean
            quote = sym_clean[3:6] if len(sym_clean) >= 6 else ""
            pair_key = f"{base}{quote}"
            if pair_key in pairs_done:
                continue
            pairs_done.add(pair_key)
            for tmpl in PAIR_FEED_TEMPLATES:
                _pull(tmpl.format(base=base, quote=quote), pair_key)

        # Deduplicate by title preserving order
        seen_text: set = set()
        unique: List[dict] = []
        for it in all_items:
            t = it.get("title", "")
            if t and t not in seen_text:
                seen_text.add(t)
                unique.append(it)
        return unique

    # ── Main cycle ───────────────────────────────────────────────────────────

    def _cycle(self) -> None:
        """Execute one monitoring cycle.

        Always: fetch news for the watched universe and push market-impact
        ALERTS to Telegram (headline + expandable details + per-pair effect).
        Additionally, when positions are open: score them and manage SLs.
        """
        self._acted = set()
        log.info("── Cycle %s ──", _now_iso())

        # 1. Open positions + the watched pair universe (dashboard-enabled)
        all_positions = self.client.get_open_positions()
        enabled = self.client.get_enabled_pairs()

        if enabled is None:
            positions = all_positions
        else:
            positions = [
                p for p in all_positions
                if p.get("symbol", "").upper().rstrip("._-Rr")[:6] in enabled
                or p.get("symbol", "").upper().rstrip("._-Rr") in enabled
            ]
        log.info("Open positions: %d (of %d)", len(positions), len(all_positions))

        # Watched pairs: dashboard selection, else open positions, else majors.
        if enabled:
            watch_pairs = sorted(enabled)
        elif positions:
            watch_pairs = sorted({p.get("symbol", "").upper() for p in positions})
        else:
            watch_pairs = ["EURUSD", "GBPUSD", "USDJPY", "USDCHF",
                           "AUDUSD", "NZDUSD", "USDCAD"]
        watch_currencies = sorted({c for pair in watch_pairs
                                   for c in _parse_symbol(pair) if c in MAJORS})

        # 2. Collect rich news items across all feeds (+ direct X posts first —
        # they are the primary source, wire coverage is the echo)
        items = self._collect_items(positions, watch_currencies)
        if self.x_fetcher is not None:
            try:
                items = self.x_fetcher.fetch_posts() + items
            except Exception as exc:  # noqa: BLE001
                log.debug("X fetch failed: %s", exc)
        headlines = [it["title"] for it in items]
        log.info("Unique headlines fetched: %d (age ≤ %sh)",
                 len(headlines), self.max_age_hours)

        # 2b. Headline audit trail — so "did the agent even SEE that news?"
        # is answerable from disk, not guesswork.
        self._append_headline_log(items)

        # 3a. Market-wide impact alerts (works with zero positions open)
        if self.alerts_enabled:
            try:
                self._scan_alerts(items, watch_pairs)
            except Exception as exc:  # noqa: BLE001
                log.warning("Alert scan failed: %s", exc)

        if not positions:
            log.info("No open positions to manage. Waiting for next cycle.")
            return
        if len(headlines) < self.min_headlines:
            log.warning(
                "Too few headlines (%d, need %d) — skipping evaluation this cycle.",
                len(headlines), self.min_headlines,
            )
            return

        # 3b. Evaluate each position
        decisions: List[TradeDecision] = []
        for pos in positions:
            decision = evaluate_trade(
                position=pos,
                headlines=headlines,
                sentiment_threshold=self.sentiment_threshold,
                trail_factor=self.trail_factor,
                be_buffer_pips=self.be_buffer_pips,
                cut_loss_threshold=self.cut_loss_threshold if self.cut_loss_enabled else 0.0,
            )
            decisions.append(decision)
            self._log_decision(decision)

            # 4. Act (or not). close_position carries no new_sl by design.
            if decision.action != "hold" and (
                    decision.new_sl is not None or decision.action == "close_position"):
                ticket = decision.ticket
                if ticket in self._acted:
                    continue
                self._acted.add(ticket)

                if self.live_mode:
                    ok = self.client.post_command(decision)
                    status = "✅ sent" if ok else "❌ failed"
                    log.info("    Command %s → %s", decision.action, status)
                else:
                    log.info(
                        "    [DRY-RUN] Would send: %s | new_sl=%s",
                        decision.action,
                        f"{decision.new_sl:.5f}" if decision.new_sl is not None else "— (market close)",
                    )

                # ── Telegram notification ────────────────────────────────────
                tg = self.telegram
                if tg is not None and self._tg_notify_news:
                    import threading
                    pos_match = next(
                        (p for p in positions if p.get("ticket") == ticket), {}
                    )
                    entry_price = float(pos_match.get("open_price", pos_match.get("entry", 0)))
                    old_sl      = float(pos_match.get("sl", decision.new_sl))

                    def _tg_notify(
                        _d=decision, _e=entry_price, _o=old_sl, _live=self.live_mode
                    ):
                        try:
                            if _d.action == "close_position":
                                tg.news_cut_loss(
                                    symbol=_d.symbol,
                                    side=_d.side,
                                    ticket=_d.ticket,
                                    net_score=_d.net_score,
                                    score_base=_d.score_base,
                                    score_quote=_d.score_quote,
                                    entry=_e,
                                    reason=_d.reason,
                                    live_mode=_live,
                                )
                            elif _d.action == "trail_sl":
                                tg.news_favourable(
                                    symbol=_d.symbol,
                                    side=_d.side,
                                    ticket=_d.ticket,
                                    net_score=_d.net_score,
                                    score_base=_d.score_base,
                                    score_quote=_d.score_quote,
                                    new_sl=_d.new_sl,
                                    old_sl=_o,
                                    reason=_d.reason,
                                    live_mode=_live,
                                )
                            else:  # move_sl_be
                                tg.news_against(
                                    symbol=_d.symbol,
                                    side=_d.side,
                                    ticket=_d.ticket,
                                    net_score=_d.net_score,
                                    score_base=_d.score_base,
                                    score_quote=_d.score_quote,
                                    new_sl=_d.new_sl,
                                    entry=_e,
                                    reason=_d.reason,
                                    live_mode=_live,
                                )
                        except Exception as exc:
                            log.debug("Telegram notification failed: %s", exc)

                    threading.Thread(target=_tg_notify, daemon=True).start()

        # 5. Append audit log
        self._append_decision_log(headlines, decisions)

    _headline_seen: set = set()

    def _append_headline_log(self, items: List[dict]) -> None:
        """Append every NEW fetched headline to state/news_headlines.jsonl
        (bounded to ~4000 lines) — the audit trail for missed-news questions."""
        try:
            path = Path("state/news_headlines.jsonl")
            new = [it for it in items
                   if self._headline_key(it.get("title", "")) not in self._headline_seen]
            if not new:
                return
            with open(path, "a", encoding="utf-8") as fh:
                for it in new:
                    self._headline_seen.add(self._headline_key(it.get("title", "")))
                    fh.write(json.dumps({"ts": _now_iso(), "title": it.get("title", ""),
                                         "source": it.get("source", "")}) + "\n")
            if len(self._headline_seen) % 50 == 0:        # occasional trim
                lines = path.read_text().splitlines()
                if len(lines) > 4000:
                    path.write_text("\n".join(lines[-4000:]) + "\n")
        except Exception as exc:  # noqa: BLE001
            log.debug("Headline log write failed: %s", exc)

    # ── Market-wide news alerts ──────────────────────────────────────────────

    def _load_alerts_sent(self) -> Dict[str, dict]:
        try:
            if self._alerts_sent_path.exists():
                data = json.loads(self._alerts_sent_path.read_text())
                if isinstance(data, dict):
                    # Migrate old format (key -> ts string) to (key -> {ts, title})
                    return {k: (v if isinstance(v, dict) else {"ts": v, "title": ""})
                            for k, v in data.items()}
        except Exception as exc:  # noqa: BLE001
            log.debug("Could not load alert dedupe store: %s", exc)
        return {}

    def _save_alerts_sent(self) -> None:
        try:
            cutoff = (_now_utc() - timedelta(hours=self.alert_dedupe_hours)).isoformat()
            self._alerts_sent = {h: rec for h, rec in self._alerts_sent.items()
                                 if rec.get("ts", "") >= cutoff}
            self._alerts_sent_path.write_text(json.dumps(self._alerts_sent, indent=1))
        except Exception as exc:  # noqa: BLE001
            log.debug("Could not save alert dedupe store: %s", exc)

    def _is_fresh(self, item: dict) -> bool:
        """Alert-worthiness requires a PROVEN fresh timestamp. Items with no
        parseable publish time are rejected — that loophole let wire services'
        rehashes of yesterday's posts through as if they were new."""
        iso = item.get("published_iso") or ""
        if not iso:
            return False
        try:
            dt = datetime.fromisoformat(iso)
        except ValueError:
            return False
        return (_now_utc() - dt) <= timedelta(minutes=self.alert_max_age_min)

    @staticmethod
    def _title_tokens(title: str) -> set:
        return {w for w in re.sub(r"[^a-z0-9 ]", " ", (title or "").lower()).split()
                if len(w) > 3}

    def _too_similar_to_sent(self, title: str) -> bool:
        """Fuzzy duplicate-coverage check: same story, different wording.
        Jaccard overlap ≥ 0.55 with any recently-alerted title → duplicate."""
        toks = self._title_tokens(title)
        if not toks:
            return False
        for rec in self._alerts_sent.values():
            prev = self._title_tokens(rec.get("title", ""))
            if not prev:
                continue
            j = len(toks & prev) / len(toks | prev)
            if j >= 0.55:
                return True
        return False

    @staticmethod
    def _headline_key(title: str) -> str:
        import hashlib
        norm = re.sub(r"\W+", " ", (title or "").lower()).strip()
        return hashlib.sha1(norm.encode()).hexdigest()[:16]

    def _is_priority(self, title: str) -> bool:
        """Presidential / social-media headline? → relaxed gate + queue jump."""
        t = (title or "").lower()
        return any(term in t for term in self.alert_priority_terms)

    def _scan_alerts(self, items: List[dict], watch_pairs: List[str]) -> None:
        """Find market-moving headlines and push them to Telegram with the
        likely per-pair impact. Deduped (48h) and capped per cycle.
        Presidential/social-post headlines use a relaxed score gate and are
        sent FIRST — they can move pairs 1% before normal signals accrue."""
        candidates: List[tuple] = []
        for it in items[:120]:
            key = self._headline_key(it.get("title", ""))
            if not key or key in self._alerts_sent:
                continue
            if not self._is_fresh(it):
                continue                      # stale or unproven timestamp
            if self._too_similar_to_sent(it.get("title", "")):
                continue                      # rehash of an already-sent story
            prio = bool(it.get("is_post")) or self._is_priority(it.get("title", ""))
            gate = self.alert_min_score * (self.alert_priority_factor if prio else 1.0)
            impact = analyze_headline_impact(it, watch_pairs, gate)
            if impact:
                impact["priority"] = prio
                candidates.append((int(prio), abs(impact["score"]), key, it, impact))

        if not candidates:
            log.info("Alerts: no market-moving headlines this cycle.")
            return
        candidates.sort(key=lambda c: (-c[0], -c[1]))   # priority first, then strength
        picked = candidates[:self.alert_max_per_cycle]
        log.info("Alerts: %d candidate(s), sending top %d.", len(candidates), len(picked))

        for _, _, key, it, impact in picked:
            # Mark as sent FIRST so a Telegram hiccup can't cause a re-spam loop.
            # Title is stored so follow-up coverage gets similarity-deduped.
            self._alerts_sent[key] = {"ts": _now_iso(), "title": it.get("title", "")}

            cur_line = "  ".join(f"{c} {s:+.2f}" for c, s in
                                 sorted(impact["currency_scores"].items(),
                                        key=lambda kv: -abs(kv[1])))
            details_parts = []
            if it.get("summary"):
                details_parts.append(it["summary"])
            details_parts.append("Sentiment triggers: " +
                                 (", ".join(impact["triggers"]) or "—"))
            details_parts.append("Currency scores: " + cur_line)
            details_parts.append("Pairs: " + "  ".join(
                f"{i['pair']} {i['arrow']} ({i['why']})" for i in impact["pair_impacts"]))
            details = "\n\n".join(details_parts)

            log.info("🗞 ALERT%s [%s %s %+0.2f] %s",
                     " ⚡PRIORITY" if impact.get("priority") else "",
                     impact["driver"], impact["direction"], impact["score"],
                     it.get("title", "")[:110])

            tg = self.telegram
            if tg is not None and self._tg_notify_alert:
                import threading
                threading.Thread(
                    target=tg.news_alert, daemon=True,
                    kwargs=dict(
                        headline=it.get("title", ""),
                        source=it.get("source", ""),
                        published=it.get("published", "")[:22],
                        link=it.get("link", ""),
                        driver=impact["driver"],
                        score=impact["score"],
                        direction=impact["direction"],
                        pair_impacts=impact["pair_impacts"],
                        details=details,
                        live_mode=self.live_mode,
                        priority=impact.get("priority", False),
                    ),
                ).start()

            # Audit trail alongside SL decisions
            try:
                with open("state/news_alerts.jsonl", "a", encoding="utf-8") as fh:
                    fh.write(json.dumps({
                        "ts": _now_iso(), "key": key,
                        "headline": it.get("title", ""), "source": it.get("source", ""),
                        "link": it.get("link", ""), "driver": impact["driver"],
                        "score": impact["score"], "direction": impact["direction"],
                        "pair_impacts": impact["pair_impacts"],
                        "triggers": impact["triggers"],
                        "priority": impact.get("priority", False),
                    }) + "\n")
            except Exception as exc:  # noqa: BLE001
                log.debug("Could not write alert log: %s", exc)

        self._save_alerts_sent()

    def _log_decision(self, d: TradeDecision) -> None:
        icons = {"trail_sl": "📈 TRAIL SL ", "move_sl_be": "🛡  MOVE → BE",
                 "close_position": "✂️  CUT LOSS ", "hold": "➖ HOLD     "}
        icon = icons.get(d.action, d.action)
        new_sl_str = f"{d.new_sl:.5f}" if d.new_sl else "—"
        log.info(
            "[%s] %s %s %s  net=%.2f  new_sl=%s",
            d.ticket, d.symbol.ljust(10), d.side.upper().ljust(4),
            icon, d.net_score, new_sl_str,
        )
        log.info("    %s", d.reason[:180])

    def _append_decision_log(self, headlines: List[str], decisions: List[TradeDecision]) -> None:
        """Write decision summary to JSONL for later audit / dashboard display."""
        entry = {
            "ts": _now_iso(),
            "live_mode": self.live_mode,
            "headline_count": len(headlines),
            "position_count": len(decisions),
            "decisions": [
                {
                    "ticket": d.ticket,
                    "symbol": d.symbol,
                    "side": d.side,
                    "action": d.action,
                    "new_sl": d.new_sl,
                    "net_score": round(d.net_score, 4),
                    "score_base": round(d.score_base, 4),
                    "score_quote": round(d.score_quote, 4),
                    "reason": d.reason[:400],
                }
                for d in decisions
            ],
        }
        try:
            with open("state/news_decisions.jsonl", "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")
        except Exception as exc:
            log.debug("Could not write decision log: %s", exc)

    # ── Main loop ────────────────────────────────────────────────────────────

    def run(self) -> None:
        """Start the monitoring loop. Runs until Ctrl+C."""
        log.info("Agent running. Press Ctrl+C to stop.")
        while True:
            try:
                self._cycle()
            except KeyboardInterrupt:
                log.info("Stopped by user.")
                break
            except Exception as exc:
                log.exception("Unexpected error in cycle: %s", exc)

            try:
                log.info("Next cycle in %ss…", self.poll_seconds)
                time.sleep(self.poll_seconds)
            except KeyboardInterrupt:
                log.info("Stopped by user.")
                break


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="News Monitoring Sub-Agent — trails or protects SL based on news sentiment."
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to config.yaml (default: config.yaml in current directory)",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Force live mode regardless of config (overrides news_agent.live_mode)",
    )
    args = parser.parse_args()

    agent = NewsAgent(config_path=args.config)
    if args.live and not agent.live_mode:
        log.warning("--live flag set: overriding live_mode to True")
        agent.live_mode = True

    agent.run()


if __name__ == "__main__":
    main()
