# SLC Trading Bot

Automated implementation of the SLC price action playbook (`../SLC-Price-Action-Playbook.md`)
with paper/live trading, an MT5 data bridge, a self-tuning agent, Telegram alerts,
and a web dashboard.

```
MT5 terminal ──(MT5DataBridge EA, HTTP push/poll)──► server.py :8765
                                                      ├─ engine.py   SLC signals, paper broker, live commands
                                                      ├─ storage.py  SQLite (data/trading.db)
                                                      ├─ agent.py    self-evaluation + bounded auto-tuning
                                                      ├─ notifier.py Telegram
                                                      └─ dashboard   http://localhost:8765
```

## 1. Install & run (this machine)

```bash
cd trading-bot
pip install -r requirements.txt
python server.py
```

Open **http://localhost:8765**. One process runs everything.

## 2. MT5 side (one-time)

1. Open `MT5DataBridge.mq5` in MetaEditor and **recompile (F7)** — v2.30 adds
   open/close trade execution. The old `.ex5` is v2.20 and can only manage SL.
2. MT5 → Tools → Options → Expert Advisors → tick *Allow WebRequest* and add
   `http://<your-server-ip>:8765` (the IP `server.py` prints at startup).
3. Attach the EA to any chart. Inputs that matter:
   - `ServerHost` — IP of the machine running `server.py` (`127.0.0.1` if same machine)
   - `AllowTradeExecution` — **false by default.** Leave false for paper trading.
     Set true ONLY when you switch the dashboard to LIVE.
   - `MaxLotsPerTrade`, `MaxOpenPositions` — broker-side hard caps.
4. Within ~60 s the dashboard header shows **EA: connected** and charts fill.

## 3. Telegram setup (2 minutes)

1. In Telegram, message **@BotFather** → `/newbot` → pick a name → copy the **token**.
2. Message your new bot anything (e.g. "hi") so it can reply to you.
3. Get your chat id: message **@userinfobot** — it replies with your numeric **id**.
4. Dashboard → Telegram panel → paste token + chat id → Enable → **Send test message**.

You'll get alerts on: trade opened, TP1 hit, trade closed, skipped signals (optional),
agent adjustments, and mode switches.

## 4. Paper vs Live

- **PAPER** (default): engine simulates fills on live MT5 prices, full TP1/BE/trail
  management, P&L tracked against a virtual balance (`paper_balance`, default 10,000).
- **LIVE**: engine queues `open_trade` commands; the EA executes them on your account.
  Requires `AllowTradeExecution=true` in EA inputs *and* switching the dashboard to LIVE
  (it asks for confirmation). Exits at broker are detected and reconciled automatically.
- **OFF**: data keeps flowing and analysis runs; no new trades. Open trades are still managed.

Run paper for **at least 50 trades** before considering live. No strategy guarantees
returns; this is educational software, not financial advice.

## 5. The self-tuning agent

Evaluates every 4 h (config) once ≥15 trades are closed. It may only:
raise/lower `min_grade` (A/B), nudge `atr_buffer` (±0.05 within 0.25–0.60),
nudge `min_rr` (±0.1 within 1.8–3.0), disable a losing symbol (≥20 trades,
< −0.2R expectancy; re-trialed after 14 days), disable a losing mode (≥25 trades).
It can **never** touch risk %, daily/weekly stops, concurrency, or trading mode.
Every change is logged in the dashboard Agent Log and announced on Telegram.
Toggle it off in the header; "Evaluate now" forces a run.

## 6. Dashboard

- **Performance** — win rate, expectancy (R), profit factor, total R, grade A/B split.
- **Chart** — MT5 bars; ▲▼ entry markers, ⚑ exit flags, dashed SL/TP1/TP2 lines on open trades.
- **History** — filter by mode, speed, symbol, grade, win/loss, period.
- **Engine analysis** — live per-symbol reasoning (bias, regime, why it's waiting).
- **Pairs manager** — toggle symbols; the EA follows within 30 s.
- **Settings** — risk %, RR, ATR buffer, stops, grades, intraday/swing toggles.

## 7. Files

| File | Role |
|---|---|
| `config.yaml` | startup defaults (DB-stored settings win after first run) |
| `server.py` | Flask app + EA endpoints + starts engine/agent/notifier threads |
| `engine.py` | signal execution, paper broker, live commands, trade management |
| `strategy.py` | pure SLC logic (structure, liquidity, sweep, confirmation, ATR regime) |
| `agent.py` | performance evaluation + bounded self-tuning |
| `notifier.py` | Telegram queue |
| `storage.py` | SQLite layer (`data/trading.db` is created automatically) |
| `dashboard/index.html` | the web UI |

## Troubleshooting

- **EA: offline** — check WebRequest allow-list URL matches `ServerHost:Port`, firewall,
  and that both machines are on the same network.
- **No trades for days** — normal. The system trades when all 6 checklist items align;
  check Engine Analysis to see what it's waiting for per symbol.
- **`open_trade REFUSED`** in MT5 Experts tab — `AllowTradeExecution` is still false.
- Scalping (1–5m) isn't supported: the bridge pushes 15m+ bars by design.
