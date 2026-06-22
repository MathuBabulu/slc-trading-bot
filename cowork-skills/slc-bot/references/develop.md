# Sector: develop

How to extend the bot correctly. Read this before editing code. The global safety rules in SKILL.md are
hard constraints — additive, paper-validated changes only; never weaken a rail.

## Architecture (canonical build at repo root / `trading-bot/`)

| File | Role |
|---|---|
| `server.py` | Flask app, EA endpoints, dashboard, starts threads; `seed_settings()` + the `/api/settings` allow-set gate which settings are writable |
| `engine.py` | signal execution, paper broker, live command queue, trade mgmt (TP1→BE→trail), spread window; `try_execute` (SLC) and `ingest_external_signal` (TradingView) apply the rails; `engine_loop` drives it |
| `strategy.py` | pure SLC + chart-pattern logic; `analyze()` + `MODE_TFS` |
| `strategies/__init__.py` | **strategy-plugin registry**; `Strategy` base, `SLCStrategy` (delegates to `strategy.analyze`), `REGISTRY`, `active()`, `generate_all()` |
| `tv_webhook.py` | TradingView auth + ticker→symbol mapping + payload parsing (pure) |
| `storage.py` | SQLite (WAL), runtime settings incl. credentials |
| `agent.py` | bounded self-tuning (whitelist only) |
| `notifier.py` / `telegram_notifier.py` | dual-channel Telegram + Discord |
| `news_agent.py` / `news_evaluator.py` | RSS news monitor + SL management (separate process) |
| `tv_context.py` | TradingView confluence snapshot (read-side, no account) |
| `tests/` | `test_strategy_registry.py`, `test_tv_webhook.py` (no MT5 needed) |

The engine is **strategy-agnostic**: `engine_loop` asks `strategies.active(p)` which strategies run, then
`strategies.generate_all(...)` for each; every returned signal goes through the SAME `try_execute` rails.
Global rails live in the engine, never per strategy.

## Add a new strategy / technique (horizontal scaling)

1. **Implement a plugin** in `strategies/__init__.py`: subclass `Strategy`, set `name`, implement
   `is_enabled(params)` (read a `strategy_<name>_enabled` setting), `modes(params)`, and
   `generate(symbol, trade_mode, bars_by_tf, params, spread, live_price)` returning the SAME shape SLC
   returns: `{"signal": <dict|None>, "info": <dict>}`. A `signal` must carry the keys `try_execute`
   reads: `key, symbol, trade_mode, side, setup, entry, sl, tp, tp1, grade, rr` (look at how
   `strategy.analyze` builds them). Keep heavy logic in its own module (e.g. `strategies/<name>_logic.py`)
   and have the plugin call it — mirrors how `SLCStrategy` delegates to `strategy.py`.
2. **Register it:** append an instance to `REGISTRY`.
3. **Wire its setting:** add `strategy_<name>_enabled` to `seed_settings()` defaults in `server.py` and to
   the `/api/settings` allow-set so it's dashboard-toggleable.
4. **Test it** like `tests/test_strategy_registry.py`: assert it registers, the enable flag works, and on
   synthetic bars its verdict is sane and tagged. New strategies must NOT change SLC's output.
5. **Gate to live:** the new strategy clears its OWN ≥50 closed-paper-trade, positive-expectancy gate
   (paper/shadow first) before it trades live. It shares the global rails — it cannot widen a stop, raise
   risk above the cap, or bypass concurrency/loss limits.

## Extend the TradingView webhook / alert capture (vertical scaling)

- Parsing/auth live in `tv_webhook.py` (`parse_payload`, `map_symbol`, `check_token`); routing lives in
  `engine.ingest_external_signal`. To support a new alert field or action, edit the parser and the
  ingest function together, and keep ingest routing every candidate through the rails (mode, valid stop
  on the correct side, spread, RR, concurrency, correlation, balance, loss limits, sizing). A TradingView
  alert must never become a raw market order and never flip the mode.
- The endpoint is off unless `tradingview_webhook_enabled` AND a `tradingview_webhook_token` are set; the
  token is checked with `hmac.compare_digest`. Cover new behaviour in `tests/test_tv_webhook.py`.
- Operating reality: webhooks need a paid TradingView plan (Essential+; Premium+ for 24/7 server-side
  alerts) and a PUBLIC https endpoint (VPS or tunnel — TradingView can't reach localhost). See
  `docs/TRADINGVIEW-WEBHOOK.md`.

## Add notifications / alerts

Dual-channel notify goes through `notifier.send` (Telegram + Discord), reachable in the engine via
`notify(...)`. Reuse it; respect `notify_signals`. Shadow trades are SILENT — never notify on shadow.
New toggles go through `seed_settings()` + the `/api/settings` allow-set (and ideally a dashboard control),
and credentials stay in the DB, never in source.

## Workflow discipline (keeps edits correct + fast)

- `view`/`grep -n` the exact anchor line before any `str_replace` (whitespace + trailing commas matter).
- In tests, set `storage._DB_PATH = os.path.join(tempfile.mkdtemp(), "x.db")` BEFORE `storage.init()` so
  nothing persists in the repo; seed settings; set `engine.feed_state["prices"][sym]` with
  `tick_value`/`tick_size` for sizing; set `feed_state["last_feed_t"]` so the feed isn't "stale".
- `python3 -m py_compile <files>` before running suites to catch syntax errors early.
- Run the suites: `cd "$BOT_DIR" && python3 tests/test_strategy_registry.py && python3 tests/test_tv_webhook.py`.
  A Flask route check (`server.app.test_client()`) needs `flask` installed.
- Before zipping a deliverable: remove `__pycache__/`, `data/`, `state/`; confirm `.gitignore` still
  excludes `data/`, `state/`, `*.db`, `.backups/`; scan for secrets. Keep the repo PRIVATE.
- Keep `CLAUDE.md` (repo map + invariants) up to date when you add modules or rules.

## Keep THIS skill in sync (upgrade it alongside the code)

This skill is a static bundle — it does not auto-discover new code. Treat updating it as part of any change
that adds surface area, in the SAME commit:
- **New endpoint** → add a row to `references/api.md`; surface it in `scripts/slc.py` only if it's worth a
  quick command.
- **New setting** → add it to the allow-set list in `references/api.md` (and note any new toggle).
- **New strategy / technique** → it's already covered by the "add a strategy" steps here; if it introduces
  a new operating concept, add a line to `references/operate.md` or `analyze.md`.
- **New subsystem** (e.g. a roadmap item — portfolio-risk layer, audit log, TCA) → if it's a new *kind* of
  task, add a new sector reference file and a row to the sector table in `SKILL.md`; otherwise extend the
  closest existing reference.
- **New invariant / rail** → add it to the Global safety rules in `SKILL.md` AND to `CLAUDE.md`.

Keep the body lean: once a skill loads it stays in context across turns, so every line is a recurring token
cost. State what to do, not why. Then repackage and re-share:
```bash
bash scripts/package.sh           # -> slc-bot.skill (a .skill is just a zip of this folder)
```
Re-install/refresh in Cowork (Settings → Capabilities → Skills). If the skill is shared org-wide or with the
team, an updated upload replaces the shared copy — colleagues don't re-upload, they just get the new version.

## Tests prove logic, not live behaviour
The suites validate logic + that the rails fire, on a temp DB + synthetic feed. They do NOT prove live
behaviour — that needs the Mac/VPS with MT5, and (for the webhook) a paid TradingView plan + public
endpoint. Promote to live only through the gate, at minimum size, with one system live per MT5 account.
