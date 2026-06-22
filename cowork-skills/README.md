# Cowork skills

**Use `slc-bot/` — the consolidated skill.** It replaces the four separate root-level
`slc-*.skill` bundles (status, sanity, backtest, tv-context) with ONE skill that has
sectors (operate / analyze / develop) loaded on demand via progressive disclosure —
lower token/credit overhead, better/faster output, and it also covers development work
(adding strategies, extending the TradingView webhook, running tests, packaging).

Install in Cowork: Settings → Capabilities → add the packaged `slc-bot.skill`
(package it with the skill-creator, or zip this folder).

The legacy build's four skills under
`../legacy/pattern-strategy-fastapi/cowork-skills/` target the superseded FastAPI build
(port 8765) — reference only.
