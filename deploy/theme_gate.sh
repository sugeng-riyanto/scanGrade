#!/usr/bin/env bash
# ─── ScanGrade — the readability gate ────────────────────────────────────────
#
# Runs the static checks that decide whether a release would ship a page nobody
# can read, or a stylesheet that no longer matches its own templates, and exits
# non-zero if any of them would:
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
#     catches a stale tailwind.css for the colour utilities a class-bearing
#     attribute or a script names — which is only half of that question, so the
#     general answer is the check below.
#
#   tests/unit/test_css_freshness.py
#     the guard on that check, and on how the gate reads it: a stale stylesheet
#     is a failure, an unanswerable question is not, and a plain check never
#     writes the stylesheet it is checking.
#
#   deploy/css_freshness.py
#     whether the committed tailwind.css is the one these templates produce. The
#     stylesheet is committed, not built on the box, so a template that gains a
#     utility and a build nobody ran ship a class that does not exist: no error,
#     no missing resource, and an element that quietly keeps its ancestor's
#     spacing or size. It rebuilds with the project's own `css:build` command and
#     compares the bytes, so arbitrary values (`min-w-[480px]`) and the classes
#     only JavaScript names are covered too, and it names the template that asked
#     for each missing one.
#
#   tests/unit/test_theme_stylesheet.py
#     the app's own stylesheet put back *inside* base.html as a <style> block,
#     where it cannot be cached and rides in every page's HTML — or linked
#     before tailwind.css, which silently stops the dark remap from winning the
#     cascade.
#
#   tests/unit/test_language_toggle.py
#     the language half of the same promise: the document says which language it
#     is in. A page whose copy is Indonesian under `<html lang="en">` is read
#     aloud with an English voice, and a page that neither switches nor declares
#     its language makes the reader guess. Copy that was never translated cannot
#     be switched, so such a page declares `content_lang = 'id'` instead of
#     quietly claiming English.
#
#   deploy/i18n_coverage.py
#     how *much* of each template is bilingual — pairs / (pairs + leftovers) —
#     against the floors in deploy/i18n_baseline.json. It is the only check here
#     that prints a number, because the number is the point: 48 templates have no
#     version in the other language yet, and a count nobody can see is a count
#     that goes backwards. It reads templates, not pages, so a partial that
#     renders inside a translated page is counted too.
#
#   deploy/schema_contract.py
#     whether the code, the repository's SQL and the API agree about the data.
#     A `select` naming a column that does not exist is not a `None`: PostgREST
#     refuses the whole request (PGRST205 / 42703) and a route's `try/except`
#     turns it into an empty page — an empty audit log, a roster with nobody on
#     it. It also refuses a policy any caller can satisfy without a session,
#     which is the anon key every page carries: `USING (true)` on `exams` handed
#     an anonymous caller the answer key of every published paper. Offline by
#     default (it reads .py and .sql, needs no credentials); `--live` asks the
#     API and the database as well, which is how four columns and two views that
#     no file here creates were found. It also refuses a role the code compares
#     against that the `profiles.role` CHECK constraint cannot hold: `teacher`,
#     `student` and `admin` were migrated away by 007, so `role == 'teacher'` is a
#     branch that never runs and a guard that is not there.
#
#   tests/unit/test_landing_facilities.py
#     whether the landing page's *facility* claims are real, the way
#     deploy/claims_gate.py holds its performance ones. Each card names the
#     artifact that proves it (`data-facility`), and the page may not advertise a
#     facility this repository cannot show — nor keep a card for one that was
#     deleted. The question-type card is counted from
#     `question_types.PICKER_TYPES` rather than restated, because it said "3 Tipe
#     Soal" for a while after the builder had grown to six.
#
# All of them belong in this gate because they fail the same way — invisibly,
# with the page answering 200 and the other theme looking fine.
#
# It exists as a script rather than a bare pytest line because two places run it
# and they must run the *same* thing: the pre-commit hook (deploy/git-hooks/) and
# the auto-deploy on the VPS (deploy/scangrade-deploy.sh, as a hard gate before
# the app is reloaded). Two copies of "how the gate runs" is how one of them
# quietly stops running.
#
# The checks are static — they read the templates and parse the stylesheets.
# No database, no network, no running app, so this is safe to run anywhere and
# takes a couple of seconds. (The freshness check does run Node, and it is the
# one part of this gate that can report "could not measure" — a box without
# node_modules must not be able to reject a release it cannot judge.)
#
# Exit codes, and the difference between them is the whole point:
#
#   0  the release is readable, compiled, translated and matches its own SQL.
#   1  a real finding in the release. The deploy rolls back.
#   2  **this box** cannot answer the question — no node, no node_modules, no
#      SQL to read, a build that failed. Nothing is wrong with the release, and
#      a checker that breaks must not be able to take the site down, so the
#      deploy says so loudly and continues WITHOUT rolling back.
#   3  the release **removed the check** — one of the files below is gone, or the
#      named tests collected nothing. That is a property of the release, not of
#      the box, and it is refused like any other finding: a gate someone can
#      delete is a gate that stops running, silently, from that commit onwards.
#
# Usage:  bash deploy/theme_gate.sh
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# Word-split on purpose: pytest takes them as separate paths.
TESTS="tests/unit/test_dark_theme_contrast.py tests/unit/test_tailwind_class_names.py tests/unit/test_theme_stylesheet.py tests/unit/test_language_toggle.py tests/unit/test_i18n_coverage.py tests/unit/test_css_freshness.py tests/unit/test_landing_facilities.py"

# ── Armament ─────────────────────────────────────────────────────────────────
# Everything that makes this a gate: the checks themselves, and the three tools
# they run through. A commit that deletes one of these is not a release with a
# defect — it is the release that removes the check which would have found the
# next defect, and every release after it ships unexamined. So the list is
# checked up front, and a missing file is exit 3 (about the release), not exit 2
# (about the box).
ARMAMENT="$TESTS deploy/i18n_coverage.py deploy/css_freshness.py deploy/schema_contract.py"
for check in $ARMAMENT; do
  # `-s` and not `-f`: a file that exists and holds nothing is the same release
  # as one where it was deleted, only quieter.
  if [ ! -s "$REPO/$check" ]; then
    echo "theme gate: DISARMED — $check is missing or empty in this release." >&2
    echo "            It is part of the check itself, not of what the check judges," >&2
    echo "            so deleting it does not make a release pass: it makes every" >&2
    echo "            release after this one unchecked. Refusing (exit 3)." >&2
    exit 3
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

# The coverage table is printed, not just checked: the gate is where a release is
# looked at, and "54.0% across 115 templates" is the number that says whether the
# translation half of this app moved. Its exit codes are its own (0 pass, 1 a
# page lost bilingual copy, 2 cannot compare) and the tool explains itself, so the
# only job here is to run it and relay the verdict.
COVERAGE=$("$PY" "$REPO/deploy/i18n_coverage.py" 2>&1)
COV_RC=$?
echo "$COVERAGE"
if [ "$COV_RC" -ne 0 ]; then
  echo >&2
  echo "theme gate: FAILED — the language coverage check refused this release." >&2
  exit 1
fi

# The committed stylesheet is what the app serves, so it is judged here rather
# than at the moment somebody notices a page looks wrong. Its exit codes are its
# own: 0 in sync, 1 stale, 2 could not measure (no node, no node_modules on this
# box, a build that failed) — and 2 is deliberately not a 1, because a box that
# cannot rebuild a stylesheet is in no position to reject a release.
CSS_OUT=$("$PY" "$REPO/deploy/css_freshness.py" 2>&1)
CSS_RC=$?

# The data half: the code, the SQL this repository carries, and the API. Same exit
# conventions — 0 agree, 1 a real disagreement, 2 could not measure — and the same
# rule about 2: naming no SQL files is not evidence that the release is wrong.
SCHEMA_OUT=$("$PY" "$REPO/deploy/schema_contract.py" 2>&1)
SCHEMA_RC=$?

if [ "$SCHEMA_RC" -eq 1 ]; then
  echo >&2    echo "theme gate: FAILED — the code and the database disagree, a policy lets in a" >&2
    echo "            caller with no session, or a role is compared against a name the" >&2
    echo "            database cannot hold. Each one answers 200 with the wrong page: a" >&2
    echo "            refused query renders empty, a row any caller can read is a row" >&2
    echo "            that is public, and a branch that can never be true is a guard" >&2
    echo "            that is not there." >&2
  echo "$SCHEMA_OUT" | sed 's/^/    /' >&2
  exit 1
fi

if [ "$RC" -ne 0 ] && [ "$RC" -ne 5 ] && [ "$CSS_RC" -eq 0 ]; then
  echo "$CSS_OUT"   # the stylesheet half is fine; say so before the failure
fi

# A real finding outranks an inability to look: a stale or unreadable release is
# refused even when the other check could not run at all.
if [ "$CSS_RC" -eq 1 ]; then
  echo >&2
  echo "theme gate: FAILED — the committed tailwind.css is not the one these" >&2
  echo "            templates produce, so this release would serve classes that" >&2
  echo "            do not exist. Every page still answers 200." >&2
  echo "$CSS_OUT" | sed 's/^/    /' >&2
  exit 1
fi

if [ "$RC" -eq 0 ]; then
  if [ "$SCHEMA_RC" -eq 2 ]; then
    echo >&2
    echo "theme gate: the schema contract COULD NOT RUN — this release is NOT checked" >&2
    echo "            against the database. Nothing about the release is wrong; this" >&2
    echo "            box cannot answer the question." >&2
    echo "$SCHEMA_OUT" | sed 's/^/    /' >&2
    exit 2
  fi
  if [ "$CSS_RC" -eq 0 ]; then
    echo "$SCHEMA_OUT"
    echo "$CSS_OUT"
    echo "theme gate: OK — readable in both themes, every named utility is compiled, the committed stylesheet is the one the templates produce, the app's own stylesheet stays a cached file, every page declares the language of its own copy, no template translates less than it did, and every table, column and policy the code names is one this repository declares, with every role it compares against one the database holds, and every facility the landing page advertises one this repository can show"
    exit 0
  fi
  echo >&2
  echo "theme gate: the stylesheet freshness check COULD NOT RUN — this release is" >&2
  echo "            NOT checked against the templates. Nothing about the release is" >&2
  echo "            wrong; this box cannot answer the question." >&2
  echo "$CSS_OUT" | sed 's/^/    /' >&2
  exit 2
fi

# pytest exit 5 is "no tests collected": the files are there and hold nothing,
# so the gate would report green to a `grep -q passed` — or to anything reading
# the exit code as "the check finished". Same class as a deleted file: the
# release removed the check, not the defect. Exit 3, so it is refused.
if [ "$RC" -eq 5 ]; then
  echo "theme gate: DISARMED — the named checks collected no tests." >&2
  echo "            The files exist and assert nothing, which reads as a pass to" >&2
  echo "            everything except pytest itself. Refusing (exit 3)." >&2
  echo "$OUTPUT" >&2
  exit 3
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
  * name the class literally instead of assembling it at render time — a
    `{% set %}` map, a whole class string per branch, or just the full name; or
  * run `npm run css:build` and commit app/static/css/tailwind.css. The
    stylesheet is committed, so any template change that names a new utility
    needs a rebuild to go with it. To see which template asked for which missing
    class before committing: `python deploy/css_freshness.py`.

  a page that does not say which language it is in:
  * translate the page and add it to TRANSLATED in tests/unit/test_language_toggle.py;
    or, if its copy is Indonesian and the toggle cannot change that, declare it:
    `{% set content_lang = 'id' %}` above the page's `{% extends %}`.

  a template that translates less than the last release:
  * bind the string as a pair — `x-text="t('Indonesia','English')"` — and see which
    one it is with `python deploy/i18n_coverage.py --show <template>`; or, if the
    loss was intended (copy moved between templates), record it deliberately with
    `python deploy/i18n_coverage.py --write-baseline`. That file is committed and
    is never rewritten by a deploy: a gate that raises its own floor while a
    release is passing makes the regression the new yardstick.

Do not skip this: neither failure is visible to any other check. The app still
compiles, every page still answers 200, and nothing in the console complains.

EOF
echo "$OUTPUT" >&2
exit 1
