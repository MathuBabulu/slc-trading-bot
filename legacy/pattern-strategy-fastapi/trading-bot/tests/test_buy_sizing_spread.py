"""Regression test: buys must be sized on the SPREAD-INCLUSIVE entry.

Bug: a buy fills at the ask (entry + spread), but position sizing used the raw
signal entry, so lots were sized for a smaller stop distance and the trade
over-risked (up to 3.3× the 1% target on a tight stop / wide spread). The fix
folds the spread into the buy-side risk distance in evaluate_signal.

These tests check both the sizing math and the end-to-end invariant
(|pnl| ≈ |R| × risked_money) that previously fired CRITICAL on every buy.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from strategy.risk import Instrument, RiskConfig, RiskState, evaluate_signal
from execution.base import OrderRequest
from execution.paper import PaperRouter
from marketdata.base import Bar

INSTR = Instrument(symbol="X", pip_size=0.001, pip_value=1.0,
                   tick_value=1.0, tick_size=0.001)


def _sig(side="buy", entry=100.0, sl=99.0, tp=102.0, rr=2.0):
    return SimpleNamespace(symbol="X", side=side, entry=entry, sl=sl, tp=tp, rr=rr)


def _state():
    return RiskState(starting_equity=100_000, current_equity=100_000)


def test_buy_lots_account_for_spread():
    """With spread, the buy's risk distance grows, so fewer lots are sized."""
    cfg = RiskConfig()
    no_sp = {}
    evaluate_signal(_sig(), INSTR, cfg, _state(), sizing=no_sp, entry_spread=0.0)
    with_sp = {}
    evaluate_signal(_sig(), INSTR, cfg, _state(), sizing=with_sp, entry_spread=0.5)
    # entry 100→sl 99 = 1.0 risk; +0.5 spread → 1.5 risk → ~1/1.5 the lots.
    assert with_sp["risked_money"] > 0
    # money-at-risk per lot is larger once the spread is included
    assert with_sp["money_per_lot"] > no_sp["money_per_lot"]


def _round_trip(tmp_path, entry_spread_for_sizing, fill_spread):
    """Size a BUY, fill it (ask = entry + fill_spread), stop it out, and capture
    any sizing-invariant alert. Returns the list of alerts (empty = clean)."""
    alerts = []
    cfg = RiskConfig()
    sizing = {}
    ok, why, lots = evaluate_signal(_sig(), INSTR, cfg, _state(), sizing=sizing,
                                    entry_spread=entry_spread_for_sizing)
    assert ok, why
    r = PaperRouter(starting_equity=100_000, instruments={"X": {"pip_size": 0.001,
                    "pip_value": 1.0}}, ledger_path=str(tmp_path / "l.json"),
                    slippage_pips=0.0, commission_per_lot=0.0, scale_out=False,
                    alert=lambda t: alerts.append(t))
    r.submit(OrderRequest(ticket=1, symbol="X", side="buy", lots=lots,
                          entry=100.0, sl=99.0, tp=102.0, setup="DB", timeframe="1h",
                          detected_at="2026-06-15T07:00:00Z",
                          entry_bar_time="2026-06-15T08:00:00Z",
                          tick_value=1.0, tick_size=0.001,
                          risked_money=sizing["risked_money"], spread=fill_spread))
    # later bar that hits the stop (buy exits at bid: bar.low <= sl)
    r.on_bar("X", Bar(symbol="X", timeframe="1h", time="2026-06-15T09:00:00Z",
                      open=99.5, high=99.6, low=98.9, close=99.0, volume=10))
    return alerts


def test_fixed_sizing_has_no_invariant_violation(tmp_path):
    # Size WITH the same spread the fill will use → P&L and risk agree.
    alerts = _round_trip(tmp_path, entry_spread_for_sizing=0.5, fill_spread=0.5)
    assert alerts == [], f"unexpected sizing-invariant alert: {alerts}"


def test_old_unspread_sizing_would_violate(tmp_path):
    # Size WITHOUT spread (the old bug) but fill WITH spread → invariant fires.
    alerts = _round_trip(tmp_path, entry_spread_for_sizing=0.0, fill_spread=0.5)
    assert any("SIZING INVARIANT" in a for a in alerts), \
        "expected the pre-fix mismatch to trip the invariant"


def test_sell_sizing_unaffected_by_spread():
    cfg = RiskConfig()
    a, b = {}, {}
    evaluate_signal(_sig(side="sell", entry=100.0, sl=101.0, tp=98.0),
                    INSTR, cfg, _state(), sizing=a, entry_spread=0.0)
    evaluate_signal(_sig(side="sell", entry=100.0, sl=101.0, tp=98.0),
                    INSTR, cfg, _state(), sizing=b, entry_spread=0.5)
    assert a["money_per_lot"] == b["money_per_lot"], "sells must ignore entry spread"


if __name__ == "__main__":
    import tempfile
    from pathlib import Path
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            import inspect
            if "tmp_path" in inspect.signature(fn).parameters:
                with tempfile.TemporaryDirectory() as d:
                    fn(Path(d))
            else:
                fn()
            print(f"  PASS  {name}")
            passed += 1
    print(f"\n{passed} tests passed.")
