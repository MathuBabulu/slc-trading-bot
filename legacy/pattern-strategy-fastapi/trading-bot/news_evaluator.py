"""
News Sentiment Evaluator
========================
Scores financial news headlines for currency sentiment and determines
what SL management action (if any) each open position should receive.

No external NLP libraries required — uses keyword phrase scoring.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

# ── Currency → keyword mapping ───────────────────────────────────────────────
# Headlines mentioning these keywords are considered relevant to that currency.
CURRENCY_KEYWORDS: Dict[str, List[str]] = {
    "USD": [
        "dollar", "usd", "fed", "federal reserve", "fomc", "us economy",
        "american economy", "us gdp", "us cpi", "nonfarm payroll", "powell",
        "us inflation", "us employment", "us manufacturing", "us consumer",
        "us retail", "us pmi", "us housing", "us trade", "us debt",
        # White-House / geopolitical drivers of USD (added 11 Jun — a
        # presidential statement that reversed pairs scored 0.00 before this)
        "trump", "white house", "us president", "u.s. president",
        "washington", "us treasury", "us tariff", "us sanctions", "potus",
    ],
    "EUR": [
        "euro", "eur", "ecb", "european central bank", "eurozone",
        "eu economy", "lagarde", "germany", "france", "eu inflation",
        "european inflation", "eu gdp", "euro area", "european growth",
        "european union", "brussels",
    ],
    "GBP": [
        "pound", "gbp", "sterling", "boe", "bank of england", "uk economy",
        "british", "bailey", "uk gdp", "uk cpi", "uk inflation", "uk employment",
        "uk retail", "uk manufacturing", "uk pmi",
    ],
    "JPY": [
        "yen", "jpy", "boj", "bank of japan", "japanese", "japan economy",
        "ueda", "japan gdp", "japan cpi", "japan inflation", "japanese growth",
    ],
    "AUD": [
        "aussie", "aud", "rba", "reserve bank of australia", "australia economy",
        "australian", "australia gdp", "australia cpi", "australia employment",
    ],
    "NZD": [
        "kiwi", "nzd", "rbnz", "new zealand", "nz economy", "nz gdp",
        "nz cpi", "nz employment",
    ],
    "CAD": [
        "loonie", "cad", "boc", "bank of canada", "canadian", "canada economy",
        "canada gdp", "canada cpi", "canada employment", "canada oil",
    ],
    "CHF": [
        "franc", "chf", "snb", "swiss national bank", "swiss", "switzerland",
        "swiss economy",
    ],
    "XAU": [
        "gold", "xau", "precious metal", "bullion", "gold price", "gold demand",
    ],
    "XAG": [
        "silver", "xag", "silver price", "silver demand",
    ],
    "OIL": [
        "oil", "crude", "wti", "brent", "opec", "petroleum", "oil price",
        "energy prices", "oil demand", "oil supply",
    ],
    "NAS": [
        "nasdaq", "tech stocks", "technology stocks", "ndx", "tech sector",
    ],
    "SPX": [
        "s&p", "sp500", "s&p 500", "us stocks", "wall street", "us equities",
    ],
}

# ── Sentiment phrase scoring ─────────────────────────────────────────────────
# Positive = bullish, negative = bearish. Scale: ±1.0 (moderate) to ±2.0 (strong).
BULLISH_PHRASES: Dict[str, float] = {
    # Strong (±2.0)
    "rate hike": 2.0,
    "hawkish": 2.0,
    "beat expectations": 2.0,
    "beats expectations": 2.0,
    "stronger than expected": 2.0,
    "record high": 2.0,
    "aggressive tightening": 2.0,
    "above forecast": 2.0,
    "above expectations": 2.0,
    "better than expected": 2.0,
    "upside surprise": 2.0,
    "blowout": 2.0,
    "surges": 1.5,
    "surged": 1.5,
    "jumped": 1.5,
    "soars": 1.5,
    "soared": 1.5,
    # Moderate (±1.0)
    "rise": 1.0,
    "rises": 1.0,
    "rising": 1.0,
    "gain": 1.0,
    "gains": 1.0,
    "gaining": 1.0,
    "higher": 1.0,
    "positive": 1.0,
    "growth": 1.0,
    "expand": 1.0,
    "expanded": 1.0,
    "increase": 1.0,
    "increased": 1.0,
    "improved": 1.0,
    "improvement": 1.0,
    "strong": 1.0,
    "beat": 1.0,
    "optimistic": 1.0,
    "recovery": 1.0,
    "rally": 1.0,
    "rallied": 1.0,
    "climbed": 1.0,
    "advanced": 1.0,
    "strengthens": 1.0,
    "strengthened": 1.0,
    "tightening": 1.0,
    "upbeat": 1.0,
    "robust": 1.0,
    "accelerates": 1.0,
    "accelerated": 1.0,
    # Tweet/post-driven relief vocabulary (added 11 Jun)
    "trade deal": 2.0,
    "deal reached": 2.0,
    "agreement reached": 1.5,
    "truce": 1.5,
    "suspends tariffs": 1.5,
    "lifts tariffs": 1.5,
    "lifts sanctions": 1.5,
    "tax cut": 1.5,
    "tax cuts": 1.5,
    "stimulus": 1.5,
    "backs down": 1.0,
}

BEARISH_PHRASES: Dict[str, float] = {
    # Strong (±2.0)
    "rate cut": 2.0,
    "dovish": 2.0,
    "miss expectations": 2.0,
    "misses expectations": 2.0,
    "weaker than expected": 2.0,
    "recession": 2.0,
    "aggressive easing": 2.0,
    "below forecast": 2.0,
    "below expectations": 2.0,
    "worse than expected": 2.0,
    "downside surprise": 2.0,
    "misses forecasts": 2.0,
    "missed forecasts": 2.0,
    "collapsed": 2.0,
    "plunged": 1.5,
    "plunge": 1.5,
    "slumps": 1.5,
    "slumped": 1.5,
    "selloff": 1.5,
    # Moderate (±1.0)
    "fall": 1.0,
    "falls": 1.0,
    "falling": 1.0,
    "drop": 1.0,
    "drops": 1.0,
    "dropping": 1.0,
    "decline": 1.0,
    "declines": 1.0,
    "declining": 1.0,
    "lower": 1.0,
    "negative": 1.0,
    "slow": 1.0,
    "slows": 1.0,
    "slowing": 1.0,
    "weak": 1.0,
    "weaker": 1.0,
    "miss": 1.0,
    "missed": 1.0,
    "pessimistic": 1.0,
    "retreated": 1.0,
    "weakens": 1.0,
    "weakened": 1.0,
    "contraction": 1.0,
    "contracting": 1.0,
    "downturn": 1.0,
    "easing": 1.0,
    "downbeat": 1.0,
    "disappointing": 1.0,
    "disappointed": 1.0,
    "decelerates": 1.0,
    "decelerated": 1.0,
    "concern": 0.5,
    "concerns": 0.5,
    "uncertainty": 0.5,
    "risk": 0.5,
    # Geopolitical / policy shocks (added 11 Jun): presidential statements,
    # tariffs and sanctions reverse pairs but scored 0 in the old lexicon.
    "tariff": 1.5,
    "tariffs": 1.5,
    "trade war": 2.0,
    "sanction": 1.0,
    "sanctions": 1.0,
    "government shutdown": 1.5,
    "shutdown": 1.0,
    "impeach": 1.0,
    "escalation": 1.0,
    "retaliation": 1.5,
    "retaliate": 1.5,
    "threatens": 1.0,
    "threat": 0.5,
    "crisis": 1.5,
    "turmoil": 1.5,
    "emergency": 1.0,
    # Tweet/post-driven headline verbs (added 11 Jun — presidential posts
    # move pairs ~1% and routinely use this vocabulary)
    "slams": 1.0,
    "attacks": 1.0,
    "blasts": 1.0,
    "trade tensions": 1.5,
    "tensions": 1.0,
    "bans": 1.0,
    "blocks": 1.0,
    "demands": 0.5,
}


# ── Equity/stock-market context filter ──────────────────────────────────────
# When a headline is primarily about EQUITY markets (stock indices, IPOs, share
# prices) the bullish/bearish phrases like "surges", "soars", "jumps" describe
# equity performance — NOT currency strength. Applying them to FX scores was
# the root cause of the NZDUSD trailing-SL bug on 12 Jun (headlines "Wall
# Street surges / Sensex soars as Trump calls off Iran strikes" matched the USD
# keywords via "trump" and the equity verbs as USD-bullish, yielding net=+0.75
# and triggering a trail_sl that was unjustified for FX).
#
# Guard: if a headline has an equity-context word AND no FX-specific word,
# skip its phrase scoring for currency sentiment entirely. This correctly
# zeroes out stock-market headlines while keeping genuine FX-driving headlines
# (e.g. "Dollar surges after Fed rate hike") unaffected.
EQUITY_CONTEXT_WORDS: frozenset = frozenset({
    "wall street", "sensex", "nifty", "dow jones", "dow climbs", "dow jumps",
    "dow surges", "dow soars", "s&p 500", "sp 500", "nasdaq futures",
    "stock market", "equity market", "equities", "stock futures",
    "shares surge", "shares soar", "shares jump", "shares rise", "shares rally",
    "stock rally", "stocks rally", "stocks jump", "stocks surge", "stocks soar",
    "market rally", "ipo ", " ipo", "initial public offering",
    "nikkei", "ftse", "dax", "cac 40", "hang seng", "shanghai composite",
    "asx 200", "kospi", "bse", "bombay stock", "indian stock",
    "tech stocks", "earnings", "quarterly earnings", "quarterly results",
    "trillionaire", "billion valuation", "market cap", "market debut",
    "spaceX ipo", "historic ipo", "biggest ipo",
})

# FX-specific context: at least one of these must appear for a headline to
# be scored for currency sentiment when it also contains an equity term.
FX_CONTEXT_WORDS: frozenset = frozenset({
    "dollar", "usd", "euro", "eur", "pound", "gbp", "yen", "jpy",
    "forex", "fx ", " fx", "currency", "exchange rate", "parity",
    "fed rate", "rate hike", "rate cut", "federal reserve rate",
    "ecb rate", "boj rate", "rba rate", "rbnz rate",
    "inflation rate", "central bank rate", "interest rate decision",
    "nonfarm payroll", "cpi data", "gdp data",
})


@dataclass
class SentimentResult:
    currency: str
    score: float            # normalised −1.0 … +1.0; positive = bullish
    raw_score: float
    matched_phrases: List[str] = field(default_factory=list)
    headline_count: int = 0  # relevant headlines found


@dataclass
class TradeDecision:
    ticket: int
    symbol: str
    side: str               # "buy" | "sell"
    action: str             # "trail_sl" | "move_sl_be" | "hold"
    new_sl: Optional[float]
    reason: str
    score_base: float
    score_quote: float
    net_score: float        # positive = news favours the trade direction


# ── Helpers ──────────────────────────────────────────────────────────────────

def _parse_symbol(symbol: str) -> Tuple[str, str]:
    """Return (base_currency, quote_currency) for a trading symbol."""
    # Strip common broker suffixes: .r, _SB, m, etc.
    sym = re.sub(r'[._-].*$', '', symbol.upper()).strip()
    if len(sym) < 3:
        return sym, "USD"
    # Named commodities / indices
    if sym in ("XAUUSD", "GOLD"):
        return "XAU", "USD"
    if sym in ("XAGUSD", "SILVER"):
        return "XAG", "USD"
    if sym in ("USOIL", "UKOIL", "XTIUSD", "WTI", "BRENT"):
        return "OIL", "USD"
    if sym in ("NAS100", "NASDAQ", "NDX", "US100"):
        return "NAS", "USD"
    if sym in ("SPX500", "SP500", "US500", "SPX"):
        return "SPX", "USD"
    # Standard 6-char FX pair
    if len(sym) >= 6:
        return sym[:3], sym[3:6]
    return sym[:3], "USD"


def score_currency_sentiment(currency: str, headlines: List[str]) -> SentimentResult:
    """
    Compute a normalised sentiment score (−1 to +1) for a currency
    across a list of news headlines.
    """
    keywords = CURRENCY_KEYWORDS.get(currency, [currency.lower()])
    raw_score = 0.0
    matched: List[str] = []
    relevant_count = 0

    for headline in headlines:
        hl = headline.lower()
        # Only score if the headline mentions this currency
        if not any(kw in hl for kw in keywords):
            continue

        # Equity/stock-market context filter: headlines about equity indices,
        # IPOs, or share-price moves use the same "surges/soars/jumps" vocabulary
        # as FX-strength descriptions. If the headline is primarily about equities
        # AND has no FX-specific anchor (dollar, rate decision, etc.), skip it —
        # it tells us nothing reliable about currency strength.
        is_equity_context = any(ew in hl for ew in EQUITY_CONTEXT_WORDS)
        has_fx_context = any(fk in hl for fk in FX_CONTEXT_WORDS)
        if is_equity_context and not has_fx_context:
            log.debug(
                "Skipping equity-context headline for %s FX scoring: %.80s",
                currency, headline,
            )
            continue

        relevant_count += 1
        for phrase, weight in BULLISH_PHRASES.items():
            if phrase in hl:
                raw_score += weight
                matched.append(f"+{phrase}({weight:.0f})")
        for phrase, weight in BEARISH_PHRASES.items():
            if phrase in hl:
                raw_score -= weight
                matched.append(f"-{phrase}({weight:.0f})")

    # Clamp to [−4, +4] then normalise to [−1, +1]
    clamped = max(-4.0, min(4.0, raw_score))
    normalised = clamped / 4.0

    return SentimentResult(
        currency=currency,
        score=normalised,
        raw_score=raw_score,
        matched_phrases=matched[:12],
        headline_count=relevant_count,
    )


def calculate_trail_sl(position: dict, trail_factor: float = 1.5) -> Optional[float]:
    """
    Calculate new trailing stop-loss price.

    Uses the original risk distance (|entry − sl|) multiplied by trail_factor
    as the buffer behind current price.  Only trails in the profitable direction
    — never widens the stop.

    Returns None if conditions are not met (price not yet profitable,
    no SL set, or trail would move SL in the wrong direction).
    """
    try:
        entry = float(position["entry"])
        current = float(position["current"])
        sl = float(position.get("sl", 0))
        side = position.get("side", "buy").lower()
    except (KeyError, ValueError, TypeError):
        return None

    if entry == 0 or current == 0:
        return None

    if side == "buy":
        if sl == 0:
            return None
        original_risk = entry - sl
        if original_risk <= 0:
            return None          # SL already above entry — unusual, skip
        if current <= entry:
            return None          # Not yet in profit
        trail_dist = original_risk * trail_factor
        new_sl = round(current - trail_dist, 5)
        return new_sl if new_sl > sl else None   # only move up

    elif side == "sell":
        if sl == 0:
            return None
        original_risk = sl - entry
        if original_risk <= 0:
            return None
        if current >= entry:
            return None          # Not yet in profit
        trail_dist = original_risk * trail_factor
        new_sl = round(current + trail_dist, 5)
        return new_sl if new_sl < sl else None   # only move down

    return None


def calculate_be_sl(position: dict, be_buffer_pips: float = 2.0) -> Optional[float]:
    """
    Calculate break-even stop-loss (entry + small buffer to cover spread).

    Returns None if the trade is not yet in profit, or if the SL is
    already at or past break-even.
    """
    try:
        entry = float(position["entry"])
        current = float(position["current"])
        sl = float(position.get("sl", 0))
        side = position.get("side", "buy").lower()
        symbol = position.get("symbol", "EURUSD")
    except (KeyError, ValueError, TypeError):
        return None

    if entry == 0:
        return None

    # Pip size by symbol
    sym = symbol.upper()
    if "JPY" in sym:
        pip = 0.01
    elif any(x in sym for x in ("XAU", "GOLD")):
        pip = 0.10
    elif any(x in sym for x in ("OIL", "WTI", "XTI")):
        pip = 0.01
    else:
        pip = 0.0001

    buffer = be_buffer_pips * pip

    if side == "buy":
        if current <= entry:
            return None          # Not in profit
        be_sl = round(entry + buffer, 5)
        if sl > 0 and sl >= be_sl:
            return None          # SL already at or above BE
        return be_sl

    elif side == "sell":
        if current >= entry:
            return None
        be_sl = round(entry - buffer, 5)
        if sl > 0 and sl <= be_sl:
            return None
        return be_sl

    return None


def evaluate_trade(
    position: dict,
    headlines: List[str],
    sentiment_threshold: float = 0.25,
    trail_factor: float = 1.5,
    be_buffer_pips: float = 2.0,
    cut_loss_threshold: float = 0.5,
) -> TradeDecision:
    """
    Evaluate one open position against current news and return an action.

    Decision logic:
      net_score ≥ +threshold        →  trail_sl   (news favours the trade)
      net_score ≤ −threshold        →  move_sl_be (news against; trade in profit)
      net_score ≤ −cut_loss_threshold AND trade underwater (BE impossible)
                                    →  close_position (cut the loss at market)
      otherwise                     →  hold

    `cut_loss_threshold` is deliberately STRICTER than `sentiment_threshold`
    (default −0.5 vs −0.25): closing a trade is more aggressive than
    tightening a stop, so it demands stronger evidence. Set 0 to disable.
    """
    ticket = int(position.get("ticket", 0))
    symbol = position.get("symbol", "")
    side = position.get("side", "buy").lower()

    base, quote = _parse_symbol(symbol)

    s_base = score_currency_sentiment(base, headlines)
    s_quote = score_currency_sentiment(quote, headlines)

    # Net score from trade's perspective:
    #   BUY  = long base, short quote → bullish base or bearish quote = good
    #   SELL = short base, long quote  → bearish base or bullish quote = good
    if side == "buy":
        net = s_base.score - s_quote.score
    else:
        net = s_quote.score - s_base.score

    log.debug(
        "[%s %s %s] base=%s(%.2f) quote=%s(%.2f) net=%.2f",
        ticket, symbol, side, base, s_base.score, quote, s_quote.score, net,
    )

    if net >= sentiment_threshold:
        new_sl = calculate_trail_sl(position, trail_factor)
        if new_sl is not None:
            reason = (
                f"Favorable news for {side.upper()} {symbol}: "
                f"{base}={s_base.score:+.2f} {quote}={s_quote.score:+.2f} net={net:+.2f}. "
                f"Phrases: {'; '.join((s_base.matched_phrases + s_quote.matched_phrases)[:6])}"
            )
            return TradeDecision(ticket, symbol, side, "trail_sl", new_sl, reason,
                                 s_base.score, s_quote.score, net)
        else:
            reason = (
                f"Favorable news (net={net:+.2f}) but trade not yet in profit "
                f"or SL already trailing. Holding."
            )
            return TradeDecision(ticket, symbol, side, "hold", None, reason,
                                 s_base.score, s_quote.score, net)

    elif net <= -sentiment_threshold:
        new_sl = calculate_be_sl(position, be_buffer_pips)
        if new_sl is not None:
            reason = (
                f"Adverse news for {side.upper()} {symbol}: "
                f"{base}={s_base.score:+.2f} {quote}={s_quote.score:+.2f} net={net:+.2f}. "
                f"Moving SL to break-even. "
                f"Phrases: {'; '.join((s_base.matched_phrases + s_quote.matched_phrases)[:6])}"
            )
            return TradeDecision(ticket, symbol, side, "move_sl_be", new_sl, reason,
                                 s_base.score, s_quote.score, net)
        else:
            # BE is impossible. Distinguish WHY: already protected (SL at/past
            # BE) → nothing to do; UNDERWATER → the trade is losing while the
            # news says it will lose more. With STRONG adverse evidence, cut it.
            underwater = None
            try:
                entry = float(position["entry"])
                current = float(position["current"])
                underwater = (current < entry) if side == "buy" else (current > entry)
            except (KeyError, TypeError, ValueError):
                underwater = None     # can't tell → never cut blind

            if cut_loss_threshold > 0 and underwater and net <= -cut_loss_threshold:
                reason = (
                    f"STRONG adverse news for {side.upper()} {symbol} while underwater: "
                    f"{base}={s_base.score:+.2f} {quote}={s_quote.score:+.2f} net={net:+.2f} "
                    f"(cut gate −{cut_loss_threshold:.2f}). Break-even impossible — "
                    f"closing at market to cut the loss. "
                    f"Phrases: {'; '.join((s_base.matched_phrases + s_quote.matched_phrases)[:6])}"
                )
                return TradeDecision(ticket, symbol, side, "close_position", None, reason,
                                     s_base.score, s_quote.score, net)

            reason = (
                f"Adverse news (net={net:+.2f}) but trade not in profit or SL already at/past BE. "
                + (f"Underwater, holding (cut gate is −{cut_loss_threshold:.2f})."
                   if underwater else "Cannot move to break-even yet.")
            )
            return TradeDecision(ticket, symbol, side, "hold", None, reason,
                                 s_base.score, s_quote.score, net)

    else:
        reason = (
            f"Neutral news for {symbol} "
            f"({base}={s_base.score:+.2f} {quote}={s_quote.score:+.2f} net={net:+.2f}, "
            f"threshold=±{sentiment_threshold:.2f}). No action."
        )
        return TradeDecision(ticket, symbol, side, "hold", None, reason,
                             s_base.score, s_quote.score, net)
