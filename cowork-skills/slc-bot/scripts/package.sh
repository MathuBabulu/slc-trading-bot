#!/usr/bin/env bash
# Repackage this skill folder into slc-bot.skill (a .skill bundle is a plain zip).
# Usage:  bash scripts/package.sh  [output_dir]
# Run from the skill root (the folder containing SKILL.md) or anywhere — it resolves its own path.
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAME="$(basename "$SKILL_DIR")"
OUT_DIR="${1:-$SKILL_DIR/..}"
OUT="$OUT_DIR/$NAME.skill"

cd "$SKILL_DIR/.."
# clean caches so they don't get bundled
find "$NAME" -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
find "$NAME" -name '*.pyc' -delete 2>/dev/null || true
rm -f "$OUT"
zip -r -q -X "$OUT" "$NAME" -x '*/__pycache__/*' '*.pyc' '*/.DS_Store'
echo "packaged: $OUT"
unzip -l "$OUT" | sed 's/^/  /'
