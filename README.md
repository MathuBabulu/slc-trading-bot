# SLC Price Action Trading Bot

Automated implementation of the **SLC price-action playbook** (*Structure · Liquidity · Confirmation*)
for Forex, metals and crypto. A single Python process ingests live data from MetaTrader 5, runs the
SLC strategy at intraday and swing speeds, paper- or live-trades it through an MT5 Expert Advisor,
manages every position to playbook rules, and reports to a web dashboard plus Telegram/Discord. A
bounded self-tuning agent and a news-monitoring agent run alongside it, inside hard safety rails.

> ⚠️ **Educational software, not financial advice.** Run in paper mode for at least 50 trades with
> positive expectancy before considering live trading. See [`LICENSE.md`](LICENSE.md).
>
> 🔐 **This archive contains live credentials** (Telegram bot token, Discord webhook) inside
> `trading-bot/data/trading.db`. Keep the repository **private** and read [`SECURITY.md`](SECURITY.md)
> before pushing anywhere.

---

## Architecture

```
MT5 terminal ──(SLCDataBridge.mq5 EA · HTTP push every 5s / poll commands)──► server.py  :8766
                                                                              │
   ┌──────────────────────────────────────────────────────────────────────────────────────┐
   │ server.py    Flask app · EA endpoints · serves dashboard · starts the threads below     │
   │ engine.py    SLC signal execution · paper broker · live command queue · trade mgmt      │
   │ strategy.py  pure SLC + chart-pattern logic (structure, liquidity, sweep, confirmation) │
   │ storage.py   SQLite (data/trading.db, WAL mode) · runtime settings incl. credentials    │
   │ agent.py     bounded self-tuning (whitelisted params only, never risk/stops/mode)        │
   │ notifier.py / telegram_notifier.py   dual-channel Telegram + Discord                     │
   └──────────────────────────────────────────────────────────────────────────────────────┘
                                                                              ▲
news_agent.py (separate process) ── Google-News RSS → sentiment → SL management + market alerts
dashboard/index.html ── web UI at http://localhost:8766
```

## Current configuration (authoritative — from `trading-bot/data/trading.db`)

| Setting | Value |
|---|---|
| Trading mode | **paper** (virtual balance) |
| Active speed | swing (intraday available) |
| Server | Flask on `0.0.0.0:8766`, dashboard at `http://localhost:8766` |
| EA | `SLCDataBridge.mq5` **v2.30**, magic number **770001** |
| Notifications | Telegram **on**, Discord **on** |
| Risk | 1% per A+ trade, min RR 2.5, ATR buffer 0.35, daily stop −2%, weekly −5% |
| Volume gate | `vol_mult = 1.0` (applied to paper config after shadow validation) |
| Universe | 22 enabled pairs + 24 on the watch list (FX majors/crosses, metals, indices, crypto) |

> Note: `trading-bot/README.md` and parts of `SETUP-GUIDE.md` were written earlier and still mention
> port 8765 and the old `MT5DataBridge` EA. The values in the table above (port **8766**,
> `SLCDataBridge`) are current — see [`DEVELOPMENT-HISTORY.md`](DEVELOPMENT-HISTORY.md) §3.

## Quick start (the machine running the bot)

```bash
cd trading-bot
pip install -r requirements.txt
python3 server.py            # prints the dashboard URL + LAN IP on startup
```

Open **http://localhost:8766**, then attach the EA in MT5. Full step-by-step (with the MT5
WebRequest allow-list, firewall and EA inputs) is in [`SETUP-GUIDE.md`](SETUP-GUIDE.md).

## What's in this archive

| Doc | Read it for |
|---|---|
| [`README.md`](README.md) | this overview |
| [`TEAM-ONBOARDING.md`](TEAM-ONBOARDING.md) | getting a teammate from zip → running bot, and who owns what |
| [`SETUP-GUIDE.md`](SETUP-GUIDE.md) | detailed MT5 + server + Telegram setup, phase by phase |
| [`WEBHOOKS-AND-INTEGRATIONS.md`](WEBHOOKS-AND-INTEGRATIONS.md) | every endpoint, webhook, token, port and magic number |
| [`SECURITY.md`](SECURITY.md) | the live secrets in this bundle and how to rotate them |
| [`SLC-Price-Action-Playbook.md`](SLC-Price-Action-Playbook.md) | the strategy this code implements |
| [`DEVELOPMENT-HISTORY.md`](DEVELOPMENT-HISTORY.md) | how it was built, key decisions, open items |
| [`LICENSE.md`](LICENSE.md) | licensing + disclaimer |

| Code & data | |
|---|---|
| `trading-bot/` | the Python application (server, engine, strategy, agents, dashboard) |
| `trading-bot/data/trading.db` | live SQLite history + runtime settings (**contains credentials**) |
| `trading-bot/state/` | runtime logs, news decisions, spread traces, sanity reports |
| `SLCDataBridge.mq5` / `.original.mq5` | MT5 data-bridge Expert Advisor (v2.30) and baseline |
| `slc-*.skill` | four Cowork skills to operate the bot conversationally |
| `*-REPORT.md`, `*.patch`, `*.diff`, `volume_gate_shadow.py`, `recover-db.sh`, `hallucination_check.py` | strategy experiments, validation reports, and ops tooling |

## Status & roadmap

Live in paper mode. Recently validated forward: a relative-volume confirmation gate and a
dynamic spread-based stop-loss. Open items (tick-accurate EA spread reporting, paper
commission/swap modelling, TP2 trailing) are tracked in
[`DEVELOPMENT-HISTORY.md`](DEVELOPMENT-HISTORY.md#open-items--todos--drafts).
