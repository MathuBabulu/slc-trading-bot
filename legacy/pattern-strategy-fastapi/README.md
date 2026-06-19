# Pattern Strategy — Price-Action FX Trading Bot

*A.k.a. **SLC** (Structure · Liquidity · Confirmation). Proaxive desk · FX-first · paper mode (INR).*

An automated **MetaTrader 5 price-action trading bot** with a web dashboard, a news
sub-agent, and Telegram/Discord notifications. It scans classic chart patterns across
multiple FX pairs and timeframes, filters them through layered confirmation / context /
correlation / news / risk gates, and paper-trades them with staged scale-out management.

> ⚠️ **This package contains LIVE credentials** (Discord webhook, Telegram bot token, X
> API token) in `trading-bot/config.yaml` and in `WEBHOOK_AND_SECRETS.md`. They are
> included on purpose so the team can run the bot as-is. **Do not push this repo to a
> public GitHub remote without first reading the "Security" section below.**

---

## What it does

1. **Ingests bars from MT5.** The `MT5DataBridge` Expert Advisor (in `trading-bot/ea/`)
   pushes OHLCV bars (M15→D1) plus the broker's exact tick value/size over HTTP to the
   bot's FastAPI server on **port 8765**.
2. **Detects price-action patterns.** Core setups are **Double Top / Double Bottom**.
   Head & Shoulders, Inverse H&S, Triple Top/Bottom, Rectangle and Trendline detectors
   exist but are **disabled pending offline validation**.
3. **Enters at the R2 retest** — the *second* test of the pattern level, not on the
   neckline break.
4. **Confirms** each signal through a stack of gates: candle anatomy (≥70% body, ≤30%
   opposing wick), slow-approach momentum, higher-timeframe trend context, correlation +
   Choppiness-Index filter, an indicator/clarity score, and a (non-blocking) volume-profile
   bonus.
5. **Sizes and risk-checks** the trade: 1% risk per trade, minimum 1:2 reward:risk, with
   daily/weekly trade caps, a daily-loss cap, a max-drawdown halt, and a kill-switch file.
6. **Manages the position** with staged scale-out (1:2 → close 50% + stop to break-even;
   1:3 → trail +1R; 1:4 → trail +2R) and a **lower-timeframe reversal exit**.
7. **Watches the news.** A separate news agent scores FX headlines (incl. a priority lane
   for presidential / social-media market movers), trails or cuts losing trades on adverse
   news, and pushes market-news alerts to Telegram/Discord.
8. **Reports everything** to the web dashboard and to Telegram + Discord, and journals
   every trade for later validation.

Everything currently runs in **paper mode** (simulated fills against an INR ledger,
₹100,000 starting capital). Live MT5 order routing is wired but intentionally stubbed.

## Strategy parameters (current, from `config.yaml`)

| Area | Setting |
|---|---|
| Account | Paper · INR · ₹100,000 starting equity |
| Instruments | 12 enabled (7 FX majors + 5 JPY crosses); full 42-instrument catalog preserved as comments |
| Timeframes | 1h, 2h, 4h, 1d (15m/30m disabled — too noisy) |
| Patterns | Double Top + Double Bottom only (others disabled) |
| Risk / trade | **1.0%** of equity (lowered from 2.0% on 16 Jun 2026) |
| Min reward:risk | 2.0 |
| Trade caps | 3/day, 12/week |
| Loss caps | 3% daily loss halt · 10% max-drawdown halt |
| Scale-out | 1:2 → 50% + BE · 1:3 → +1R · 1:4 → +2R |
| News agent | `live_mode: true` (acts on paper tickets) · sentiment threshold 0.25 |
| Notifications | Telegram + Discord, header "Pattern Strategy" |

See `docs/PROJECT_CONTEXT_HANDOFF.md` and `strategy-study/` for the full rationale.

## Repository layout

```
pattern-strategy-bot/
├── README.md                  ← you are here (project description + overview)
├── INSTRUCTIONS.md            ← setup, run, operate, deploy
├── WEBHOOK_AND_SECRETS.md     ← webhook URL, API endpoints, tokens (LIVE values)
├── .gitignore
├── docs/                      ← deep context for the team
│   ├── PROJECT_CONTEXT_HANDOFF.md   ← architecture, file map, change history
│   ├── IMPLEMENTATION_PROMPT.md     ← the improvement-pass spec
│   ├── Pattern-Strategy-Scheduler.md← the daily automated tasks
│   └── Institutional_Trading_Playbook.pdf  ← source strategy reference
├── trading-bot/               ← THE BOT (Python, FastAPI). Push this to GitHub.
│   ├── server.py              ← FastAPI server + StrategyEngine entry point
│   ├── config.yaml            ← live config (LIVE secrets inside)
│   ├── config.example.yaml    ← redacted template
│   ├── requirements.txt
│   ├── strategy/              ← patterns, confirmation, risk, htf, correlation,
│   │                            volume_profile, shadow, cooldown, journal, news, engine
│   ├── execution/             ← paper router (+ mt5 router stub)
│   ├── marketdata/            ← MT5 bar source
│   ├── news_agent.py / news_evaluator.py
│   ├── telegram_notifier.py / discord_notifier.py / notifications.py
│   ├── validate_*.py          ← visual/sanity validators (run on the Mac)
│   ├── tools/                 ← reset_ledger, shadow_report, pattern_sanity_check,
│   │                            watchdog, launchd plists, install_autostart.sh
│   ├── tests/                 ← unit tests (run with pytest / unittest)
│   ├── ea/                    ← MT5DataBridge.mq5 (v2.20) + compiled .ex5
│   └── state/                 ← runtime state (only small config files included)
├── trading-dashboard/         ← single-page HTML/JS dashboard (Chart.js)
├── strategy-study/            ← knowledge base, labeled dataset, tuning + validation
└── cowork-skills/             ← 4 companion Cowork skills (.skill bundles)
```

## Quick start

```bash
cd trading-bot
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# config.yaml is already populated with live values; review it.
python3 server.py          # FastAPI server + engine on :8765
python3 news_agent.py       # (separate terminal) the news sub-agent
```

Attach `ea/MT5DataBridge.mq5` to any chart in MT5, set its `ServerHost`/`Port` to this
machine's IP and `8765`, and add that URL to MT5's **Allow WebRequest** list. Open the
dashboard at the URL the server prints (it serves `../trading-dashboard/` at `/`).

Full details — including the launchd autostart services and the daily scheduled tasks —
are in **`INSTRUCTIONS.md`**.

## Security

`config.yaml` and `WEBHOOK_AND_SECRETS.md` contain **real, live credentials**. Before you
push to a **public** GitHub repository, do one of the following:

- **Rotate + templatize (recommended):** rotate the Telegram token, Discord webhook and X
  token, then keep only `config.example.yaml` in git and load real values from environment
  variables or an un-tracked `config.yaml`.
- **Keep it private:** push only to a **private** repo that the team controls.

`.gitignore` already excludes runtime state and the Python venv, and contains a
**commented-out** `config.yaml` line — uncomment it to stop tracking the live config in one
step. `WEBHOOK_AND_SECRETS.md` is also listed there (commented) for the same reason.

## Disclaimer

Provided as-is for the team's own use. Algorithmic trading carries real financial risk;
even with the safety rails, bugs or unusual market conditions can cause losses. Keep it in
paper mode until the metrics are trusted, and never risk capital you can't afford to lose.
