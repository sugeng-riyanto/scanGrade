#!/usr/bin/env bash
# ─── ScanGrade — the readability gate ────────────────────────────────────────
#
# Runs three static checks and exits non-zero if any would ship a page nobody
# can read — or cost every page for no reason:
#
#   tests/unit/test_dark_theme_contrast.py
#     a template, or a colour a template paints, that is unreadable — in dark
#     mode (a utility with no remap) or in light mode (a tint and the accent on
#     it, such as `bg-amber-100 text-amber-600` at 2.86:1).
#
#   tests/unit/test_tailwind_class_names.py
#     a Tailwind utility whose *name* is built at render time — `from-{{ colour }}`
#     in Jinja, or `'from-' + colour` in Alpine or a script. Tailwind reads the
#     template as text, generates nothing, and the element silently keeps its
#     ancestor's styling: white text on a white box, no error anywhere. It also
#     catches a stale tailwind.css, which is committed rather than built here.
#
#   tests/unit/test_theme_stylesheet.py
#     the app's own stylesheet put back *inside* base.html as a <style> block,
#     where it cannot be cached and rides in every page's HTML — or linked
#     before tailwind.css, which silently stops the dark remap from winning the
#     cascade.
#
# All three belong in this gate because they fail the same way — invisibly, with
# the page answering 200 and the other theme looking fine.
#
# It exists as a script rather than a bare pytest line because two places run it
# and they must run the *same* thing: the pre-commit hook (deploy/git-hooks/) and
# the auto-deploy on the VPS (deploy/scangrade-deploy.sh, as a hard gate before
# the app is reloaded). Two copies of "how the gate runs" is how one of them
# quietly stops running.
#
# The checks are static — they read the templates and parse the stylesheets.
# No database, no network, no running app, so this is safe to run anywhere and
# takes a couple of seconds.
#
# Usage:  bash deploy/theme_gate.sh
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# Word-split on purpose: pytest takes them as separate paths.
TESTS="tests/unit/test_dark_theme_contrast.py tests/unit/test_tailwind_class_names.py tests/unit/test_theme_stylesheet.py"

for check in $TESTS; do
  if [ ! -f "$REPO/$check" ]; then
    echo "theme gate: $check is missing — the check cannot run, which is not a pass" >&2
    exit 2
  fi
done

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
OUTPUT=$("$PY" -m pytest $TESTS -q -p no:cacheprovider --no-header 2>&1)
RC=$?

if [ "$RC" -eq 0 ]; then
  echo "theme gate: OK — readable in both themes, every named utility is compiled, and the stylesheet stays a cached file"
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
theme gate: FAILED — this release would ship an invisible or unreadable element.

The check names every offender, and the fix depends on which rule failed:

  in dark mode — a light utility with no treatment:
  * add the utility to the dark remap in app/static/css/theme.css, or
  * give the element an explicit dark: variant, or
  * declare a new standalone page (see STANDALONE_PAGES in the check file).

  in light mode — a tint and the text painted on it:
  * add the pair to the light-mode block in app/static/css/theme.css; the rule
    names both classes, so the tint stays as designed and only the accent moves.

  the stylesheet is back inside base.html, or linked out of order:
  * move the rules into app/static/css/theme.css and keep its <link> after
    tailwind.css — an inline block is re-sent on every page and cannot be
    cached, and the wrong order loses the dark remap with no error.

  a utility that is named but never generated:
  * run `npm run css:build` if the committed stylesheet is stale; or
  * name the class literally instead of assembling it at render time — a
    `{% set %}` map, a whole class string per branch, or just the full name.

Do not skip this: neither failure is visible to any other check. The app still
compiles, every page still answers 200, and nothing in the console complains.

EOF
echo "$OUTPUT" >&2
exit 1
