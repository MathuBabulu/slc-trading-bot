# Setup & Operating Instructions

How to install, configure, run, operate and (eventually) deploy the Pattern Strategy bot.
Read `README.md` first for the high-level overview.

---

## 1. Prerequisites

- **macOS or Windows** with **Python 3.10+** (developed on 3.10).
- **MetaTrader 5** terminal with an account at your broker (paper/demo is fine). The bot
  gets its price data from MT5 via the bundled Expert Advisor — there is no other data feed.
- Optional, for notifications: a **Telegram bot** + chat, and a **Discord webhook**.
- Optional, for the presidential/social news lane: an **X (Twitter) API bearer token**.

## 2. Install

```bash
cd trading-bot
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

`requirements.txt` covers the server (FastAPI/uvicorn/websockets), data handling
(pandas/numpy), config (pyyaml) and the news scrape (requests/beautifulsoup4). The
`MetaTrader5` Python package is only needed for **live** mode and is commented out.

## 3. Configure

The repo ships a populated **`config.yaml`** (live values already in place) and a redacted
**`config.example.yaml`** template. To start from scratch instead:

```bash
cp config.example.yaml config.yaml
```

Key sections (see inline comments in the file for everything):

- `account` — `mode: paper`, `starting_equity: 100000`, `currency: INR`.
- `instruments` — 12 enabled (majors + JPY crosses). `display` **must** match your broker's
  symbol names. The full 42-instrument catalog is preserved as comments.
- `timeframes` — `1h, 2h, 4h, 1d`.
- `strategy` — pattern toggles (DT/DB on), clarity score, cooldown, HTF filter, indicator
  filter, volume profile, confirmation thresholds, correlation filter.
- `risk` — `per_trade_pct: 1.0`, `min_rr: 2.0`, trade/loss caps, scale-out, LTF reversal exit.
- `news` / `news_agent` — ForexFactory + Google-News/X sources, sentiment thresholds,
  alerting and cut-loss behaviour.
- `telegram` / `discord` — notification channels (see `WEBHOOK_AND_SECRETS.md`).
- `server` — `host: 0.0.0.0`, `port: 8765`, `serve_dashboard: true`.
- `mt5` — live-mode account fields (ignored in paper mode; password comes from the
  `MT5_PASSWORD` env var, never the file).

> The engine **loads config at startup** — restart the server after any config change.

## 4. Connect MetaTrader 5 (the data bridge)

1. Copy `trading-bot/ea/MT5DataBridge.ex5` into your MT5 `MQL5/Experts/` folder
   (or open `MT5DataBridge.mq5` in MetaEditor and compile). EA version is **2.20**.
2. In MT5: **Tools → Options → Expert Advisors → Allow WebRequest for listed URL**, and add
   `http://<this-machine-IP>:8765` (use `http://127.0.0.1:8765` if MT5 runs on the same
   machine).
3. Attach the EA to any one chart. Set its inputs: `ServerHost` = this machine's IP,
   `Port` = `8765`. The EA auto-resolves broker symbol names and pushes the
   dashboard-enabled pairs across all configured timeframes.
4. If your machine's IP changes (DHCP), update both the EA input and the MT5 WebRequest
   allow-list — a stale IP shows up as MT5 errors 5203 / 4014.

## 5. Run

### Manual (two terminals)

```bash
cd trading-bot && source .venv/bin/activate
python3 server.py        # FastAPI server + StrategyEngine on :8765
```
```bash
cd trading-bot && source .venv/bin/activate
python3 news_agent.py     # news monitoring sub-agent
```

Open the dashboard at the URL the server prints (the server also serves
`../trading-dashboard/` at `/`).

### Autostart (recommended on the always-on machine — macOS launchd)

```bash
cd trading-bot
./tools/install_autostart.sh            # registers the launchd services
./tools/install_autostart.sh uninstall  # to remove them
```

This registers three services with auto-restart:

- `com.tradingbot.server` — the FastAPI server + engine.
- `com.tradingbot.news` — the news agent.
- `com.tradingbot.watchdog` — every ~2 min, reopens MT5 if needed and kickstarts a hung
  server via `/api/health`.

Logs: `state/launchd_server.log`, `state/launchd_newsagent.log`, `state/watchdog.log`.
After a config change, reload with **kickstart** (not a second `python3`, which would
duplicate the process):

```bash
launchctl kickstart -k gui/$(id -u)/com.tradingbot.server
launchctl kickstart -k gui/$(id -u)/com.tradingbot.news
```

## 6. Daily automated tasks (Cowork scheduler)

Four read-only/guarded tasks run each morning (full detail in
`docs/Pattern-Strategy-Scheduler.md`):

| ~Time | Task | Purpose |
|---|---|---|
| 08:03 | volume-gate shadow check | guarded auto-tune of the volume gate |
| 08:06 | daily trading-bot review | morning performance review |
| 08:21 | pattern hallucination check | verifies detected patterns are real (`tools/pattern_sanity_check.py`) |
| 08:57 | agent grounding check | audits the auto-tuner acts only on healthy data |

They run via the Cowork app and **only while it is open**. The four companion Cowork
**skills** that power status/health/news/study queries are in `cowork-skills/` (`.skill`
bundles — install from the Cowork app).

## 7. Operate

- **Check status / feed:** `python3 check_feed.py --url http://127.0.0.1:8765`
- **Run the test suite:** `python3 -m pytest -q` (or `python3 -m unittest tests.test_new_logic`)
- **Visual validators** (run on the machine with the live server):
  `python3 validate_patterns.py`, `validate_trades.py`, `validate_timeframes.py` — render
  detections/executed trades as candlestick PNGs with entry/SL/TP arrows.
- **Shadow report** (tune thresholds from data): `python3 tools/shadow_report.py`
- **Performance review:** `python3 performance_review.py`
- **Reset the paper ledger** to a clean ₹100,000 (backs up first, clears the drawdown
  halt): `python3 tools/reset_ledger.py`, then restart the server.
- **Kill switch:** create the file `state/HALT` (e.g. `touch state/HALT`) to stop new
  trading immediately; delete it to resume.

## 8. Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| Dashboard shows no bars | EA not attached, wrong IP/port, or URL missing from MT5 WebRequest allow-list. |
| MT5 errors 5203 / 4014 | Machine IP changed — update the EA input and the allow-list. |
| No Telegram/Discord messages | Check `telegram.enabled` / `discord.enabled` and the token/webhook in `config.yaml`; see `WEBHOOK_AND_SECRETS.md`. |
| Bot won't trade | Hit a cap or drawdown halt, or a `state/HALT` file exists. Reset the ledger and remove `HALT`. |
| Server seems hung | `launchctl kickstart -k gui/$(id -u)/com.tradingbot.server`, or check `state/launchd_server.log`. |

## 9. Going live (future)

Live MT5 routing is stubbed in `execution/mt5_router.py`. The recommended path for
autonomous 24/7 trading is a **Windows VPS** running MT5 + Python (see the README in
`docs/PROJECT_CONTEXT_HANDOFF.md` for the hosting comparison). Do not flip `account.mode`
to `live` until the connector is finished and the metrics are trusted in paper mode.
