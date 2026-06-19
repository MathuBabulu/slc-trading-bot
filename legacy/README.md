# legacy/pattern-strategy-fastapi — superseded build (reference only)

The earlier **FastAPI / JSON / port 8765 / `MT5DataBridge`** implementation of the SLC bot
(branded "Pattern Strategy"). **Superseded** by the canonical SLC build at the repo root.

Do not deploy and do not merge its code. Useful here:
- `pattern-strategy-bot/strategy-study/` — labeled dataset + 100+ validation PNGs + knowledge base + tuning notes
- `pattern-strategy-bot/docs/PROJECT_CONTEXT_HANDOFF.md` — full architecture + change history of this build
- `pattern-strategy-bot/trading-dashboard/` — its standalone dashboard

Secrets were stripped before commit (`config.yaml`, `WEBHOOK_AND_SECRETS.md` removed;
`config.example.yaml` kept).
