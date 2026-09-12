#!/usr/bin/env bash
# ─── ScanGrade — take a database snapshot now ────────────────────────────────
#
# Run this BEFORE pasting a migration into the Supabase SQL editor.
#
# The deploy takes its own snapshot when a release changes
# `supabase/migrations/` — but a migration applied by hand changes no file, so
# nothing can detect that one. This is the only cover for the path this project
# actually uses.
#
#   bash deploy/scangrade-db-snapshot.sh --label before-025
#   bash deploy/scangrade-db-snapshot.sh --list
#   bash deploy/scangrade-db-snapshot.sh --restore <archive>
#   bash deploy/scangrade-db-snapshot.sh --restore <archive> --dry-run
#
# It runs from the checkout, so it is usable the moment the code is there, and
# `install-auto-deploy.sh` copies it to /usr/local/bin for convenience. The repo
# copy is the source of truth: the installer used to have its own heredoc copy of
# this, which is exactly how the two drift apart.
#
# Root is required for both of its jobs: the archives hold names, phone numbers
# and exam answers and are written root-only, and --restore overwrites live data.
# ─────────────────────────────────────────────────────────────────────────────

set -uo pipefail

HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO=$(cd -- "$HERE/.." && pwd)
PYTHON="$REPO/.venv/bin/python"
TOOL="$REPO/deploy/db_snapshot.py"
OUT="${BACKUP_DIR:-/var/backups/scangrade}"
KEEP="${BACKUP_KEEP:-5}"

if [ "$(id -u)" -ne 0 ]; then
  echo "!! run this as root (current uid $(id -u))."
  echo "   Snapshots hold names, phone numbers and exam answers, so they are kept"
  echo "   root-only; --restore overwrites live data."
  exit 2
fi

[ -x "$PYTHON" ] || { echo "!! no interpreter at $PYTHON — is $REPO the checkout?"; exit 3; }
[ -f "$TOOL" ]   || { echo "!! $TOOL is missing — pull the current commit first"; exit 3; }

if [ "${1:-}" = "--list" ]; then
  found=$(ls -1 "$OUT"/scangrade-db-*.tar.gz 2>/dev/null || true)
  if [ -z "$found" ]; then
    echo "   no snapshots yet in $OUT"
  else
    ls -lht "$OUT"/scangrade-db-*.tar.gz
  fi
  exit 0
fi

# No arguments of its own beyond the pass-through: the tool validates its own
# flags, so there is one place that knows what they are.
exec "$PYTHON" "$TOOL" --repo "$REPO" --out "$OUT" --keep "$KEEP" "$@"
