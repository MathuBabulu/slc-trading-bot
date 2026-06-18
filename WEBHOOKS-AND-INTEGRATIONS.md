# Webhooks & Integrations

Every external connection the bot makes or accepts, with the exact endpoints, ports, magic number
and credentials currently in use.

> 🔐 The Telegram token, chat ID and Discord webhook below are **live**. They live in
> `trading-bot/data/trading.db` (settings table) and are reproduced here for the team's convenience.
> Treat this file and the repo as private. To rotate, see [`SECURITY.md`](SECURITY.md).

---

## 1. Hosts & ports

| What | Value |
|---|---|
| Bot server (Flask) | binds `0.0.0.0`, port **8766** |
| Dashboard | `http://localhost:8766` (or `http://<server-LAN-IP>:8766` from another machine) |
| News agent → server | `http://127.0.0.1:8766` |
| MT5 EA → server | `http://<server-LAN-IP>:8766` (must be added to MT5's WebRequest allow-list) |
| EA magic number | **770001** (identifies this bot's positions; the news agent only manages these) |

The server prints its dashboard URL and detected LAN IP on startup. The LAN IP changes with DHCP —
if the EA goes "offline," re-check the IP and the allow-list entry first.

## 2. MT5 ⇄ server (the EA "bridge")

The Expert Advisor `SLCDataBridge.mq5` (v2.30) is timer-based. It pushes a JSON feed and bars to the
server over HTTP and polls for trade commands. There is **no inbound connection to MT5** — MT5 always
initiates, which is why the URL must be allow-listed inside the terminal.

One-time MT5 setup: **Tools → Options → Expert Advisors → Allow WebRequest for listed URL**, then add
`http://<server-LAN-IP>:8766` (exact host + port, `http://`, no trailing slash). Attach the EA with
`AllowTradeExecution = false` for paper. Full walkthrough in [`SETUP-GUIDE.md`](SETUP-GUIDE.md).

### Server HTTP API

EA-facing endpoints (the integration surface):

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/mt5_feed` | EA pushes live bid/ask/spread per symbol (~every 5 s) |
| POST | `/api/mt5_bars` | EA pushes OHLC bars (~every 60 s) |
| GET | `/api/pairs` | EA polls which symbols the dashboard has enabled |
| GET | `/api/commands/next` | EA polls for the next queued trade command (open/close/modify) |
| POST | `/api/commands/ack/<cmd_id>` | EA acknowledges a command it executed |
| GET | `/api/status` | health / connection check |

Dashboard & control endpoints:

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | dashboard UI |
| GET | `/api/state`, `/api/bars`, `/api/trades`, `/api/performance`, `/api/equity`, `/api/signals`, `/api/agent_log` | dashboard data feeds |
| GET | `/api/spread`, `/spread` | spread trace readout |
| POST | `/api/settings` | save runtime settings (risk, RR, pairs, Telegram/Discord, etc.) |
| POST | `/api/commands` | enqueue a manual command |
| POST | `/api/trade/close/<trade_id>` | manually close a trade |
| POST | `/api/telegram_test` | send a test notification |
| POST | `/api/agent/run` | force a self-tuning agent evaluation now |

## 3. Telegram

Outbound only, via the Telegram Bot API: `https://api.telegram.org/bot<token>/sendMessage`
with `{chat_id, text, parse_mode: HTML}`. Every notification is prefixed with a header so it's
distinguishable from other bots sharing the same Telegram account.

**Live credentials (in the DB):**

```
telegram_enabled = true
telegram_bot_token = <SET-VIA-DASHBOARD>
telegram_chat_id   = <SET-VIA-DASHBOARD>
```

Re-create from scratch if needed: message **@BotFather** → `/newbot` → copy token; message your bot
once; message **@userinfobot** for your numeric chat id; paste both into the dashboard Telegram panel →
Enable → Send test message.

You get alerts on: trade opened, TP1 hit (50% banked, stop to breakeven), trade closed (TP2 / stop /
trailing / manual, with price + P&L + R), news-driven SL changes, mode switches, and agent
adjustments. Shadow trades are intentionally silent.

## 4. Discord

Outbound only, via a Discord **incoming webhook**. The shared notifier converts the Telegram-HTML
message to Discord markdown and POSTs it to the webhook URL in parallel with Telegram.

**Live credentials (in the DB):**

```
discord_enabled     = true
discord_webhook_url  = <SET-VIA-DASHBOARD>
```

Re-create from scratch if needed: Discord → Server Settings → Integrations → Webhooks → New Webhook →
copy URL → paste into the dashboard.

> Operational gotcha: the **main bot reads notifier settings live** on each send, but the
> **news agent builds its notifier once at startup** — so after enabling/changing Discord you must
> restart the news agent for it to pick up the change. A `config.yaml` edit also requires a server
> restart to take effect.

## 5. News data source

`news_agent.py` pulls headlines from **free Google News RSS** (no API key, no webhook in). It scores
sentiment, and for the bot's own open positions (filtered by magic **770001**) it may trail the stop,
move to breakeven, or cut a losing trade on a strong adverse score. In live mode the EA still refuses
any SL change that *loosens* a stop. Market-wide headline alerts are pushed to Telegram/Discord.

## 6. Credentials summary

| Integration | Where stored | Direction | Status |
|---|---|---|---|
| Telegram bot | `data/trading.db` → settings | outbound | live, enabled |
| Discord webhook | `data/trading.db` → settings | outbound | live, enabled |
| MT5 EA | terminal-side WebRequest allow-list | inbound to server | configured per machine |
| Google News RSS | none (public) | outbound | no key |

`trading-bot/config.yaml` ships these as **empty strings** by design — the real values are written to
the database at runtime from the dashboard. They are documented here only because this archive is a
private team hand-off.
