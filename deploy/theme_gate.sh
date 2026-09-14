#!/usr/bin/env bash
# ─── ScanGrade — the readability gate ────────────────────────────────────────
#
# Runs the contrast checks in tests/unit/test_dark_theme_contrast.py and exits
# non-zero if a template, or a colour a template paints, would ship unreadable
# text — in dark mode (a utility with no remap) or in light mode (a tint and the
# accent on it, such as `bg-amber-100 text-amber-600` at 2.86:1).
#
# It exists as a script rather than a bare pytest line because two places run it
# and they must run the *same* thing: the pre-commit hook (deploy/git-hooks/) and
# the auto-deploy on the VPS (deploy/scangrade-deploy.sh, as a hard gate before
# the app is reloaded). Two copies of "how the gate runs" is how one of them
# quietly stops running.
#
# The checks are static — they read the templates and parse base.html's
# stylesheet. No database, no network, no running app, so this is safe to run
# anywhere and takes a couple of seconds.
#
# Usage:  bash deploy/theme_gate.sh
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
TESTS="tests/unit/test_dark_theme_contrast.py"

if [ ! -f "$REPO/$TESTS" ]; then
  echo "theme gate: $TESTS is missing — the check cannot run, which is not a pass" >&2
  exit 2
fi

# The interpreter differs by platform: a Linux checkout has .venv/bin/python, a
# Windows one (Git Bash, where the pre-commit hook runs) has
# .venv/Scripts/python.exe. Either is preferred over the system python because
# the check imports pytest, which requirements.txt pins into the venv.
PY=""
for candidate in "$REPO/.venv/bin/python" "$REPO/.venv/Scripts/python.exe"; do
  [ -x "$candidate" ] && PY="$candidate" && break
done
if [ -z "$PY" ]; then
  for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then PY="$candidate"; break; fi
  done
fi
if [ -z "$PY" ]; then
  echo "theme gate: no python interpreter found — cannot run the check" >&2
  exit 2
fi

# -p no:cacheprovider keeps the gate from writing .pytest_cache into a checkout
# the deploy has just declared clean; a dirty tree there blocks the next release.
cd "$REPO" || exit 2
OUTPUT=$("$PY" -m pytest "$TESTS" -q -p no:cacheprovider --no-header 2>&1)
RC=$?

if [ "$RC" -eq 0 ]; then
  echo "theme gate: OK — every template and colour utility is readable in both themes"
  exit 0
fi

# pytest exit 5 is "no tests collected", which would silently disable the gate
# while looking green to a `grep -q passed`.
if [ "$RC" -eq 5 ]; then
  echo "theme gate: no checks were collected — the gate is not running" >&2
  echo "$OUTPUT" >&2
  exit 2
fi

cat >&2 <<'EOF'
theme gate: FAILED — this release would show unreadable text.

The check names every offender, and the fix depends on which half failed:

  in dark mode — a light utility with no treatment:
  * add the utility to the dark remap in app/templates/base.html, or
  * give the element an explicit dark: variant, or
  * declare a new standalone page (see STANDALONE_PAGES in the check file).

  in light mode — a tint and the text painted on it:
  * add the pair to the light-mode block in app/templates/base.html; the rule
    names both classes, so the tint stays as designed and only the accent moves.

Do not skip this: the failure is invisible to every other check. The app still
compiles, every page still answers 200, and a screenshot in the other theme
looks correct.

EOF
echo "$OUTPUT" >&2
exit 1
