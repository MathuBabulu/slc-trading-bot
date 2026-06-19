#!/bin/zsh
# Watchdog — run by launchd every 2 minutes (com.tradingbot.watchdog).
#
# 1. MetaTrader 5: GUI app, can't be supervised by KeepAlive → reopen if gone.
# 2. Trading server: even if the process exists, verify it ANSWERS HTTP;
#    if not, kickstart the launchd service (KeepAlive handles plain crashes,
#    this catches hangs).
# All actions are logged to state/watchdog.log with timestamps.

BOT_DIR="${0:A:h:h}"            # tools/watchdog.sh → trading-bot/
TS() { date -u "+%Y-%m-%dT%H:%M:%SZ" }

# ── 1. MetaTrader 5 terminal ────────────────────────────────────────────────
# Match both the native app name and the Wine-wrapped binary.
if ! pgrep -if "MetaTrader 5|terminal64.exe" > /dev/null 2>&1; then
  echo "$(TS) MT5 not running — reopening"
  open -ga "MetaTrader 5" 2>/dev/null \
    || open -ga "MetaTrader5" 2>/dev/null \
    || echo "$(TS)   ! Could not open MetaTrader 5 — check the app name in /Applications"
fi

# ── 2. Server answering HTTP? ───────────────────────────────────────────────
if ! curl -s -m 5 "http://127.0.0.1:8765/api/health" > /dev/null 2>&1; then
  echo "$(TS) Server not answering on :8765 — kickstarting"
  launchctl kickstart -k "gui/$(id -u)/com.tradingbot.server" 2>/dev/null \
    || echo "$(TS)   ! kickstart failed (service not loaded?)"
fi

# ── 3. News agent process present? (KeepAlive should handle this; log only) ─
if ! pgrep -if "news_agent.py" > /dev/null 2>&1; then
  echo "$(TS) News agent not running — kickstarting"
  launchctl kickstart -k "gui/$(id -u)/com.tradingbot.newsagent" 2>/dev/null \
    || echo "$(TS)   ! kickstart failed (service not loaded?)"
fi

exit 0
