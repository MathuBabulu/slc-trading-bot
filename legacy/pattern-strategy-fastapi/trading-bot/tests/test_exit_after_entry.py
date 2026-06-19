"""Regression tests for the look-ahead / same-bar fill bug.

Bug (pre-fix): an open position was managed against ANY bar of its symbol+
timeframe, with no check that the bar closed after the entry. The entry bar
itself (or replayed backfill bars) booked TP/SL instantly, producing exits
timestamped at or BEFORE the entry.

Core invariant under test: **every bar-driven exit must close STRICTLY AFTER
its entry bar.**  These tests fail against the old code and pass against the
fixed code (the entry-bar guard in PaperRouter.on_bar + the entry-bar offset in
ShadowTracker.on_bar).
"""
from __future__ import annotations

import os
import sys

# Make the package importable when run directly or via pytest from any cwd.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from marketdata.base import Bar
from execution.base import OrderRequest
from execution.paper import PaperRouter

INSTRUMENTS = {"EURUSD": {"pip_size": 0.0001, "pip_value": 10.0}}
ENTRY_BAR = "2026-06-15T08:00:00Z"


def _router(tmp_path, scale_out=False):
    return PaperRouter(
        starting_equity=100_000,
        instruments=INSTRUMENTS,
        ledger_path=str(tmp_path / "ledger.json"),
        slippage_pips=0.0,
        commission_per_lot=0.0,
        scale_out=scale_out,
    )


def _buy_req():
    # entry 1.10000, risk 10 pips, TP at +20 pips (2R)
    return OrderRequest(
        ticket=1, symbol="EURUSD", side="buy", lots=0.10,
        entry=1.10000, sl=1.09900, tp=1.10200,
        setup="DB", timeframe="1h",
        detected_at="2026-06-15T07:00:00Z",
        entry_bar_time=ENTRY_BAR,
    )


def _bar(time, high, low, *, tf="1h"):
    return Bar(symbol="EURUSD", timeframe=tf, time=time,
               open=1.10000, high=high, low=low, close=(high + low) / 2,
               volume=1000.0)


# --------------------------------------------------------------------------- #
# PaperRouter
# --------------------------------------------------------------------------- #
def test_entry_bar_cannot_close_position(tmp_path):
    """A bar with the SAME timestamp as the entry bar must NOT fill TP/SL."""
    r = _router(tmp_path)
    r.submit(_buy_req())
    # This bar's range blows through the TP, but it IS the entry bar.
    closures = r.on_bar("EURUSD", _bar(ENTRY_BAR, high=1.10500, low=1.09950))
    assert closures == [], "entry bar must not close the position (look-ahead)"
    assert len(r.open_positions()) == 1


def test_bar_before_entry_cannot_close_position(tmp_path):
    """A bar that closed BEFORE the entry (e.g. replayed backfill) is ignored."""
    r = _router(tmp_path)
    r.submit(_buy_req())
    closures = r.on_bar("EURUSD", _bar("2026-06-15T06:00:00Z",
                                       high=1.10500, low=1.09800))
    assert closures == [], "a pre-entry bar must never fill the position"
    assert len(r.open_positions()) == 1


def test_later_bar_closes_and_exit_is_after_entry(tmp_path):
    """A bar strictly after the entry bar fills TP, and close_time > entry_time."""
    r = _router(tmp_path)
    r.submit(_buy_req())
    # Entry bar first (no-op), then a genuinely later bar that touches TP.
    r.on_bar("EURUSD", _bar(ENTRY_BAR, high=1.10010, low=1.09990))
    closures = r.on_bar("EURUSD", _bar("2026-06-15T09:00:00Z",
                                       high=1.10250, low=1.10000))
    assert len(closures) == 1, "the post-entry TP touch should close the trade"
    c = closures[0]
    assert c.close_time > c.entry_time, (
        f"exit {c.close_time} must be strictly after entry {c.entry_time}")
    assert c.entry_time == ENTRY_BAR


def test_invariant_holds_across_a_bar_stream(tmp_path):
    """Feed the entry bar + a forward stream; EVERY closure exits after entry."""
    for scale_out in (False, True):
        r = _router(tmp_path, scale_out=scale_out)
        r.submit(_buy_req())
        stream = [
            _bar("2026-06-15T05:00:00Z", 1.10300, 1.09700),  # before entry
            _bar(ENTRY_BAR, 1.10300, 1.09850),               # entry bar
            _bar("2026-06-15T09:00:00Z", 1.10120, 1.10010),
            _bar("2026-06-15T10:00:00Z", 1.10450, 1.10100),  # hits TP / trails
            _bar("2026-06-15T11:00:00Z", 1.10500, 1.10300),
        ]
        all_closures = []
        for b in stream:
            all_closures += r.on_bar("EURUSD", b)
        assert all_closures, f"expected at least one close (scale_out={scale_out})"
        for c in all_closures:
            assert c.close_time > c.entry_time, (
                f"VIOLATION scale_out={scale_out}: exit {c.close_time} "
                f"<= entry {c.entry_time}")


def test_legacy_position_without_entry_bar_time_is_still_guarded(tmp_path):
    """A position carried across a restart (no entry_bar_time) must fall back to
    fill_time and still reject stale bars — the CADJPY-runner incident.
    """
    r = _router(tmp_path)
    r.submit(_buy_req())
    pos = r.open_positions()[0]
    pos.entry_bar_time = ""                          # legacy: field didn't exist
    pos.fill_time = "2026-06-15T08:17:27.395000Z"    # wall-clock fill
    # A stale backfill bar from weeks earlier that would hit TP/SL.
    closures = r.on_bar("EURUSD", _bar("2026-05-28T03:00:00Z",
                                       high=1.10500, low=1.09800))
    assert closures == [], "stale bar must not close a legacy position"
    assert len(r.open_positions()) == 1
    # A genuinely-later bar still manages it normally.
    closures = r.on_bar("EURUSD", _bar("2026-06-15T09:00:00Z",
                                       high=1.10250, low=1.10000))
    assert len(closures) == 1
    assert closures[0].close_time > closures[0].entry_time


def test_old_behaviour_without_guard_would_fail(tmp_path):
    """Document the bug: with NO reference at all the entry bar DOES close it.

    This proves the guard is load-bearing — clearing both the entry-bar time and
    the fill-time fallback reproduces the original look-ahead fill.
    """
    r = _router(tmp_path)
    r.submit(_buy_req())
    pos = r.open_positions()[0]
    pos.entry_bar_time = ""        # simulate the pre-fix code (no guard)
    pos.fill_time = ""             # and no fallback reference either
    closures = r.on_bar("EURUSD", _bar(ENTRY_BAR, high=1.10500, low=1.09950))
    assert closures, "without any reference the entry bar fills instantly (the bug)"


# --------------------------------------------------------------------------- #
# ShadowTracker
# --------------------------------------------------------------------------- #
def test_shadow_does_not_resolve_on_entry_bar(tmp_path):
    from types import SimpleNamespace
    from strategy.shadow import ShadowTracker

    st = ShadowTracker(
        outcomes_path=str(tmp_path / "out.jsonl"),
        pending_path=str(tmp_path / "pending.json"),
    )
    sig = SimpleNamespace(
        symbol="EURUSD", timeframe="1h", setup="DB", side="buy",
        entry=1.10000, sl=1.09900, tp=1.10200, rr=2.0, clarity_score=70.0,
        detected_at="2026-06-15T07:00:00Z",
    )
    st.register(sig, stage="confirmation", failed="candle_anatomy")

    # First post-registration bar = the ENTRY bar. Even though its range spans
    # the TP, it must NOT resolve the shadow.
    n = st.on_bar(_bar("2026-06-15T08:00:00Z", high=1.10500, low=1.09950))
    assert n == 0, "shadow must not resolve on its own entry bar"
    assert st.pending_count() == 1

    # The NEXT bar may resolve it.
    n = st.on_bar(_bar("2026-06-15T09:00:00Z", high=1.10250, low=1.10000))
    assert n == 1, "shadow should resolve on a bar strictly after entry"
    assert st.pending_count() == 0


if __name__ == "__main__":
    import tempfile
    from pathlib import Path
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            with tempfile.TemporaryDirectory() as d:
                fn(Path(d))
            print(f"  PASS  {name}")
            passed += 1
    print(f"\n{passed} tests passed.")
