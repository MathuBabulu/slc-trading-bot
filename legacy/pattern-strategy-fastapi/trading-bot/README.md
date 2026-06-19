# Price-Action Trading Bot

A Python trading bot that implements the price-action playbook from your
strategy notes:

- **Setups**: Double Top / Double Bottom (v1), Head & Shoulders, Inverse H&S,
  Triple Top / Bottom, Rectangle, Trendline break, Fakeout (v2)
- **Entry rule**: enter at R2 (second test of pattern level), *not* after
  neckline break
- **Confirmations**: slow approach (momentum check) + medium-body candle
  with ≥70% body and ≤30% opposing wick
- **Risk**: 1% per trade, minimum 1:2 R:R, only M15/H1/H4 timeframes,
  weekly cap of 12 trades, daily loss cap, news avoidance
- **Trade management**: exit before 1:2 if a counter-pattern forms; auto-flat
  before high-impact news (CPI / FOMC / NFP)

It pairs with the existing `trading-dashboard/` for visualisation, and adds a
**paper / live toggle** so you can run the same code in simulated or real-money
mode.

---

## v1 scope — paper-only, no MT5 required

You told me MT5 isn't set up yet and you want paper-only first. That's the
**recommended** path. v1 ships with:

| Component                | Implementation                                |
|--------------------------|-----------------------------------------------|
| Market data              | `yfinance` (Yahoo Finance, free, no key)      |
| Pattern detection        | Double Top / Double Bottom on M15 / H1 / H4   |
| Confirmation             | Candle anatomy + momentum (slow approach)     |
| Risk engine              | 1% sizing, 1:2 R:R, weekly + daily caps       |
| News filter              | ForexFactory scrape + manual JSON fallback    |
| Order execution          | **Paper ledger only** (no broker connection)  |
| Dashboard transport      | FastAPI + WebSocket on `localhost:8765`       |
| Persistence              | JSON files in `state/`                        |

Live MT5 mode is wired in but **disabled by default** with a clearly stubbed
`mt5_router.py`. You can flip the dashboard toggle to "live" only after MT5 is
configured.

---

## MT5 hosting — my recommendation

You're on a Mac. You said you want **fully autonomous live trading** later.
Those two facts together push the answer toward a **Windows VPS**, not Wine.

### Option A — Windows VPS (recommended once you go live)

You rent a small Windows VM, install the standard MT5 terminal and Python on
it, and the bot runs 24/7 even when your Mac is closed. The Mac dashboard talks
to the VPS over a WebSocket.

Pros
: Official MT5 Python API works natively. Stable 24/7 uptime. Low latency to
  broker servers if you pick the right region. Survives Mac sleep, Mac
  reboots, Mac OS upgrades.

Cons
: ~$8–25 / month. Initial setup ~30 min. You manage Windows updates.

Suggested providers
: Contabo Windows VPS, FXVM, ForexVPS.net, Vultr, AWS Lightsail. Pick a
  region near your broker's London / NY datacentre.

### Option B — Mac via CrossOver / Wine + `mt5linux`

You install MT5 inside CrossOver or a stock Wine bottle, and run the
community `mt5linux` bridge that exposes the MT5 Python API over RPC.

Pros
: Free. Fully local. No subscription.

Cons
: Wine setups break with macOS upgrades. `mt5linux` is community-maintained
  with no official Anthropic-style support. Mac must stay awake and connected
  for the bot to trade. **Not recommended for autonomous live trading.**

### Option C — MQL5 Expert Advisor inside MT5

All strategy and execution logic written in MQL5, running inside the terminal
on a Windows VPS. The Python dashboard becomes view-only.

Pros
: The "official" autonomous trading path. Most reliable. Lowest latency.

Cons
: Strategy logic must be ported from Python to MQL5. Harder to extend.

### My pick

For your goals: **Option A (Windows VPS)** when you go live. Use v1 paper mode
on the Mac with `yfinance` data until you're ready to commit, then we'll add
the MT5 connector and you'll deploy the bot to a VPS.

---

## Architecture

```
trading-bot/
├── server.py                  ← FastAPI entry point (start here)
├── config.example.yaml        ← copy to config.yaml and edit
├── requirements.txt
├── strategy/
│   ├── patterns.py            ← Double Top/Bottom detection
│   ├── confirmation.py        ← candle + momentum checks
│   ├── risk.py                ← position sizing, caps, kill switch
│   ├── news.py                ← ForexFactory scrape + filter
│   └── engine.py              ← bar-by-bar orchestration loop
├── marketdata/
│   ├── base.py                ← DataSource interface
│   ├── yfinance_source.py     ← live for v1
│   └── mt5_source.py          ← stub for v2
├── execution/
│   ├── base.py                ← OrderRouter interface
│   ├── paper.py               ← simulated fills, equity curve
│   └── mt5_router.py          ← stub (raises NotImplementedError)
├── server/
│   ├── api.py                 ← REST endpoints
│   └── websocket.py           ← bot ↔ dashboard live channel
└── state/
    ├── paper_ledger.json      ← persisted simulated trades
    ├── signals.json           ← every signal the bot has produced
    └── news_cache.json        ← cached ForexFactory calendar
```

### Data flow

1. `engine.py` polls `marketdata` every minute for new closed bars
2. For each instrument × timeframe, it runs `patterns.py` detectors
3. If a pattern fires, `confirmation.py` checks slow approach + candle anatomy
4. `news.py` blocks the signal if a high-impact event is within ±30 min
5. `risk.py` sizes the position and checks caps (1%, 1:2, 12/week, daily loss)
6. If everything passes, the signal goes to the active `OrderRouter`:
   - `paper.py` simulates fill at the next bar's open
   - `mt5_router.py` would send a real order (stubbed in v1)
7. WebSocket pushes the signal + open positions + equity to the dashboard

---

## Quick start

```bash
cd "trading-bot"

# One-time
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config.example.yaml config.yaml

# Edit config.yaml: choose instruments, timeframes, account size

# Run
python server.py
```

Then open the dashboard (`../trading-dashboard/index.html`). It will detect the
bot is running and show the **Live Bot** tab with paper/live toggle, current
signals, and the simulated equity curve.

---

## Safety rails

Even in paper mode the bot enforces:

| Rail               | Default            | Configurable in `config.yaml` |
|--------------------|--------------------|-------------------------------|
| Per-trade risk     | 1 % of equity      | `risk.per_trade_pct`          |
| Daily trade cap    | 3 trades / day     | `risk.daily_trade_cap`        |
| Weekly trade cap   | 12 trades / week   | `risk.weekly_trade_cap`       |
| Daily loss cap     | 3 % of equity      | `risk.daily_loss_pct`         |
| Total drawdown cap | 10 % of start eq.  | `risk.max_drawdown_pct`       |
| Kill switch file   | `state/HALT`       | touch it to stop trading      |

Hitting any cap **flattens all open positions and pauses new entries until
the next session boundary** (daily caps reset at broker midnight, weekly caps
on Sunday close, drawdown cap requires manual reset).

When live mode lands, these same rails apply. There is no way to disable them
short of editing the source — that's intentional.

---

## What's stubbed for v2

- Pattern detectors for HS, IHS, Triple Top/Bottom, Rectangle, Trendline,
  Fakeout (only DT/DB shipped in v1)
- MT5 connector for live orders + live tick data
- Backtest harness with proper walk-forward
- Per-pattern win-rate attribution in the dashboard
- Cloud deploy script for the Windows VPS

---

## Licence + risk disclaimer

This code is provided as-is for your personal use. Algorithmic trading
involves real financial risk. Even with the safety rails, a bug or an
unexpected market condition can cause losses. Run in paper mode until you
trust the metrics, and never risk capital you can't afford to lose.
