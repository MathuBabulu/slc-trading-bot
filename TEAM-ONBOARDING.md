# Team Onboarding

How to go from this zip to a running bot, and how the project is meant to be operated. New to the
strategy itself? Read [`SLC-Price-Action-Playbook.md`](SLC-Price-Action-Playbook.md) first — the code
is just an honest implementation of it.

## 1. Prerequisites

- **Python 3.10+** on the machine that will run the bot (macOS or Linux; the owner runs macOS).
- **MetaTrader 5** on a machine that can reach the bot over the LAN (can be the same machine).
- A broker account in MT5 for live data (paper trading still needs the live MT5 price feed).
- Optional: a Telegram account and/or a Discord server for notifications (already configured — see
  [`WEBHOOKS-AND-INTEGRATIONS.md`](WEBHOOKS-AND-INTEGRATIONS.md)).

## 2. Get it running (≈10 minutes)

```bash
unzip SLC-Trading-Bot-team-archive.zip
cd slc-trading-bot/trading-bot
pip install -r requirements.txt
python3 server.py
```

The server prints the dashboard URL and the machine's LAN IP. Open **http://localhost:8766** — the
header will show *EA: offline* until MT5 is connected. Then follow
[`SETUP-GUIDE.md`](SETUP-GUIDE.md) to compile/attach the EA and allow-list the WebRequest URL.

> Ports/EA naming: the live system uses **port 8766** and the **`SLCDataBridge`** EA. The older
> `trading-bot/README.md` mentions 8765 / `MT5DataBridge`; prefer 8766 / `SLCDataBridge`.

## 3. What ships with state (and what that means)

This is a **working snapshot**, not a blank template:

- `trading-bot/data/trading.db` — full paper trade history, equity curve, signals, **and the live
  Telegram/Discord credentials**. On first run the values in this DB win over `config.yaml`.
- `trading-bot/state/` — runtime logs, news decisions, spread traces, sanity/validation reports.
- `trading-bot/.backups/` — timestamped backups of config/code taken before edits.

Because the credentials are live, **read [`SECURITY.md`](SECURITY.md)** before pushing this anywhere.
If a teammate wants a clean start, delete `trading-bot/data/trading.db` (it's recreated empty) and
re-enter credentials via the dashboard.

## 4. Paper vs live (the safety model)

- **PAPER** (current): the engine simulates fills on live MT5 prices and tracks P&L against a virtual
  balance. Full TP1/breakeven/trail management runs. This is the default and where you should stay.
- **LIVE**: requires *both* `AllowTradeExecution = true` in the EA inputs *and* switching the
  dashboard to LIVE (with a confirmation prompt). The EA refuses any stop change that loosens a stop.
- **OFF**: data flows and analysis runs, no new trades; open trades still managed.

House rule from the playbook and the build history: **50+ paper trades with positive expectancy before
going live.** If another system manages stops on the same MT5 account, run live on only one at a time.

## 5. Operating the bot

- **Dashboard** (`http://localhost:8766`): performance, chart with entry/exit markers, trade history
  filters, live per-symbol engine analysis, pairs manager, and the settings panel.
- **Cowork skills** (`slc-*.skill`): operate it conversationally — `slc-status` (open trades / equity /
  PnL), `slc-tv-context` (market snapshot, USD bias, top setups), `slc-sanity` (parameter sweep +
  health + tuning recommendations), `slc-backtest` (replay stored bars). Install the `.skill` files
  via Settings → Capabilities.
- **Self-tuning agent**: evaluates every 4 h once ≥15 trades close; only nudges a small whitelist of
  parameters and can never touch risk %, stops, concurrency, or trading mode. Toggle/force-run it from
  the dashboard header.
- **News agent** (`python3 news_agent.py`, separate process): monitors headlines and manages the
  bot's own positions' stops; restart it after changing notification settings.

## 6. Recovering from problems

- **EA offline:** server running? LAN IP correct? exact WebRequest allow-list URL? firewall? same
  network? See the troubleshooting tables in [`SETUP-GUIDE.md`](SETUP-GUIDE.md).
- **DB corruption:** run `recover-db.sh` — it stops the server gracefully, backs up the bad DB,
  rebuilds with `sqlite3 .recover`, integrity-checks, and swaps it in. The DB runs in WAL mode.
- **"Is the agent acting on bad data?"** `hallucination_check.py` (read-only) verifies DB integrity,
  feed freshness, and that the agent stayed within its rules; verdict logged to
  `hallucination_check.jsonl`.

## 7. Where to read next

- Strategy rationale and rules → [`SLC-Price-Action-Playbook.md`](SLC-Price-Action-Playbook.md)
- Every endpoint/webhook/token/port → [`WEBHOOKS-AND-INTEGRATIONS.md`](WEBHOOKS-AND-INTEGRATIONS.md)
- How and why it was built, open items → [`DEVELOPMENT-HISTORY.md`](DEVELOPMENT-HISTORY.md)
- Detailed first-time MT5 setup → [`SETUP-GUIDE.md`](SETUP-GUIDE.md)
