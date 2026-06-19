"""Abstract market-data interface.

Every adapter must return OHLCV bars normalised to a `Bar` dataclass.
Times are always UTC; the engine assumes ISO 8601 strings end with `Z`.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import List, Optional, Protocol


@dataclass
class Bar:
    symbol: str          # internal display name (e.g. "EURUSD")
    timeframe: str       # "15m" | "30m" | "1h" | "2h" | "4h" | "1d"
    time: str            # ISO 8601 UTC, end-of-bar timestamp
    open: float
    high: float
    low: float
    close: float
    volume: float        # MT5 tick volume
    # Broker spread for this bar, in PRICE units (ask − bid). 0.0 = unknown.
    # Populated either from the EA's per-bar max spread (most faithful) or, when
    # absent, sampled by the engine from the live prices feed at bar close. Used
    # by the paper router to model spread DYNAMICALLY through a trade so news /
    # session-rollover widening can realistically hit a stop.
    spread: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


class DataSource(Protocol):
    """Adapters implement this. The engine doesn't care which one is active."""

    def fetch_history(
        self,
        symbol: str,
        display: str,
        timeframe: str,
        bars: int,
    ) -> List[Bar]:
        """Return the most-recent `bars` closed bars for the symbol+timeframe."""
        ...

    def fetch_latest(
        self,
        symbol: str,
        display: str,
        timeframe: str,
        since_iso: Optional[str],
    ) -> List[Bar]:
        """Return any *new* closed bars since `since_iso` (exclusive)."""
        ...
