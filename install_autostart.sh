#!/bin/zsh
# One-time installer: registers the trading stack with macOS launchd so that
#   - server.py and news_agent.py start at login and AUTO-RESTART on crash
#   - a watchdog reopens MetaTrader 5 and kickstarts hung services every 2 min
#
# Usage:   ./tools/install_autostart.sh            (install / update)
#          ./tools/install_autostart.sh uninstall  (remove everything)
#
# IMPORTANT: before installing, stop any manually-started server.py /
# news_agent.py (Ctrl+C in their terminals) so you don't get duplicates.

set -e
BOT_DIR="${0:A:h:h}"                       # tools/ → trading-bot/
AGENTS_DIR="$HOME/Library/LaunchAgents"
SERVICES=(com.tradingbot.server com.tradingbot.newsagent com.tradingbot.watchdog)

if [[ "$1" == "uninstall" ]]; then
  for s in $SERVICES; do
    launchctl bootout "gui/$(id -u)/$s" 2>/dev/null || true
    rm -f "$AGENTS_DIR/$s.plist"
    echo "✓ removed $s"
  done
  echo "Done. Processes were stopped; start manually with: python3 server.py"
  exit 0
fi

mkdir -p "$AGENTS_DIR" "$BOT_DIR/state"
chmod +x "$BOT_DIR/tools/watchdog.sh"

for s in $SERVICES; do
  # Fill in the real bot directory and install.
  sed "s|__BOT_DIR__|$BOT_DIR|g" "$BOT_DIR/tools/launchd/$s.plist" > "$AGENTS_DIR/$s.plist"
  # Reload cleanly if already present.
  launchctl bootout "gui/$(id -u)/$s" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$AGENTS_DIR/$s.plist"
  echo "✓ installed + started $s"
done

echo ""
echo "All set. The stack now survives crashes and starts at login."
echo ""
echo "Daily commands you'll actually use:"
echo "  restart server after config change:  launchctl kickstart -k gui/$(id -u)/com.tradingbot.server"
echo "  restart news agent:                  launchctl kickstart -k gui/$(id -u)/com.tradingbot.newsagent"
echo "  stop everything:                     ./tools/install_autostart.sh uninstall"
echo "  server log:                          tail -f \"$BOT_DIR/state/launchd_server.log\""
echo "  watchdog log:                        tail -f \"$BOT_DIR/state/watchdog.log\""
