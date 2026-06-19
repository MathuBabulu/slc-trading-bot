#!/usr/bin/env python3
"""Audit: did every executed trade close on its OWN timeframe?

Each normal close in the ledger records close_time = the time of the bar that
closed it (paper.py _close_portion). A position must only ever be managed by
bars of its own timeframe, so a trade's close_time must land on that timeframe's
candle boundary. Because broker offsets are whole hours, the MINUTE of the close
time is timezone-independent, which lets us check this without knowing the broker
tz: any 1h/2h/4h/1d trade MUST close at minute 00; a 30m trade at :00/:30; a 15m
trade at :00/:15/:30/:45.

A trade closing off-boundary was closed by a LOWER timeframe's bar — the
cross-timeframe management bug. Read-only.

    python3 validate_timeframes.py
"""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path

LEDGER = Path(__file__).resolve().parent / "state" / "paper_ledger.json"
MIN_OK = {"15m": {0, 15, 30, 45}, "30m": {0, 30},
          "1h": {0}, "2h": {0}, "4h": {0}, "1d": {0}}


def parse(s):
    return datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(timezone.utc)


def main():
    closed = json.loads(LEDGER.read_text()).get("closed", [])
    if not closed:
        print("No closed trades.")
        return
    ok, viol = [], []
    print(f'{"ticket":>8} {"symbol":<8} {"tf":<4} {"reason":<11} {"close_time":<21}  verdict')
    print("-" * 72)
    for t in sorted(closed, key=lambda x: x["close_time"]):
        tf = t["timeframe"]
        reason = t.get("close_reason", "")
        dt = parse(t["close_time"])
        on_boundary = (dt.minute in MIN_OK.get(tf, {0})) and dt.second == 0
        # An ltf_rev_* close is an INTENDED lower-timeframe reversal exit, so it is
        # expected to land on a lower timeframe — not the cross-timeframe bug.
        intended_ltf = reason.startswith("ltf_rev")
        good = on_boundary or intended_ltf
        (ok if good else viol).append(t)
        verdict = ("OK" if on_boundary else
                   "OK (intended LTF reversal exit)" if intended_ltf else
                   "X  closed on a LOWER timeframe")
        print(f'{t["ticket"]:>8} {t["symbol"]:<8} {tf:<4} {reason:<13} '
              f'{t["close_time"]:<21}  {verdict}')
    print("-" * 72)
    print(f"Total {len(closed)}  ·  followed own timeframe: {len(ok)}  ·  violated: {len(viol)}")
    if viol:
        from collections import Counter
        print("Violations by timeframe:", dict(Counter(t["timeframe"] for t in viol)))
        print("\nNote: violations are higher-TF trades closed by lower-TF bars. If these "
              "predate the cross-timeframe fix, reset the ledger and re-run to confirm "
              "post-fix trades are clean.")


if __name__ == "__main__":
    main()
