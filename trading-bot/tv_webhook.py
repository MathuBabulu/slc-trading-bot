"""TradingView webhook: auth + ticker->symbol mapping + payload parsing.

PURE (no engine / DB / account access) so it is unit-testable on its own.
Routing and the safety rails live in `engine.ingest_external_signal` — a parsed
alert is a CANDIDATE, never an order. The endpoint that calls this is OFF unless
`tradingview_webhook_enabled` AND a `tradingview_webhook_token` are set; the
token is checked with `hmac.compare_digest`.
"""
import hmac
import json
import re
from typing import Any, Dict, Optional

# TradingView tickers -> the broker symbols this bot trades. Extend as needed;
# unknown tickers are skipped by the caller (we never guess a symbol). Operators
# can add per-deployment entries via the `tradingview_symbol_map` setting.
_TICKER_MAP = {
    "EURUSD": "EURUSD", "GBPUSD": "GBPUSD", "USDJPY": "USDJPY",
    "USDCHF": "USDCHF", "USDCAD": "USDCAD", "AUDUSD": "AUDUSD",
    "NZDUSD": "NZDUSD", "EURGBP": "EURGBP", "EURJPY": "EURJPY",
    "GBPJPY": "GBPJPY",
    "XAUUSD": "XAUUSD", "GOLD": "XAUUSD", "XAGUSD": "XAGUSD",
    "BTCUSD": "BTCUSD", "BTCUSDT": "BTCUSD",
    "ETHUSD": "ETHUSD", "ETHUSDT": "ETHUSD",
}

_SUFFIX_RE = re.compile(r"(\.P|\.PERP|PERP)$")


def map_symbol(ticker: Optional[str],
               overrides: Optional[Dict[str, str]] = None) -> Optional[str]:
    """Map a TradingView ticker ('OANDA:EURUSD', 'BINANCE:BTCUSDT', 'BTCUSDT.P')
    to a broker symbol. Strips an exchange prefix and a trailing perp suffix.
    Returns None for an unknown ticker — the caller skips it."""
    if not ticker:
        return None
    t = str(ticker).upper().strip()
    if ":" in t:
        t = t.split(":", 1)[1]
    t = _SUFFIX_RE.sub("", t)
    table = dict(_TICKER_MAP)
    if overrides:
        table.update({str(k).upper(): v for k, v in overrides.items()})
    return table.get(t)


def check_token(provided: Optional[str], expected: Optional[str]) -> bool:
    """Constant-time token check. Returns False unless BOTH sides are non-empty
    and equal — i.e. an unconfigured webhook authenticates nothing."""
    if not expected or not provided:
        return False
    return hmac.compare_digest(str(provided), str(expected))


def parse_payload(raw: Any) -> Dict[str, Any]:
    """Normalize a TradingView alert into a flat field dict. Accepts a JSON
    string, bytes, or an already-parsed dict (TradingView alert bodies are
    user-authored JSON). Reads a known set of fields, ignores the rest.

    Returns {"ok": bool, "error": str|None, "fields": {...}} where fields has:
    token, ticker, side ('buy'|'sell'|None), trade_mode, entry, sl, tp, tp1,
    strategy.
    """
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", "replace")
    if isinstance(raw, str):
        raw = raw.strip()
        try:
            raw = json.loads(raw) if raw else {}
        except Exception as e:
            return {"ok": False, "error": "invalid JSON: %s" % e, "fields": {}}
    if not isinstance(raw, dict):
        return {"ok": False, "error": "payload is not a JSON object", "fields": {}}

    f: Dict[str, Any] = {}
    f["token"] = raw.get("token") or raw.get("passphrase")
    f["ticker"] = raw.get("ticker") or raw.get("symbol")

    action = str(raw.get("action") or raw.get("side") or "").strip().lower()
    if action in ("buy", "long"):
        f["side"] = "buy"
    elif action in ("sell", "short"):
        f["side"] = "sell"
    else:
        f["side"] = None

    f["trade_mode"] = str(raw.get("trade_mode") or raw.get("mode") or "swing").lower()

    def _num(*keys):
        for k in keys:
            v = raw.get(k)
            if v is not None and v != "":
                try:
                    return float(v)
                except (TypeError, ValueError):
                    return None
        return None

    f["entry"] = _num("entry", "price", "close")
    f["sl"] = _num("sl", "stop", "stoploss", "stop_loss")
    f["tp"] = _num("tp", "tp2", "target", "takeprofit", "take_profit")
    f["tp1"] = _num("tp1")
    f["strategy"] = raw.get("strategy") or raw.get("strategy_name") or "tradingview"
    return {"ok": True, "error": None, "fields": f}
