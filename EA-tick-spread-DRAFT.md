# EA tick-accurate spread reporting — draft (`SLCDataBridge.mq5`)

**Goal:** report, for each watched symbol, the worst bid / best ask / max spread seen on **every tick** since the last push — not just the snapshot at push time. This makes the paper engine's dynamic-spread stop tick-accurate instead of 5-second.

**Why `CopyTicks`:** the EA is timer-based and has no `OnTick`, and `OnTick` only fires for the chart symbol anyway. `CopyTicks()` returns all ticks for *any* Market-Watch symbol over a time window, so it captures inter-push extremes for all ~45 symbols. The EA already `SymbolSelect()`s each symbol, so they're in Market Watch.

Compile in MetaEditor (F7) and re-attach the EA. Four small additions, all in `SLCDataBridge.mq5`.

---

## 1. New global (with the other globals, ~line 105)

```mql5
ulong  g_lastSpanMsc = 0;   // start (ms) of the current tick-scan window
```

## 2. Initialise it in `OnInit()` (after `EventSetTimer(1);`)

```mql5
   g_lastSpanMsc = (ulong)TimeCurrent() * 1000;   // refined on every push
```

## 3. New helper — paste above the feed-JSON builder function

```mql5
//+------------------------------------------------------------------+
//| Tick-accurate worst-case extremes for one symbol over (fromMsc, now].
//| Returns the lowest bid, highest ask and max spread (points) seen on
//| EVERY tick since the last push. Falls back to the live quote when no
//| ticks are available yet (cold start / history still syncing).        |
//+------------------------------------------------------------------+
void SpanExtremes(const string sym, const ulong fromMsc,
                  double &minBid, double &maxAsk, double &maxSprPts, double &point)
  {
   point         = SymbolInfoDouble(sym, SYMBOL_POINT);
   double curBid = SymbolInfoDouble(sym, SYMBOL_BID);
   double curAsk = SymbolInfoDouble(sym, SYMBOL_ASK);
   minBid    = curBid;                                   // sensible defaults
   maxAsk    = curAsk;
   maxSprPts = (point > 0) ? (curAsk - curBid) / point : 0;

   // bound the lookback so a cold start can't pull the whole tick history
   ulong floorMsc = (ulong)TimeCurrent() * 1000 - (ulong)(PushIntervalSec + 2) * 1000;
   ulong from     = (fromMsc > floorMsc) ? fromMsc : floorMsc;

   MqlTick ticks[];
   int n = CopyTicks(sym, ticks, COPY_TICKS_ALL, from, 0);   // -1 if not ready
   for(int i = 0; i < n; i++)
     {
      double b = ticks[i].bid, a = ticks[i].ask;
      if(b > 0 && b < minBid) minBid = b;
      if(a > 0 && a > maxAsk) maxAsk = a;
      if(b > 0 && a > 0 && point > 0)
        {
         double sp = (a - b) / point;
         if(sp > maxSprPts) maxSprPts = sp;
        }
     }
  }
```

## 4. Emit the fields in the price loop

In the feed-JSON builder, right after the existing `double spread = ...` line, compute the extremes:

```mql5
      double minBid, maxAsk, maxSprPts, pointSz;
      SpanExtremes(sym, g_lastSpanMsc, minBid, maxAsk, maxSprPts, pointSz);
```

Then add these fields to the per-symbol JSON object (e.g. right after the `"spread"` line):

```mql5
      j += "\"min_bid\":"    + DoubleToString(minBid, digits)    + ",";
      j += "\"max_ask\":"    + DoubleToString(maxAsk, digits)    + ",";
      j += "\"max_spread\":" + DoubleToString(maxSprPts, 1)      + ",";
      j += "\"point\":"      + DoubleToString(pointSz, 8)        + ",";
```

Finally, immediately **after** the price `for(...)` loop closes (before `j += "],";`), advance the window watermark once:

```mql5
   g_lastSpanMsc = (ulong)TimeCurrent() * 1000;   // next push covers a fresh span
```

---

## 5. Python side — consume the tick extremes (revise `_accum_px_window` in `engine.py`)

Replace the body of `_accum_px_window` (added in `dynamic-spread-SL.diff`) with this. It prefers the EA's tick-accurate extremes and falls back to the 5s quote if the fields aren't present, so it is safe to deploy before/after the EA is updated:

```python
def _accum_px_window(p: Dict[str, Any]) -> None:
    sym, bid, ask = p.get("symbol"), p.get("bid"), p.get("ask")
    if sym is None or bid is None or ask is None:
        return
    # EA tick-accurate extremes for THIS push when available, else the quote
    t_min_bid = p.get("min_bid", bid)
    t_max_ask = p.get("max_ask", ask)
    point     = p.get("point") or 0.0
    spr_pts   = p.get("max_spread", p.get("spread", 0))
    spr_px    = (spr_pts * point) if point else abs(ask - bid)
    win = feed_state.setdefault("px_window", {})
    w = win.get(sym)
    if w is None:
        win[sym] = {"min_bid": t_min_bid, "max_ask": t_max_ask,
                    "max_spread": spr_px, "max_spread_pts": spr_pts}
    else:
        w["min_bid"] = min(w["min_bid"], t_min_bid)
        w["max_ask"] = max(w["max_ask"], t_max_ask)
        if spr_px > w["max_spread"]:
            w["max_spread"], w["max_spread_pts"] = spr_px, spr_pts
```

With this, each 5s push already carries the worst tick within its own window, and the engine folds those across the 20s management cycle — so the stop is tested against the true tick-by-tick worst case.

## Notes / caveats

- `CopyTicks` may return `-1` or `0` on the first calls for a symbol while MT5 syncs tick history (error 4401). The helper falls back to the live quote, so nothing breaks — accuracy just ramps up after the first minute.
- ~45 symbols × a few ticks every 5s is light; if you ever see the push lag, raise `PushIntervalSec` slightly or restrict tick scanning to symbols with open positions.
- Server time base: `time_msc` and `TimeCurrent()` are both broker-server time, so the window math is internally consistent.
- This is untested MQL5 (can't compile here) — verify it builds in MetaEditor and watch the first feed payloads include `min_bid`/`max_ask`/`max_spread`/`point` before relying on it.
