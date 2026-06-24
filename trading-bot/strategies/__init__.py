"""Strategy-plugin registry.

The engine is strategy-agnostic: `engine_loop` asks `active(params)` which
strategies run, then `generate_all(...)` for each. EVERY returned signal goes
through the SAME engine rails (`engine.try_execute`). Global rails live in the
engine, never per strategy — a plugin cannot widen a stop, raise risk above the
cap, or bypass concurrency / loss limits.

SLC is plugin #1 and delegates to `strategy.analyze`; its output must be exactly
what it was before this layer existed. New strategies must NOT change it.
"""
from typing import Any, Dict, List, Optional

import strategy as _slc


class Strategy:
    """Base plugin. Subclass, set `name`, implement `generate`."""
    name = "base"

    def is_enabled(self, params: Dict[str, Any]) -> bool:
        """Read the `strategy_<name>_enabled` setting (default-on)."""
        return bool(params.get("strategy_%s_enabled" % self.name, True))

    def modes(self, params: Dict[str, Any]) -> List[str]:
        """Trade-speeds this strategy runs. Default: whatever the engine runs."""
        return list(params.get("modes", []))

    def generate(self, symbol: str, trade_mode: str,
                 bars_by_tf: Dict[str, List[Dict]], params: Dict[str, Any],
                 spread: float = 0.0,
                 live_price: Optional[float] = None) -> Dict[str, Any]:
        raise NotImplementedError


class SLCStrategy(Strategy):
    """Structure · Liquidity · Confirmation — delegates to strategy.analyze."""
    name = "slc"

    def generate(self, symbol, trade_mode, bars_by_tf, params,
                 spread=0.0, live_price=None) -> Dict[str, Any]:
        return _slc.analyze(symbol, trade_mode, bars_by_tf, params,
                            spread=spread, live_price=live_price)


# Append new plugins here. Each clears its OWN >=50-trade positive-expectancy
# paper gate (paper/shadow first) before it trades live (see CLAUDE.md).
REGISTRY: List[Strategy] = [SLCStrategy()]


def active(params: Dict[str, Any]) -> List[Strategy]:
    """Plugins enabled by their `strategy_<name>_enabled` flag."""
    return [s for s in REGISTRY if s.is_enabled(params)]


def generate_all(symbol: str, trade_mode: str,
                 bars_by_tf: Dict[str, List[Dict]], params: Dict[str, Any],
                 spread: float = 0.0, live_price: Optional[float] = None):
    """Run every active strategy that trades this mode. Returns a list of
    `(strategy_name, result)` where result is the `{signal, info}` shape
    `analyze` returns; info is tagged with the strategy name for dashboard
    transparency. With only SLC active this yields exactly one result whose
    `result` is byte-for-byte what `strategy.analyze` returned (plus the tag)."""
    out = []
    for s in active(params):
        if trade_mode not in s.modes(params):
            continue
        res = s.generate(symbol, trade_mode, bars_by_tf, params,
                         spread=spread, live_price=live_price)
        res.setdefault("info", {})["strategy"] = s.name
        out.append((s.name, res))
    return out
