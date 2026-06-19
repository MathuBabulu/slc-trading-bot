"""MT5 market-data adapter — reads OHLCV bars pushed by the MT5DataBridge EA.

The EA posts bars to /api/mt5_bars on a configurable interval (default 60s).
The server stores them in a shared dict (`mt5_store`).  This adapter reads
from that dict so the strategy engine never calls yfinance.

At cold startup (before the first EA bar push) fetch_history returns [] and
the engine warms up with 0 bars.  History fills in naturally as the EA pushes.

Bar storage layout expected in mt5_store:
    mt5_store["bars"] = {
        "EURUSD": {
            "15m": [{"t": <unix_server_time>, "o": …, "h": …, "l": …, "c": …, "v": …}, …],
            "1h":  […],
            "4h":  […],
        },
        …
    }
    mt5_store["tz_offset_sec"] = 7200   # TimeCurrent() - TimeGMT() on the MT5 server
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pandas as pd

from .base import Bar, DataSource

log = logging.getLogger(__name__)


def _unix_to_iso(ts_unix: int, tz_offset_sec: int) -> str:
    """Convert MT5 bar timestamp (server local) to UTC ISO string ending in Z."""
    utc_ts = ts_unix - tz_offset_sec
    dt = datetime.fromtimestamp(utc_ts, tz=timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


def _bars_from_store(
    store_bars: List[Dict[str, Any]],
    display: str,
    timeframe: str,
    tz_offset_sec: int,
) -> List[Bar]:
    return [
        Bar(
            symbol=display,
            timeframe=timeframe,
            time=_unix_to_iso(b["t"], tz_offset_sec),
            open=float(b["o"]),
            high=float(b["h"]),
            low=float(b["l"]),
            close=float(b["c"]),
            volume=float(b.get("v", 0) or 0),
            # Per-bar MAX spread in price units, if the EA sends it (key "sp").
            # 0.0 when absent → the engine falls back to live-feed sampling.
            spread=float(b.get("sp", 0) or 0),
        )
        for b in store_bars
    ]


class MT5Source:
    """Live market-data adapter backed by the MT5DataBridge EA push feed."""

    def __init__(self, mt5_store: Dict[str, Any]) -> None:
        self._store = mt5_store

    # ------------------------------------------------------------------ #
    # Public API (DataSource protocol)
    # ------------------------------------------------------------------ #
    def fetch_history(
        self,
        symbol: str,
        display: str,
        timeframe: str,
        bars: int,
    ) -> List[Bar]:
        store_bars = self._get_store_bars(symbol, display, timeframe)
        if store_bars is None:
            log.info(
                "MT5Source: waiting for first bar push from EA for %s %s — starting with 0 bars",
                display, timeframe,
            )
            return []

        tz = self._store.get("tz_offset_sec", 0)
        result = _bars_from_store(store_bars, display, timeframe, tz)
        return result[-bars:] if len(result) > bars else result

    def fetch_latest(
        self,
        symbol: str,
        display: str,
        timeframe: str,
        since_iso: Optional[str],
    ) -> List[Bar]:
        store_bars = self._get_store_bars(symbol, display, timeframe)
        if store_bars is None:
            return []

        tz = self._store.get("tz_offset_sec", 0)
        all_bars = _bars_from_store(store_bars, display, timeframe, tz)

        if not since_iso:
            return all_bars

        cutoff = pd.Timestamp(since_iso)
        if cutoff.tzinfo is None:
            cutoff = cutoff.tz_localize("UTC")
        else:
            cutoff = cutoff.tz_convert("UTC")

        return [
            b for b in all_bars
            if pd.Timestamp(b.time).tz_convert("UTC") > cutoff
        ]

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    def _get_store_bars(
        self, symbol: str, display: str, timeframe: str
    ) -> Optional[List[Dict[str, Any]]]:
        """Return the raw bar list from mt5_store, or None if not present."""
        bars_root: Dict = self._store.get("bars", {})
        if not bars_root:
            return None

        # Try exact symbol match, then display name
        for key in (symbol, display, symbol.upper(), display.upper()):
            sym_data = bars_root.get(key)
            if sym_data:
                tf_data = sym_data.get(timeframe)
                if tf_data:
                    return tf_data

        return None
