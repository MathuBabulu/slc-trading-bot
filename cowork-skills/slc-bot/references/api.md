# Reference: API endpoints & script flags

Canonical build — Flask on `http://127.0.0.1:8766` (config.yaml `server.port`). Read-only GETs unless noted.

## HTTP endpoints (server.py)

| Method | Path | Returns / purpose |
|---|---|---|
| GET | `/api/state` | `feed, settings, open_trades[] (+upnl), open_pnl, shadow_open, paper_balance, analysis[], server_time, ea_connected` |
| GET | `/api/performance` | `agent.evaluate()` → `n, win_pct, expectancy_R, total_R, pf, shadow{…}` |
| GET | `/api/trades?mode=&trade_mode=&symbol=&grade=&status=&result=win|loss&days=N` | `{trades:[…]}` (≤500, newest first) |
| GET | `/api/signals` | `{signals:[…]}` rows: `t,symbol,trade_mode,side,grade,entry,sl,tp,rr,status,reason,setup` (≤100) |
| GET | `/api/equity?mode=paper|live` | `{points:[{t,balance,equity}]}` |
| GET | `/api/agent_log` | `{log:[…]}` self-tuning eval/change/info (≤100) |
| GET | `/api/spread` | open-trade spread trace snapshot |
| GET | `/api/status` | positions snapshot for the news agent (live + paper pseudo-positions) |
| POST | `/api/settings` | body = subset of the **allow-set**; only whitelisted keys are written |
| POST | `/api/trade/close/<id>` | manual close (paper closes on book; live enqueues close) |
| POST | `/api/tv_webhook` | TradingView alert in (token-gated; routed through `ingest_external_signal`) |
| POST | `/api/telegram_test` | send a test notification |
| POST | `/api/agent/run` | run the bounded self-tuning agent once |
| POST | `/api/mt5_feed`, `/api/mt5_bars`, GET `/api/pairs`, `/api/commands/next`, POST `/api/commands/ack/<id>`, `/api/commands` | EA contract — **do not change** (fixed by SLCDataBridge.mq5) |

Settings **allow-set** (writable via `/api/settings`): trading_mode, modes, risk_pct, b_setup_risk_factor,
min_rr, atr_buffer, max_concurrent, daily_stop_pct, weekly_stop_pct, min_grade, regime_max, regime_b_ban,
max_spread_frac, vol_mult, enabled_pairs, watch_pairs, agent_enabled, telegram_enabled, telegram_bot_token,
telegram_chat_id, notify_signals, notify_agent, discord_enabled, discord_webhook_url, paper_balance,
agent_disabled_pairs, agent_disabled_modes, strategy_slc_enabled, tradingview_webhook_enabled,
tradingview_webhook_token, tv_default_trade_mode. (Anything not in this set is ignored by design.)

## Script CLI flags

```
sanity_check.py   --quick  --modes <csv: intraday,swing>  --notify  --apply
backtest.py       --symbols <space-separated>  --modes <csv>  --spread-mult <float>
tv_context.py     --force  --notify
hallucination_check.py   (no args; read-only DB integrity)
```

## TradingView alert payload (POST /api/tv_webhook)
```json
{ "token":"<secret>", "action":"buy|sell|close", "symbol":"{{ticker}}",
  "sl":1.0950, "tp":1.1100, "trade_mode":"intraday|swing" }
```
`sl` is required for buy/sell (wrong-side stop is refused). Exchange prefixes are stripped
(`OANDA:EURUSD`→`EURUSD`, `TVC:GOLD`→`XAUUSD`, `BINANCE:BTCUSDT`→`BTCUSD`). Disabled or bad/missing token
→ 403/401. Processed (executed / closed / skipped-by-rails / duplicate) → 200 with a result body.
