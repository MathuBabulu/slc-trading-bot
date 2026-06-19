#!/usr/bin/env python3
"""Video transcript -> watch levels.

Pulls a YouTube video's transcript (a finished live/VOD that has captions),
extracts the pairs + price levels + buy/sell bias the presenter mentioned, and
auto-loads them into the bot's Video Levels watchlist (POST /api/watch_levels).

The bot then trades those levels ONLY when your own strategy confirms, so a
mis-heard level just becomes a level that never triggers — safe to auto-load.

Usage:
    pip install youtube-transcript-api            # one-time (on your Mac)
    python3 video_levels.py https://www.youtube.com/watch?v=VIDEO_ID
    python3 video_levels.py VIDEO_ID --dry-run    # show what it found, don't load
    python3 video_levels.py --transcript-file t.txt   # use a pasted transcript instead

NOTE: real-time *live* trading from audio is not supported (see chat). This works
on a finished video's captions. Extraction is heuristic — quality depends on the
captions; that's why the bot still gates every level through the strategy.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.request
import urllib.parse
from pathlib import Path

# --- pair aliases (spoken forms / nicknames -> canonical symbol) -------------
PAIR_ALIASES = {
    "EURUSD": ["eurusd", "euro dollar", "eur usd", "fiber"],
    "GBPUSD": ["gbpusd", "pound dollar", "gbp usd", "cable"],
    "USDJPY": ["usdjpy", "dollar yen", "usd jpy"],
    "AUDUSD": ["audusd", "aussie dollar", "aud usd"],
    "USDCAD": ["usdcad", "dollar cad", "usd cad", "loonie"],
    "USDCHF": ["usdchf", "dollar swiss", "usd chf", "swissy"],
    "NZDUSD": ["nzdusd", "kiwi dollar", "nzd usd"],
    "EURGBP": ["eurgbp", "euro pound", "eur gbp"],
    "EURJPY": ["eurjpy", "euro yen", "eur jpy"],
    "GBPJPY": ["gbpjpy", "pound yen", "gbp jpy", "guppy"],
    "AUDJPY": ["audjpy", "aussie yen", "aud jpy"],
    "EURAUD": ["euraud", "euro aussie", "eur aud"],
    "EURNZD": ["eurnzd", "euro kiwi", "eur nzd"],
    "GBPAUD": ["gbpaud", "pound aussie", "gbp aud"],
    "GBPCAD": ["gbpcad", "pound cad", "gbp cad"],
    "CADJPY": ["cadjpy", "cad yen", "cad jpy"],
    "CHFJPY": ["chfjpy", "swiss yen", "chf jpy"],
    "NZDJPY": ["nzdjpy", "kiwi yen", "nzd jpy"],
    "AUDCAD": ["audcad", "aussie cad", "aud cad"],
    "AUDCHF": ["audchf", "aussie swiss", "aud chf"],
    "AUDNZD": ["audnzd", "aussie kiwi", "aud nzd"],
    "EURCAD": ["eurcad", "euro cad", "eur cad"],
    "EURCHF": ["eurchf", "euro swiss", "eur chf"],
    "XAUUSD": ["xauusd", "gold"],
    "XAGUSD": ["xagusd", "silver"],
    "XPTUSD": ["xptusd", "platinum"],
    "BTCUSD": ["btcusd", "bitcoin", "btc"],
    "ETHUSD": ["ethusd", "ethereum", "eth"],
    "LTCUSD": ["ltcusd", "litecoin"],
    "XRPUSD": ["xrpusd", "ripple", "xrp"],
    "BNBUSD": ["bnbusd", "bnb"],
    "SOLUSD": ["solusd", "solana", "sol"],
    "USOIL":  ["usoil", "wti", "crude oil", "crude", "us oil"],
    "UKOIL":  ["ukoil", "brent"],
    "NATGAS": ["natgas", "nat gas", "natural gas"],
    "US30":   ["us30", "dow jones", "dow", "wall street"],
    "US500":  ["us500", "s&p 500", "s and p", "sp500", "spx"],
    "NAS100": ["nas100", "nasdaq", "nas 100", "us tech"],
    "GER40":  ["ger40", "dax", "germany 40"],
    "UK100":  ["uk100", "ftse", "footsie"],
    "JPN225": ["jpn225", "nikkei", "japan 225"],
    "AUS200": ["aus200", "asx", "australia 200"],
}

# plausible price range per symbol (sanity filter to reject garbage numbers)
def _range(sym: str):
    # NOTE: these are SANITY bands to reject stray numbers, sized to current
    # (2026) market levels — gold ~ $4,300/oz as of Jun 2026, so old ~$2,000
    # figures are correctly rejected.
    if sym.endswith("JPY"):                 return (50, 350)
    if sym in ("XAUUSD",):                  return (2800, 8000)   # gold ~4,359 (Jun 2026)
    if sym in ("XAGUSD",):                  return (15, 150)      # silver tracks gold higher
    if sym in ("XPTUSD",):                  return (600, 3500)    # platinum
    if sym in ("USOIL", "UKOIL"):           return (20, 160)
    if sym in ("NATGAS",):                  return (0.5, 15)
    if sym in ("US30", "JPN225"):           return (15000, 70000)
    if sym in ("NAS100",):                  return (8000, 45000)
    if sym in ("GER40",):                   return (8000, 35000)
    if sym in ("US500",):                   return (2500, 9000)
    if sym in ("UK100", "AUS200"):          return (4000, 12000)
    if sym in ("BTCUSD",):                  return (5000, 250000)
    if sym in ("ETHUSD",):                  return (300, 12000)
    if sym in ("SOLUSD", "LTCUSD", "XRPUSD", "BNBUSD"): return (0.1, 2000)
    return (0.3, 3.0)                       # FX non-JPY

BUY_WORDS  = ["buy", "long", "bull", "support", "bounce", "demand", "buying", "go long"]
SELL_WORDS = ["sell", "short", "bear", "resistance", "reject", "supply", "selling", "go short"]

NUM_RE = re.compile(r"\d{1,6}(?:\.\d{1,5})?")


def fetch_transcript(video_id: str) -> str:
    """Return the transcript text via youtube-transcript-api, or '' on failure."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        print("youtube-transcript-api not installed. Run: pip install youtube-transcript-api")
        return ""
    try:
        parts = YouTubeTranscriptApi.get_transcript(video_id)
        return " ".join(p.get("text", "") for p in parts)
    except Exception as exc:  # noqa: BLE001
        print(f"Could not fetch captions for {video_id}: {exc}")
        print("The video may have no captions, or be a live stream still processing.")
        return ""


def channel_latest_ids(channel_url: str, count: int = 5) -> list:
    """Use yt-dlp to list the most recent video ids on a channel (newest first).
    Tries the /streams tab (past live streams) first, then /videos."""
    base = channel_url.rstrip("/")
    # if they passed a tab already, use it as-is
    tabs = [""] if base.endswith(("/streams", "/videos")) else ["/streams", "/videos"]
    # Prefer "python -m yt_dlp" (same env, no PATH dependence — robust under cron),
    # fall back to a "yt-dlp" binary on PATH.
    runners = [[sys.executable, "-m", "yt_dlp"], ["yt-dlp"]]
    for tab in tabs:
        url = base + tab
        for runner in runners:
            try:
                out = subprocess.run(
                    runner + ["--flat-playlist", "--playlist-end", str(count), "--print", "id", url],
                    capture_output=True, text=True, timeout=90,
                )
            except FileNotFoundError:
                continue
            except Exception as exc:  # noqa: BLE001
                print(f"yt-dlp error on {url}: {exc}")
                continue
            ids = [ln.strip() for ln in out.stdout.splitlines() if ln.strip()]
            if ids:
                return ids[:count]
    print("Could not run yt-dlp. Install it in this environment:  pip install yt-dlp")
    return []


def fetch_description(video_id: str) -> str:
    """Fallback when there are no captions: pull the video's DESCRIPTION text
    via yt-dlp (many traders list their levels there). '' if unavailable."""
    runners = [[sys.executable, "-m", "yt_dlp"], ["yt-dlp"]]
    url = f"https://www.youtube.com/watch?v={video_id}"
    for runner in runners:
        try:
            out = subprocess.run(runner + ["--skip-download", "--print", "description", url],
                                 capture_output=True, text=True, timeout=60)
        except FileNotFoundError:
            continue
        except Exception:  # noqa: BLE001
            continue
        if out.stdout.strip():
            return out.stdout
    return ""


def video_id_from(url_or_id: str) -> str:
    if "youtu" not in url_or_id:
        return url_or_id.strip()
    q = urllib.parse.urlparse(url_or_id)
    if q.query:
        v = urllib.parse.parse_qs(q.query).get("v")
        if v:
            return v[0]
    return q.path.rstrip("/").split("/")[-1]


def _find_all(hay: str, needle: str):
    out, j = [], hay.find(needle)
    while j >= 0:
        out.append(j)
        j = hay.find(needle, j + len(needle))
    return out


def extract_levels(text: str, source: str = "") -> list:
    """Heuristically extract {symbol, level, side, note} from transcript text.

    Works sentence-by-sentence so a pair is matched to the NEAREST number and
    bias word within the same sentence — avoids one pair stealing another's
    level/direction. Numbers are sanity-checked against each pair's plausible
    price range to reject stray figures (percentages, dates, etc.)."""
    low = text.lower()
    found = {}
    # Split into sentences, but DON'T split on a '.' that's a decimal point
    # (e.g. keep "1.0850" intact). Split on . ! ? ; newline otherwise.
    for sent in re.split(r"(?:(?<!\d)\.(?!\d))|[!?\n;]+", low):
        if not sent:
            continue
        # locate every pair alias + every number + every bias word in this sentence
        pair_hits = []  # (pos, symbol)
        for sym, aliases in PAIR_ALIASES.items():
            for a in aliases:
                for p in _find_all(sent, a):
                    pair_hits.append((p, sym))
        if not pair_hits:
            continue
        nums = [(m.start(), m.group()) for m in NUM_RE.finditer(sent)]
        if not nums:
            continue
        bias_hits = []  # (pos, side)
        for w in BUY_WORDS:
            bias_hits += [(p, "buy") for p in _find_all(sent, w)]
        for w in SELL_WORDS:
            bias_hits += [(p, "sell") for p in _find_all(sent, w)]

        for pos, sym in pair_hits:
            lo_rng, hi_rng = _range(sym)
            after, before = [], []
            for npos, raw in nums:
                try:
                    val = float(raw)
                except ValueError:
                    continue
                if not (lo_rng <= val <= hi_rng):
                    continue
                (after if npos >= pos else before).append((abs(npos - pos), val))
            # Prefer the nearest number stated AFTER the pair name ("crude oil ...
            # 78"); fall back to the nearest one before only if none follow.
            cands = after if after else before
            if not cands:
                continue
            cands.sort()
            level = round(cands[0][1], 5)
            side = None
            if bias_hits:
                side = sorted(bias_hits, key=lambda b: abs(b[0] - pos))[0][1]
            key = (sym, level)
            found.setdefault(key, {
                "symbol": sym, "level": level, "side": side, "tol_pips": 15,
                "note": f"from video {source}".strip(),
                "source": f"youtube:{source}", "consumed": False,
            })
    return list(found.values())


def post_levels(server: str, new_levels: list) -> None:
    base = server.rstrip("/")
    # merge with existing (dedup by symbol+level)
    existing = []
    try:
        with urllib.request.urlopen(f"{base}/api/watch_levels", timeout=5) as r:
            existing = json.loads(r.read()).get("levels", [])
    except Exception as exc:  # noqa: BLE001
        print(f"Could not read existing levels ({exc}); posting just the new ones.")
    have = {(l.get("symbol"), round(float(l.get("level", 0)), 5)) for l in existing}
    merged = list(existing)
    added = 0
    for lv in new_levels:
        if (lv["symbol"], lv["level"]) not in have:
            merged.append(lv); added += 1
    body = json.dumps({"levels": merged}).encode()
    req = urllib.request.Request(f"{base}/api/watch_levels", data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            res = json.loads(r.read())
        print(f"Loaded {added} new level(s). Watchlist now has {res.get('active')} active.")
    except Exception as exc:  # noqa: BLE001
        print(f"Could not POST to server ({exc}). Is server.py running at {base}?")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video", nargs="?", help="YouTube URL or video id")
    ap.add_argument("--channel", help="channel URL (e.g. https://www.youtube.com/@DevilTraderLive) — auto-picks the latest finished stream with captions")
    ap.add_argument("--count", type=int, default=5, help="how many recent videos to scan in --channel mode")
    ap.add_argument("--server", default="http://localhost:8765")
    ap.add_argument("--transcript-file", help="use a local transcript .txt instead of fetching")
    ap.add_argument("--dry-run", action="store_true", help="print extracted levels, don't load")
    args = ap.parse_args()

    source = ""
    text = ""
    if args.transcript_file:
        text = Path(args.transcript_file).read_text()
        source = Path(args.transcript_file).stem
    elif args.channel:
        ids = channel_latest_ids(args.channel, args.count)
        if not ids:
            print("Could not list channel videos (need yt-dlp; check the channel URL).")
            return 1
        # newest first — use the first that has captions (skips an in-progress
        # live), then fall back to the newest video's DESCRIPTION text.
        for vid in ids:
            print(f"Trying latest video {vid} …")
            t = fetch_transcript(vid)
            if t.strip():
                text, source = t, vid
                print(f"Using captions from {vid}.")
                break
        if not text.strip():
            for vid in ids:
                d = fetch_description(vid)
                if d.strip():
                    text, source = d, vid
                    print(f"No captions; using the DESCRIPTION of {vid}.")
                    break
        if not text.strip():
            print(f"None of the latest {len(ids)} videos had captions or a usable description.")
            return 1
    elif args.video:
        source = video_id_from(args.video)
        text = fetch_transcript(source)
        if not text.strip():
            text = fetch_description(source)
            if text.strip():
                print(f"No captions; using the video DESCRIPTION of {source}.")
    else:
        ap.error("provide a video URL/id, --channel, or --transcript-file")
        return 2

    if not text.strip():
        print("No transcript or description text — nothing to extract. "
              "(Try --transcript-file with a pasted transcript, or enable the Whisper audio fallback.)")
        return 1

    levels = extract_levels(text, source)
    print(f"Extracted {len(levels)} candidate level(s):")
    for lv in levels:
        print(f"  {lv['symbol']:8} {lv['level']:<12} {lv['side'] or 'auto':5}  {lv['note']}")
    if not levels:
        return 0
    if args.dry_run:
        print("\n[dry-run] not loaded.")
        return 0
    post_levels(args.server, levels)
    return 0


if __name__ == "__main__":
    sys.exit(main())
