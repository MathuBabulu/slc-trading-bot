"""Order-router interface. Paper and live both implement this."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import List, Optional, Protocol


@dataclass
class OrderRequest:
    """A bot-internal order intent (not yet filled)."""
    ticket: int
    symbol: str
    side: str             # "buy" | "sell"
    lots: float
    entry: float          # planned entry (= pattern level R2)
    sl: float
    tp: float
    setup: str
    timeframe: str
    detected_at: str      # ISO 8601 UTC
    entry_bar_time: str = ""  # time of the latest CLOSED bar at decision time. The
                              # position may only be managed by bars STRICTLY AFTER
                              # this — prevents same-bar / look-ahead exits.
    tick_value: float = 0.0   # account-ccy per tick per lot (broker-exact; for P&L)
    tick_size: float = 0.0    # price increment of one tick
    risked_money: float = 0.0   # account-ccy actually at risk (lots × money_per_lot at SL)
    sizing_basis: str = ""      # human-readable sizing basis + source (for unit-mismatch audits)
    spread: float = 0.0         # broker's live ask−bid at signal time (price units; 0 = unknown)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class OrderFill:
    """A confirmed entry fill (also the live open-position record)."""
    ticket: int
    symbol: str
    side: str
    lots: float           # CURRENT remaining lots (reduced by partial take-profits)
    fill_price: float
    fill_time: str        # ISO 8601 UTC — wall-clock time the fill was executed
    sl: float             # CURRENT stop (moved by the scale-out manager)
    tp: float
    setup: str
    timeframe: str
    # Time of the bar the entry was decided on (latest CLOSED bar at fill). The
    # position may only be managed by bars whose time is STRICTLY GREATER than
    # this — the guard that prevents same-bar / look-ahead TP/SL fills.
    entry_bar_time: str = ""
    # --- scale-out / trailing management state ---
    init_sl: float = 0.0  # original stop, to compute R = |entry - init_sl|
    r: float = 0.0        # initial risk distance in price
    orig_lots: float = 0.0  # original lot size (so 50% is of the original)
    partial_done: bool = False   # has the 1:2 half-close happened?
    sl_stage: int = 0     # 0=initial, 1=break-even, 2=+1R, 3=+2R
    tick_value: float = 0.0   # broker-exact account-ccy per tick per lot (used for P&L)
    tick_size: float = 0.0    # price increment of one tick
    risked_money: float = 0.0   # account-ccy at risk for the ORIGINAL lots (invariant basis)
    sizing_basis: str = ""      # sizing basis recorded at fill (for unit-mismatch audits)
    spread: float = 0.0         # spread captured at FILL (price units) — the
                                # "normal" baseline. Bars are BID; sell-side exits
                                # trigger at ask = bid + spread.
    max_spread_seen: float = 0.0  # widest spread seen on any bar while open
                                  # (price units) — for the spread-impact audit.

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PositionUpdate:
    """Closed-trade summary."""
    ticket: int
    symbol: str
    side: str
    lots: float
    entry: float
    exit: float
    pnl: float            # in account currency
    rr: float             # achieved R multiple
    close_time: str
    close_reason: str     # "tp" | "sl" | "manual" | "halt" | "news"
    setup: str = ""       # pattern that produced the trade (carried from the fill)
    timeframe: str = ""
    entry_time: str = ""  # ISO 8601 UTC fill time (carried from the fill)
    sl: float = 0.0       # original stop-loss level (carried from the fill)
    tp: float = 0.0       # original take-profit level
    exit_spread: float = 0.0     # spread (price units) prevailing at the exit bar
    spread_induced: bool = False # True if this stop only triggered because the
                                 # spread had widened beyond the entry baseline
                                 # (the news / session-rollover stop-out case)

    def to_dict(self) -> dict:
        return asdict(self)


class OrderRouter(Protocol):
    """The bot calls these methods. The implementation decides paper or live."""

    mode: str             # "paper" | "live"

    def submit(self, req: OrderRequest) -> Optional[OrderFill]:
        """Try to fill the order. Returns the fill on success, None on reject."""
        ...

    def open_positions(self) -> List[OrderFill]:
        """Currently open positions."""
        ...

    def on_bar(self, symbol: str, bar) -> List[PositionUpdate]:  # noqa: ANN001
        """Mark-to-market each new closed bar. Returns any closures."""
        ...

    def flatten_all(self, reason: str) -> List[PositionUpdate]:
        """Close every open position (called on news, halt, etc.)."""
        ...

    def equity(self) -> float:
        ...

    def closed_trades(self) -> list:
        ...
