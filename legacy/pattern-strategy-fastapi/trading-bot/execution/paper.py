"""Paper-trading router.

Simulates fills, P&L, and the equity curve. The dashboard reads from here in
paper mode. All state is persisted to a JSON file so the ledger survives
restarts.

Fill model:
- Entry: at the NEXT bar's open after the signal triggers (no look-ahead)
- Exit:  at SL or TP if the bar's high/low touches the level. If both are
         touched in the same bar, we assume the worse outcome (SL hit first),
         which is the conservative assumption.

Slippage and commission are configurable. P&L is computed as
    (exit - entry) * direction_sign * lots * pip_value / pip_size
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from marketdata.base import Bar
from .base import OrderFill, OrderRequest, OrderRouter, PositionUpdate

log = logging.getLogger(__name__)


class PaperRouter:
    """Implements OrderRouter for simulated trading."""

    mode = "paper"

    def __init__(
        self,
        starting_equity: float,
        instruments: Dict[str, dict],   # display -> {pip_size, pip_value}
        ledger_path: str = "state/paper_ledger.json",
        slippage_pips: float = 1.0,
        commission_per_lot: float = 7.0,   # round-turn per std lot (account ccy)
        scale_out: bool = True,            # staged partial-TP + trailing management
        alert=None,                        # callable(text) for CRITICAL invariant alerts (e.g. Telegram)
        spread_stress: Optional[dict] = None,  # optional news/rollover widening model (default off)
    ) -> None:
        self.starting_equity = starting_equity
        self.equity_value = starting_equity
        self.instruments = instruments
        self.ledger_path = Path(ledger_path)
        self.slippage_pips = slippage_pips
        self.commission_per_lot = commission_per_lot
        self.scale_out = scale_out
        self.alert = alert
        # Optional synthetic spread-stress (OFF by default → real broker spread
        # only). When enabled, multiplies the effective spread during configured
        # windows so you can rehearse a news / rollover stop-out on demand.
        self.spread_stress: dict = spread_stress or {}

        self._open: List[OrderFill] = []
        self._closed: List[PositionUpdate] = []

        self._load()

    # --------------------------------------------------------------------- #
    # OrderRouter interface
    # --------------------------------------------------------------------- #
    def submit(self, req: OrderRequest) -> Optional[OrderFill]:
        inst = self.instruments.get(req.symbol)
        if not inst:
            log.warning("Paper: unknown instrument %s; rejecting", req.symbol)
            return None

        # Real execution model. MT5 bars/quotes are BID prices and signal levels
        # are computed from them, so:
        #   - a BUY fills at the ASK  = entry + spread   (spread paid at entry)
        #   - a SELL fills at the BID = entry            (spread paid at EXIT —
        #     see the ask-adjusted exit checks in _manage_position/_plain_exit)
        # The broker's LIVE spread captured at signal time is preferred;
        # slippage_pips × pip_size is only the fallback proxy when the feed
        # hasn't supplied bid/ask for this symbol yet.
        slip = self.slippage_pips * inst["pip_size"]
        live_spread = float(getattr(req, "spread", 0.0) or 0.0)
        eff_spread = live_spread if live_spread > 0 else slip
        if req.side == "buy":
            fill_price = req.entry + eff_spread
        else:
            fill_price = req.entry

        fill = OrderFill(
            ticket=req.ticket,
            symbol=req.symbol,
            side=req.side,
            lots=req.lots,
            fill_price=round(fill_price, 5),
            fill_time=_now_iso(),
            sl=req.sl,
            tp=req.tp,
            setup=req.setup,
            timeframe=req.timeframe,
            init_sl=req.sl,
            r=round(abs(round(fill_price, 5) - req.sl), 5),
            orig_lots=req.lots,
            partial_done=False,
            sl_stage=0,
            # Reference bar for the look-ahead guard. Prefer the engine-supplied
            # latest-closed-bar time; fall back to the signal's detection bar.
            entry_bar_time=(getattr(req, "entry_bar_time", "") or
                            getattr(req, "detected_at", "") or ""),
            tick_value=getattr(req, "tick_value", 0.0) or 0.0,
            tick_size=getattr(req, "tick_size", 0.0) or 0.0,
            risked_money=getattr(req, "risked_money", 0.0) or 0.0,
            sizing_basis=getattr(req, "sizing_basis", "") or "",
            spread=round(eff_spread, 6),
        )
        self._open.append(fill)
        self._save()
        log.info("Paper fill: %s %s %.2f @ %.5f — spread %.5f (%s), risked %.2f, basis: %s",
                 fill.side, fill.symbol, fill.lots, fill.fill_price,
                 eff_spread, "live" if live_spread > 0 else "fallback",
                 fill.risked_money, fill.sizing_basis or "(none recorded)")
        return fill

    def open_positions(self) -> List[OrderFill]:
        return list(self._open)

    def on_bar(self, symbol: str, bar: Bar) -> List[PositionUpdate]:
        """For every open position on `symbol`, advance the scale-out/trailing
        manager and record any (partial or full) closes this bar."""
        closures: List[PositionUpdate] = []
        still_open: List[OrderFill] = []
        for pos in self._open:
            # Only manage a position with bars of ITS OWN timeframe — otherwise a
            # 1d bar would (wrongly) trigger SL/TP/scale-out on a 15m or 4h trade.
            if pos.symbol != symbol or (pos.timeframe and bar.timeframe
                                        and pos.timeframe != bar.timeframe):
                still_open.append(pos)
                continue
            # LOOK-AHEAD GUARD: a position may only be managed by bars that
            # closed STRICTLY AFTER its entry. Without this, the entry bar itself
            # (or a backfill/forming-bar replay) books TP/SL instantly, producing
            # exits stamped at or before the entry. Mirrors strategy/shadow.py.
            #
            # Reference = entry_bar_time, falling back to fill_time for LEGACY
            # positions opened before this field existed. The fallback is
            # essential: without it, a position carried across a restart (no
            # entry_bar_time) is unguarded and a stale backfill bar closes it at
            # a bogus historical timestamp — exactly the CADJPY-runner incident.
            ref = pos.entry_bar_time or pos.fill_time
            if ref and bar.time <= ref:
                still_open.append(pos)
                continue
            ups = self._manage_position(pos, bar)
            for u in ups:
                closures.append(u)
                self._closed.append(u)
                self.equity_value += u.pnl
            if pos.lots > 1e-9:          # still has remaining lots open
                still_open.append(pos)
        self._open = still_open
        if closures:
            self._save()
        return closures

    def flatten_all(self, reason: str) -> List[PositionUpdate]:
        closures: List[PositionUpdate] = []
        for pos in self._open:
            inst = self.instruments.get(pos.symbol, {})
            # Market close at current quote: sells buy back at the ask.
            exit_now = pos.fill_price + ((getattr(pos, "spread", 0.0) or 0.0)
                                         if pos.side == "sell" else 0.0)
            pnl = _pnl(pos, exit_now, inst, self.commission_per_lot)
            risk = abs(pos.fill_price - pos.sl)
            achieved_r = 0.0
            update = PositionUpdate(
                ticket=pos.ticket,
                symbol=pos.symbol,
                side=pos.side,
                lots=pos.lots,
                entry=pos.fill_price,
                exit=round(exit_now, 5),
                pnl=round(pnl, 2),
                rr=round(achieved_r, 2),
                close_time=_now_iso(),
                close_reason=reason,
                setup=pos.setup,
                timeframe=pos.timeframe,
                sl=pos.sl,
                tp=pos.tp,
                entry_time=pos.fill_time,
            )
            closures.append(update)
            self._closed.append(update)
            self.equity_value += update.pnl
        self._open = []
        if closures:
            self._save()
        return closures

    def equity(self) -> float:
        return self.equity_value

    def closed_trades(self) -> List[PositionUpdate]:
        return list(self._closed)

    def modify_sl(self, ticket: int, new_sl: float, reason: str = "") -> bool:
        """Move an open paper position's stop (news agent SL-to-cost / trail).

        Safety rail: the stop may only move in the PROTECTIVE direction
        (up for buys, down for sells) — a command can tighten risk, never
        widen it. Returns True if a position was modified."""
        for pos in self._open:
            if pos.ticket != ticket or pos.lots <= 1e-9:
                continue
            protective = (new_sl > pos.sl) if pos.side == "buy" else (new_sl < pos.sl)
            if not protective:
                log.info("Paper modify_sl #%s rejected: %.5f would widen risk (current %.5f)",
                         ticket, new_sl, pos.sl)
                return False
            old = pos.sl
            pos.sl = round(float(new_sl), 5)
            pos.sl_stage = max(pos.sl_stage, 1)
            self._save()
            log.info("Paper SL moved #%s %s: %.5f → %.5f (%s)",
                     ticket, pos.symbol, old, pos.sl, reason or "news agent")
            return True
        return False

    def close_at_market(self, ticket: int, bid_price: float,
                        reason: str = "news_cut_loss") -> List[PositionUpdate]:
        """Close a position's full remaining size at the current market quote
        (news-agent cut-loss). Bars/quotes are BID: buys close at bid, sells
        buy back at ask = bid + spread captured at fill."""
        out: List[PositionUpdate] = []
        if not bid_price or bid_price <= 0:
            return out
        from types import SimpleNamespace
        shim = SimpleNamespace(time=_now_iso())     # _close_portion needs .time only
        for pos in self._open:
            if pos.ticket != ticket or pos.lots <= 1e-9:
                continue
            exit_price = bid_price + ((getattr(pos, "spread", 0.0) or 0.0)
                                      if pos.side == "sell" else 0.0)
            u = self._close_portion(pos, pos.lots, exit_price, shim, reason)
            out.append(u)
            self._closed.append(u)
            self.equity_value += u.pnl
            pos.lots = 0.0
            log.info("Paper cut-loss #%s %s: closed at %.5f (%s), pnl %.2f",
                     ticket, pos.symbol, exit_price, reason, u.pnl)
            break
        self._open = [p for p in self._open if p.lots > 1e-9]
        if out:
            self._save()
        return out

    def reset(self) -> None:
        """Wipe all positions and trade history; restore equity to starting balance."""
        self._open   = []
        self._closed = []
        self.equity_value = self.starting_equity
        # Remove persisted ledger so it doesn't reload on next restart
        if self.ledger_path.exists():
            self.ledger_path.unlink()
        self._save()
        log.info("Paper ledger reset — equity restored to %.2f", self.starting_equity)

    # --------------------------------------------------------------------- #
    # Internals
    # --------------------------------------------------------------------- #
    def _manage_position(self, pos: OrderFill, bar: Bar) -> List[PositionUpdate]:
        """Advance the staged scale-out + trailing rules for one position on one
        bar, mutating the position (lots reduced, SL moved) and returning any
        closes (partial and/or final). Sets pos.lots = 0 when fully closed.

        Rules (R = initial risk = |entry - init_sl|):
          - reach 1:2  → close 50% of the ORIGINAL lots, move SL to break-even
          - reach 1:3  → move SL to entry +1R (lock +1R)
          - reach 1:4  → move SL to entry +2R (lock +2R)
          - remainder then rides until the (trailed) SL is hit
        """
        sign = 1 if pos.side == "buy" else -1
        entry = pos.fill_price
        R = pos.r if pos.r > 0 else abs(entry - pos.init_sl)
        updates: List[PositionUpdate] = []

        # Bars are BID. A sell position EXITS at the ASK, so every sell-side
        # price test is shifted by the spread. This is the DYNAMIC spread for
        # THIS bar (live-sampled or EA per-bar max, ×stress), not the entry
        # snapshot — so news / rollover widening can realistically trigger it.
        s = self._eff_spread(pos, bar)
        base_s = getattr(pos, "spread", 0.0) or 0.0   # the "normal" entry spread
        pos.max_spread_seen = max(getattr(pos, "max_spread_seen", 0.0) or 0.0, s)

        # Fallback to plain SL/TP if we can't compute R or scale-out is disabled.
        if R <= 0 or not self.scale_out:
            u = self._plain_exit(pos, bar)
            if u is not None:
                pos.lots = 0.0
                updates.append(u)
            return updates

        # Favorable excursion this bar, in R multiples (ask-based for sells).
        fav_R = (bar.high - entry) / R if pos.side == "buy" else (entry - (bar.low + s)) / R

        # Stage 1 — 1:2 → book 50% of original lots + SL to break-even.
        if not pos.partial_done and fav_R >= 2.0:
            level = round(entry + sign * 2 * R, 5)
            half = min(round(pos.orig_lots * 0.5, 2), pos.lots)
            if half > 0:
                updates.append(self._close_portion(pos, half, level, bar, "tp_partial",
                                                   exit_spread=s))
                pos.lots = round(pos.lots - half, 2)
            pos.partial_done = True
            pos.sl = round(entry, 5)            # break-even
            pos.sl_stage = max(pos.sl_stage, 1)

        # Stage 2 — 1:3 → SL to +1R.
        if pos.sl_stage < 2 and fav_R >= 3.0:
            pos.sl = round(entry + sign * 1 * R, 5)
            pos.sl_stage = 2

        # Stage 3 — 1:4 → SL to +2R.
        if pos.sl_stage < 3 and fav_R >= 4.0:
            pos.sl = round(entry + sign * 2 * R, 5)
            pos.sl_stage = 3

        # Exit check on the (possibly updated) SL. With scale-out on there is no
        # fixed TP close — the remainder rides the trailed stop.
        # Buy exits at BID (bar as-is); sell exits at ASK (bar.high + spread).
        if pos.lots > 1e-9:
            stop_hit = (bar.low <= pos.sl) if pos.side == "buy" else (bar.high + s >= pos.sl)
            if stop_hit:
                # spread-induced = a sell stop that ONLY triggered because the
                # spread widened past the entry baseline (news / rollover).
                induced = (pos.side == "sell"
                           and (bar.high + base_s) < pos.sl <= (bar.high + s))
                updates.append(self._close_portion(pos, pos.lots, pos.sl, bar,
                                                   self._sl_reason(pos),
                                                   exit_spread=s, spread_induced=induced))
                pos.lots = 0.0
        return updates

    def _plain_exit(self, pos: OrderFill, bar: Bar) -> Optional[PositionUpdate]:
        """Legacy single SL/TP full close (used when scale-out is off).
        Bars are BID: buys exit on bid, sells exit on ask = bid + spread."""
        s = self._eff_spread(pos, bar)
        base_s = getattr(pos, "spread", 0.0) or 0.0
        pos.max_spread_seen = max(getattr(pos, "max_spread_seen", 0.0) or 0.0, s)
        exit_price = None
        reason = ""
        induced = False
        if pos.side == "buy":
            if bar.low <= pos.sl:
                exit_price, reason = pos.sl, "sl"
            elif bar.high >= pos.tp:
                exit_price, reason = pos.tp, "tp"
        else:
            if bar.high + s >= pos.sl:
                exit_price, reason = pos.sl, "sl"
                induced = (bar.high + base_s) < pos.sl <= (bar.high + s)
            elif bar.low + s <= pos.tp:
                exit_price, reason = pos.tp, "tp"
        if exit_price is None:
            return None
        return self._close_portion(pos, pos.lots, exit_price, bar, reason,
                                   exit_spread=s, spread_induced=induced)

    def _sl_reason(self, pos: OrderFill) -> str:
        """Why a stop got hit, given which stage the SL is at."""
        return {0: "sl", 1: "be", 2: "trail", 3: "trail"}.get(pos.sl_stage, "sl")

    # ------------------------------------------------------------------ #
    # Dynamic spread
    # ------------------------------------------------------------------ #
    def _eff_spread(self, pos: OrderFill, bar: Bar) -> float:
        """Effective spread (price units) to use for THIS bar's exit checks:
        the bar's own spread if known (EA per-bar max or live-sampled), else the
        entry snapshot — then multiplied by the stress factor for the bar's time.
        """
        base = getattr(bar, "spread", 0.0) or 0.0
        if base <= 0.0:
            base = getattr(pos, "spread", 0.0) or 0.0
        return base * self._stress_multiplier(getattr(bar, "time", "") or "")

    def _stress_multiplier(self, bar_time: str) -> float:
        """Synthetic widening factor for the bar's UTC time. 1.0 unless the
        stress model is enabled AND the time falls in a configured window."""
        cfg = self.spread_stress or {}
        if not cfg.get("enabled", False):
            return 1.0
        windows = cfg.get("windows") or []
        if not windows or len(bar_time) < 16:
            return 1.0
        hhmm = bar_time[11:16]          # 'YYYY-MM-DDTHH:MM...' → 'HH:MM'
        for w in windows:
            start, end = w.get("start", ""), w.get("end", "")
            if not start or not end:
                continue
            inside = (start <= hhmm < end) if start <= end else (hhmm >= start or hhmm < end)
            if inside:
                return float(w.get("multiplier", cfg.get("multiplier", 1.0)) or 1.0)
        return 1.0

    def _close_portion(self, pos: OrderFill, lots: float, exit_price: float,
                       bar: Bar, reason: str, exit_spread: float = 0.0,
                       spread_induced: bool = False) -> PositionUpdate:
        """Build a closed-trade record for `lots` of `pos` exiting at `exit_price`."""
        inst = self.instruments.get(pos.symbol, {"pip_size": 0.0001, "pip_value": 10.0})
        pnl = _pnl(pos, exit_price, inst, self.commission_per_lot, lots=lots)
        R = pos.r if pos.r > 0 else abs(pos.fill_price - pos.init_sl)
        sign = 1 if pos.side == "buy" else -1
        achieved_r = ((exit_price - pos.fill_price) * sign) / R if R > 0 else 0.0
        self._check_sizing_invariant(pos, lots, achieved_r, pnl)
        update = PositionUpdate(
            ticket=pos.ticket,
            symbol=pos.symbol,
            side=pos.side,
            lots=round(lots, 2),
            entry=pos.fill_price,
            exit=round(exit_price, 5),
            pnl=round(pnl, 2),
            rr=round(achieved_r, 2),
            close_time=bar.time,
            close_reason=reason,
            setup=pos.setup,
            timeframe=pos.timeframe,
            sl=pos.init_sl,
            tp=pos.tp,
            # Bar-clock entry reference so entry_time and close_time share one
            # time axis and are directly comparable (fall back to wall-clock
            # fill_time only if no entry bar was recorded).
            entry_time=(pos.entry_bar_time or pos.fill_time),
            exit_spread=round(exit_spread, 6),
            spread_induced=bool(spread_induced),
        )
        self._check_time_invariant(update)
        if spread_induced:
            # Visibility: this is the live-realism case the user asked to see —
            # a stop that fired only because the spread widened (news/rollover).
            log.warning(
                "SPREAD-INDUCED STOP %s #%s: %s hit at spread %.5f (entry baseline "
                "%.5f, widest seen %.5f) — would NOT have triggered at normal spread",
                pos.symbol, pos.ticket, reason, exit_spread,
                getattr(pos, "spread", 0.0) or 0.0,
                getattr(pos, "max_spread_seen", 0.0) or 0.0)
        return update

    def _check_time_invariant(self, update: PositionUpdate) -> None:
        """Guarantee every bar-driven exit closes STRICTLY AFTER its entry.

        A violation means a position was filled against the wrong bar (the
        look-ahead bug). We refuse to silently record it: this is the regression
        the entry-bar guard in on_bar() exists to prevent.
        """
        et, ct = update.entry_time, update.close_time
        if et and ct and ct <= et:
            msg = (f"TIME INVARIANT VIOLATION {update.symbol} #{update.ticket}: "
                   f"close_time {ct} <= entry_time {et} — look-ahead/same-bar fill")
            log.critical(msg)
            if self.alert:
                try:
                    self.alert("🚨 " + msg)
                except Exception as exc:  # noqa: BLE001
                    log.warning("Invariant alert failed: %s", exc)

    def _check_sizing_invariant(self, pos: OrderFill, lots: float,
                                achieved_r: float, pnl: float) -> None:
        """Verify |pnl| ≈ |R| × risked_money for this closed leg (±10%).

        `risked_money` was recorded at fill for the ORIGINAL lots, so the leg's
        share is risked_money × (lots / orig_lots). Catches sizing/P&L basis
        mismatches (e.g. tick_value in the wrong currency, index mis-sizing)
        the moment they happen instead of after the equity curve is ruined.
        Skipped for near-break-even closes where commission dominates.
        """
        risked = getattr(pos, "risked_money", 0.0) or 0.0
        orig = pos.orig_lots or pos.lots
        if risked <= 0 or orig <= 0 or abs(achieved_r) < 0.25:
            return
        risked_leg = risked * (lots / orig)
        expected = abs(achieved_r) * risked_leg
        # Tolerance: 10% of expected, but never tighter than commission + slippage noise.
        tol = max(0.10 * expected, self.commission_per_lot * lots + 1.0)
        if abs(abs(pnl) - expected) <= tol:
            return
        msg = (f"SIZING INVARIANT VIOLATION {pos.symbol} #{pos.ticket}: "
               f"|pnl|={abs(pnl):.2f} but |R|×risked={expected:.2f} "
               f"(R={achieved_r:+.2f}, risked_leg={risked_leg:.2f}, lots={lots:.2f}/{orig:.2f}, "
               f"basis: {pos.sizing_basis or 'unknown'}) — sizing and P&L disagree, check tick_value units")
        log.critical(msg)
        if self.alert:
            try:
                self.alert("🚨 " + msg)
            except Exception as exc:  # noqa: BLE001
                log.warning("Invariant alert failed: %s", exc)

    def reversal_exit(self, ticket: int, exit_price: float, bar: Bar, reason: str,
                      close_fraction: float = 0.5, move_sl_to_be: bool = True
                      ) -> List[PositionUpdate]:
        """Close part of a position early because an opposing pattern formed on a
        lower timeframe, then (optionally) pull the runner's stop to break-even.

        Closes `close_fraction` of the CURRENT remaining lots. close_time is set
        to the triggering lower-timeframe bar's time (so the audit can recognise
        this as an intended lower-TF exit, not the cross-timeframe bug)."""
        out: List[PositionUpdate] = []
        for pos in self._open:
            if pos.ticket != ticket or pos.lots <= 1e-9:
                continue
            # Market close: a sell buys back at the ASK (bars/close are bid).
            if pos.side == "sell":
                exit_price = exit_price + (getattr(pos, "spread", 0.0) or 0.0)
            lots = round(pos.lots * close_fraction, 2)
            if lots <= 0:                 # too small to split -> close the lot whole
                lots = pos.lots
            lots = min(lots, pos.lots)
            u = self._close_portion(pos, lots, exit_price, bar, reason)
            out.append(u)
            self._closed.append(u)
            self.equity_value += u.pnl
            pos.lots = round(pos.lots - lots, 2)
            if move_sl_to_be and pos.lots > 1e-9:
                better = (pos.side == "buy" and pos.sl < pos.fill_price) or \
                         (pos.side == "sell" and pos.sl > pos.fill_price)
                if better:
                    pos.sl = pos.fill_price
                    pos.sl_stage = max(pos.sl_stage, 1)
            break
        self._open = [p for p in self._open if p.lots > 1e-9]
        if out:
            self._save()
        return out

    def _save(self) -> None:
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot = {
            "starting_equity": self.starting_equity,
            "equity": self.equity_value,
            "open": [asdict(p) for p in self._open],
            "closed": [asdict(p) for p in self._closed],
            "saved_at": _now_iso(),
        }
        self.ledger_path.write_text(json.dumps(snapshot, indent=2))

    def _load(self) -> None:
        if not self.ledger_path.exists():
            return
        try:
            raw = json.loads(self.ledger_path.read_text())
            self.equity_value = float(raw.get("equity", self.starting_equity))
            self._open   = [OrderFill(**r)      for r in raw.get("open", [])]
            self._closed = [PositionUpdate(**r) for r in raw.get("closed", [])]
            log.info("Loaded paper ledger: %d open, %d closed, equity %.2f",
                     len(self._open), len(self._closed), self.equity_value)
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not load paper ledger: %s", exc)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _pnl(pos: OrderFill, exit_price: float, inst: dict, commission_per_lot: float,
         lots: Optional[float] = None) -> float:
    """P&L in account currency for `lots` (defaults to pos.lots).

    Uses the SAME basis the position was sized with: prefer the broker-exact
    tick value captured at fill time (so sizing and P&L always agree — this is
    what stops indices/crypto from being mis-sized 10x), falling back to the
    config pip_size/pip_value only when no tick data is present.
    """
    use_lots = pos.lots if lots is None else lots
    sign = 1 if pos.side == "buy" else -1
    move = (exit_price - pos.fill_price) * sign
    tv = getattr(pos, "tick_value", 0.0)
    ts = getattr(pos, "tick_size", 0.0)
    if tv and ts and tv > 0 and ts > 0:
        gross = (move / ts) * tv * use_lots
    else:
        pip_size  = inst.get("pip_size", 0.0001)
        pip_value = inst.get("pip_value", 10.0)
        gross = (move / pip_size) * pip_value * use_lots
    commission = commission_per_lot * use_lots
    return gross - commission
