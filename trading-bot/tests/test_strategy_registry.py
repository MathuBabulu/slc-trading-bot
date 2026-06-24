"""Strategy-plugin registry tests. No MT5 needed.

    cd "$BOT_DIR" && python3 tests/test_strategy_registry.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# isolate any accidental DB writes BEFORE importing modules that touch storage
import storage
storage._DB_PATH = os.path.join(tempfile.mkdtemp(), "test.db")

import strategy
import strategies


def _params(**over):
    p = {"modes": ["intraday", "swing"], "min_rr": 2.5, "atr_buffer": 0.35,
         "regime_max": 2.5, "regime_b_ban": 1.5, "min_grade": "B", "vol_mult": 0.0}
    p.update(over)
    return p


def test_slc_registered():
    names = [s.name for s in strategies.REGISTRY]
    assert "slc" in names, names
    assert any(isinstance(s, strategies.SLCStrategy) for s in strategies.REGISTRY)


def test_enable_flag():
    assert any(s.name == "slc" for s in strategies.active(_params()))                       # default-on
    assert any(s.name == "slc" for s in strategies.active(_params(strategy_slc_enabled=True)))
    assert not any(s.name == "slc" for s in strategies.active(_params(strategy_slc_enabled=False)))


def test_generate_all_tags_and_runs():
    # insufficient bars -> analyze returns signal None + a note; the registry
    # must still return exactly one slc-tagged result
    res = strategies.generate_all("EURUSD", "swing", {}, _params())
    assert len(res) == 1
    name, r = res[0]
    assert name == "slc"
    assert r["info"]["strategy"] == "slc"
    assert r["signal"] is None
    assert "note" in r["info"]


def test_disabled_strategy_does_not_run():
    assert strategies.generate_all("EURUSD", "swing", {}, _params(strategy_slc_enabled=False)) == []


def test_mode_filtering():
    # a plugin only runs the modes it declares; SLC declares params["modes"]
    assert strategies.generate_all("EURUSD", "intraday", {}, _params(modes=["swing"])) == []


def test_slc_output_unchanged():
    # the plugin must delegate to strategy.analyze unchanged (only adds the tag)
    p = _params()
    direct = strategy.analyze("EURUSD", "swing", {}, p, spread=0.0, live_price=None)
    via = strategies.generate_all("EURUSD", "swing", {}, p, spread=0.0, live_price=None)[0][1]
    assert via["signal"] == direct["signal"]
    info_wo_tag = {k: v for k, v in via["info"].items() if k != "strategy"}
    assert info_wo_tag == direct["info"]


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print("ok  ", fn.__name__)
    print("\n%d passed" % len(fns))
