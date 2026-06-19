#!/usr/bin/env python3
"""Reset the paper ledger to a clean starting balance.

What it does:
  1. Backs up state/paper_ledger.json to state/backups/paper_ledger_<UTC-ts>.json
     (the backup is NEVER deleted by this script).
  2. Writes a fresh ledger: starting equity from config.yaml (account.starting_equity,
     default 10000), empty open/closed lists.
  3. Removes the kill-switch file (state/HALT) if present.

The max-drawdown halt (`halted_for_dd`) lives only in memory and derives from the
ledger equity, so a server RESTART after this reset fully clears it.

Run from the trading-bot directory ON THE MAC:
    python3 tools/reset_ledger.py            # interactive confirm
    python3 tools/reset_ledger.py --yes      # no prompt
"""
from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

BOT_ROOT = Path(__file__).resolve().parent.parent
LEDGER = BOT_ROOT / "state" / "paper_ledger.json"
BACKUP_DIR = BOT_ROOT / "state" / "backups"
HALT_FILE = BOT_ROOT / "state" / "HALT"
CONFIG = BOT_ROOT / "config.yaml"


def _starting_equity() -> float:
    """Read account.starting_equity from config.yaml (default 10000)."""
    try:
        import yaml  # the bot already depends on this
        cfg = yaml.safe_load(CONFIG.read_text())
        return float(cfg["account"]["starting_equity"])
    except Exception as exc:  # noqa: BLE001
        print(f"  ! Could not read config.yaml ({exc}) — using 10000")
        return 10000.0


def main() -> int:
    if "--yes" not in sys.argv:
        ans = input("Reset paper ledger to starting equity? Backup will be kept. [y/N] ")
        if ans.strip().lower() not in ("y", "yes"):
            print("Aborted — nothing changed.")
            return 1

    equity = _starting_equity()
    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y%m%dT%H%M%SZ")

    # 1. Backup (never deleted)
    if LEDGER.exists():
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        backup = BACKUP_DIR / f"paper_ledger_{ts}.json"
        shutil.copy2(LEDGER, backup)
        print(f"  ✓ Backed up old ledger -> {backup.relative_to(BOT_ROOT)}")
    else:
        print("  - No existing ledger found (nothing to back up)")

    # 2. Fresh ledger
    fresh = {
        "starting_equity": equity,
        "equity": equity,
        "open": [],
        "closed": [],
        "saved_at": now.isoformat().replace("+00:00", "Z"),
    }
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    LEDGER.write_text(json.dumps(fresh, indent=2))
    print(f"  ✓ Ledger reset: equity {equity:.2f}, 0 open, 0 closed")

    # 3. Kill switch
    if HALT_FILE.exists():
        HALT_FILE.unlink()
        print("  ✓ Removed kill-switch file state/HALT")

    print("\nNOW RESTART THE SERVER (the drawdown halt lives in memory):")
    print('  cd "$(dirname "$0")/.." 2>/dev/null; python3 server.py')
    return 0


if __name__ == "__main__":
    sys.exit(main())
