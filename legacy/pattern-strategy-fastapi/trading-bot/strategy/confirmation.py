"""Confirmation rules.

Two checks from the playbook:

1.  Candle anatomy at the trigger bar:
    - Body / range ratio must be >= min_body_ratio (default 0.70)
    - Opposing wick / range ratio must be <= max_opposing_wick_ratio (0.30)
    - "Opposing wick" = upper wick for buys, lower wick for sells

2.  Momentum (slow approach):
    - The market should approach the level *slowly*. We compare the ATR of
      the last `lookback_bars` bars to the prior baseline ATR — if the
      approach is too violent (ratio above threshold), reject.

Each check returns a structured `CheckResult` (name, passed, value, threshold,
detail). `confirm()` returns (passed, checks) so the engine can log EXACTLY
which check failed and by how much — previously only the last reason string
survived, so candle-anatomy failures were logged as "✓ Slow approach OK".
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import List, Optional, Tuple

from marketdata.base import Bar
from .patterns import Signal


@dataclass
class ConfirmationConfig:
    # Shadow data (12 Jun, n=278 candle_anatomy rejections, avgR +1.68) showed
    # the 0.70 body-ratio threshold was starving the strategy. Lowered to 0.55
    # — still requires a meaningful directional body, just not a near-perfect
    # marubozu. Shadow mode continues measuring what's still rejected at 0.55.
    min_body_ratio: float = 0.55
    max_opposing_wick_ratio: float = 0.30
    momentum_lookback_bars: int = 5
    momentum_max_atr_ratio: float = 1.20


@dataclass
class CheckResult:
    """Structured outcome of one confirmation check."""
    name: str                       # "candle_anatomy" | "momentum"
    passed: bool
    value: Optional[float] = None   # measured quantity (body ratio, ATR ratio, ...)
    threshold: Optional[float] = None
    detail: str = ""                # human-readable

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def note(self) -> str:
        """Dashboard-style annotated string (kept for display compatibility)."""
        return ("✓ " if self.passed else "✗ ") + self.detail


def check_candle_anatomy(bar: Bar, side: str, cfg: ConfirmationConfig) -> CheckResult:
    name = "candle_anatomy"
    rng = bar.high - bar.low
    if rng <= 0:
        return CheckResult(name, False, value=0.0, threshold=cfg.min_body_ratio,
                           detail="Zero-range candle")
    body = abs(bar.close - bar.open)
    upper_wick = bar.high - max(bar.close, bar.open)
    lower_wick = min(bar.close, bar.open) - bar.low

    body_ratio = body / rng
    if body_ratio < cfg.min_body_ratio:
        return CheckResult(name, False, value=round(body_ratio, 3),
                           threshold=cfg.min_body_ratio,
                           detail=f"Body ratio {body_ratio:.2f} < {cfg.min_body_ratio:.2f}")

    # For a sell signal we want a bearish rejection candle: close < open,
    # tall body, small upper wick (the rejection of the high).
    if side == "sell":
        if bar.close >= bar.open:
            return CheckResult(name, False, value=round(body_ratio, 3),
                               threshold=cfg.min_body_ratio,
                               detail="Sell trigger needs a bearish (red) candle")
        opposing = upper_wick / rng
        if opposing > cfg.max_opposing_wick_ratio:
            return CheckResult(name, False, value=round(opposing, 3),
                               threshold=cfg.max_opposing_wick_ratio,
                               detail=f"Upper wick {opposing:.2f} > {cfg.max_opposing_wick_ratio:.2f}")
    else:  # buy
        if bar.close <= bar.open:
            return CheckResult(name, False, value=round(body_ratio, 3),
                               threshold=cfg.min_body_ratio,
                               detail="Buy trigger needs a bullish (green) candle")
        opposing = lower_wick / rng
        if opposing > cfg.max_opposing_wick_ratio:
            return CheckResult(name, False, value=round(opposing, 3),
                               threshold=cfg.max_opposing_wick_ratio,
                               detail=f"Lower wick {opposing:.2f} > {cfg.max_opposing_wick_ratio:.2f}")

    return CheckResult(name, True, value=round(body_ratio, 3), threshold=cfg.min_body_ratio,
                       detail=f"Candle OK (body {body_ratio:.2f}, opposing wick {opposing:.2f})")


def _atr(bars: List[Bar]) -> float:
    if len(bars) < 2:
        return 0.0
    total = 0.0
    for i in range(1, len(bars)):
        prev_close = bars[i - 1].close
        tr = max(
            bars[i].high - bars[i].low,
            abs(bars[i].high - prev_close),
            abs(bars[i].low - prev_close),
        )
        total += tr
    return total / (len(bars) - 1)


def check_momentum(bars: List[Bar], cfg: ConfirmationConfig) -> CheckResult:
    """Approach must be slow: recent ATR <= ratio * prior baseline ATR."""
    name = "momentum"
    n = cfg.momentum_lookback_bars
    if len(bars) < n + 14:
        return CheckResult(name, True, threshold=cfg.momentum_max_atr_ratio,
                           detail="Insufficient history for momentum check (allowing)")
    recent = bars[-n:]
    baseline = bars[-(n + 14): -n]
    recent_atr = _atr(recent)
    baseline_atr = _atr(baseline)
    if baseline_atr <= 0:
        return CheckResult(name, True, threshold=cfg.momentum_max_atr_ratio,
                           detail="No baseline volatility (allowing)")
    ratio = recent_atr / baseline_atr
    if ratio > cfg.momentum_max_atr_ratio:
        return CheckResult(name, False, value=round(ratio, 3),
                           threshold=cfg.momentum_max_atr_ratio,
                           detail=f"Approach too fast (ATR ratio {ratio:.2f} > {cfg.momentum_max_atr_ratio:.2f})")
    return CheckResult(name, True, value=round(ratio, 3), threshold=cfg.momentum_max_atr_ratio,
                       detail=f"Slow approach OK (ATR ratio {ratio:.2f})")


def confirm(signal: Signal, bars: List[Bar],
            cfg: ConfirmationConfig) -> Tuple[bool, List[CheckResult]]:
    """Run all confirmation checks. Returns (passed, structured check results).

    Use `[c.note for c in checks]` for the human-readable annotations and
    `failed_check(checks)` for the first failing check's name.
    """
    checks: List[CheckResult] = [
        check_candle_anatomy(bars[-1], signal.side, cfg),
        check_momentum(bars, cfg),
    ]
    return all(c.passed for c in checks), checks


def failed_check(checks: List[CheckResult]) -> Optional[str]:
    """Name of the first failing check, or None if all passed."""
    for c in checks:
        if not c.passed:
            return c.name
    return None
