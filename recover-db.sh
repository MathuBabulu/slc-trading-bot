#!/bin/bash
# Recover the corrupted SLC trading.db (run on the Mac, with the bot STOPPED).
# Corruption: "rowid out of order" in the bars table -> sqlite3 .recover rebuilds it.
set -e
cd "$(dirname "$0")/trading-bot/data"
TS=$(date +%Y%m%d-%H%M%S)

echo "1) Stopping the server gracefully (NOT kill -9)…"
launchctl bootout "gui/$(id -u)/com.slc.server" 2>/dev/null || launchctl stop com.slc.server 2>/dev/null || true
sleep 2

echo "2) Backing up the corrupt DB -> trading.db.corrupt-$TS"
cp trading.db "trading.db.corrupt-$TS"

echo "3) Recovering into trading_recovered.db…"
sqlite3 trading.db ".recover" | sqlite3 trading_recovered.db

echo "4) Integrity check on the recovered DB:"
sqlite3 trading_recovered.db "PRAGMA integrity_check;"
echo "   bars rows recovered: $(sqlite3 trading_recovered.db 'SELECT COUNT(*) FROM bars;')"

echo "5) Swapping recovered DB into place…"
mv trading.db "trading.db.bad-$TS"
mv trading_recovered.db trading.db

echo "6) Enabling WAL journal mode (crash-resilient) …"
sqlite3 trading.db "PRAGMA journal_mode=WAL;"

echo "7) Restarting the server gracefully…"
cd ..
launchctl kickstart -k "gui/$(id -u)/com.slc.server" 2>/dev/null || \
  launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.slc.server.plist" 2>/dev/null || \
  echo "   -> start the server your usual way"

echo "Done. Watch state/server.launchd.log — the 'malformed' errors should stop."
echo "If integrity_check above did NOT say 'ok', do not swap; keep trading.db.corrupt-$TS and ask for help."
