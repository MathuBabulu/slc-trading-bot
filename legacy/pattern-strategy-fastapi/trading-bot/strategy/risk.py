"""Risk + safety rails.

This is the single source of truth for *all* sizing and *all* halt conditions.
Both paper and live modes consume it. There is intentionally no override
mechanism short of editing this file.

Caps:
- per_trade_pct        : 1 % of equity at risk per trade (default)
- min_rr               : 2.0 (signals with planned RR below this are dropped)
- daily_trade_cap      : 3 fills per UTC day
- weekly_trade_cap     : 12 fills per ISO week
- daily_loss_pct       : 3 % — halt new entries today if breached
- max_drawdown_pct     : 10 % — halt all trading until manual reset
- kill_switch_file     : if the file exists, refuse every signal

`size_position` returns position size in *lots* (1.0 = standard lot).
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

from .patterns import Signal

log = logging.getLogger(__name__)


@dataclass
class RiskConfig:
    per_trade_pct: float = 1.0
    min_rr: float = 2.0
    daily_trade_cap: int = 3
    weekly_trade_cap: int = 12
    daily_loss_pct: float = 3.0
    max_drawdown_pct: float = 10.0
    kill_switch_file: str = "state/HALT"


@dataclass
class Instrument:
    symbol: str        # internal display name
    pip_size: float
    pip_value: float   # account-ccy per pip per 1 standard lot (static fallback)
    # Live, broker-exact sizing inputs pushed by the MT5 EA (preferred when set).
    # money-at-risk per lot = (price_distance / tick_size) * tick_value
    tick_value: Optional[float] = None   # account-ccy per tick per 1 lot
    tick_size: Optional[float] = None    # price increment of one tick


@dataclass
class RiskState:
    starting_equity: float
    current_equity: float
    realized_today: float = 0.0       # net P&L today (UTC day)
    realized_this_week: float = 0.0   # net P&L this ISO week
    trades_today: int = 0
    trades_this_week: int = 0
    last_day: str = ""                # UTC date string, for reset detection
    last_week: str = ""               # ISO year-week, for reset detection
    halted_for_dd: bool = False

    def rollover_if_needed(self, now: Optional[datetime] = None) -> None:
        now = now or datetime.now(timezone.utc)
        today = now.strftime("%Y-%m-%d")
        week = f"{now.isocalendar().year}-W{now.isocalendar().week:02d}"
        if today != self.last_day:
            self.realized_today = 0.0
            self.trades_today = 0
            self.last_day = today
        if week != self.last_week:
            self.realized_this_week = 0.0
            self.trades_this_week = 0
            self.last_week = week


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def evaluate_signal(
    signal: Signal,
    instrument: Instrument,
    cfg: RiskConfig,
    state: RiskState,
    sizing: Optional[dict] = None,
    entry_spread: float = 0.0,
) -> Tuple[bool, str, float]:
    """Return (accept, reason, lots).

    If a dict is passed as `sizing`, it is populated ON ACCEPT with exactly how
    the position was sized, so the fill can persist it and every close can be
    verified against the invariant |pnl| ≈ |R| × risked_money:
        {risked_money, money_per_lot, basis, source, tick_value, tick_size}

    Order of checks matches severity: kill switch first, then hard caps, then
    sizing. If any reject, `lots` is 0.
    """
    state.rollover_if_needed()

    # 1. Kill switch
    if Path(cfg.kill_switch_file).exists():
        return False, "Kill switch file present", 0.0

    # 2. Drawdown halt
    if state.halted_for_dd:
        return False, "Max drawdown halt — bot stopped, requires manual reset", 0.0
    dd_pct = (state.starting_equity - state.current_equity) / state.starting_equity * 100
    if dd_pct >= cfg.max_drawdown_pct:
        state.halted_for_dd = True
        return False, f"Max drawdown breached ({dd_pct:.2f}% >= {cfg.max_drawdown_pct:.2f}%)", 0.0

    # 3. Daily loss cap
    daily_loss_pct = max(0.0, -state.realized_today) / state.current_equity * 100
    if daily_loss_pct >= cfg.daily_loss_pct:
        return False, f"Daily loss cap reached ({daily_loss_pct:.2f}%)", 0.0

    # 4. Trade-count caps
    if state.trades_today >= cfg.daily_trade_cap:
        return False, f"Daily trade cap reached ({state.trades_today}/{cfg.daily_trade_cap})", 0.0
    if state.trades_this_week >= cfg.weekly_trade_cap:
        return False, f"Weekly trade cap reached ({state.trades_this_week}/{cfg.weekly_trade_cap})", 0.0

    # 5. Minimum RR
    if signal.rr < cfg.min_rr:
        return False, f"RR {signal.rr:.2f} below minimum {cfg.min_rr:.2f}", 0.0

    # 6. Position sizing — size on the SPREAD-INCLUSIVE entry.
    # A buy fills at the ASK = entry + spread, so its TRUE risk is
    # |entry + spread - sl|. Sizing on the raw signal entry under-states the
    # stop distance and OVER-RISKS the trade (mildly with a wide stop, badly
    # when the stop is tight or the spread wide — e.g. a 3.3× over-risk seen on
    # USDCAD). Sells fill at the bid, so their entry is unaffected (the spread is
    # paid at exit instead and is modelled there).
    eff_entry = signal.entry + entry_spread if signal.side == "buy" else signal.entry
    risk_per_unit_price = abs(eff_entry - signal.sl)
    if risk_per_unit_price <= 0:
        return False, "Zero risk distance", 0.0

    risk_in_money = state.current_equity * (cfg.per_trade_pct / 100.0)

    # Prefer the broker-exact tick value pushed live by the MT5 EA; this makes
    # cross-pair sizing correct without hand-tuned pip_value. Fall back to the
    # static pip_size/pip_value from config when tick data isn't available yet.
    tv, ts = instrument.tick_value, instrument.tick_size
    if tv and ts and tv > 0 and ts > 0:
        money_per_lot = (risk_per_unit_price / ts) * tv
        source = "mt5"
        basis = f"MT5 tick {tv:.4f}/{ts:g}"
    else:
        risk_in_pips = risk_per_unit_price / instrument.pip_size
        if risk_in_pips <= 0:
            return False, "Risk distance below pip resolution", 0.0
        money_per_lot = risk_in_pips * instrument.pip_value
        source = "config_fallback"
        basis = f"{risk_in_pips:.1f} pips x {instrument.pip_value} (config)"

    if money_per_lot <= 0:
        return False, "Zero money-at-risk per lot", 0.0
    lots = round(risk_in_money / money_per_lot, 2)
    if lots < 0.01:
        return False, f"Position size below minimum 0.01 lot ({lots:.4f})", 0.0

    # ACTUAL money at risk after lot rounding (what the invariant checks against).
    actual_risked = round(lots * money_per_lot, 2)
    if sizing is not None:
        sizing.update({
            "risked_money": actual_risked,
            "money_per_lot": round(money_per_lot, 4),
            "basis": basis,
            "source": source,                 # "mt5" = broker-exact; "config_fallback" = suspect units
            "tick_value": tv or 0.0,
            "tick_size": ts or 0.0,
            "entry_spread": round(entry_spread, 6),   # spread folded into buy-side risk
        })
    log.info("Sizing %s %s: %.2f lots, risked %.2f (target %.2f) — basis: %s [%s]",
             signal.symbol, signal.side, lots, actual_risked, risk_in_money, basis, source)

    return True, f"Risk OK: {lots:.2f} lots, {actual_risked:.2f} risked ({basis})", lots


def record_fill(state: RiskState) -> None:
    """Called after the order router has accepted a fill (paper or live)."""
    state.rollover_if_needed()
    state.trades_today += 1
    state.trades_this_week += 1


def record_close(state: RiskState, pnl: float) -> None:
    """Called when a position closes (paper fill exit or live deal)."""
    state.rollover_if_needed()
    state.realized_today += pnl
    state.realized_this_week += pnl
    state.current_equity += pnl
