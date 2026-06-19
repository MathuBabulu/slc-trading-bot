"""Per-level signal cooldown.

The DT/DB detectors re-fire every tick while price hovers at a tested level —
in the 24h before this was added, ~1,600 of 2,239 logged signals were re-fires
of the same handful of levels. This module suppresses a new signal when one
for the SAME symbol × timeframe × side was already produced at a level within
`atr_mult × ATR` of the new one in the last `bars` bars.

Suppressions are reported back so the engine can emit `signal:deduped` with a
reference to the prior signal (the funnel stays auditable).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


@dataclass
class CooldownConfig:
    enabled: bool = True
    atr_mult: float = 0.5   # level proximity, in multiples of current ATR
    bars: int = 10          # how many bars a signalled level stays "hot"


class LevelCooldown:
    def __init__(self, cfg: Optional[CooldownConfig] = None) -> None:
        self.cfg = cfg or CooldownConfig()
        # (symbol, timeframe, side) -> list of (level, detected_at_iso)
        self._recent: Dict[Tuple[str, str, str], List[Tuple[float, str]]] = {}

    def check(self, symbol: str, timeframe: str, side: str, level: float,
              atr: float, bar_times: List[str]) -> Optional[dict]:
        """If a recent signal exists at (about) this level, return a dict
        describing the prior signal (=> suppress). Otherwise record this
        signal and return None (=> proceed).

        `bar_times` are the cached bar timestamps for (symbol, timeframe),
        oldest→newest; used to count how many bars have elapsed since a prior
        signal (robust across restarts and gaps, unlike a wall-clock TTL).
        """
        if not self.cfg.enabled:
            return None
        key = (symbol, timeframe, side)
        entries = self._recent.get(key, [])

        # Prune entries older than cfg.bars bars.
        kept: List[Tuple[float, str]] = []
        for lvl, t in entries:
            elapsed = sum(1 for bt in bar_times if bt > t)
            if elapsed <= self.cfg.bars:
                kept.append((lvl, t))

        tol = max(self.cfg.atr_mult * atr, 0.0)
        hit: Optional[dict] = None
        if tol > 0:
            for lvl, t in kept:
                if abs(level - lvl) <= tol:
                    hit = {"prior_level": lvl, "prior_detected_at": t,
                           "distance": round(abs(level - lvl), 6),
                           "tolerance": round(tol, 6)}
                    break

        if hit is None:
            # Record this signal's level as now-hot.
            now = bar_times[-1] if bar_times else ""
            kept.append((level, now))
        self._recent[key] = kept
        return hit
