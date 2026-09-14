#!/usr/bin/env bash
# ─── ScanGrade — install the repo's git hooks into this clone ────────────────
#
# Hooks are not cloned: .git/hooks/ belongs to the checkout, not to the repo, so
# an honoured hook has to be installed once per machine. Run this after cloning:
#
#     bash deploy/install-git-hooks.sh
#
# It copies the *hook entry points* only. What they run stays in the repo
# (deploy/theme_gate.sh), so re-running after a pull is what keeps a hook current
# rather than re-copying logic that could drift from it.
#
# Reversible without surprises: the previous hook, if any, is moved aside rather
# than deleted, and the file this installs is the only one it touches. To remove
# it, delete .git/hooks/<name> and rename the .bak back.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
SRC_DIR="$REPO/deploy/git-hooks"
DEST_DIR=$(git -C "$REPO" rev-parse --git-dir 2>/dev/null) || {
  echo "!! $REPO is not a git checkout — nothing to install into"; exit 1; }
# `--git-dir` is relative to the working directory it was run in, so it can come
# back as the bare ".git" while the script is elsewhere.
case "$DEST_DIR" in
  /*) ;;                                   # already absolute
  *)  DEST_DIR="$REPO/$DEST_DIR" ;;
esac
DEST_DIR="$DEST_DIR/hooks"

[ -d "$SRC_DIR" ] || { echo "!! no $SRC_DIR in this checkout"; exit 1; }
[ -d "$DEST_DIR" ] || mkdir -p "$DEST_DIR"

STAMP=$(date +%Y%m%d-%H%M%S)
installed=0

for src in "$SRC_DIR"/*; do
  [ -f "$src" ] || continue
  name=$(basename "$src")
  dest="$DEST_DIR/$name"

  if [ -e "$dest" ] && ! cmp -s "$src" "$dest"; then
    mv "$dest" "$dest.bak-$STAMP"
    echo "   existing $name moved aside to $name.bak-$STAMP"
  fi

  cp "$src" "$dest"
  chmod 0755 "$dest" 2>/dev/null || true   # no-op on Windows, which is fine
  echo "   installed $name"
  installed=$((installed + 1))
done

if [ "$installed" -eq 0 ]; then
  echo "!! $SRC_DIR has no hooks to install"; exit 1
fi

echo
echo "Done — $installed hook(s) in $DEST_DIR"
echo "They run the checks in $REPO/deploy/theme_gate.sh."
echo "To skip once: git commit --no-verify"
