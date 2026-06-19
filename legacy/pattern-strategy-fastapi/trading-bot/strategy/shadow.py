"""Shadow mode — what would the rejected signals have done?

Every signal that passes detection but is rejected by a gate (confirmation,
htf_context, clarity, choppiness, correlation, news, risk) is registered here
and tracked forward on subsequent bars of its own symbol × timeframe to its
hypothetical TP or SL:

  - SL touched first        → outcome "loss",  r = -1
  - TP touched first        → outcome "win",   r = +planned RR
  - both in one bar         → "loss" (same conservative tie-break as PaperRouter)
  - max_bars without either → "timeout", r = mark-to-market at the last close

Resolved outcomes are appended to state/shadow_outcomes.jsonl; pending
entries are persisted to state/shadow_pending.json so a restart doesn't lose
them. Aggregate with tools/shadow_report.py — that report (expectancy BY
REJECTING CHECK) is the evidence for tuning the 0.70 body-ratio and 1.20
ATR-ratio thresholds, instead of intuition.

Best-effort: nothing in here may raise into the trading loop.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

log = logging.getLogger(__name__)


class ShadowTracker:
    def __init__(
        self,
        outcomes_path: str = "state/shadow_outcomes.jsonl",
        pending_path: str = "state/shadow_pending.json",
        max_bars: int = 100,
    ) -> None:
        self.outcomes_path = Path(outcomes_path)
        self.pending_path = Path(pending_path)
        self.max_bars = max_bars
        self._pending: List[dict] = []
        self._seq = 0
        self._load_pending()

    # ------------------------------------------------------------------ #
    # Registration (called by the engine's _reject funnel)
    # ------------------------------------------------------------------ #
    def register(self, sig, stage: str, failed: Optional[str] = None) -> None:
        """Start tracking a rejected signal. Near-duplicates (same symbol/tf/
        side/setup within ~0.1% of the same entry) are skipped."""
        try:
            entry = float(sig.entry)
            for p in self._pending:
                if (p["symbol"] == sig.symbol and p["tf"] == sig.timeframe
                        and p["side"] == sig.side and p["setup"] == sig.setup
                        and entry and abs(p["entry"] - entry) / entry < 0.001):
                    return
            self._seq += 1
            self._pending.append({
                "id": f"{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{self._seq}",
                "symbol": sig.symbol,
                "tf": sig.timeframe,
                "setup": sig.setup,
                "side": sig.side,
                "entry": entry,
                "sl": float(sig.sl),
                "tp": float(sig.tp),
                "rr": float(getattr(sig, "rr", 0.0) or 0.0),
                "clarity_score": float(getattr(sig, "clarity_score", 0.0) or 0.0),
                "stage": stage,
                "failed_check": failed,
                "registered_at": getattr(sig, "detected_at", "") or "",
                "entry_bar_time": "",   # set to the first post-registration bar (the entry)
                "bars_seen": 0,
            })
            self._save_pending()
        except Exception as exc:  # noqa: BLE001
            log.debug("Shadow register failed: %s", exc)

    # ------------------------------------------------------------------ #
    # Bar updates (called by the engine for every new closed bar)
    # ------------------------------------------------------------------ #
    def on_bar(self, bar) -> int:
        """Advance every pending shadow on this bar's symbol × timeframe.
        Returns the number of shadows resolved this bar."""
        resolved = 0
        try:
            still: List[dict] = []
            for p in self._pending:
                if p["symbol"] != bar.symbol or p["tf"] != (bar.timeframe or p["tf"]):
                    still.append(p)
                    continue
                # Ignore bars at/before registration (the trigger bar itself).
                if p["registered_at"] and bar.time <= p["registered_at"]:
                    still.append(p)
                    continue
                # The FIRST bar strictly after registration is the ENTRY bar —
                # the hypothetical fill happens at its open, so it must NOT also
                # resolve the trade. Record it and wait for the next bar. This
                # removes the look-ahead/same-bar win that inflated the win rate
                # (and also dedupes any forming-bar refresh with the same stamp).
                if not p.get("entry_bar_time"):
                    p["entry_bar_time"] = bar.time
                    still.append(p)
                    continue
                if bar.time <= p["entry_bar_time"]:
                    still.append(p)
                    continue
                p["bars_seen"] += 1
                outcome = self._outcome(p, bar)
                if outcome is None:
                    still.append(p)
                    continue
                self._write_outcome(p, *outcome, bar_time=bar.time)
                resolved += 1
            if resolved:
                self._pending = still
                self._save_pending()
            else:
                self._pending = still
        except Exception as exc:  # noqa: BLE001
            log.debug("Shadow on_bar failed: %s", exc)
        return resolved

    def pending_count(self) -> int:
        return len(self._pending)

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    def _outcome(self, p: dict, bar) -> Optional[tuple]:
        """(outcome, r) if this bar resolves the shadow, else None."""
        buy = p["side"] == "buy"
        sl_hit = (bar.low <= p["sl"]) if buy else (bar.high >= p["sl"])
        tp_hit = (bar.high >= p["tp"]) if buy else (bar.low <= p["tp"])
        if sl_hit:                       # both-in-one-bar → conservative loss
            return "loss", -1.0
        if tp_hit:
            return "win", p["rr"] or 0.0
        if p["bars_seen"] >= self.max_bars:
            risk = abs(p["entry"] - p["sl"])
            r = ((bar.close - p["entry"]) / risk if buy
                 else (p["entry"] - bar.close) / risk) if risk > 0 else 0.0
            return "timeout", round(r, 2)
        return None

    def _write_outcome(self, p: dict, outcome: str, r: float, bar_time: str) -> None:
        rec = dict(p)
        rec.update({
            "outcome": outcome,             # win | loss | timeout
            "r": round(r, 2),               # achieved R (planned RR on win, -1 on loss)
            "resolved_at": bar_time,
        })
        try:
            self.outcomes_path.parent.mkdir(parents=True, exist_ok=True)
            with self.outcomes_path.open("a") as fh:
                fh.write(json.dumps(rec) + "\n")
        except Exception as exc:  # noqa: BLE001
            log.warning("Shadow outcome write failed: %s", exc)

    def _save_pending(self) -> None:
        try:
            self.pending_path.parent.mkdir(parents=True, exist_ok=True)
            self.pending_path.write_text(json.dumps(self._pending, indent=1))
        except Exception as exc:  # noqa: BLE001
            log.debug("Shadow pending save failed: %s", exc)

    def _load_pending(self) -> None:
        try:
            if self.pending_path.exists():
                data = json.loads(self.pending_path.read_text())
                if isinstance(data, list):
                    self._pending = data
                    log.info("Shadow: restored %d pending signal(s)", len(data))
        except Exception as exc:  # noqa: BLE001
            log.warning("Shadow pending load failed: %s", exc)
