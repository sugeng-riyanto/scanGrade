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
#   deploy/school_authority_gate.py
#     whether every teacher-facing write route files its rows under the school of
#     *this* request. A teacher can be an active member of more than one NPSN, so a
#     route has two schools it could name, and `school_id` is the column that scopes
#     a paper's weighting, its pupils, its marks and its reports: a write that
#     resolves it from a body, the profile's school or the session crosses a tenant
#     boundary while every page still answers 200. Four releases shipped that defect
#     and each was found by hand; this is the general check.
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
#   tests/unit/test_essay_answer_integrity.py
#     the essay editor, executed in a real DOM (jsdom) against the module the page
#     loads: the list buttons, the stored text, the caret, the plain-text paste. It
#     is here because it is the one check whose dependency can silently be missing:
#     without `node_modules` every DOM case skips and the run still exits 0, so the
#     half of the feature that touches a pupil's answer would be the half nothing
#     read. A skipped test is refused below, not counted as a pass.
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
#   tests/unit/test_static_tree.py
#     whether every file under app/static is one the app asks for, and whether it
#     is in a commit at all. The directory is served wholesale, so a scratch file
#     dropped in it is a page on the site — reachable by anyone who guesses the
#     name — with no template, no route and no reader; and a file git does not
#     track is a page that exists on one machine and nowhere else. What counts as
#     *asked for* is a static URL (`/static/<path>`, `asset_v('<path>')`,
#     `url_for('static', filename=...)`), a file beside it under app/static that
#     names it, or a directory the app builds URLs under. A bare mention of the
#     name is not a reference, and that is load-bearing: a route that answers
#     `/loaderio-<hash>.html` is exactly how a redundant static copy of that page
#     went unnoticed in the served tree.
#
#   tests/unit/test_no_committed_secrets.py
#     the one check here that is not about whether a page can be read, and it is
#     here for the same reason as the rest: nothing else on the release path
#     looks at it. A Supabase **service-role** key spelled out in the repository
#     bypasses every RLS policy — it is the one credential the whole design leans
#     on the database to survive — and a literal Flask secret lets anyone who
#     reads the public repo forge a session cookie or a CSRF token. The scan
#     reads every tracked file for a JWT whose payload says `service_role`, and
#     refuses a hard-coded Flask secret, while a two-character stand-in (the shape
#     a test fixture has and no real key does) is deliberately not a finding, so
#     the guard cannot pass by looking at nothing nor fail on a placeholder that
#     grants nothing.
#
#   deploy/supabase_region.py
#     which project this app talks to, and where that database is. `AGENTS.md`
#     recorded the project ref one letter short — 19 characters where the real one
#     has 20 — and it was copied from there into a merge runbook and the Windows dev
#     script; the same bullet put the database in Singapore on a **UU PDP
#     disclosure**. Nothing read either fact for weeks. The tool holds the record
#     to itself, to the disclosure, to the capacity row, and to this checkout's
#     `.env` when there is one; a release whose `.env` names another project is a
#     release whose data is in another tenant, and every page still answers 200.
#     Where there is no `.env` it *says so* in one line (`the configuration was
#     NOT measured`) instead of skipping the comparison, and the sentence this
#     gate prints then claims only the record.
#
#   tests/unit/test_merge_runbook.py (as a command, not a pytest file)
#     whether `docs/deploy/QUESTION_TYPES_MERGE_RUNBOOK.md` still says which side
#     won each file. Its tables are a measurement — the page prints the recipe and
#     the counts — and a record nobody re-runs is a paragraph claiming to be one.
#     The module re-applies the classifier to the merge the page records and
#     refuses a row whose label has drifted. One case is not a refusal, on purpose:
#     a **shallow clone** does not carry the three historical commits the record
#     names, and that is a property of the box rather than of the release — so it
#     exits 0 and says `were NOT re-run`, and the sentence below withholds its
#     claim. A full clone whose refs are missing is exit 2, and a doc whose labels
#     disagree is exit 1.
#
# All of them belong in this gate because they fail the same way — invisibly,
# with the page answering 200 and the other theme looking fine. The secret scan
# fails invisibly in the sharpest sense: the release works exactly as intended,
# and hands the key that guards every other tenant to anyone who opens the repo.
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
#      SQL to read, a build that failed, or a checkout the app will not construct
#      in because it holds more modified tracked files than its own scan bound.
#      Nothing is wrong with *the release*, and nothing about it was read either,
#      which is the difference from exit 1: the deploy says which of the two it
#      was and, like the other refusals, rolls back — carrying on would ship a
#      commit nobody read, which is the same as having no gate at all (the
#      preflight refuses the run outright when this box cannot run the gate, so
#      reaching here with exit 2 means the box changed between the two).
#   3  the release **removed the check** — one of the files below is gone, the
#      named tests collected nothing, or a named test *skipped* instead of
#      running. That is a property of the release, not of the box, and it is
#      refused like any other finding: a gate someone can delete is a gate that
#      stops running, silently, from that commit onwards.
#
#      A skip is the quieter form of the same thing, and the one this gate used
#      to wave through: pytest exits 0 for a suite whose dependencies are absent
#      and whose tests therefore all declined to run, which is the same green a
#      real pass prints. The essay editor is where that was live — its DOM half
#      needs node and `jsdom`, so a checkout with no `node_modules` ran the
#      Python half of `test_essay_answer_integrity.py`, skipped every case that
#      executes the editor, and reported the release tested. The arm below refuses
#      it either way: 3 when the box has `node_modules` (the release's own tooling
#      is incomplete) and 2 when it has none at all (this box never installed it).
#
#   tests/unit/test_template_artifacts.py
#     a Jinja closer left in a template's *text* — an edit that replaces a tag and
#     keeps its `%}`. Jinja has no opener to consume it, so it renders: the reader
#     sees `%}` on the sheet. It is checked here rather than trusted to the suites
#     because presence is asserted everywhere and absence nowhere: the print suite
#     renders this document and passes with a stray line of punctuation on it.
#
#   tests/unit/test_rendered_absence.py
#     what a rendered page must never *show* — a Python `None`, an unrendered
#     `{{ }}`, a float `NaN`, a traceback, a pasted password — swept over every
#     document `deploy/render_fixture_gate.py` renders. The suite asserts presence
#     between three and twenty-four times per file and absence almost nowhere, so a
#     page that grows an extra value satisfies every one of those assertions; the
#     marker list is measured against the whole corpus at zero hits before it ships.
#
#   tests/unit/test_shell_path_portability.py
#     a test or tool that runs a shell by an absolute path — `["/bin/bash",
#     script]`, `os.system("/bin/sh -c …")`, `shutil.which("/bin/bash")` — which
#     passes on the box and dies on the Windows checkout the same suite also runs
#     on; and the same claim one line earlier, in a tracked script's `#!` line.
#     The rule is about paths rather than interpreters: a bare name,
#     `sys.executable`, `#!/usr/bin/env bash` and `#!/bin/sh` all pass. Python is
#     read with `ast` rather than grepped, because a sentence *about* `/bin/bash`
#     is not a process call and a guard that reads prose refuses its own
#     explanation; it reads `tests/` and `deploy/` — the trees a checkout runs on
#     either machine — and the shebang half reads the files git tracks, so a
#     scratch script in a working tree is not a claim about what a clone has.
#
#   deploy/render_fixture_gate.py
#     whether the report documents and the fixtures that render them still agree.
#     A sheet that starts reading a key its fixture never supplies draws the hole as
#     an empty string — a 200, no error, and every assertion about the rest of the
#     sheet still passing — so the gate renders each document from each fixture with
#     `StrictUndefined` and refuses the release when a fixture lags its reader.
#
# Usage:  bash deploy/theme_gate.sh
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# Word-split on purpose: pytest takes them as separate paths.
TESTS="tests/unit/test_dark_theme_contrast.py tests/unit/test_tailwind_class_names.py tests/unit/test_theme_stylesheet.py tests/unit/test_language_toggle.py tests/unit/test_i18n_coverage.py tests/unit/test_css_freshness.py tests/unit/test_landing_facilities.py tests/unit/test_static_tree.py tests/unit/test_no_committed_secrets.py tests/unit/test_render_fixture_gate.py tests/unit/test_template_artifacts.py tests/unit/test_rendered_absence.py tests/unit/test_essay_answer_integrity.py tests/unit/test_essay_toolbar.py tests/unit/test_doc_links.py tests/unit/test_supabase_region.py tests/unit/test_ci_workflows.py tests/unit/test_shell_path_portability.py tests/unit/test_exam_graph_local.py tests/unit/test_scanmeet_vendored_sdk.py tests/unit/test_schema_bootstrap.py"

# The app's own refusal markers, and the reason they are named here rather than
# spelled out in the block that reads them: they are literals in
# `app/utils/checkout_integrity.py`, so a rename there would leave this gate
# matching a string nothing prints — it would fall through to "this release ships
# an unreadable element" and blame a release for a checkout. The two assignments
# are compared against the module's own constants by
# `tests/unit/test_checkout_integrity.py`.
CONSTRUCT_MARKER="SCANGRADE-UNREPRODUCIBLE"
ALLOW_DIRTY_VAR="SCANGRADE_ALLOW_DIRTY_CHECKOUT"

# ── Armament ─────────────────────────────────────────────────────────────────
# Everything that makes this a gate: the checks themselves, and the three tools
# they run through. A commit that deletes one of these is not a release with a
# defect — it is the release that removes the check which would have found the
# next defect, and every release after it ships unexamined. So the list is
# checked up front, and a missing file is exit 3 (about the release), not exit 2
# (about the box).
ARMAMENT="$TESTS tests/unit/test_merge_runbook.py deploy/i18n_coverage.py deploy/css_freshness.py deploy/schema_contract.py deploy/render_fixture_gate.py deploy/school_authority_gate.py deploy/doc_links.py deploy/doc_links_gone.txt deploy/supabase_region.py"
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
REFUSED=""
for candidate in "$REPO/.venv/bin/python" "$REPO/.venv/Scripts/python.exe" python3 python; do
  case "$candidate" in
    */*) path="$candidate" ;;
    *) path=$(command -v "$candidate" 2>/dev/null) || continue ;;
  esac
  [ -n "$path" ] && [ -x "$path" ] || continue
  # `command -v` finds a name, not an interpreter. Windows ships an App Execution
  # Alias for python3 that is executable, sits on PATH and prints "Python was not
  # found" instead of running anything — so this gate picked it, every tool it ran
  # printed that line, and the failure surfaced as "the language coverage check
  # refused this release": a Windows box blamed for a translation regression it did
  # not have. A candidate has to answer before it is trusted.
  if "$path" -c 'import sys' >/dev/null 2>&1; then PY="$path"; break; fi
  REFUSED="$REFUSED
    $path (executable, but does not run Python)"
 done
if [ -z "$PY" ]; then
  echo "theme gate: no working python interpreter found — cannot run the check" >&2
  if [ -n "$REFUSED" ]; then
    echo "            Tried, and each was refused:$REFUSED" >&2
    echo "            Install Python 3.12 or create .venv in this checkout." >&2
  fi
  exit 2
fi

# -p no:cacheprovider keeps the gate from writing .pytest_cache into a checkout
# the deploy has just declared clean; a dirty tree there blocks the next release.
cd "$REPO" || exit 2
# `-rs` is not decoration: the skip arm below names each test that declined to
# run, and a summary count alone sends its reader to the wrong file.
OUTPUT=$("$PY" -m pytest $TESTS -q -rs -p no:cacheprovider --no-header 2>&1)
RC=$?

# ── The one refusal that is about the checkout and not the release ───────────
#
# `app/utils/checkout_integrity.py` is asked at construction, and one of its
# refusals is reachable here: a checkout with more modified tracked paths than its
# scan bound is one the app will not construct in, so every check above comes back
# "error at setup" — once per test, in a wall of `E   SystemExit: 1`. Read as a
# release defect that is a false entry in the ledger, and it is the state a laptop
# mid-feature is permanently in: the checks never ran, so there is nothing to
# blame on the release and nothing to pass either.
#
# Hence exit 2, and the refusal's own remedy printed with it. The cap is the one
# refusal a development run may answer for itself (`SCANGRADE_ALLOW_DIRTY_CHECKOUT`,
# which the refusal itself names), and a box that serves and the deploy's probe
# refuse that permission, so the workaround is never the answer for a release.
# A *measured* blob — a path whose stored bytes no checkout of HEAD can reproduce
# — is not this case: it is a property of the release, so it stays a finding.
# checkout_refusal:start
case "$OUTPUT" in
  *"$CONSTRUCT_MARKER"*)
    case "$OUTPUT" in
      *"$ALLOW_DIRTY_VAR"*)
        echo >&2
        echo "theme gate: NOT CHECKED — this checkout has more modified tracked files" >&2
        echo "            than the app's own scan bound, so the app refused to construct" >&2
        echo "            and not one check in this gate ran. Nothing here is a finding" >&2
        echo "            about the release; and nothing here is a pass either, which is" >&2
        echo "            why this is exit 2 rather than exit 1." >&2
        echo >&2
        echo "            A development checkout answers the question for itself, which" >&2
        echo "            is the refusal's own remedy — it is refused on a box that" >&2
        echo "            serves and in the deploy's probe, so it can never answer it" >&2
        echo "            for a release:" >&2
        echo "                $ALLOW_DIRTY_VAR=1 bash deploy/theme_gate.sh" >&2
        exit 2
        ;;
      *)
        echo >&2
        echo "theme gate: FAILED — a path in this checkout stores bytes no checkout of" >&2
        echo "            HEAD can reproduce, so the app will not construct in it and not" >&2
        echo "            one check in this gate ran. That is a property of the release:" >&2
        echo "            it can neither be merged nor explained while it serves, and the" >&2
        echo "            remedy is not a restore (the path's own filter writes through" >&2
        echo "            it) — HEAD's bytes go in verbatim and git is told to read that" >&2
        echo "            path without the filter. The deploy's own heal does exactly that" >&2
        echo "            before this gate runs, so reaching here means it did not happen:" >&2
        echo >&2
        printf '%s\n' "$OUTPUT" | grep -E '^SCANGRADE-UNREPRODUCIBLE|^ +[^ ]' | head -12 >&2
        exit 1
        ;;
    esac
    ;;
esac
# checkout_refusal:end

# The coverage table is printed, not just checked: the gate is where a release is
# looked at, and "54.0% across 115 templates" is the number that says whether the
# translation half of this app moved. Its exit codes are its own (0 pass, 1 a
# page lost bilingual copy, 2 cannot compare) and the tool explains itself, so the
# only job here is to run it and relay the verdict.
COVERAGE=$("$PY" "$REPO/deploy/i18n_coverage.py" 2>&1)
COV_RC=$?
echo "$COVERAGE"
if [ "$COV_RC" -eq 2 ]; then
  # Exit 2 is the tool saying it could not measure, which is a property of this
  # box and not of the release: an unreadable template, a missing baseline. Said
  # in its own words, because a refusal here names a page a release made worse.
  echo >&2
  echo "theme gate: COULD NOT MEASURE — deploy/i18n_coverage.py exited 2." >&2
  echo "            Nothing is being refused; this box could not read the tree." >&2
  exit 2
fi
if [ "$COV_RC" -ne 0 ]; then
  echo >&2
  echo "theme gate: FAILED — the language coverage check refused this release." >&2
  exit 1
fi

# The documentation half: a page is the only part of this repository nothing
# executes, so a path it names rots in silence. Both kinds were in this tree when
# the check was written — a merge runbook listing six files it had dropped, and a
# `## Key Files` block naming four templates a rename had left behind — and every
# one of those pages opened, rendered, and sent its reader nowhere. Its exit codes
# are its own: 0 pass, 1 a reference names something this repository does not track
# (or the recorded-exception list has gone stale), 2 could not measure — no tracked
# markdown, no git in the tree — which is a property of the box and refuses nothing.
# It asks the *repository* and not the disk, so the answer is the same for the box
# that clones it as for the box that wrote it — and a page naming a file that has
# been written but not yet `git add`ed is exactly the state a commit is about to
# fix, which is why this gate is the one place that asks.
# doclinks:start
DOCLINKS_CLAIM=""
DOCLINKS_OUT=$("$PY" "$REPO/deploy/doc_links.py" 2>&1)
DOCLINKS_RC=$?
if [ "$DOCLINKS_RC" -eq 1 ]; then
  echo >&2
  echo "theme gate: FAILED — a page, a docstring or a gate's own message names a" >&2
  echo "            path this repository does not track. Nothing renders wrong and every" >&2
  echo "            page still answers 200: the cost is paid later, by whoever follows the" >&2
  echo "            page — or reads the \"run this script\" line mid-incident — and finds" >&2
  echo "            nothing there. The same is true of a link's '#section': the page is" >&2
  echo "            there and the heading is not, so the reader lands at the top of a page" >&2
  echo "            that no longer says what the link promised. If the file exists on this" >&2
  echo "            box, it is not in git — stage it, or drop the reference." >&2
  printf '%s\n' "$DOCLINKS_OUT" | sed 's/^/    /' >&2
  exit 1
fi
if [ "$DOCLINKS_RC" -eq 2 ]; then
  echo "theme gate: the documentation links were NOT checked — this release carries" >&2
  echo "            no claim that its pages resolve. That is a property of this box," >&2
  echo "            not of the release." >&2
  printf '%s\n' "$DOCLINKS_OUT" | sed 's/^/    /' >&2
fi
if [ "$DOCLINKS_RC" -eq 0 ]; then
  DOCLINKS_CLAIM=", and every path a page, a docstring or a gate's own message names is one this repository tracks, with every anchor a link names one the page it points at carries"
  printf '%s\n' "$DOCLINKS_OUT"
fi
# doclinks:end

# ── The merge runbook's labels, re-run ──────────────
# Its tables claim which side won each file, and the page prints the recipe that
# classified them. Re-running that recipe is what makes the record a measurement
# instead of prose: a row relabelled, a section retitled, or a row moved into the
# wrong table are all green in every other check and wrong to the reader who acts on
# them. It runs here because the pre-commit hook is where a page is still changeable;
# the suite keeps its own assertions about the real document.
#
# The one non-refusal is a **shallow clone**. A record of three historical commits
# cannot be re-read where they were never fetched, and that is a property of the box
# — the way somebody cloned it — not of the release. So that case exits 0 and says
# `were NOT re-run`, and the claim below is withheld on that phrase. Exit 2 (a full
# clone whose refs are missing, or a git that will not answer) is this box failing to
# look, reported rather than refused, exactly as the other tool blocks do.
# runbook:start
RUNBOOK_CLAIM=""
RUNBOOK_OUT=$("$PY" -m tests.unit.test_merge_runbook 2>&1)
RUNBOOK_RC=$?
if [ "$RUNBOOK_RC" -eq 1 ]; then
  echo >&2
  echo "theme gate: FAILED — the question-types merge runbook no longer says which" >&2
  echo "            side won each file. Nothing renders wrong and the page still" >&2
  echo "            opens: a reader acts on a row that the recorded merge contradicts." >&2
  echo "            The tool names the row and the evidence either way:" >&2
  echo "                $PY -m tests.unit.test_merge_runbook" >&2
  printf '%s\n' "$RUNBOOK_OUT" | sed 's/^/    /' >&2
  exit 1
fi
if [ "$RUNBOOK_RC" -eq 2 ]; then
  echo "theme gate: the merge runbook's labels were NOT re-run — this clone does not" >&2
  echo "            carry the commits the record names, or git would not answer. That" >&2
  echo "            is a property of this box, not of the release, so nothing is" >&2
  echo "            refused and nothing is claimed either." >&2
  printf '%s\n' "$RUNBOOK_OUT" | sed 's/^/    /' >&2
fi
if [ "$RUNBOOK_RC" -eq 0 ]; then
  case "$RUNBOOK_OUT" in
    *"were NOT re-run"*)
      # A shallow clone: the tool's own sentence says it did not compare, so the
      # success sentence must not claim that it did.
      printf '%s\n' "$RUNBOOK_OUT" ;;
    *)
      RUNBOOK_CLAIM=", and every row of the question-types merge runbook still names the side the recorded merge gives its file"
      printf '%s\n' "$RUNBOOK_OUT" ;;
  esac
fi
# runbook:end

# ── Which project this app talks to, and where that database is ─────────────
# The ref was wrong in the tree for weeks — a 19-character typo in `AGENTS.md`,
# copied from there into a merge runbook and the Windows dev script — and the
# same bullet had the database in Singapore on a UU PDP disclosure. Nothing read
# either one: the app takes its URL from `.env`, and `.env` was right. So the
# tool holds the record to itself and to the disclosure, and to this checkout's
# `.env` where there is one.
#
# Its exit codes are its own: 0 the record is one ref in one region (and the
# configuration agrees or is named as unmeasured), 1 a real disagreement, 2 could
# not measure (no tracked files, no git). A run with no `.env` is not a skip and
# not a finding: it prints `the configuration was NOT measured` and the success
# sentence below claims only the record — the box, whose `.env` is what the app
# serves from, gets the other half judged.
# region:start
REGION_CLAIM=""
REGION_OUT=$("$PY" "$REPO/deploy/supabase_region.py" 2>&1)
REGION_RC=$?
if [ "$REGION_RC" -eq 1 ]; then
  echo >&2
  echo "theme gate: FAILED — the project ref or the database's region is not one" >&2
  echo "            value in this release. A host that reaches another Supabase" >&2
  echo "            project is another tenant's data, and a disclosure that names" >&2
  echo "            the wrong region is a UU PDP statement that is false; every page" >&2
  echo "            still answers 200 in both cases." >&2
  printf '%s\n' "$REGION_OUT" | sed 's/^/    /' >&2
  exit 1
fi
if [ "$REGION_RC" -eq 2 ]; then
  echo "theme gate: the project ref and the database's region were NOT read — this" >&2
  echo "            checkout has no git or no tracked files, so nothing about the" >&2
  echo "            release's record was proven. That is a property of this box, not" >&2
  echo "            of the release, so nothing is refused." >&2
  printf '%s\n' "$REGION_OUT" | sed 's/^/    /' >&2
fi
if [ "$REGION_RC" -eq 0 ]; then
  REGION_CLAIM=", and every place this repository records the Supabase project — the ref in six files, the capacity row and every dashboard link — names one project in one region, which is the one region the UU PDP disclosure and the capacity page both render from configuration instead of spelling"
  # Only a run that read the configuration may say it agreed with it. The phrase
  # is the tool's own sentence, and the suite pins both halves of this contract.
  case "$REGION_OUT" in
    *"the configuration was measured"*)
      REGION_CLAIM="$REGION_CLAIM, confirmed against this checkout's .env" ;;
  esac
  printf '%s\n' "$REGION_OUT"
fi
# region:end

# The report documents and the fixtures that render them, in one matrix: every
# document against every fixture, rendered with `StrictUndefined`, so a key a
# document reads and a fixture does not supply is a refusal rather than a blank on
# the sheet. Its exit codes are its own — 0 pass, 1 a fixture lags its reader or a
# document has none, 2 could not measure (the app would not build, a fixture would
# not import) — and 2 is a property of this box, so it is said loudly and nothing is
# refused: a laptop whose venv is half-built is in no position to reject a release.
FIXTURES_CLAIM=""
FIXTURES_OUT=$("$PY" "$REPO/deploy/render_fixture_gate.py" 2>&1)
FIXTURES_RC=$?
if [ "$FIXTURES_RC" -eq 1 ]; then
  echo >&2
  echo "theme gate: FAILED — a report fixture and a report document no longer agree." >&2
  echo "            A sheet prints a hole where the missing key belongs: the page still" >&2
  echo "            answers 200, nothing raises, and the fixture's own tests still pass" >&2
  echo "            because they assert about the parts that did render." >&2
  echo "$FIXTURES_OUT" | sed 's/^/    /' >&2
  exit 1
fi
if [ "$FIXTURES_RC" -eq 2 ]; then
  echo "theme gate: the report documents were NOT rendered against their fixtures —" >&2
  echo "            the app would not build or a fixture would not import here. That is a" >&2
  echo "            property of this box, not of the release, so nothing is refused." >&2
  echo "$FIXTURES_OUT" | sed 's/^/    /' >&2
fi
if [ "$FIXTURES_RC" -eq 0 ]; then
  FIXTURES_CLAIM=", and every report document is rendered from a fixture with no key missing"
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
  echo >&2
  echo "theme gate: FAILED — the code and the database disagree, a policy lets in a" >&2
    echo "            caller with no session, or a role is compared against a name the" >&2
    echo "            database cannot hold. Each one answers 200 with the wrong page: a" >&2
    echo "            refused query renders empty, a row any caller can read is a row" >&2
    echo "            that is public, and a branch that can never be true is a guard" >&2
    echo "            that is not there." >&2
  echo "$SCHEMA_OUT" | sed 's/^/    /' >&2
  exit 1
fi

# ── The same question through the other door: has this repository's SQL been ─
# ── applied to the database it names? ───────────────────────────────────────
# The check above reads the *files*, and it is happy either way. This one asks the
# live schema, and it is the only thing in this repository that can see a migration
# it **carries** and the database never got — written, reviewed, merged, never pasted
# into the SQL Editor. That state is silent from every other direction: the offline
# half reads only the files, the app imports and starts, and the first query that
# names the missing object is refused (PGRST205 for a table, 42703 for a column) into
# an empty page instead of an error. `--require-applied` exits 1 exactly then.
#
# Exit 2 (no SQL to read, no credentials, an API that will not answer) is a property
# of THIS BOX, and it must not wear this gate's exit 2: the deploy reads exit 2 as
# "this is not a release to ship" and rolls it back and quarantines it, so a laptop
# with no .env — or one Supabase call that timed out — would take a good release down.
# It is said loudly and the release carries on, which is the rule every gate here
# follows about "could not measure".
# applied_check:start
# What the success line may claim, decided by whether the question was answered at
# all. A gate that says "every object this repository declares is in the live schema"
# on a box with no credentials is claiming a measurement nobody took — the same
# defect as a page that reports a number it never read.
APPLIED_CLAIM=""
APPLIED_OUT=$("$PY" "$REPO/deploy/schema_contract.py" --require-applied 2>&1)
APPLIED_RC=$?
if [ "$APPLIED_RC" -eq 1 ]; then
  echo >&2
  echo "theme gate: FAILED — this repository declares a table or a column the live" >&2
  echo "            database does not have, so a migration it carries was never applied." >&2
  echo "            Nothing is broken yet and every page still answers 200: the query that" >&2
  echo "            names it is refused (PGRST205 / 42703) and renders empty. Apply the" >&2
  echo "            migration, then commit this again:" >&2
  echo "                .venv/bin/python deploy/apply_migration.py <file>.sql --commit" >&2
  echo "            If it was applied moments ago, PostgREST may not have reloaded its" >&2
  echo "            schema cache yet — re-run this before looking anywhere else." >&2
  echo "$APPLIED_OUT" | sed 's/^/    /' >&2
  exit 1
fi
if [ "$APPLIED_RC" -eq 2 ]; then
  echo "theme gate: the live schema was NOT asked whether this release's SQL has been" >&2
  echo "            applied — no credentials here, or the API could not answer. That is a" >&2
  echo "            property of this box, not of the release, so nothing is refused." >&2
  echo "$APPLIED_OUT" | sed 's/^/    /' >&2
fi
if [ "$APPLIED_RC" -eq 0 ]; then
  APPLIED_CLAIM=", and every table and column this repository declares is one the live database already has"
fi
# applied_check:end

# ── The tenant half: which school is this write *for*? ──────────────────────
# Every teacher-facing write route files its rows under the school of *this* request.
# A teacher can be an active member of more than one NPSN since migration 044, so a
# route has two schools it could name — the one this request acts in
# (`g.user_school_id`) and the one their profile names — and `school_id` is the column
# that scopes a paper's weighting, its pupils, its marks and its reports. Four
# releases shipped a route that resolved it from a request body, from the profile's
# school or from the session, and every one was found by reading a diff. This is the
# general check, so the fifth is refused by the build instead of by a reader.
#
# Its exit codes are its own and the tool explains them: 0 clean, 1 a route that
# resolves its school from somewhere else, 2 could not measure (no route modules, or
# one that will not parse) — the box's property, relayed as such rather than as a
# finding.
SCHOOL_OUT=$("$PY" "$REPO/deploy/school_authority_gate.py" 2>&1)
SCHOOL_RC=$?
if [ "$SCHOOL_RC" -eq 1 ]; then
  echo >&2
  echo "theme gate: FAILED — a teacher-facing write route resolves the school it files" >&2
  echo "            a row under from somewhere other than this request's active school." >&2
  echo "            Every page still answers 200 and the app still starts; the row lands" >&2
  echo "            in the wrong tenant, where it scopes that school's weighting, pupils," >&2
  echo "            marks and reports." >&2
  echo "$SCHOOL_OUT" | sed 's/^/    /' >&2
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

# ── A test that skipped is a check that did not run ─────────────────────────
# pytest exits 0 for a suite in which every test skipped, and its summary says
# "passed" either way. So a checkout whose JS tooling was never installed runs the
# Python half of `test_essay_answer_integrity.py`, declines every case that
# executes the editor in a real DOM, and hands back the same green a full run
# prints — the release is then "tested" by a run that never touched it. The exit-5
# arm below covers a file that collected nothing; this covers the quieter version,
# where the tests exist and refused to run.
# skip_arm:start
SKIPPED=$(printf '%s\n' "$OUTPUT" | grep -oE '[0-9]+ skipped' | grep -oE '[0-9]+' | tail -1 || true)
SKIPPED=${SKIPPED:-0}
if [ "$RC" -eq 0 ] && [ "$SKIPPED" -gt 0 ]; then
  echo >&2
  echo "theme gate: these checks did not run:" >&2
  # pytest indents its short summary; matching `^SKIPPED` would print the header
  # and no test names, which is a refusal that says "some check" and no more.
  printf '%s\n' "$OUTPUT" | grep -E '^[[:space:]]*SKIPPED' | sed 's/^ */    /' >&2
  if [ -d "$REPO/node_modules" ]; then
    echo >&2
    echo "theme gate: DISARMED — $SKIPPED check(s) skipped while the rest passed." >&2
    echo "            A skip is not a pass: pytest exits 0 and nothing here read the" >&2
    echo "            code that test stands for. This box has node_modules, so the" >&2
    echo "            dependency the check needs is missing from the release's own" >&2
    echo "            tooling — the check cannot prove what this commit ships." >&2
    echo "            Install it and run this again:" >&2
    echo "                npm install" >&2
    echo "            Refusing (exit 3): a check that declines to run is a check that" >&2
    echo "            is no longer there." >&2
    exit 3
  fi
  echo >&2
  echo "theme gate: the checks were NOT all run — $SKIPPED skipped, and this box has" >&2
  echo "            no node_modules at all, so its JS half cannot run here. Nothing" >&2
  echo "            about the release is wrong, and nothing about it was proven." >&2
  echo "                npm install" >&2
  exit 2
fi
# skip_arm:end

if [ "$RC" -eq 0 ]; then
  if [ "$SCHEMA_RC" -eq 2 ]; then
    echo >&2
    echo "theme gate: the schema contract COULD NOT RUN — this release is NOT checked" >&2
    echo "            against the database. Nothing about the release is wrong; this" >&2
    echo "            box cannot answer the question." >&2
    echo "$SCHEMA_OUT" | sed 's/^/    /' >&2
    exit 2
  fi
  if [ "$SCHOOL_RC" -eq 2 ]; then
    echo >&2
    echo "theme gate: the school authority check COULD NOT RUN — this release is NOT" >&2
    echo "            checked for teacher writes that resolve the wrong school. Nothing" >&2
    echo "            about the release is wrong; this box cannot answer the question." >&2
    echo "$SCHOOL_OUT" | sed 's/^/    /' >&2
    exit 2
  fi
  if [ "$CSS_RC" -eq 0 ]; then
    echo "$SCHEMA_OUT"
    echo "$CSS_OUT"
    echo "$FIXTURES_OUT"
    echo "$SCHOOL_OUT"
    echo "theme gate: OK — readable in both themes, every named utility is compiled, the committed stylesheet is the one the templates produce, the app's own stylesheet stays a cached file, every file under /static/ is one the app asks for and in a commit, every page declares the language of its own copy, no template prints a Jinja closer as text, no rendered document shows a value only a server-side object could produce, no template translates less than it did, the essay editor's behaviour was executed in a real DOM rather than skipped, no tracked file carries a service-role key or a hard-coded Flask secret, and every table, column and policy the code names is one this repository declares, with every role it compares against one the database holds${APPLIED_CLAIM}${FIXTURES_CLAIM}, and every facility the landing page advertises one this repository can show, and every teacher-facing write route files its rows under the school of this request${DOCLINKS_CLAIM}${RUNBOOK_CLAIM}${REGION_CLAIM}"
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

  a file under /static/ that nothing asks for, or that git does not track:
  * if it is an asset, have a page ask for it — `/static/<path>`,
    `asset_v('<path>')` or `url_for('static', filename='<path>')`; or
  * delete it. `/static/` serves every file below it to anyone who guesses the
    name, so a scratch page there is a page on the site. A route whose path
    happens to contain the file's name is not a reference to it.

  a service-role key or a literal Flask secret in a tracked file:
  * take the value out of the repository and into the box's .env, where it
    belongs — `deploy/bootstrap.sh` now demands all four values from the
    environment (`: "${VAR:?}"`) instead of carrying them;
  * then rotate it. Removing it from HEAD does not remove it from the history,
    so the key that leaked is still readable there: see docs/SECRET_ROTATION.md
    for the swap-and-revoke steps.

  a page that does not say which language it is in:
  * translate the page and add it to TRANSLATED in tests/unit/test_language_toggle.py;
    or, if its copy is Indonesian and the toggle cannot change that, declare it:
    `{% set content_lang = 'id' %}` above the page's `{% extends %}`.

  a teacher-facing write route that resolves a school from somewhere else:
  * take the school from the request's own resolution — `filing_school_for_request()`
    for a create, `g.get("user_school_id")` for a row in this request's school — and
    never from the body, the session, or the profile's school; if the route is meant
    to write into a row, guard the row with `@require_school_access` so its school is
    known to be this caller's. The tool names the kind it found and the line it is on:
        .venv/bin/python deploy/school_authority_gate.py

  a template that translates less than the last release:
  * bind the string as a pair — `x-text="t('Indonesia','English')"` — and see which
    one it is with `python deploy/i18n_coverage.py --show <template>`; or, if the
    loss was intended (copy moved between templates), record it deliberately with
    `python deploy/i18n_coverage.py --write-baseline`. That file is committed and
    is never rewritten by a deploy: a gate that raises its own floor while a
    release is passing makes the regression the new yardstick.

  note: a release that adds bilingual copy, or a page, is *not* this failure. The
  deploy raises the box's own floors for it before this gate runs
  (`deploy/i18n_coverage.py --adopt`, box-local, only ever raised), so the gap
  between the committed file and today's tree never quarantines a release. Only a
  page below a floor it was already held to is refused.

Do not skip this: neither failure is visible to any other check. The app still
compiles, every page still answers 200, and nothing in the console complains.

EOF
echo "$OUTPUT" >&2
exit 1
