"""Tests for the volume-profile confirmation module."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from marketdata.base import Bar
from strategy.volume_profile import (
    build_profile, cumulative_delta, volume_clarity_bonus,
)


def _bar(o, h, l, c, v, t="2026-06-15T00:00:00Z"):
    return Bar(symbol="X", timeframe="1h", time=t,
               open=o, high=h, low=l, close=c, volume=v)


def _cluster_bars():
    """Most volume parked around price 100.0 (up bars → positive delta proxy),
    a little low-volume noise up at ~101.0."""
    bars = []
    for i in range(40):
        # up bar (close > open) centred on 100.0
        bars.append(_bar(99.98, 100.05, 99.95, 100.02, 1000.0,
                         t=f"2026-06-15T{i:02d}:00:00Z"))
    for i in range(5):
        bars.append(_bar(101.00, 101.05, 100.95, 101.00, 20.0,
                         t=f"2026-06-16T{i:02d}:00:00Z"))
    return bars


def test_poc_sits_at_the_high_volume_price():
    prof = build_profile(_cluster_bars(), lookback=120, bins=50)
    assert prof.poc is not None
    assert abs(prof.poc - 100.0) < 0.10, f"POC {prof.poc} should be ~100.0"
    assert prof.va_low <= prof.poc <= prof.va_high
    assert prof.total_volume > 0


def test_empty_or_zero_volume_is_safe():
    assert build_profile([], 120, 50).poc is None
    zero = [_bar(100, 100.1, 99.9, 100, 0.0) for _ in range(10)]
    prof = build_profile(zero, 120, 50)
    assert prof.poc is None and prof.total_volume == 0.0
    assert cumulative_delta(zero) == (0.0, 0.0)


def test_delta_sign_follows_direction():
    up = [_bar(100, 100.2, 99.9, 100.1, 100.0) for _ in range(5)]
    dn = [_bar(100, 100.1, 99.8, 99.9, 100.0)]
    net, norm = cumulative_delta(up + dn, lookback=10)
    assert net > 0 and 0 < norm <= 1.0          # 5 up vs 1 down → net buy
    net2, norm2 = cumulative_delta(dn * 5 + up[:1], lookback=10)
    assert net2 < 0 and -1.0 <= norm2 < 0


def test_bonus_awarded_at_node_with_aligned_delta():
    bars = _cluster_bars()
    prof = build_profile(bars, 120, 50)
    # Entry exactly at POC, buy side, and recent delta is positive (up bars).
    bonus, reasons, readings = volume_clarity_bonus(
        prof.poc, "buy", bars, cfg={})
    assert bonus == 12.0 + 8.0, f"expected node+delta bonus, got {bonus}: {reasons}"
    assert any("POC" in r or "HVN" in r for r in reasons)
    assert any("delta" in r for r in reasons)
    assert "profile" in readings


def test_no_bonus_far_from_node_and_misaligned_delta():
    bars = _cluster_bars()
    prof = build_profile(bars, 120, 50)
    # Entry far above any node, and SELL while delta is positive → no confluence.
    far_entry = prof.va_high + 50 * (prof.bin_width or 1.0)
    bonus, reasons, _ = volume_clarity_bonus(far_entry, "sell", bars, cfg={})
    assert bonus == 0.0, f"expected no bonus, got {bonus}: {reasons}"


def test_bonus_never_exceeds_cap_and_returns_floats():
    bars = _cluster_bars()
    prof = build_profile(bars, 120, 50)
    bonus, _, _ = volume_clarity_bonus(prof.poc, "buy", bars, cfg={})
    assert isinstance(bonus, float)
    assert 0.0 <= bonus <= 20.0


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  PASS  {name}")
            passed += 1
    print(f"\n{passed} tests passed.")
