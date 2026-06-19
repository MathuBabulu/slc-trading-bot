# Security notes — read before sharing or pushing

This archive was deliberately bundled **with live runtime data**, at the owner's request, so the
team gets a working snapshot. That means real secrets are inside it.

## What live secrets are in this bundle

| Secret | Location |
|---|---|
| Telegram bot token `<redacted-telegram-token>` | `trading-bot/data/trading.db` (settings table) |
| Telegram chat ID `<redacted-chat-id>` | `trading-bot/data/trading.db` |
| Discord webhook URL `<redacted-discord-webhook>` | `trading-bot/data/trading.db` |
| MT5 account login number + balances | flows through `trading-bot/state/*.log` (not the token, but privacy-relevant) |
| LAN IP addresses (`<redacted-LAN-IP>`) | `trading-bot/state/server.launchd.log` |

`trading-bot/config.yaml` itself is clean (credentials are empty strings) — the live values are only
in the database and logs.

## Do this

1. **Keep the repository private.** Anyone with the Telegram token can control the bot and read its
   chat; anyone with the Discord webhook can post to that channel.
2. **Do not make it public** without first rotating the credentials (below) and scrubbing the DB.
3. Share the zip over a trusted channel, not a public link.

## How to rotate the credentials

- **Telegram:** message **@BotFather** → `/revoke` → pick the bot → it issues a new token; paste the
  new token into the dashboard Telegram panel. The old token stops working immediately.
- **Discord:** Server Settings → Integrations → Webhooks → delete the webhook (or "Copy" a new URL);
  paste the new URL into the dashboard.
- After rotating, the old values in `data/trading.db` and the logs in this archive are harmless.

## If you would rather ship a clean repo later

To exclude all runtime data and secrets from version control, add these lines to `.gitignore` and
remove the files from the working tree (`git rm --cached -r trading-bot/data trading-bot/state`):

```gitignore
trading-bot/data/
trading-bot/state/
trading-bot/.backups/
```

Then re-enter the Telegram/Discord credentials through the dashboard on each deployment. The code is
written for exactly this: secrets live in the database at runtime, never in source.

## Excluded from this archive

For size and hygiene, two corrupt SQLite dumps from the 2026-06-16 recovery incident
(`trading.db.bad-*` ≈ 81 MB, `trading.db.corrupt-*` ≈ 73 MB) and Python `__pycache__`, `.DS_Store`
and FUSE artifacts were left out. Everything else is as-is.
