"""Tests for dynamic (per-bar) spread modelling in the paper router.

Goal: replicate live conditions — the spread costing each exit is the spread
prevailing on THAT bar (EA per-bar max, or live-sampled, or stress-widened),
not a frozen entry snapshot. A widened spread (news / session rollover) must be
able to trigger a stop that the normal spread would not, and such exits are
flagged `spread_induced` for visibility.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from marketdata.base import Bar
from execution.base import OrderRequest
from execution.paper import PaperRouter

INSTR = {"USDJPY": {"pip_size": 0.01, "pip_value": 9.0}}
ENTRY_BAR = "2026-06-15T08:00:00Z"


def _router(tmp_path, scale_out=False, spread_stress=None):
    return PaperRouter(
        starting_equity=100_000, instruments=INSTR,
        ledger_path=str(tmp_path / "ledger.json"),
        slippage_pips=0.0, commission_per_lot=0.0,
        scale_out=scale_out, spread_stress=spread_stress,
    )


def _sell_req(spread=0.005):
    # sell 100.000, SL 100.100 (above), TP 99.800 — baseline spread 0.005
    return OrderRequest(
        ticket=1, symbol="USDJPY", side="sell", lots=0.10,
        entry=100.000, sl=100.100, tp=99.800, setup="DT", timeframe="1h",
        detected_at="2026-06-15T07:00:00Z", entry_bar_time=ENTRY_BAR,
        spread=spread,
    )


def _bar(time, high, low, spread=0.0):
    return Bar(symbol="USDJPY", timeframe="1h", time=time, open=(high + low) / 2,
               high=high, low=low, close=(high + low) / 2, volume=100.0,
               spread=spread)


def test_widened_spread_triggers_stop_that_normal_spread_would_not(tmp_path):
    r = _router(tmp_path)
    r.submit(_sell_req(spread=0.005))
    # bid high 100.080: at normal 0.005 spread ask=100.085 < SL 100.100 → no hit.
    # With a widened 0.030 spread on the bar, ask=100.110 ≥ SL → stop fires.
    closures = r.on_bar("USDJPY", _bar("2026-06-15T09:00:00Z",
                                       high=100.080, low=99.900, spread=0.030))
    assert len(closures) == 1, "widened spread should trigger the sell stop"
    c = closures[0]
    assert c.close_reason in ("sl", "be", "trail")
    assert c.spread_induced is True
    assert abs(c.exit_spread - 0.030) < 1e-9


def test_normal_spread_does_not_trigger(tmp_path):
    r = _router(tmp_path)
    r.submit(_sell_req(spread=0.005))
    # same bar, but spread stays normal (bar carries none → falls back to entry)
    closures = r.on_bar("USDJPY", _bar("2026-06-15T09:00:00Z",
                                       high=100.080, low=99.900, spread=0.0))
    assert closures == [], "at normal spread the stop must NOT trigger"
    assert len(r.open_positions()) == 1


def test_bar_spread_overrides_entry_snapshot_and_tracks_max(tmp_path):
    r = _router(tmp_path)
    r.submit(_sell_req(spread=0.005))
    # a non-triggering bar with a wider spread — should update max_spread_seen
    r.on_bar("USDJPY", _bar("2026-06-15T09:00:00Z", high=99.950, low=99.900,
                            spread=0.020))
    pos = r.open_positions()[0]
    assert pos.max_spread_seen >= 0.020


def test_stress_model_widens_only_inside_window(tmp_path):
    stress = {"enabled": True,
              "windows": [{"start": "23:55", "end": "00:10", "multiplier": 10.0}]}
    # Bar OUTSIDE the rollover window — normal spread, no trigger.
    r1 = _router(tmp_path, spread_stress=stress)
    r1.submit(_sell_req(spread=0.005))
    out = r1.on_bar("USDJPY", _bar("2026-06-15T09:00:00Z", high=100.080,
                                   low=99.900, spread=0.005))
    assert out == [], "outside the window spread is not stressed"
    # Bar INSIDE the rollover window — 0.005 × 10 = 0.050 → ask 100.130 ≥ SL.
    r2 = _router(tmp_path / "b", spread_stress=stress)
    r2.submit(_sell_req(spread=0.005))
    out = r2.on_bar("USDJPY", _bar("2026-06-15T23:58:00Z", high=100.080,
                                   low=99.900, spread=0.005))
    assert len(out) == 1 and out[0].spread_induced is True, \
        "inside the rollover window the stress multiplier should trigger the stop"


def test_buy_exit_is_not_spread_induced(tmp_path):
    r = _router(tmp_path)
    # buy 100.000, SL 99.900 (below). Buys exit at the BID (bars are bid), so a
    # spread can't manufacture a buy stop in this model.
    r.submit(OrderRequest(ticket=2, symbol="USDJPY", side="buy", lots=0.1,
                          entry=100.0, sl=99.9, tp=100.2, setup="DB",
                          timeframe="1h", detected_at="2026-06-15T07:00:00Z",
                          entry_bar_time=ENTRY_BAR, spread=0.005))
    closures = r.on_bar("USDJPY", _bar("2026-06-15T09:00:00Z", high=100.05,
                                       low=99.89, spread=0.030))
    assert len(closures) == 1 and closures[0].spread_induced is False


def test_scale_out_path_uses_dynamic_spread(tmp_path):
    # Position already partial-done, stop trailed to break-even (entry 100.0).
    r = _router(tmp_path, scale_out=True)
    r.submit(_sell_req(spread=0.005))
    pos = r.open_positions()[0]
    pos.partial_done, pos.sl, pos.sl_stage, pos.lots = True, 100.000, 1, 0.05
    # bid high 99.990: normal ask 99.995 < BE 100.0 (no hit); widened 0.02 →
    # ask 100.010 ≥ 100.0 → trailing stop hit, spread-induced.
    closures = r.on_bar("USDJPY", _bar("2026-06-15T09:00:00Z", high=99.990,
                                       low=99.900, spread=0.020))
    assert len(closures) == 1
    assert closures[0].spread_induced is True


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
