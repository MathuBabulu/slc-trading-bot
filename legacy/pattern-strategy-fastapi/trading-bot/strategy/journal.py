"""Per-trade journal — a permanent, self-contained record of every trade.

For each ticket we write one JSON file capturing everything needed to re-validate
the trade later, even after the live bar window has rolled off the server:

    state/trade_journal/<ticket>.json
    {
      "ticket", "symbol", "timeframe", "side", "setup",
      "entry_time", "entry_price", "sl", "tp", "rr",
      "signal":      { full detector signal, incl. notes + pattern geometry },
      "pattern_bars":[ {t,o,h,l,c,v}, ... ],   # the bars that FORMED the pattern
      "trade_bars":  [ {t,o,h,l,c,v}, ... ],   # every bar from entry through exit
      "exits":       [ {close_time, exit, lots, pnl, rr, reason}, ... ],
      "status":      "open" | "closed",
      "net_pnl":     <sum of exit pnl>
    }

Writes are best-effort: journalling never raises into the trading loop.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List

log = logging.getLogger(__name__)


class TradeJournal:
    def __init__(self, directory: str = "state/trade_journal") -> None:
        self.dir = Path(directory)
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:  # noqa: BLE001
            log.warning("Journal dir create failed: %s", exc)

    def _path(self, ticket: int) -> Path:
        return self.dir / f"{ticket}.json"

    def _read(self, ticket: int) -> Dict[str, Any]:
        p = self._path(ticket)
        if p.exists():
            try:
                return json.loads(p.read_text())
            except Exception:  # noqa: BLE001
                pass
        return {}

    def _write(self, ticket: int, rec: Dict[str, Any]) -> None:
        try:
            self._path(ticket).write_text(json.dumps(rec, indent=2))
        except Exception as exc:  # noqa: BLE001
            log.warning("Journal write failed for #%s: %s", ticket, exc)

    def open_trade(self, fill, sig, pattern_bars: List[dict]) -> None:
        """Record entry context + the bars that formed the pattern."""
        try:
            rec = {
                "ticket": fill.ticket,
                "symbol": fill.symbol,
                "timeframe": fill.timeframe,
                "side": fill.side,
                "setup": fill.setup,
                "entry_time": fill.fill_time,
                "entry_price": fill.fill_price,
                "sl": fill.sl,
                "tp": fill.tp,
                "rr": getattr(sig, "rr", 0.0),
                "lots": fill.orig_lots or fill.lots,
                "signal": sig.to_dict() if hasattr(sig, "to_dict") else {},
                "pattern_bars": list(pattern_bars or []),
                "trade_bars": [],
                "exits": [],
                "status": "open",
                "net_pnl": 0.0,
            }
            self._write(fill.ticket, rec)
        except Exception as exc:  # noqa: BLE001
            log.warning("Journal open_trade failed: %s", exc)

    def record_close(self, update, trade_bars: List[dict], still_open: bool) -> None:
        """Append an exit leg, refresh the in-between bars, update status."""
        try:
            rec = self._read(update.ticket)
            if not rec:                      # opened before journalling existed
                rec = {
                    "ticket": update.ticket, "symbol": update.symbol,
                    "timeframe": update.timeframe, "side": update.side,
                    "setup": update.setup, "entry_time": update.entry_time,
                    "entry_price": update.entry, "sl": update.sl, "tp": update.tp,
                    "signal": {}, "pattern_bars": [], "trade_bars": [], "exits": [],
                    "status": "open", "net_pnl": 0.0,
                }
            rec["exits"].append({
                "close_time": update.close_time,
                "exit": update.exit,
                "lots": update.lots,
                "pnl": update.pnl,
                "rr": update.rr,
                "reason": update.close_reason,
            })
            if trade_bars:
                rec["trade_bars"] = list(trade_bars)   # full entry..latest-close window
            rec["net_pnl"] = round(sum(e.get("pnl", 0.0) for e in rec["exits"]), 2)
            rec["status"] = "open" if still_open else "closed"
            self._write(update.ticket, rec)
        except Exception as exc:  # noqa: BLE001
            log.warning("Journal record_close failed: %s", exc)
