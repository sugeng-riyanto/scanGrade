#!/usr/bin/env bash
# ─── ScanGrade auto-deploy ───────────────────────────────────────────────────
# Run every couple of minutes by scangrade-deploy.timer, through the launcher
# deploy/install-auto-deploy.sh installs as /usr/local/bin/scangrade-deploy. The
# launcher execs *this* file in the checkout rather than a copy of it, so this is
# the script that runs and a fix here takes effect on the next tick — Gate 0
# below refuses to run at all from a copy that has drifted from it.
#
# It takes NO arguments, on purpose: this is the one thing root runs unattended,
# so it must not be usable as a general-purpose command runner. Anything that
# could be passed in (a branch, a commit, a path) is fixed below instead.
#
# It also refuses to leave a broken release running. If the new code does not
# import, build its routes, contain a template that is readable in both themes,
# or answer on the app port, it puts the previous commit back and restarts that.
# An unattended deploy that only knows how to move forward is worse than no
# automation at all.
#
# And a refused commit is *quarantined*: rolling back moves the checkout, not the
# branch, so without that the next tick would fetch the same commit and fail the
# same way every two minutes until somebody pushed a fix. See quarantine-logic
# below — the tick after a refusal says why and does nothing until the branch
# moves (or an operator touches /etc/scangrade-deploy.release).
#
# Log: journalctl -u scangrade-deploy.service
# ─────────────────────────────────────────────────────────────────────────────

set -uo pipefail

REPO="/opt/scangrade"
SERVICE="scangrade"
BRANCH="main"
APP_PORT=8000
LOCK="/run/scangrade-deploy.lock"
PAUSE_FILE="/etc/scangrade-deploy.pause"
STATE_DIR="/var/lib/scangrade-deploy"
#: A commit a gate refused, recorded so the next tick does not walk into the
#: same gate again; and the two one-shot files that override it. See
#: quarantine-logic.
QUARANTINE_FILE="$STATE_DIR/quarantined"
#: The last few refusals, kept beside the quarantine record. A quarantine is
#: *replaced* by the next refusal and *removed* by the release that lifts it — both
#: correct for the tick, and both erasing the evidence exactly when a later release
#: has passed and somebody comes looking. So every refusal is copied here, byte for
#: byte, into a bounded directory: the current record still answers "what is held",
#: and this answers "what has been refused lately". See quarantine-logic.
REFUSALS_DIR="$STATE_DIR/refusals"
REFUSALS_KEEP=5
RELEASE_FILE="/etc/scangrade-deploy.release"
#: The same one-shot release, asked for from the app instead of a shell. The app
#: runs as the service user and cannot write /etc, so the installer creates this
#: directory owned by that user (mode 0750, so only it and root can reach it).
#: A file in it means exactly what RELEASE_FILE means, and only its *existence*
#: is read — nothing a web request can write is ever executed, so the request
#: cannot carry a command.
REQUEST_DIR="$STATE_DIR/requests"
RELEASE_REQUEST="$REQUEST_DIR/release"
#: The same directory, a different ask. A release request retries a refused commit
#: against the same yardstick; this one also rewrites the perf baseline from the box
#: as it is now, because a release the box's own drift (not the code) held can never
#: pass by retrying it. It is an explicit release too — a held commit is retried —
#: since the reason an operator asks is almost always that held commit.
REBASELINE_REQUEST="$REQUEST_DIR/rebaseline"
#: Why the last run refused to deploy for a reason that is *not* about a commit:
#: the box is not armed to check releases at all. Kept next to the quarantine
#: record because it answers the same question from the operator's side ("why is
#: nothing deploying?"), and read by /super-admin/deploy-status. Written by
#: armament_preflight, removed the moment the box is armed again.
UNARMED_FILE="$STATE_DIR/unarmed"
#: Why the last run refused a release *before it moved*: a dirty checkout, a fetch
#: that could not reach GitHub, a migration release with no recovery point, a merge
#: that could not happen. Each of those exits before the release is judged, so none
#: of them quarantines — a quarantine is a record about a *commit* — and none of
#: them leaves a symptom: the previous release serves, the site is untouched, and
#: from outside the box looks like one with no new commits. Read by
#: /super-admin/deploy-status; see preflight-logic below.
PREFLIGHT_FILE="$STATE_DIR/refused-before-merge"
#: The diff of whatever blocked that run, beside the refusal it belongs to. The
#: record names the file; a filename cannot answer "is this an unintended leftover
#: or a hotfix nobody committed?" — and those two have opposite right answers. The
#: first line is the refusal's own timestamp, which is how the page proves which
#: refusal it describes, and the same call that writes the record writes (or
#: clears) this: a diff left beside a *different* refusal reads as its cause.
PREFLIGHT_DIFF_FILE="$STATE_DIR/refused-before-merge.diff"
#: Bounded on both axes. Lines, because a diff is proportional to the change; bytes,
#: because a rebuilt minified stylesheet is one enormous line and the state
#: directory must not grow with it on every tick.
PREFLIGHT_DIFF_LINES=200
PREFLIGHT_DIFF_BYTES=24000
#: Prefix of the line the runner leaves where it cut a diff. The page searches for
#: this literal so it can say the *record* is short instead of implying the change
#: is; a test holds the two files to the same string.
PREFLIGHT_DIFF_MARK="[sg: diff truncated at"
#: (The gate configs themselves are named beside the gate that reads them, not
#: here: `test_the_deploy_passes_every_generated_setting` in the perf-gate guards
#: anchors on each conf assignment and checks the environment-list right after it,
#: so hoisting them would silently detach a setting from the gate that reads it.)
#: Where the installer puts the two launchers it renders from
#: `deploy/entrypoint.sh`. A successful release re-renders them from the checkout
#: it just deployed, so the installed file cannot fall behind the repo — see
#: refresh-launcher-logic.
INSTALLED_BIN_DIR="/usr/local/bin"
INSTALLED_RUNNER="$INSTALLED_BIN_DIR/scangrade-deploy"
INSTALLED_SNAPSHOT="$INSTALLED_BIN_DIR/scangrade-db-snapshot"
#: The recovery lever. Short on purpose — it is typed by hand on a provider console
#: where nothing can be pasted. It is the one installed name whose *root* is not the
#: checkout (see fetch-lever-logic): the box that needs it is the box whose checkout
#: cannot move, so it is rendered against `$LEVER_DIR` below and comes out of the
#: fetched branch rather than out of a release.
INSTALLED_RECOVER="$INSTALLED_BIN_DIR/sgfix"
#: Where the lever read out of `origin/$BRANCH` is kept, and the tree the installed
#: name is rendered against. Deliberately not the checkout: a lever pointing at a
#: checkout that cannot be updated is the stale tool the fetch has just beaten.
LEVER_DIR="${SG_LEVER_DIR:-$STATE_DIR/lever}"
BACKUP_DIR="/var/backups/scangrade"
BACKUP_KEEP=5
SNAPSHOT_CMD="$REPO/deploy/db_snapshot.py"
LOG_TAG="scangrade-deploy"

log() { echo "[$(date '+%F %T')] $*"; }

# ── Quarantine: a refused release is not retried forever ─────────────────────
# quarantine-logic:start
# The gates below roll a rejected release back to the previous commit. That is
# right, and it is incomplete: the rollback moves the *checkout*, not
# `origin/main`. So the next tick fetches the same commit, sees it is not what is
# deployed, pulls it, and walks into the same gate — rejected, rolled back,
# re-pulled, every two minutes, for as long as the bad commit sits on the branch.
# The symptom is a box that reloads itself (and its Celery worker) forever while
# the journal fills with the same failure, and the only way out is a human pushing
# a fix. None of it is visible from a page: the previous release is serving
# throughout.
#
# So a release that a *gate* rejects is quarantined by commit. A quarantined
# commit is not retried; the next tick says so and exits. It comes back on its
# own: the moment `origin/main` moves to another commit the quarantine is lifted,
# because a new commit is a new release and the refused one is no longer the
# question being asked.
#
# Two things deliberately do *not* quarantine, because they are properties of the
# box rather than of the release, and retrying them is the correct behaviour:
#
#   * a failed fetch, or a snapshot that could not be taken — nothing has been
#     merged at that point, and the script already says it will retry next tick;
#   * a pip install that failed, which is usually the network.
#
# And one escape hatch, for the case where a gate was right about the box and
# wrong about the release — a busy VPS that made the performance gate diverge, a
# smoke password rotated without a commit. Touching this file releases the
# quarantine for exactly one attempt and is then consumed:
#
#     touch /etc/scangrade-deploy.release
#
# It is the per-commit sibling of the pause file, not a replacement for it: the
# pause file freezes *every* deploy and stays until removed, while this one
# unfreezes a single commit and disappears on use.
#
# The super-admin deploy-status page asks for the same thing, and it cannot touch
# /etc either — which is the point, not an obstacle. So the state directory
# carries a second, weaker one: a directory the *service user* owns, where the
# app drops a file whose existence is the request. Both are read the same way and
# both are consumed by the attempt, so neither can become a standing override.

quarantine_sha() {
  [ -f "$QUARANTINE_FILE" ] || return 1
  sed -n '1p' "$QUARANTINE_FILE" 2>/dev/null || return 1
}

quarantine_reason() {
  [ -f "$QUARANTINE_FILE" ] || return 1
  sed -n '3p' "$QUARANTINE_FILE" 2>/dev/null || return 1
}

# These read two globals rather than taking arguments, because the script takes
# none — `test_deploy_script_takes_no_arguments` forbids a positional parameter
# anywhere in this file, and it is right to: this is the only thing root runs
# unattended. The globals can only ever name the one release under judgement
# (`AFTER_FULL`, set once from origin/$BRANCH), which also removes any chance of
# quarantining a commit other than the one a gate actually refused.

# ── the last few refusals, so a lift does not erase the evidence ─────────────
#
# A quarantine is at its most useful to read exactly when it is gone. The next
# commit on the branch replaces the held record, and an explicit release removes it
# outright — both correct, because the *current* refusal is the one the tick must
# act on — but the gate's numbers and the commit they are about leave the box at the
# same moment, which is when somebody has come to ask why nothing deployed. The
# perf card already recovers the *held* commit's own measurement from the gate's
# history; this keeps the whole record, so the run of refusals is readable even
# after a later release passes.
#
# A directory rather than one file, because each record has to be the quarantine
# file's *exact* bytes — a gate's own output is the evidence and framing it into a
# larger file would mean inventing a delimiter its lines could collide with. The
# name is the wall-clock second plus the candidate's short sha, so sorting by name
# is sorting by time and a record is found by the commit it names. Nothing here
# decides whether to retry: a history write that fails is ignored, because it must
# never stop a refusal from being recorded.
refusal_history_name() {
  printf 'refused-%s-%s\n' "$(date +%s)" "${AFTER:-unknown}"
}

# Keep the newest `$REFUSALS_KEEP` records and drop the rest. Names sort by time, so
# this needs no timestamps to stat: list newest-first and delete everything past the
# limit. A record is one refusal; the count is the roster's size, not a duration.
refusal_history_prune() {
  local keep="${REFUSALS_KEEP:-5}" n=0 name=""
  while IFS= read -r name; do
    [ -n "$name" ] || continue
    n=$((n + 1))
    [ "$n" -le "$keep" ] && continue
    rm -f "$REFUSALS_DIR/$name" 2>/dev/null || true
  done < <(ls -1 "$REFUSALS_DIR" 2>/dev/null | sort -r)
}

refusal_history_write() {
  local name="" n=0
  mkdir -p "$REFUSALS_DIR" 2>/dev/null || return 0
  # Readable by the app (the status page shows this) and traversable by it whatever
  # root's umask happens to be, the way the quarantine record is meant to be.
  chmod 0755 "$REFUSALS_DIR" 2>/dev/null || true
  [ -s "$QUARANTINE_FILE" ] || return 0
  name=$(refusal_history_name)
  # The name is second-resolution, so two refusals inside one second would otherwise
  # overwrite each other. Suffix rather than skip: a lost record is the very thing
  # this file exists to prevent.
  while [ -e "$REFUSALS_DIR/$name" ]; do
    n=$((n + 1))
    name="$(refusal_history_name).$n"
  done
  cp "$QUARANTINE_FILE" "$REFUSALS_DIR/$name" 2>/dev/null || return 0
  chmod 0644 "$REFUSALS_DIR/$name" 2>/dev/null || true
  refusal_history_prune
  return 0
}

# Record a refusal. Called only after a gate has judged a *merged* release, so a
# transient failure earlier in the run can never freeze a good commit.
quarantine_write() {
  local reason="${FAIL_REASON:-unknown gate}"
  # How many of the gate's lines, and how wide each one, are decided *here* rather
  # than at each gate. A gate's job is to say which of its lines are the finding;
  # the record's shape is this function's, so a gate added later that forgets to
  # bound its own output cannot put a transcript on the status page. A traceback and
  # a pytest assertion both fit; a whole log does not.
  local detail_lines=6 detail_width=400
  mkdir -p "$STATE_DIR" 2>/dev/null || true
  {
    printf '%s\n%s\n%s\n' "$AFTER_FULL" "$(date -Is)" "$reason"
    # The three lines above keep their meaning, and the gate's own words follow
    # them: the status page reads the first three positionally, so line 3 is still
    # the gate's *name* for every reader that predates this.
    if [ -n "${FAIL_DETAIL:-}" ]; then
      printf '%s\n' "$FAIL_DETAIL" \
        | cut -c-"$detail_width" | sed -n "1,${detail_lines}p"
    fi
    true
  } > "$QUARANTINE_FILE" 2>/dev/null || true
  # Kept, before the detail is cleared: this is the copy that survives the lift, and
  # it has to be the same bytes the current record has.
  refusal_history_write
  # Written once: keeping it would let the next refusal quote a gate that did not
  # refuse it, and nothing has to stay true for the record to be honest.
  FAIL_DETAIL=""
  log "QUARANTINED ${AFTER:-?} ($reason) — it will not be retried"
  log "    a new commit on $BRANCH lifts this by itself; to retry it as-is:"
  log "        touch $RELEASE_FILE"
}

# One-shot explicit release. Returns 0 either way; it only reports.
#
# Either file buys the same single attempt: the root-owned one an operator makes
# over ssh, and the request the deploy-status page makes. Both are removed here,
# so an attempt costs its file and a second refusal quarantines again — a release
# is never a standing permission for a commit a gate keeps refusing.
quarantine_honour_release() {
  local asked="" held="" path=""
  for path in "$RELEASE_FILE" "$RELEASE_REQUEST" "$REBASELINE_REQUEST"; do
    [ -e "$path" ] || continue
    asked="${asked:+$asked, }$path"
    [ "$path" = "$REBASELINE_REQUEST" ] && REBASELINE_REQUESTED=1
  done
  [ -n "$asked" ] || return 0
  held=$(quarantine_sha) || held=""
  if [ -n "$held" ]; then
    log "explicit release requested ($asked) — clearing the quarantine on ${held:0:7}"
  fi
  [ "${REBASELINE_REQUESTED:-0}" = "1" ] && log \
    "the perf gate will re-measure the box (--rebaseline): the baseline is rewritten from the box as it is now"
  rm -f "$QUARANTINE_FILE" "$RELEASE_FILE" "$RELEASE_REQUEST" "$REBASELINE_REQUEST" 2>/dev/null || true
  return 0
}

# 0 = this release may proceed; 1 = it is the quarantined commit, skip the tick.
# The log is the whole point: "nothing deployed" has to say why, here, because
# the journal is the only place an unattended box can be asked.
quarantine_gate() {
  local candidate="$AFTER_FULL" held="" reason=""
  [ -n "$candidate" ] || return 0
  held=$(quarantine_sha) || held=""
  [ -n "$held" ] || return 0
  if [ "$held" = "$candidate" ]; then
    reason=$(quarantine_reason) || reason="unknown"
    # One line, because this prints every two minutes until the branch moves.
    # The full instructions were printed once, when the refusal was recorded —
    # `journalctl` still has them, and a gate that fills the journal is a gate
    # somebody turns off.
    log "QUARANTINED ${AFTER:-?} ($reason) — not retried, so the box stops"
    log "    re-pulling it: push a fix to $BRANCH, or touch $RELEASE_FILE"
    return 1
  fi
  log "quarantine lifted: $BRANCH now points at ${candidate:0:7}, not the refused ${held:0:7}"
  rm -f "$QUARANTINE_FILE" 2>/dev/null || true
  return 0
}
# quarantine-logic:end

# ── A refusal that happens *before* the release moves ────────────────────────
# preflight-logic:start
# A quarantine is a record about a commit, which is why it is written only after a
# release has been merged and judged. That leaves the other half of "why is nothing
# deploying?" with no record at all. The run can refuse before it touches the
# checkout — a dirty tree, a fetch with no network, a migration release with no
# recovery point, a merge that cannot happen (a stale `.git/index.lock` is one) —
# and every one of those exits quietly. Nothing is merged, nothing is quarantined,
# the previous release keeps serving, and from outside the box is indistinguishable
# from one with no new commits. Not hypothetical: a box sat five commits behind for
# three hours, fetching every tick and never merging, while its own status page
# said "No Held Release" and "0 uncommitted files".
#
# So each pre-merge refusal writes down what it was: which step, when, the exit
# code, and the *command's* output rather than this script's guess about it. The
# record is positional, like the quarantine's, and the status page reads it:
#
#     line 1  the step, as a key the page has a sentence for
#     line 2  when it refused (ISO)
#     line 3  the exit code
#     line 4  the commit under judgement (`origin/$BRANCH`), or empty when the run
#             refused before there was one
#     line 5+ the runner's own words
#
# The values arrive through globals rather than arguments for the same reason the
# quarantine block's do: this script takes no arguments at any level
# (`test_deploy_script_takes_no_arguments`), and a refusal must not be able to name
# a step other than the one that refused it.
#
# It is not a quarantine and it does not try to be one: a fetch that cannot reach
# GitHub is the world's fault and not the commit's, so the next tick simply tries
# again. Nothing here decides whether to retry — the exit codes below already do.
PREFLIGHT_GATE=""
PREFLIGHT_EXIT=""
PREFLIGHT_DETAIL=""
PREFLIGHT_DIFF=""

preflight_write() {
  mkdir -p "$STATE_DIR" 2>/dev/null || true
  # One timestamp for both files: the diff's header is what ties it to the refusal
  # it belongs to, so two `date` calls would be two clocks and the page would read
  # its own diff as stale.
  local at
  at=$(date -Is)
  {
    printf '%s\n%s\n%s\n%s\n' "${PREFLIGHT_GATE:-unknown}" "$at" \
      "${PREFLIGHT_EXIT:-?}" "${AFTER_FULL:-}"
    printf '%s\n' "${PREFLIGHT_DETAIL:-}"
  } > "$PREFLIGHT_FILE" 2>/dev/null || true
  # Readable by the app (the status page shows this) and owned by root.
  chmod 0644 "$PREFLIGHT_FILE" 2>/dev/null || true

  # The diff of whatever blocked the run, when the refusal is *about* the
  # checkout's contents. Removed on every other refusal: a diff is evidence about
  # one refusal, and one left beside a fetch failure would be read as its reason.
  if [ -n "${PREFLIGHT_DIFF:-}" ]; then
    {
      printf '%s\n' "$at"
      printf '%s\n' "$PREFLIGHT_DIFF"
    } > "$PREFLIGHT_DIFF_FILE" 2>/dev/null || true
    chmod 0644 "$PREFLIGHT_DIFF_FILE" 2>/dev/null || true
  else
    rm -f "$PREFLIGHT_DIFF_FILE" 2>/dev/null || true
  fi
}

# A merge is the moment this stage stopped refusing, so a record about failing to
# get this far no longer describes the box. A later refusal rewrites it; a release
# that merges and is then rolled back is the *quarantine's* record, not this one's.
preflight_forget() {
  rm -f "$PREFLIGHT_FILE" 2>/dev/null || true
  rm -f "$PREFLIGHT_DIFF_FILE" 2>/dev/null || true
}

# The checkout's tracked changes as a diff, bounded, with a line saying so when a
# cap bit — a silently shortened diff would read as a smaller change than it is.
# `HEAD` rather than the index, so a staged edit cannot hide from it; untracked
# paths have no diff at all and stay visible in the record's own `git status`
# lines. The external diff and textconv drivers are off because this runs as root
# over a tree somebody else may have edited: a diff is data here, never a command.
preflight_diff() {
  local tmp lines bytes
  tmp=$(mktemp 2>/dev/null) || return 0
  if ! as_owner git -C "$REPO" diff --no-color --no-ext-diff --no-textconv HEAD -- \
      > "$tmp" 2>/dev/null; then
    rm -f "$tmp"
    return 0
  fi
  lines=$(wc -l < "$tmp" 2>/dev/null || echo 0)
  bytes=$(wc -c < "$tmp" 2>/dev/null || echo 0)
  if [ "${bytes:-0}" -gt "$PREFLIGHT_DIFF_BYTES" ]; then
    head -c "$PREFLIGHT_DIFF_BYTES" "$tmp"
    printf '\n%s %s bytes of %s]\n' "$PREFLIGHT_DIFF_MARK" "$PREFLIGHT_DIFF_BYTES" "$bytes"
  elif [ "${lines:-0}" -gt "$PREFLIGHT_DIFF_LINES" ]; then
    head -n "$PREFLIGHT_DIFF_LINES" "$tmp"
    printf '%s %s lines of %s]\n' "$PREFLIGHT_DIFF_MARK" "$PREFLIGHT_DIFF_LINES" "$lines"
  else
    cat "$tmp"
  fi
  rm -f "$tmp"
}
# preflight-logic:end

# ── The step a run stopped at, whatever stopped it ───────────────────────────
# last-stop-logic:start
# Everything above records the refusals somebody thought of. That is the right
# place for those, and a card that can say "the fetch could not reach GitHub" is
# worth more than one that says "the run failed" — but it leaves the other half:
# the run that stops somewhere nobody wrote a record for. A step added later, a
# command that exits non-zero with no branch around it, a `pipefail` in a helper
# nobody looked at twice. Those end the run with a line in the middle of a 1 300-
# line journal and nothing outside the box can name the step it was in.
#
# So the runner tracks the phase it is in and records any non-zero exit itself,
# whatever caused it. The record is written from the EXIT trap, which is the one
# place a new failure path cannot forget to write it — the opposite of the rule
# the named refusals follow, and the reason both exist.
#
#     line 1  the step, as a key the status page has a sentence for
#     line 2  when it stopped (ISO)
#     line 3  the exit code
#     line 4  the commit under judgement (`origin/$BRANCH`), or empty when the run
#             stopped before there was one
#
# The step is a global rather than an argument for the same reason the two records
# above are: this script takes no arguments at any level (a positional parameter
# anywhere in this file would make root's unattended runner steerable), and a step
# that could be passed a value could also name the wrong one. `RUN_STEP` is
# therefore assigned by name at each phase, and nothing else writes it.
#
# A run that *finishes* deletes the record, so what is here describes a box that is
# still stopping on that step rather than one that stopped once and recovered. Only
# `done` clears it: a tick that exits 0 because it had nothing to do, or because
# another run held the lock, is not a run that recovered, and treating it as one
# would erase the record of the tick that is still failing.
RUN_STEP="start"
LAST_STOP_CODE=""
LAST_STOP_FILE="$STATE_DIR/last-stop"

last_stop_write() {
  mkdir -p "$STATE_DIR" 2>/dev/null || true
  {
    printf '%s\n%s\n%s\n%s\n' "${RUN_STEP:-unknown}" "$(date -Is)" \
      "${LAST_STOP_CODE:-?}" "${AFTER_FULL:-}"
  } > "$LAST_STOP_FILE" 2>/dev/null || true
  # Readable by the app (the status page shows this) and owned by root.
  chmod 0644 "$LAST_STOP_FILE" 2>/dev/null || true
}

last_stop_clear() { rm -f "$LAST_STOP_FILE" 2>/dev/null || true; }

on_exit() {
  LAST_STOP_CODE="$?"
  if [ "$LAST_STOP_CODE" -eq 0 ]; then
    [ "$RUN_STEP" = "done" ] && last_stop_clear
    return 0
  fi
  last_stop_write
}
trap on_exit EXIT
# last-stop-logic:end

# ── A stale index lock is healed before it can stall a release ───────────────
# lock-heal-logic:start
# `git merge` writes the index, so a `.git/index.lock` left behind — a git killed
# mid-write, a box powered off at the wrong moment — stops every release. The only
# place that says why is git's own stderr, and it is not the place anybody looks.
#
# What made that stall invisible is that the guard below does *not* catch it:
# `git status --porcelain` has no reason to rewrite an index that did not change,
# so it succeeds — with empty output — through the very lock that stops every
# write. The checkout therefore passed the dirty guard looking clean, the fetch
# reached GitHub every two minutes, the merge never happened, and nothing on the
# box or on the status page could say so. Not hypothetical: a box sat ten commits
# and three days behind that way, and the only way out was a shell.
#
# A lock file is evidence that a git *was* running, not that one is. So it is
# removed when, and only when, no git process is there to own it, and both answers
# are acted on rather than guessed at:
#
#   * no git process: the file is removed and the run carries on, so the release
#     moves on the same tick — which is the whole point of doing it here rather
#     than in a runbook.
#   * a git process, or a file root could not remove: the run refuses with its own
#     exit code and a record naming this step, so the page shows a locked checkout
#     instead of a box that merely looks as though it has no new commits.
#
# One call site, before the first thing that reads *or* writes the index — that
# guard included, because the same lock is what makes it report an unreadable
# checkout. A lock that appears after it (during a slow fetch, or a long snapshot)
# is caught by the merge's own refusal, which carries git's words verbatim, and
# healed by the next tick. No case here needs a human at a console.
#
# There is a third answer, and it is refused rather than rounded to "no git": a
# process table this run cannot read is not evidence that nobody holds the lock,
# and removing the file on that basis is the one way this could destroy work. The
# same rule as `checkout_unreadable` and `armament`: "we could not tell" is not
# "it is fine".
INDEX_LOCK=""
LOCK_HOLDERS=""
#: Where the process table is. A constant rather than a literal so the matching
#: rule below can be run against a table a test controls, on a machine whose own
#: `/proc` says nothing about what this box would do.
PROC_ROOT="/proc"

lock_path() {
  # Asked of git rather than assumed: a linked worktree keeps its index in the
  # worktree's own gitdir, and this is that path. Reads only — it cannot create the
  # lock it is about to look for.
  if [ -z "$INDEX_LOCK" ]; then
    local dir
    dir=$(as_owner git -C "$REPO" rev-parse --absolute-git-dir 2>/dev/null)
    [ -n "$dir" ] || dir="$REPO/.git"
    INDEX_LOCK="$dir/index.lock"
  fi
  printf '%s\n' "$INDEX_LOCK"
}

#: Whether this run can read a process table at all. `[ -d ]` on a numeric entry
#: rather than on the directory itself: an empty or unmounted `/proc` is exactly
#: the case that must not be read as "no git is running".
process_table_readable() {
  local pid
  for pid in "$PROC_ROOT"/[0-9]*; do
    [ -d "$pid" ] || continue
    return 0
  done
  return 1
}

lock_holders() {
  # `<pid>/comm` rather than a process listing: one read per process, no procps
  # and no `lsof` — neither is a given on a minimal box — and it is the name the
  # kernel records. Helpers count, so the pattern covers a `git-remote-https` a
  # fetch left behind; `comm` truncates that at 15 characters, hence `git-*`.
  #
  # Deliberately repo-blind: a git running anywhere on the box is treated as a
  # possible holder. Being wrong that way costs one delayed tick; being wrong the
  # other way corrupts somebody's in-flight `git add`.
  local pid comm
  for pid in "$PROC_ROOT"/[0-9]*; do
    [ -r "$pid/comm" ] || continue
    read -r comm < "$pid/comm" 2>/dev/null || continue
    case "$comm" in
      git|git-*) printf '%s %s\n' "${pid#"$PROC_ROOT"/}" "$comm" ;;
    esac
  done
}

lock_heal() {
  local lock
  lock=$(lock_path)
  [ -e "$lock" ] || return 0

  if ! process_table_readable; then
    log "index lock at $lock, and $PROC_ROOT cannot be read — refusing rather than"
    log "    removing a lock that may have a live owner"
    PREFLIGHT_GATE=lock_refused PREFLIGHT_EXIT=17 \
      PREFLIGHT_DETAIL="the index lock at $lock is there and no process table could be read at $PROC_ROOT, so whether a git owns it is unknown" \
      preflight_write
    exit 17
  fi

  LOCK_HOLDERS=$(lock_holders)
  if [ -n "$LOCK_HOLDERS" ]; then
    log "index lock at $lock, and a git process is running — leaving it alone:"
    printf '%s\n' "$LOCK_HOLDERS" | sed 's/^/    /'
    PREFLIGHT_GATE=lock_refused PREFLIGHT_EXIT=17 \
      PREFLIGHT_DETAIL="a git process holds the index lock at $lock: $(printf '%s' "$LOCK_HOLDERS" | tr '\n' ' ')" \
      preflight_write
    exit 17
  fi

  if ! rm -f "$lock" 2>/dev/null; then
    log "index lock at $lock cannot be removed by root — refusing rather than deploying"
    PREFLIGHT_GATE=lock_refused PREFLIGHT_EXIT=17 \
      PREFLIGHT_DETAIL="the index lock at $lock exists and root could not remove it" \
      preflight_write
    exit 17
  fi

  log "removed a stale index lock at $lock — no git process held it"
  return 0
}
# lock-heal-logic:end

# ── A refusal the runner has already made is not made again ─────────────────
# refusal-streak-logic:start
# The box this was being fixed for had been refused for a day: `M
# app/routes/admin_sekolah.py`, `check out has local changes - NOT deploying`, exit
# 4, every two minutes. A runner from this commit heals that on the *first* tick —
# the path overlaps the release, so it is set aside and the merge proceeds. What
# stayed is the shape the heal itself cannot complete: the state directory cannot be
# created, the disk holding it is full, or `git diff` cannot write. Then the same
# refusal repeats, tick after tick, about the same paths, and every tick it repeats is
# a tick the box does not release. A refusal that has become a loop, wearing the
# clothes of patience.
#
# So the runner keeps count, in two files rather than one, because they answer
# different questions: **how many** ticks running this refusal has been made (the
# threshold reads that) and **what it was about** — its paths, not its prose, since
# the same sentence about different files is a different problem. Past
# `$REFUSAL_STREAK_MAX` the box-local-edit heal stops re-attempting the shape that has
# already failed and uses the degraded one instead (see `box_edits_choose_home`), and
# the release proceeds.
#
# Three properties, and each one is a way this could do harm instead of good:
#
#   * **The count is per path set.** An unrelated edit must not inherit a count from a
#     refusal it has nothing to do with, so a different set of paths starts again at
#     one. Compared byte for byte against the list this run would record.
#   * **A count that cannot be read is zero, never a crash.** These are files written
#     by an earlier run of a script that may since have changed its format, and this
#     is the process that must not fail to start because of them. A missing file, an
#     empty one, a word where a number belongs: all zero.
#   * **Recording it is bookkeeping and can never fail a run.** If `$STATE_DIR` is the
#     very thing that is broken, the refusal still happens for its real reason; it is
#     simply not remembered, and the next tick starts the count again.
#
# Cleared whenever the tree no longer holds the edit the refusal was about, and after
# every heal that completed — a stale count would make the next, unrelated stall look
# like a loop on its first tick.
#
#: How many ticks running the same refusal may be made before the runner stops making
#: it. Three: the timer ticks every two minutes and a box that is stuck is stuck for
#: hours, so one would degrade on the first attempt — before anything has been shown to
#: repeat — and a larger number is patience pretending to be policy.
REFUSAL_STREAK_MAX=3
#: What a refusal was about, and how many ticks running it has been made.
REFUSAL_STREAK_FILE="$STATE_DIR/refusal-streak"
REFUSAL_STREAK_PATHS="$STATE_DIR/refusal-streak.paths"
#: Where the evidence goes when its preferred home cannot take it. `/run` on purpose:
#: tmpfs, and therefore a different filesystem from `/var/lib/scangrade-deploy` — the
#: failure this exists for is a `/var` that is full or read-only, and a fallback inside
#: it would share its fate and be worth nothing.
REFUSAL_FALLBACK_DIR="${SG_REFUSAL_FALLBACK_DIR:-/run/scangrade-set-aside}"
#: How many ticks running the refusal being recorded has now been made. Set by
#: `refusal_streak_record`, and read by the refused run itself for the sentence it
#: leaves behind and the line it prints.
REFUSAL_STREAK_COUNT=0
#: The paths a refusal is about. Set by the caller before it records, and read by
#: `refusal_streak_list`; a global because no helper in this script takes an argument.
REFUSAL_PATHS=""

#: The paths this refusal is about, one per line, in the order they will be written.
#: Sorted and de-duplicated so that the same paths in a different order are the same
#: refusal — the tree does not order its own status output for us — and stripped of
#: carriage returns, because the comparison below is byte for byte and these files can
#: be looked at (or edited) on a machine that writes them: a stray `\r` would make
#: every refusal a first refusal, and the memory would be a no-op that looks fine.
refusal_streak_list() {
  printf '%s\n' "${REFUSAL_PATHS:-}" | tr -d '\r' | sed '/^$/d' | LC_ALL=C sort -u
}

#: The recorded count, or zero when it is missing, empty, or not a number. A `\r` is
#: stripped for the same reason `refusal_streak_list` strips it: these files can be
#: looked at on a machine that writes them, and a count that reads back as zero turns
#: a loop into a first attempt — a memory that is a no-op while looking like it works.
refusal_streak_recorded() {
  local previous
  previous=$(head -n 1 "$REFUSAL_STREAK_FILE" 2>/dev/null | tr -d '\r')
  case "$previous" in ''|*[!0-9]*) previous=0 ;; esac
  printf '%s' "$previous"
}

#: Is the refusal this run is about the same one the file already holds? Compared as
#: normalised text rather than raw bytes, because a `\r` in the stored file would make
#: every refusal a first refusal — the memory would be a no-op while looking fine.
refusal_streak_same() {
  [ -f "$REFUSAL_STREAK_PATHS" ] || return 1
  printf '%s\n' "$(refusal_streak_list)" | cmp -s - \
    <(tr -d '\r' < "$REFUSAL_STREAK_PATHS") 2>/dev/null
}

#: Count this refusal, and remember what it was about. A different path set starts
#: again at one; the same one inherits the count it had.
refusal_streak_record() {
  local list previous="" count=1
  list=$(refusal_streak_list)
  if refusal_streak_same; then
    previous=$(refusal_streak_recorded)
    count=$((previous + 1))
  fi
  # Best effort, and deliberately not a reason to fail: this is the record of a
  # refusal that is about to happen anyway, and a box whose state directory is the
  # broken thing still has to refuse for the real reason.
  mkdir -p "$STATE_DIR" 2>/dev/null || true
  printf '%s\n' "$count" > "$REFUSAL_STREAK_FILE" 2>/dev/null || true
  printf '%s\n' "$list" > "$REFUSAL_STREAK_PATHS" 2>/dev/null || true
  REFUSAL_STREAK_COUNT="$count"
}

#: How many ticks running the refusal this run is about has already been made. Zero
#: when the box has not refused, or refused about something else, or the files cannot
#: be read — every one of which means "not a loop".
refusal_streak_count() {
  REFUSAL_STREAK_COUNT=0
  if refusal_streak_same; then
    REFUSAL_STREAK_COUNT="$(refusal_streak_recorded)"
  fi
}

#: True when this refusal has already been made `$REFUSAL_STREAK_MAX` ticks running —
#: which is the definition of a loop rather than patience.
refusal_streak_exhausted() {
  refusal_streak_count
  [ "$REFUSAL_STREAK_COUNT" -ge "$REFUSAL_STREAK_MAX" ]
}

#: The condition the refusal was about is gone, or the heal completed.
refusal_streak_clear() {
  rm -f "$REFUSAL_STREAK_FILE" "$REFUSAL_STREAK_PATHS" 2>/dev/null || true
  REFUSAL_STREAK_COUNT=0
}
# refusal-streak-logic:end

# ── A box-local edit is set aside, not refused: the release's, and a stale one ─
# box-edits-logic:start
# The guard below used to refuse on *any* local change, before the fetch — so one
# hand edit was a permanent stop. The box could not deploy, and could not receive
# the release that added a way to clear it, which is the shape of a deadlock rather
# than a matter of patience. Measured on this box: eight commits behind for hours
# with `M app/routes/admin_sekolah.py`, and the only way out was a console.
#
# Refusing was the right instinct and the wrong rule. A fast-forward merge fails
# only where the incoming commits write a path that is also changed here, so the
# question is not "is the tree dirty" but "would this merge clobber something".
# Four answers, and each is acted on rather than guessed at:
#
#   * a path this release also writes: the box's version is set aside first — its
#     diff against HEAD written to `$BOX_EDITS_DIR`, and a path HEAD does not have
#     copied out whole rather than diffed, because there is no blob to apply a diff
#     to — then the path is restored so the merge can proceed;
#   * a path this release does not write, but that is **stale**: older than
#     `$BOX_EDITS_STALE_SECONDS`, so nobody is coming back for it. The merge would
#     not fail on it, and that is exactly why it has to be handled: left alone it is
#     carried for as long as the box lives — dirty on every tick, set aside by
#     nothing, named nowhere. It is preserved and restored the same way an
#     overlapping path is, and the record says it was age rather than a merge;
#   * a path this release does not write and that is not stale: left exactly as it
#     is, and reported. Nothing can clobber it and it may well be somebody's morning's
#     work, so it must not stop a release and must not be moved. This is what ends the
#     "dirty box fetches every two minutes and deploys nothing, forever" state;
#   * the set-aside cannot be written: refused. A heal that cannot say what it moved
#     is a silent loss, and that is the one failure this block exists to prevent.
#     `dirty_checkout` is that refusal's gate, so the page keeps its sentence.
#
# Staleness is the one rule here that acts on a path the release never asked about,
# so it is deliberately the conservative one: a day, and only the path's own mtime can
# say it (see `box_edit_is_stale`). A path that cannot be dated is not stale.
#
# Every heal leaves a record naming the commit, the paths, the action taken on each
# and where the preserved bytes are — written *before* the paths are restored, so a
# checkout that fails afterwards fails with the evidence already on disk. Nothing
# here is deleted: a reverted path is in its patch, a moved path is in its own file.
# The record is also the memory: a dirty set that has not changed since the newest
# record names it is not written down again, because a note every two minutes would
# push the record of anything actually preserved off the page that reads the newest
# few.
BOX_EDITS_DIR="$STATE_DIR/set-aside"
#: How many heals keep their record. Small, because each record is evidence about
#: one release and the page shows the newest — the same reasoning as `REFUSALS_KEEP`.
BOX_EDITS_KEEP=5
#: How long the box's own edit to a path has to have stood before the runner stops
#: waiting for a release to write it. The overlap rule answers "would this merge
#: clobber it"; this one answers a question the merge never asks — is anybody still
#: working on it. Without it a path no release writes is carried for as long as the
#: box lives, dirty on every tick, and nothing ever sets it aside or names it.
#:
#: A day: longer than any single sitting at a checkout, so no one is mid-edit, and
#: short enough that a box is not left carrying an abandoned change indefinitely. It
#: is measured from the path's own mtime and nothing else, because that is the only
#: clock that answers the question — see `box_edit_is_stale`.
BOX_EDITS_STALE_SECONDS=86400
#: Set by `local_edits_heal`. Empty means the tree was clean, or nothing overlapped.
BOX_EDITS_SET_ASIDE=""
BOX_EDITS_KEPT=""
BOX_EDITS_RECORD=""
BOX_EDITS_PATCH=""
BOX_EDITS_MOVED=""
BOX_EDITS_BASE=""
#: The paths in this run's dirty set that are stale — older than the threshold, as a
#: newline list. Set beside `$BOX_EDITS_OVERLAP`, and recorded separately from it so
#: the page can say *why* the box's version of a path was preserved.
BOX_EDITS_STALE=""
#: What the refusal sentences call the reason these paths are being preserved. A
#: global for the same reason `$BOX_EDITS_OVERLAP` is: no helper in this block takes a
#: positional parameter — `test_deploy_script_takes_no_arguments` forbids the script
#: from reading its invocation arguments at any level.
BOX_EDITS_CAUSE=""
#: The path `box_edit_is_stale` is being asked about. A global rather than an argument
#: for the reason above, and read by no one else.
BOX_EDITS_STALE_PATH=""
#: Where this run's set-aside goes, and in what shape. The preferred directory, unless
#: the refusal-streak block says this very refusal has already been made
#: `$REFUSAL_STREAK_MAX` ticks running — in which case the shape that failed is not
#: attempted once more; see `box_edits_choose_home`.
BOX_EDITS_DIR_EFFECTIVE=""
#: 1 = keep the files themselves rather than a diff of them. The degraded shape, for
#: when the git in the pipe is what could not write: a whole file is a superset of its
#: diff, so nothing is lost, only the size.
BOX_EDITS_WHOLE=0
#: Set once a degraded pass is running, so a refusal inside it is the last word rather
#: than the beginning of another degradation — a loop inside the block that exists to
#: end one.
BOX_EDITS_DEGRADED=0
#: The paths this run's heal is about, as a newline list. A global because
#: `box_edits_choose_home` hands it to the streak block, and no helper in this script
#: takes an argument.
BOX_EDITS_OVERLAP=""

#: The paths inside the repo that one `git status --porcelain` line names. Three
#: characters are the status, then the path; a rename or copy names two, joined by
#: ` -> `, and a merge is blocked by either of them.
box_edit_paths() {
  local line path
  while IFS= read -r line; do
    [ -n "${line//[[:space:]]/}" ] || continue
    path=${line:3}
    case "$path" in
      *' -> '*) printf '%s\n' "${path%% -> *}" "${path##* -> }" ;;
      *)        printf '%s\n' "$path" ;;
    esac
  done
}

#: Is the edit to the path in `$BOX_EDITS_STALE_PATH` older than the threshold?
#:
#: The path's own mtime, because it is the only clock that answers the question: the
#: refusal memory is cleared on every tick the heal lets through, so it says nothing
#: about age, and a deletion leaves no file to date at all. A path that cannot be
#: dated is therefore *not* stale — the same rule as everywhere else in this block: a
#: reading that failed is not a reason to act, and `stat` failing is a reading that
#: failed. `--` because a path is untrusted text, and one that begins with `-` must
#: not become an option.
box_edit_is_stale() {
  local modified now
  case "${BOX_EDITS_STALE_SECONDS:-}" in ''|*[!0-9]*) return 1 ;; esac
  [ "$BOX_EDITS_STALE_SECONDS" -gt 0 ] || return 1
  modified=$(stat -c %Y -- "$REPO/$BOX_EDITS_STALE_PATH" 2>/dev/null) || return 1
  case "$modified" in ''|*[!0-9]*) return 1 ;; esac
  now=$(date +%s)
  [ "$((now - modified))" -ge "$BOX_EDITS_STALE_SECONDS" ]
}

#: What the refusal below is about. A global rather than an argument, because no
#: helper in this script reads the invocation's parameters — that is what keeps the
#: runner unsteerable from outside (`test_deploy_script_takes_no_arguments`).
BOX_EDITS_REASON=""

#: The refusal for a heal that cannot be completed. One gate and one exit, so the
#: page's sentence and systemd's exit code still describe the same run.
box_edits_refuse() {
  # Counted before the refusal is made, because a refusal the runner does not remember
  # is one it will make again on the next tick — and the memory is what lets the tick
  # after that choose a shape which has not failed yet (see `box_edits_choose_home`).
  REFUSAL_PATHS="$BOX_EDITS_OVERLAP"
  refusal_streak_record
  [ "${REFUSAL_STREAK_COUNT:-0}" -gt 1 ] && \
    BOX_EDITS_REASON="$BOX_EDITS_REASON (the same refusal for $REFUSAL_STREAK_COUNT ticks running)"
  log "$BOX_EDITS_REASON"
  PREFLIGHT_GATE=dirty_checkout PREFLIGHT_EXIT=4 \
    PREFLIGHT_DIFF="$(preflight_diff)" \
    PREFLIGHT_DETAIL="$BOX_EDITS_REASON" preflight_write
  exit 4
}

#: The shape this run's set-aside takes, chosen *before* any of it is attempted.
#:
#: It cannot be decided half-way. Once a path has been moved out of the tree, a
#: re-attempt somewhere else has already lost its own starting state — it would try to
#: move a path that is no longer there and refuse forever — so the choice has to be
#: made while the tree is still the box's. And it has to be made at all, because the
#: alternative is the loop this block exists to end: the same refusal, about the same
#: paths, on every tick.
#:
#: Two things change together, and each answers a different failure: the directory,
#: because `/var` is what could not take it, and the file shape, because a diff is what
#: could not be written. The evidence is recorded either way — the record goes beside
#: it and the journal says where that is, so a degraded pass is louder, never quieter.
box_edits_choose_home() {
  BOX_EDITS_DIR_EFFECTIVE="$BOX_EDITS_DIR"
  BOX_EDITS_WHOLE=0
  [ "${BOX_EDITS_DEGRADED:-0}" = 0 ] || return 0
  REFUSAL_PATHS="$BOX_EDITS_OVERLAP"
  refusal_streak_exhausted || return 0
  BOX_EDITS_DEGRADED=1
  BOX_EDITS_DIR_EFFECTIVE="$REFUSAL_FALLBACK_DIR"
  BOX_EDITS_WHOLE=1
  # Same stamp, different directory: the record keeps naming one base.
  BOX_EDITS_BASE="$REFUSAL_FALLBACK_DIR/${BOX_EDITS_BASE##*/}"
  log "this refusal has been made $REFUSAL_STREAK_COUNT ticks running, so $BOX_EDITS_DIR is not tried again:"
  log "    setting ${BOX_EDITS_OVERLAP//$'\n'/ } aside whole in $REFUSAL_FALLBACK_DIR and letting the release proceed"
}

#: The paths the newest note already names as left alone, one per line and sorted.
#:
#: Read back out of the record rather than remembered in a file of its own: the record
#: *is* the memory, and a second copy of it could disagree with the thing the page
#: reads. Empty when there is no record yet, which makes the comparison below write the
#: first note — the only one it should.
box_edits_noted_kept() {
  local newest
  newest=$(ls -1t "$BOX_EDITS_DIR"/*.txt 2>/dev/null | head -n 1)
  [ -n "$newest" ] || return 0
  sed -n 's/^kept //p' "$newest" 2>/dev/null | LC_ALL=C sort
}

#: Keep the newest `$BOX_EDITS_KEEP` records, and the bytes they point at.
box_edits_prune() {
  local old
  old=$(ls -1t "$BOX_EDITS_DIR"/*.txt 2>/dev/null | tail -n +"$((BOX_EDITS_KEEP + 1))")
  [ -n "$old" ] || return 0
  printf '%s\n' "$old" | while IFS= read -r f; do
    [ -n "$f" ] || continue
    rm -f "$f" "${f%.txt}.patch" 2>/dev/null || true
    rm -rf "${f%.txt}.files" 2>/dev/null || true
  done
}

local_edits_heal() {
  # Reads the runner's own `$DIRTY` and `$CHANGED` rather than taking arguments:
  # no helper in this script reads the invocation's parameters, which is what keeps
  # it unsteerable from outside — see `test_deploy_script_takes_no_arguments`.
  local status="${DIRTY:-}" incoming="${CHANGED:-}"
  [ -n "${status//[[:space:]]/}" ] || return 0

  local path
  BOX_EDITS_BASE="$BOX_EDITS_DIR/$(date -u '+%Y%m%dT%H%M%SZ')-$(printf '%s' "${AFTER_FULL:-unknown}" | cut -c1-12)-$$"

  # Overlap is decided per path, against the release's own file list, because that
  # list is the only thing that can be clobbered. A path the release does not write,
  # and that has stood untouched for longer than `$BOX_EDITS_STALE_SECONDS`, is set
  # aside too — nothing clobbers it, but nobody is coming back for it either, and
  # carrying it means a box that is dirty on every tick for ever and never says where
  # the change went. Everything else is reported and left exactly as it is.
  local overlap="" kept="" stale=""
  while IFS= read -r path; do
    [ -n "$path" ] || continue
    if printf '%s\n' "$incoming" | grep -Fxq -- "$path"; then
      overlap="${overlap}${path}"$'\n'
    else
      BOX_EDITS_STALE_PATH="$path"
      if box_edit_is_stale; then
        stale="${stale}${path}"$'\n'
      else
        kept="${kept}${path}"$'\n'
      fi
    fi
  done < <(printf '%s\n' "$status" | box_edit_paths)
  BOX_EDITS_KEPT=$(printf '%s' "$kept")
  BOX_EDITS_STALE=$(printf '%s' "$stale")
  # What the sentences below call the reason a path is being preserved. Two reasons
  # start the same machinery, so the record has to say which was which: the release
  # writing the path, or the path having stood longer than the threshold. The
  # overlap wins the wording when both are true of the same path, because that is the
  # reason a merge could not have gone ahead — the weaker claim must not dress it up.
  if [ -n "${overlap//[[:space:]]/}" ] && [ -n "${stale//[[:space:]]/}" ]; then
    BOX_EDITS_CAUSE="overlaps this release or is stale"
  elif [ -n "${stale//[[:space:]]/}" ]; then
    BOX_EDITS_CAUSE="is stale"
  else
    BOX_EDITS_CAUSE="overlaps this release"
  fi
  # From here a path is preserved or it is reported, and which of the two reasons put
  # it in this set stops mattering: the shape it is kept in, the home it goes to and
  # the record it leaves are the same either way.
  overlap="${overlap}${stale}"
  BOX_EDITS_OVERLAP=$(printf '%s' "$overlap")

  if [ -n "${stale//[[:space:]]/}" ]; then
    log "these local changes have stood untouched for longer than ${BOX_EDITS_STALE_SECONDS}s, so they are treated as stale and set aside rather than waited for:"
    printf '%s\n' "$stale" | sed '/^$/d; s/^/    /'
  fi

  if [ -z "${overlap//[[:space:]]/}" ]; then
    # Nothing overlaps this release and nothing here is stale, so nothing a refusal
    # could have been about is still here: whatever was counted is no longer this
    # box's problem.
    refusal_streak_clear
    [ -z "${kept//[[:space:]]/}" ] && return 0
    log "checkout has local changes this release does not write — leaving them alone:"
    printf '%s\n' "$kept" | sed '/^$/d; s/^/    /'
    # One note per *set* of paths, not one per tick. A box carrying a local edit no
    # release writes stays dirty for as long as somebody leaves it there, and a note
    # every two minutes would fill the newest few the page reads with the same
    # sentence — so the record of anything that *was* preserved would stop being
    # named within a day, which is the opposite of what this directory is for.
    if [ "$(printf '%s\n' "$kept" | sed '/^$/d' | LC_ALL=C sort)" \
       = "$(box_edits_noted_kept)" ]; then
      return 0
    fi
    # Best effort, and only a note: nothing here is touched, so failing to write it
    # down cannot lose anything — and refusing to deploy over a missing note about
    # files this release does not even write is the forever-stall this removes.
    mkdir -p "$BOX_EDITS_DIR" 2>/dev/null || true
    box_edits_record
    return 0
  fi

  # Decided here, before anything is attempted, and never in the middle of it.
  box_edits_choose_home

  if ! mkdir -p "$BOX_EDITS_DIR_EFFECTIVE" 2>/dev/null; then
    BOX_EDITS_REASON="a local edit $BOX_EDITS_CAUSE and $BOX_EDITS_DIR_EFFECTIVE cannot be created, so it could not be set aside"
    box_edits_refuse
  fi

  # A path HEAD has is preserved as a diff and restored; one it does not is copied
  # out whole, because there is no blob for a diff to apply to. Asked of git rather
  # than inferred from the status code: an added-but-unstaged path carries a code no
  # reader of `status` would call untracked.
  local -a touched=() moved=()
  while IFS= read -r path; do
    [ -n "$path" ] || continue
    if as_owner git -C "$REPO" cat-file -e "HEAD:$path" 2>/dev/null; then
      touched+=("$path")
    else
      moved+=("$path")
    fi
  done < <(printf '%s\n' "$overlap")

  if [ "${#touched[@]}" -gt 0 ] && [ "$BOX_EDITS_WHOLE" = 1 ]; then
    # The degraded shape: it is the git in this pipe that failed, so keep the files
    # themselves and take git out of it. A whole file is a superset of its diff, so
    # nothing is lost — only the size — and the restore below still runs, which is
    # what leaves the tree clean enough to merge.
    BOX_EDITS_MOVED="$BOX_EDITS_BASE.files"
    for path in "${touched[@]}"; do
      if ! mkdir -p "$BOX_EDITS_MOVED/$(dirname "$path")" 2>/dev/null \
         || ! cp -a -- "$REPO/$path" "$BOX_EDITS_MOVED/$path" 2>/dev/null; then
        BOX_EDITS_REASON="a local edit to $path $BOX_EDITS_CAUSE and could not be kept in $BOX_EDITS_MOVED, so it would be lost rather than set aside"
        box_edits_refuse
      fi
    done
  elif [ "${#touched[@]}" -gt 0 ]; then
    BOX_EDITS_PATCH="$BOX_EDITS_BASE.patch"
    if ! as_owner git -C "$REPO" diff --no-color --no-ext-diff --no-textconv HEAD -- \
        "${touched[@]}" > "$BOX_EDITS_PATCH" 2>/dev/null; then
      rm -f "$BOX_EDITS_PATCH"
      BOX_EDITS_REASON="a local edit to ${touched[*]} $BOX_EDITS_CAUSE and could not be written to $BOX_EDITS_PATCH, so it would be lost rather than set aside"
      box_edits_refuse
    fi
  fi

  if [ "${#moved[@]}" -gt 0 ]; then
    BOX_EDITS_MOVED="$BOX_EDITS_BASE.files"
    for path in "${moved[@]}"; do
      if ! mkdir -p "$BOX_EDITS_MOVED/$(dirname "$path")" 2>/dev/null; then
        BOX_EDITS_REASON="$path $BOX_EDITS_CAUSE and cannot be copied into $BOX_EDITS_MOVED, so it would be lost rather than set aside"
        box_edits_refuse
      fi
      if ! mv -f -- "$REPO/$path" "$BOX_EDITS_MOVED/$path" 2>/dev/null; then
        BOX_EDITS_REASON="$path $BOX_EDITS_CAUSE and could not be moved into $BOX_EDITS_MOVED, so it would be lost rather than set aside"
        box_edits_refuse
      fi
    done
  fi

  BOX_EDITS_SET_ASIDE=$(printf '%s' "$overlap")
  box_edits_record

  if [ "${#touched[@]}" -gt 0 ]; then
    if ! as_owner git -C "$REPO" checkout HEAD -- "${touched[@]}" 2>/dev/null; then
      BOX_EDITS_REASON="the box's version of ${touched[*]} is set aside at $BOX_EDITS_PATCH, but restoring those paths failed — the merge is not attempted on a tree it would clobber"
      box_edits_refuse
    fi
    log "set aside ${touched[*]} (kept in $BOX_EDITS_PATCH) and restored them, so the release can merge"
  fi
  if [ "${#moved[@]}" -gt 0 ]; then
    log "moved ${moved[*]} into $BOX_EDITS_MOVED, so the release can merge"
  fi
  [ -z "${kept//[[:space:]]/}" ] || {
    log "and left these alone, because this release does not write them:"
    printf '%s\n' "$kept" | sed '/^$/d; s/^/    /'
  }
  # The set-aside completed, so the box is past whatever it was refusing: the next tick
  # must not inherit this count.
  refusal_streak_clear
  box_edits_prune
  return 0
}

#: The record one heal leaves behind. A positional file format, like the refusal
#: record's, so the page can read it without parsing prose: when, the commit, where
#: the preserved bytes are, then one line per path — what was done with it.
box_edits_record() {
  # No arguments, like every helper here: it writes down what `local_edits_heal`
  # left in the globals above.
  local at
  at=$(date -Is)
  BOX_EDITS_RECORD="$BOX_EDITS_BASE.txt"
  {
    printf '%s\n' "$at"
    printf '%s\n' "${AFTER_FULL:-}"
    printf 'patch %s\n' "$BOX_EDITS_PATCH"
    printf 'files %s\n' "$BOX_EDITS_MOVED"
    printf '%s\n' "$BOX_EDITS_SET_ASIDE" | sed '/^$/d; s/^/set-aside /'
    printf '%s\n' "${BOX_EDITS_STALE:-}" | sed '/^$/d; s/^/stale /'
    printf '%s\n' "$BOX_EDITS_KEPT" | sed '/^$/d; s/^/kept /'
  } > "$BOX_EDITS_RECORD" 2>/dev/null && {
    # Root-only: a hand edit can hold a credential, and this file is a copy of it.
    chmod 0600 "$BOX_EDITS_RECORD" 2>/dev/null || true
    return 0
  }
  BOX_EDITS_RECORD=""
  # A record that cannot be written is only fatal when there is something to lose.
  if [ -n "${BOX_EDITS_SET_ASIDE//[[:space:]]/}" ]; then
    BOX_EDITS_REASON="a local edit $BOX_EDITS_CAUSE and its record could not be written to $BOX_EDITS_BASE.txt, so what was set aside could not be said"
    box_edits_refuse
  fi
  log "could not record the checkout's local changes at $BOX_EDITS_BASE.txt — nothing was set aside, so nothing is lost by it"
  return 0
}
# box-edits-logic:end

# ── The installed launcher refreshes itself from the checkout ─────────────
# refresh-launcher-logic:start
# The two installed names are rendered from `deploy/entrypoint.sh`, and nothing
# used to re-render them: a fix to that template sat in the repo while root kept
# running the launcher of the day it was installed, and the only way to deliver it
# was `install-auto-deploy.sh` by hand — the manual step automatic deployment
# exists to remove. So a release that gets all the way to the end re-renders both
# from the checkout it just deployed.
#
# What that heals with no root step: a launcher rendered from an older
# `entrypoint.sh` (it still runs the checkout, so nothing is broken, but the
# template's fix is not in effect — the deploy-status page calls it
# `launcher_stale`), a missing launcher or snapshot command, and a copy that still
# matches the checkout byte-for-byte — Gate 0 lets that one through precisely
# because it is the same code.
#
# What it cannot heal, and why the installer still exists: a copy that has
# *drifted*. That copy refuses with exit 14 before any gate can pass, and this runs
# at the end of a release that passed, so no release ever reaches it. Nothing
# inside a file that is not the checkout's can apply the checkout's logic — that is
# the one step a file cannot take for itself.
#
# Three properties, and each one is why the code looks the way it does:
#
#   * **It never installs what it has not parsed.** The installed path is the only
#     thing the timer runs; a half-written launcher is a box that cannot deploy at
#     all, which is strictly worse than a stale one. So the render goes to a file
#     beside the target, the placeholder check and `bash -n` both have to pass,
#     and every failure leaves the old file exactly where it was.
#   * **It is atomic.** The staged file is written in the target's own directory,
#     so `mv` is a rename on one filesystem and no tick can observe a partial
#     file — a copy through /tmp would cross filesystems and lose that.
#   * **It is skipped when the bytes already match.** An unconditional write would
#     raise the mtime on every release, and the page that reports the arrangement
#     compares *content* on purpose (Gate 0 uses `cmp`), so a timestamp that moved
#     without the content moving would be a signal that lies.
#
# It does not fail the release. The app is up and the smoke test passed; rolling
# that back because a *launcher refresh* failed would trade a working site for a
# bookkeeping fix, and the deploy-status page reports the arrangement either way.
#
# The target and its label arrive through globals rather than arguments, which is
# the same choice the quarantine block makes: this script is the one thing root
# runs unattended, and it must never read its own argv — a helper that took
# parameters would have to (`test_deploy_script_takes_no_arguments`).
refresh_launcher() {
  local target="$LAUNCHER_TARGET" label="$LAUNCHER_LABEL" staged=""
  local template="${LAUNCHER_TEMPLATE:-$REPO/deploy/entrypoint.sh}"
  local root="${LAUNCHER_ROOT:-$REPO}"
  # Read once, then cleared: which template and which root a name renders from is a
  # per-name decision (the two deploy names render against the checkout, the lever
  # against the tree the fetch materialised), and the assignment-prefix form above a
  # function call has been shell-version dependent about whether it outlives the call.
  # A leaked root would render the *runner's* launcher against another tree — the one
  # file whose rendered root both Gate 0 and the status page read.
  LAUNCHER_TEMPLATE=""; LAUNCHER_ROOT=""
  [ -f "$template" ] || return 0

  staged="$(dirname "$target")/.$(basename "$target").new.$$"
  if ! sed "s|@REPO@|$root|" "$template" > "$staged" 2>/dev/null; then
    rm -f "$staged" 2>/dev/null || true
    log "launcher refresh: could not render deploy/entrypoint.sh — $label left as it is"
    return 0
  fi
  if grep -q '@REPO@' "$staged" 2>/dev/null; then
    rm -f "$staged" 2>/dev/null || true
    log "launcher refresh: the render still holds @REPO@ — not installing an unrendered $label"
    return 0
  fi
  if ! bash -n "$staged" 2>/dev/null; then
    rm -f "$staged" 2>/dev/null || true
    log "launcher refresh: the rendered $label does not parse — leaving the installed one alone"
    return 0
  fi
  # Same bytes: nothing to do, and nothing to say on every tick.
  if [ -f "$target" ] && cmp -s "$staged" "$target"; then
    rm -f "$staged" 2>/dev/null || true
    return 0
  fi

  chmod 0755 "$staged" 2>/dev/null || true
  chown root:root "$staged" 2>/dev/null || true
  if mv -f "$staged" "$target" 2>/dev/null; then
    log "launcher refreshed: $label at $target now renders from $REPO"
  else
    rm -f "$staged" 2>/dev/null || true
    log "launcher refresh: could not replace $target — $label left as it is"
  fi
  return 0
}

# Every name the installer installs, because all of them are rendered from the same
# template and the snapshot command is the one that was silently broken by being
# a copy (it derived the checkout from its own location).
#
# The lever renders against `$LEVER_DIR` rather than the checkout, and the reason is
# the same one that gave the block below its own block: the box that most needs the
# lever is the box that cannot release, so a name pointing at the checkout would hand
# that box the logic of the commit it is stuck on. `fetch-lever-logic` materialises
# the tree from the fetched branch on every tick; this line is what keeps the name in
# step with it after a release, and it is a no-op when the bytes already agree. A box
# that has not ticked since that tree landed finds no template here and skips.
refresh_installed_launchers() {
  LAUNCHER_TARGET="$INSTALLED_RUNNER" LAUNCHER_LABEL="the deploy runner" refresh_launcher
  LAUNCHER_TARGET="$INSTALLED_SNAPSHOT" LAUNCHER_LABEL="the snapshot command" refresh_launcher
  LAUNCHER_TARGET="$INSTALLED_RECOVER" LAUNCHER_LABEL="the recovery lever" \
  LAUNCHER_TEMPLATE="$LEVER_DIR/deploy/entrypoint.sh" LAUNCHER_ROOT="$LEVER_DIR" \
    refresh_launcher
}
# refresh-launcher-logic:end

# ── The lever arrives by the fetch, not by a release ─────────────────────────
# fetch-lever-logic:start
# `refresh_installed_launchers` runs as the last step of a release that passed, so the
# recovery lever reached a box only if some release had already landed there — and the
# box that needs the lever is the box whose release is being refused. The heal that
# taught the runner to set a box-local edit aside travels in a release too, which is
# how a box can sit for a day refusing *before its own fetch*: its fixed commit is held
# by the very refusal the fix exists to clear. This block is the way out of that
# circle.
#
# The fetch is the only step with the property needed — it writes refs and never the
# tree, so it succeeds on a checkout that is dirty, rolled back, held by a quarantine
# or refused by a gate. Whatever `origin/$BRANCH` names can therefore be read out of
# the fetched commit without merging anything, and the lever installed from there is
# what makes recovery one word instead of a console session.
#
# It materialises the two files the lever is, both out of that commit:
#
#   * `deploy/scangrade-recover.sh` — the lever itself, which a box stuck on a commit
#     from before it existed does not have in its checkout at all;
#   * `deploy/entrypoint.sh` — the template `/usr/local/bin/sgfix` is rendered from,
#     so the installed name follows however the lever is meant to be run.
#
# and the name is then installed from *there*. Rendered against `$LEVER_DIR` and not
# `$REPO` on purpose: the checkout's copy is the one thing a stuck box cannot update,
# so a lever pointing at it would hand back exactly the stale tool the fetch has just
# beaten.
#
# Four properties, and each one is why the code looks the way it does:
#
#   * **It moves nothing.** No merge, no reset, no switch of revision, no reload — a
#     read of the fetched commit and two writes under the state directory. That is what
#     makes it safe on every tick, before any gate has spoken; anything that could move
#     the tree belongs in the release, where a rollback is waiting for it.
#   * **Both blobs are read before either lands.** A commit that carried one file and
#     not the other must not leave a lever that half-exists.
#   * **It parses before it installs.** This runs as root on a box that is already in
#     trouble, so a lever that cannot run is the one failure there is no room for. A
#     failure leaves the installed lever exactly as it was, and says so.
#   * **It is a rename inside the target's own directory, and skipped when the bytes
#     match.** A lever that is running keeps the inode it started with, and a tick that
#     changed nothing raises no mtime — the same rule the launcher refresh above holds,
#     because an mtime that moves while the content does not is a signal that lies.
#
# A commit from before the lever existed carries neither file, and that is not a fault:
# it is every box's first tick after this landed. So does a commit whose lever does not
# parse. Both cases return quietly with the installed lever untouched.
materialise_lever_from_origin() {
  # Two statements, not one: `local a=… b="$a/…"` declares every name local and unset
  # *before* it assigns any of them, so the second expansion reads an unbound `a`.
  local dir="$LEVER_DIR/deploy"
  local staged_lever="$dir/.scangrade-recover.new.$$"
  local staged_entry="$dir/.entrypoint.new.$$"

  if ! mkdir -p "$dir" 2>/dev/null; then
    log "lever: cannot write $dir — the installed lever stays as it is"
    return 0
  fi

  if ! as_owner git -C "$REPO" show "origin/$BRANCH:deploy/entrypoint.sh" \
       > "$staged_entry" 2>/dev/null; then
    rm -f "$staged_lever" "$staged_entry" 2>/dev/null || true
    return 0
  fi
  if ! as_owner git -C "$REPO" show "origin/$BRANCH:deploy/scangrade-recover.sh" \
       > "$staged_lever" 2>/dev/null; then
    rm -f "$staged_lever" "$staged_entry" 2>/dev/null || true
    return 0
  fi

  if ! bash -n "$staged_lever" 2>/dev/null; then
    rm -f "$staged_lever" "$staged_entry" 2>/dev/null || true
    log "lever: origin/$BRANCH's scangrade-recover.sh does not parse — keeping the installed lever"
    return 0
  fi
  if ! bash -n "$staged_entry" 2>/dev/null; then
    rm -f "$staged_lever" "$staged_entry" 2>/dev/null || true
    log "lever: origin/$BRANCH's entrypoint.sh does not parse — keeping the installed lever"
    return 0
  fi

  if [ -f "$dir/scangrade-recover.sh" ] && [ -f "$dir/entrypoint.sh" ] \
     && cmp -s "$staged_lever" "$dir/scangrade-recover.sh" \
     && cmp -s "$staged_entry" "$dir/entrypoint.sh"; then
    rm -f "$staged_lever" "$staged_entry" 2>/dev/null || true
  else
    chmod 0755 "$staged_lever" 2>/dev/null || true
    chmod 0644 "$staged_entry" 2>/dev/null || true
    chown root:root "$staged_lever" "$staged_entry" 2>/dev/null || true
    if ! mv -f "$staged_lever" "$dir/scangrade-recover.sh" 2>/dev/null; then
      rm -f "$staged_lever" "$staged_entry" 2>/dev/null || true
      log "lever: could not install origin/$BRANCH's lever — keeping the installed one"
      return 0
    fi
    if ! mv -f "$staged_entry" "$dir/entrypoint.sh" 2>/dev/null; then
      rm -f "$staged_entry" 2>/dev/null || true
      log "lever: origin/$BRANCH's launcher template could not be installed"
    fi
    log "lever: installed from origin/$BRANCH into $dir"
  fi

  # The word itself, rendered from that template against that tree. Called even when the
  # bytes matched, because the tree can be current while the installed name is missing or
  # was rendered from an older template — and `refresh_launcher` is the one place that
  # decides, doing nothing when the bytes agree.
  LAUNCHER_TARGET="$INSTALLED_RECOVER" LAUNCHER_LABEL="the recovery lever" \
  LAUNCHER_TEMPLATE="$dir/entrypoint.sh" LAUNCHER_ROOT="$LEVER_DIR" \
    refresh_launcher
  return 0
}
# fetch-lever-logic:end

# ── Serialise runs ───────────────────────────────────────────────────────────
# The timer already skips while the unit is active, but a manual run can overlap
# a timer run. Exiting 0 keeps a skipped run from looking like a failed one.
RUN_STEP="lock"
exec 9>"$LOCK" || { log "cannot open lock $LOCK"; exit 0; }
if ! flock -n 9; then
  log "another deploy is already running — skipping this round"
  exit 0
fi

if [ "$(id -u)" -ne 0 ]; then
  log "must run as root (it restarts $SERVICE) — current uid $(id -u)"
  PREFLIGHT_GATE=not_root PREFLIGHT_EXIT=2 \
    PREFLIGHT_DETAIL="uid $(id -u) is not root, so this run cannot reload $SERVICE" \
    preflight_write
  exit 2
fi

# ── Maintenance window ───────────────────────────────────────────────────────
# Create the file to freeze deploys (e.g. during exam week) without touching the
# timer; remove it to let the next tick catch up.
if [ -e "$PAUSE_FILE" ]; then
  log "paused by $PAUSE_FILE — not deploying"
  exit 0
fi

# runner-identity:start
# /usr/local/bin/scangrade-deploy is a launcher that execs this file, so what
# runs is always the commit the checkout is on. It used to be an installed
# *copy*, and a copy stops receiving fixes the moment it lands: every later
# change to the gates below stayed on GitHub while the timer kept deploying with
# the logic of whatever commit was current the day it was installed. Nothing
# compared the two, so the drift was invisible until somebody re-ran the
# installer by hand — which is the manual step this automation exists to remove.
#
# So a copy is refused. Running this file straight from the checkout is the
# normal case, and a copy that still matches the checkout is the same code and
# harmless; what must never pass quietly is a copy that *differs*, because that
# is a deploy about to run yesterday's logic.
#
# The comparison is against the checkout as it stands now, before anything is
# fetched or merged, so an ordinary update to this very file cannot look like a
# mismatch. (Exec'ing it in place is safe even when the pull rewrites it: bash
# reads a script file into its buffer up front — measured on a 28 KB script that
# was replaced, and shrunk to 75 bytes, mid-run: all 120 iterations executed, no
# mixed lines — so there is no need to stage a private copy, which would only add
# a file that could itself go stale.)
#
# It sits after the pause check deliberately: a frozen box is deploying nothing,
# and a fault in a file nobody is running is not worth a journal line every two
# minutes.
RUN_STEP="identity"
SELF=$(readlink -f "$0" 2>/dev/null || echo "$0")
REPO_RUNNER=$(readlink -f "$REPO/deploy/scangrade-deploy.sh" 2>/dev/null || echo "$REPO/deploy/scangrade-deploy.sh")
if [ "$SELF" != "$REPO_RUNNER" ] && ! cmp -s "$SELF" "$REPO_RUNNER"; then
  log "REFUSING: this is an installed COPY of the runner, not the checkout's"
  log "    running : $SELF"
  log "    checkout: $REPO_RUNNER"
  log "    a copy stops receiving fixes the moment it is installed, so this box"
  log "    would keep deploying with the logic of an older commit — including"
  log "    gates that have since been added or corrected."
  log "    fix once, as root:  bash $REPO/deploy/install-auto-deploy.sh"
  exit 14
fi
# runner-identity:end

# ── Preflight: is this box armed to check a release at all? ─────────────────
#
# Every gate below can be *skipped*, and each skip was a sentence in this journal
# and nothing else: the readability gate without pytest logs "this release is NOT
# contrast-checked" and carries on, a missing smoke conf is "skipping the per-role
# smoke test", and a claims or performance gate that cannot run says "could not
# measure". A box in that state deploys every commit while checking almost none of
# them — the site stays green, the journal fills with hedges, and "the gates ran"
# quietly stops being true. That is an unarmed box, and it is exactly the state
# that must not ship code no gate has looked at.
#
# So the armament is checked once, before anything is fetched, and a missing check
# refuses the run outright: nothing is pulled, nothing is reloaded, no release is
# staged. It is a refusal to *deploy*, not a rollback — no release is under
# judgement here, the box is — which is why it has its own exit code and why it is
# NOT quarantined: a quarantine is a record about a commit, and the next tick
# should re-check (cheaply) and say so again rather than stay silent.
#
# The definition of "armed" lives in one place: deploy/arm-auto-deploy.sh --check,
# which judges the installed runner (a copy is unarmed), the gate config and the
# roster. It is read-only and safe as any user, and it is the same report an
# operator gets from the console — so the deploy cannot disagree with the tool
# they arm the box with. Its output *is* the record: filtering it here would put a
# second, weaker copy of the same judgement in this file.
armament_preflight() {
  local checker="$REPO/deploy/arm-auto-deploy.sh"
  [ -f "$checker" ] || {
    ARMAMENT_OUT="the armament checker is missing from this checkout: $checker
it arrives with the installer, and until it is there this box cannot say what is armed"
    return 1
  }
  ARMAMENT_OUT=$(bash "$checker" --check 2>&1)
}

RUN_STEP="armament"
ARMAMENT_OUT=""
if ! armament_preflight; then
  log "UNARMED — REFUSING TO DEPLOY: this box is not set up to check a release"
  printf '%s\n' "$ARMAMENT_OUT" | sed 's/^/    /'
  log "    Nothing was fetched and nothing was reloaded. Deploying from this state"
  log "    would ship code that no gate has looked at."
  log "    arm it once, as root:  bash $REPO/deploy/arm-auto-deploy.sh"
  mkdir -p "$STATE_DIR" 2>/dev/null || true
  {
    date -Is
    printf '%s\n' "$ARMAMENT_OUT"
  } > "$UNARMED_FILE" 2>/dev/null || true
  # Readable by the app (the status page shows this) and owned by root.
  chmod 0644 "$UNARMED_FILE" 2>/dev/null || true
  exit 15
fi
# Armed. The record of a previous refusal must not outlive the state it describes.
rm -f "$UNARMED_FILE" 2>/dev/null || true

[ -d "$REPO/.git" ] || {
  log "$REPO is not a git checkout — refusing"
  PREFLIGHT_GATE=no_checkout PREFLIGHT_EXIT=3 \
    PREFLIGHT_DETAIL="$REPO is not a git checkout" preflight_write
  exit 3
}
[ -x "$REPO/.venv/bin/gunicorn" ] || {
  log "no virtualenv at $REPO/.venv — refusing"
  PREFLIGHT_GATE=no_virtualenv PREFLIGHT_EXIT=3 \
    PREFLIGHT_DETAIL="no gunicorn at $REPO/.venv/bin, so nothing here could serve a release" \
    preflight_write
  exit 3
}

# Act as whoever owns the checkout, so git and pip never hit "dubious ownership"
# and never leave root-owned files behind in a tree another user has to use.
OWNER=$(stat -c '%U' "$REPO")
# HOME must be the owner's real home, not the repo: git finds its credentials
# there, and pip puts its download cache there. Pointing HOME at $REPO made pip
# create $REPO/.cache, which showed up as an untracked file and then tripped this
# script's own "checkout has local changes" guard on every later run.
OWNER_HOME=$(getent passwd "$OWNER" | cut -d: -f6)
[ -n "$OWNER_HOME" ] || OWNER_HOME=/tmp
as_owner() { runuser -u "$OWNER" -- env HOME="$OWNER_HOME" GIT_TERMINAL_PROMPT=0 GIT_ASKPASS=/bin/true "$@"; }

cd "$REPO" || {
  log "cannot enter $REPO — refusing"
  PREFLIGHT_GATE=no_checkout PREFLIGHT_EXIT=3 \
    PREFLIGHT_DETAIL="cannot enter $REPO" preflight_write
  exit 3
}

# ── A stale index lock, before anything touches the index ────────────────────
# Called twice in a run, and both times are load-bearing. Here first: the guard
# below reads the index through the same lock (a lock is what makes it report an
# unreadable checkout), the fetch writes refs, and the merge at the end writes the
# index — so the earliest quiet moment covers all three. The second call sits
# immediately before that merge, because on a release that ships SQL this one is
# minutes old by then. It costs one `stat` when there is no lock, and `lock_heal`
# caches the path it asked git for, so the second call cannot disagree with the
# first about where the lock is.
RUN_STEP="checkout"
lock_heal

# ── Guard against clobbering hand edits ──────────────────────────────────────
# A `git status` that *failed* used to be read as "no local changes": the command
# substitution came back empty either way, and empty is what a clean tree looks
# like. So a checkout git could not read was merged into, which then failed at the
# merge with a message about fast-forwards. "We could not tell" is not "it is
# fine" — the same rule `app/utils/armament.py` follows for the armament checker —
# so an unreadable checkout is its own refusal, with git's own error as the reason.
#
# What the tree *contains* is read here and decided later, on purpose. Whether a
# local change can be clobbered depends on which paths the release writes, and that
# list does not exist until the fetch has happened — so refusing here, before it,
# is what made a hand edit a permanent stop rather than a warning. The read stays
# where it is (it is the cheapest moment, and `lock_heal` above has just made the
# index readable); the decision moved down to `local_edits_heal`, after `CHANGED`.
DIRTY=""
if ! DIRTY=$(as_owner git -C "$REPO" status --porcelain 2>&1); then
  log "could not read the checkout's state — NOT deploying:"
  printf '%s\n' "$DIRTY" | sed 's/^/    /'
  PREFLIGHT_GATE=checkout_unreadable PREFLIGHT_EXIT=4 \
    PREFLIGHT_DETAIL="${DIRTY:-git status exited non-zero with no output}" preflight_write
  exit 4
fi
if [ -n "$DIRTY" ]; then
  log "checkout has local changes — deciding what to do once the fetch says what they touch:"
  echo "$DIRTY" | sed 's/^/    /'
fi

BEFORE=$(as_owner git -C "$REPO" rev-parse --short HEAD)

# ── Fetch ────────────────────────────────────────────────────────────────────
# stderr is captured rather than left to the journal: this is the one refusal whose
# cause is entirely inside git's own message (credentials, DNS, a proxy), and the
# status page is for an operator with no shell to read that journal from.
RUN_STEP="fetch"
FETCH_OUT=""
if ! FETCH_OUT=$(as_owner git -C "$REPO" fetch --quiet origin "$BRANCH" 2>&1); then
  log "git fetch failed (network or credentials) — will retry next tick"
  [ -n "$FETCH_OUT" ] && printf '%s\n' "$FETCH_OUT" | sed 's/^/    /'
  PREFLIGHT_GATE=fetch_failed PREFLIGHT_EXIT=5 \
    PREFLIGHT_DETAIL="${FETCH_OUT:-git fetch exited non-zero with no output}" \
    preflight_write
  exit 5
fi

AFTER=$(as_owner git -C "$REPO" rev-parse --short "origin/$BRANCH")
# The full sha, not the short one: the quarantine is a record of *which commit*
# was refused, and a 7-character prefix is a search key, not an identity. It is
# also what `git rev-parse` answers with, so a later comparison is exact.
AFTER_FULL=$(as_owner git -C "$REPO" rev-parse "origin/$BRANCH")
# Which gate refused this release, written into the quarantine record. Empty
# until something refuses; every refusal path sets it before quarantining.
FAIL_REASON=""

# The refusing gate's *own* output, written under FAIL_REASON in the same record.
# FAIL_REASON is the runner's summary — "perf gate (slower than the last release
# that passed)" — and a summary of a measurement is not actionable: the question an
# operator has to answer is by how much, and whether two noisy probes on a 1-vCPU
# box could have produced it. The gate printed exactly that, and the runner was
# keeping only its own sentence, so the numbers reached nobody who was not reading
# the journal over ssh — which is the trip the status page exists to remove.
#
# Bounded, because a record an operator reads is the finding rather than the gate's
# transcript, and cleared by `quarantine_write` once it has been written, so a second
# refusal in the same tick can never inherit the first gate's numbers.
FAIL_DETAIL=""

#: Set by `quarantine_honour_release` when the operator asked for a re-measurement.
#: Initialised here because the script runs under `set -u` and the perf step reads it
#: whether or not a request was seen.
REBASELINE_REQUESTED=0

# An operator's explicit release is consumed even when there is nothing to
# deploy, so a pending request cannot sit on the box and surprise a later tick.
quarantine_honour_release

# The lever is materialised here, and the position is the feature: after the fetch,
# because only then does `origin/$BRANCH` name anything; before the nothing-new exit
# below, because an up-to-date box whose lever is missing is exactly a box that needs
# one; and long before the merge, which is the step a stuck box cannot reach.
materialise_lever_from_origin

if [ "$BEFORE" = "$AFTER" ]; then
  exit 0                      # nothing new; stay silent so the journal stays quiet
fi

# A commit a gate already refused is not tried again. This is what stops the
# reject-roll-back-re-pull loop: the rollback moved the checkout, not the branch.
if ! quarantine_gate; then
  exit 0
fi

log "new commit on origin/$BRANCH: $BEFORE -> $AFTER"

CHANGED=$(as_owner git -C "$REPO" diff --name-only "$BEFORE" "origin/$BRANCH")

# ── The dirty guard's decision, now that what can be clobbered is known ───────
# A path this release writes and the box has changed is set aside (preserved, and
# recorded) rather than refused; a path it does not write is left alone. Only a
# heal that cannot be *written down* refuses — `dirty_checkout`, the gate that used
# to fire on every local change.
RUN_STEP="checkout"
local_edits_heal

# ── Recovery point, before anything moves ────────────────────────────────────
# A release can be put back with its code from git alone. Its *data* cannot. So a
# release that ships SQL is not allowed to start without a snapshot taken while
# the old schema is still the one being served — the schema a bad migration would
# change is the one this capture reads.
#
# It runs as root, deliberately: the archive holds personal data, and being
# readable only by root (0600 inside a 0700 directory) is what keeps a backup from
# becoming a breach. It is therefore taken here rather than in the as_owner path.
#
# The failure that matters is a *silent* one, so this is a hard gate: if the
# snapshot cannot be taken, the release is not deployed at all. Nothing has been
# merged at this point, so refusing costs nothing but a retry.
RUN_STEP="snapshot"
SNAPSHOT=""
if echo "$CHANGED" | grep -qE '^supabase/migrations/[^/]+\.sql$'; then
  log "release changes supabase/migrations — taking a data snapshot first"
  if ! mkdir -p "$BACKUP_DIR" || ! chmod 0700 "$BACKUP_DIR"; then
    log "cannot use $BACKUP_DIR — refusing to deploy a migration without a snapshot"
    PREFLIGHT_GATE=snapshot_refused PREFLIGHT_EXIT=12 \
      PREFLIGHT_DETAIL="cannot create or protect $BACKUP_DIR" preflight_write
    exit 12
  fi
  # Captured rather than streamed: when it fails, its own output is the reason the
  # record carries (`no SUPABASE_URL / SUPABASE_SERVICE_KEY — nothing to do …`), and
  # that sentence is the difference between a card an operator can act on and
  # "the snapshot failed".
  SNAP_OUT=""
  if SNAP_OUT=$("$REPO/.venv/bin/python" "$SNAPSHOT_CMD" --repo "$REPO" --out "$BACKUP_DIR" \
       --keep "$BACKUP_KEEP" --label "$AFTER" --quiet 2>&1); then
    [ -n "$SNAP_OUT" ] && printf '%s\n' "$SNAP_OUT" | sed 's/^/    /'
    SNAPSHOT=$(ls -1t "$BACKUP_DIR"/scangrade-db-*.tar.gz 2>/dev/null | head -1)
    if [ -n "$SNAPSHOT" ]; then
      log "snapshot: $SNAPSHOT"
    else
      log "snapshot command succeeded but left no archive — refusing to deploy"
      PREFLIGHT_GATE=snapshot_refused PREFLIGHT_EXIT=12 \
        PREFLIGHT_DETAIL="the snapshot command succeeded but left no archive in $BACKUP_DIR" \
        preflight_write
      exit 12
    fi
  else
    log "SNAPSHOT FAILED — not deploying a migration release without a recovery point"
    log "nothing has been merged; $BEFORE is untouched. Retry on the next tick."
    PREFLIGHT_GATE=snapshot_refused PREFLIGHT_EXIT=12 \
      PREFLIGHT_DETAIL="${SNAP_OUT:-the snapshot command exited non-zero with no output}" \
      preflight_write
    exit 12
  fi
else
  log "no migration in this release — no snapshot needed"
fi

# ── The last look before the merge ───────────────────────────────────────────
# The heal above ran before the fetch and, on a release that ships a migration,
# before a data snapshot that can take minutes. A lock that appears in between
# would land on the merge and be read as a merge failure — the exact confusion
# this whole block exists to end — so the index is checked once more here, where
# the write is about to happen.
RUN_STEP="merge"
lock_heal

# stderr captured for the same reason the fetch's is, and here it is the only thing
# that can name the cause: a stale `.git/index.lock` makes this fail while `git
# status` succeeds, so "not a fast-forward" is a guess — and it was the guess the
# runner printed for three hours while nothing deployed.
MERGE_OUT=""
if ! MERGE_OUT=$(as_owner git -C "$REPO" merge --ff-only --quiet "origin/$BRANCH" 2>&1); then
  log "could not merge origin/$BRANCH into $BEFORE — leaving $BEFORE in place"
  [ -n "$MERGE_OUT" ] && printf '%s\n' "$MERGE_OUT" | sed 's/^/    /'
  # Git's words above are the record; this only adds what git cannot know — who
  # owns the lock, or that nobody does. Reached on failure alone, so a healthy box
  # pays one `stat` for the privilege of never being told "history rewritten?".
  if [ -e "$(lock_path)" ]; then
    LOCK_HOLDERS=$(lock_holders)
    if [ -n "$LOCK_HOLDERS" ]; then
      log "    and the index lock is held by:"
      printf '%s\n' "$LOCK_HOLDERS" | sed 's/^/        /'
    else
      log "    and a .git/index.lock is there even though no git process holds it"
    fi
  fi
  PREFLIGHT_GATE=merge_refused PREFLIGHT_EXIT=6 \
    PREFLIGHT_DETAIL="${MERGE_OUT:-git merge --ff-only exited non-zero with no output}" \
    preflight_write
  exit 6
fi
# Merged: every pre-merge check passed, so a record of a refusal to get this far is
# stale, and leaving it would report a box that is deploying as one that is not.
preflight_forget
log "pulled; $(echo "$CHANGED" | wc -l) file(s) changed"

# ── Gate 0 survives the release, or the release does not happen ─────────────
#
# Gate 0 is the only thing that refuses a runner which is not the checkout's, and
# this file is now the new commit's: if the block is gone, this very run is the
# last one that could have noticed. The launcher refuses to exec a checkout
# without it, so the effect otherwise lands one tick later — as a box that cannot
# deploy at all, discovered by an operator rather than by the release.
#
# Refusing here is cheaper and says the right thing: the commit that removes the
# check is refused, quarantined, and the previous one keeps serving. It costs two
# greps and closes the only path by which "lacks Gate 0" could be *reached*
# deliberately, as opposed to inherited from an old install.
# The fence's name is spelled in two pieces on purpose. The block above is the
# only place in this file where the whole marker appears, so a script with that
# block removed carries no trace of it — which is what makes "does the block
# survive?" a question this check can answer with a grep rather than a parse.
IDENTITY_FENCE="runner-identity"
if ! grep -q "^# ${IDENTITY_FENCE}:start\$" "$REPO/deploy/scangrade-deploy.sh" 2>/dev/null ||
   ! grep -q "^# ${IDENTITY_FENCE}:end\$" "$REPO/deploy/scangrade-deploy.sh" 2>/dev/null; then
  log "this release removes Gate 0 — the runner would no longer be able to refuse a"
  log "    copy of itself, so it is refused rather than deployed: rolling back to $BEFORE"
  FAIL_REASON="Gate 0 (the release removed the runner-identity block)"
  quarantine_write
  as_owner git -C "$REPO" reset --hard --quiet "$BEFORE"
  exit 16
fi

# ── Dependencies ─────────────────────────────────────────────────────────────
RUN_STEP="dependencies"
if echo "$CHANGED" | grep -qx "requirements.txt"; then
  log "requirements.txt changed — installing"
  if ! as_owner "$REPO/.venv/bin/pip" install -q -r "$REPO/requirements.txt"; then
    log "pip install FAILED — rolling back"
    as_owner git -C "$REPO" reset --hard --quiet "$BEFORE"
    exit 7
  fi
fi

# ── Gate 1: does it compile? ─────────────────────────────────────────────────
RUN_STEP="compile"
if ! as_owner "$REPO/.venv/bin/python" -m compileall -q "$REPO/app" >/dev/null 2>&1; then
  log "python compileall FAILED — rolling back to $BEFORE"
  FAIL_REASON="python compileall (exit 8)"
  quarantine_write
  as_owner git -C "$REPO" reset --hard --quiet "$BEFORE"
  exit 8
fi

# ── Gate 2: does the app actually build, under the real configuration? ───────
# Constructing it registers every blueprint and route, and validates .env — so a
# bad import, a duplicate endpoint or a broken key is caught here instead of by
# whoever opens the site next. The schedulers are switched off explicitly: the
# retention loop runs a purge pass the moment it starts, and a deploy check has
# no business deleting anything.# START_BACKGROUND_SCHEDULERS=false is the marker that says "this construction is a
# deploy probe, not the app about to serve" — it is set here and by nothing else in
# the repository, and it dates from the first commit of this script, so every copy
# of the runner ever installed carries it. That is what lets the *app* refuse a
# release from a runner which is not the checkout's, which is the one refusal a
# stale copy cannot make for itself (nothing inside that copy can judge it).
#
# The app refuses only under this marker, so a refusal here always fails the
# release and never the site: gunicorn constructs the same app without it.
RUN_STEP="construct"
CONSTRUCT_OUT=$(as_owner env START_BACKGROUND_SCHEDULERS=false "$REPO/.venv/bin/python" -c '
import sys
from app import create_app
app = create_app()          # the production configuration, from .env
rules = {r.rule for r in app.url_map.iter_rules()}
missing = {"/auth/login"} - rules
if missing:
  sys.exit("missing core routes: %s" % sorted(missing))
print("app constructs ok (%d routes)" % len(rules))
' 2>&1)
CONSTRUCT_RC=$?
if [ "$CONSTRUCT_RC" -ne 0 ]; then
  printf '%s\n' "$CONSTRUCT_OUT" | sed 's/^/    /'
  if printf '%s\n' "$CONSTRUCT_OUT" | grep -q 'SCANGRADE-UNARMED'; then
    # The app's own verdict, not a construct error: it read the installed runner
    # (deploy/arm-auto-deploy.sh --check) and found this box unable to check a
    # release. "app did not construct" would send the next reader hunting for a
    # Python fault that is not there.
    log "the app refused to be deployed by a runner that is not armed — rolling back"
    log "    to $BEFORE. Fix the runner (install-auto-deploy.sh), not the app."
    FAIL_REASON="runner not armed (the app refused to be deployed by it)"
  else
    log "app failed to construct — rolling back to $BEFORE"
    FAIL_REASON="app did not construct (exit 9)"
  fi
  # The traceback's own last lines, which are the fault and its message: a Python
  # exception says nothing useful at the top and everything at the bottom.
  FAIL_DETAIL=$(printf '%s\n' "$CONSTRUCT_OUT" | grep -vE '^[[:space:]]*$' | tail -n 6)
  quarantine_write
  as_owner git -C "$REPO" reset --hard --quiet "$BEFORE"
  exit 9
fi
log "$CONSTRUCT_OUT"

# ── Gate 3: is this release readable? ────────────────────────────────────────
# A template, or a colour utility one of them uses, can leave a page unreadable
# in dark mode while looking perfectly fine in the mode its author was working
# in — and nothing errors. It cannot be caught by any of the gates above, by the
# smoke test below (which checks that pages *answer*, not that they can be read),
# or by looking at a screenshot in light mode. So it is its own gate, and it runs
# before the app is reloaded, when rolling back is still free.
#
# Exit 2 is "the check could not run" — a missing interpreter, or pytest absent
# from the venv. That used to be logged loudly and waved through, on the argument
# that a broken checker must not take the site down. The argument is right about
# the *site* and wrong about the *release*: proceeding means shipping a commit
# nobody read for contrast, which is the same as having no gate at all. It rolls
# back now. (The preflight above refuses the whole run when this box cannot run
# the gate at all, so reaching here with exit 2 means the box changed between the
# two checks — the rollback is the safe half of that race.) Exit 1 is a real
# finding and also rolls back.
#
# Exit 3 is the one the gate added for a release that *removes the check itself*
# — a file it runs is gone, or the named tests collected nothing. That is a
# property of the release rather than of the box, so it rolls back with exit 1;
# the journal says which of the two it was, because "unreadable text" and "the
# gate has been deleted" are fixed in different places.
RUN_STEP="theme"
THEME_OUT=$(as_owner bash "$REPO/deploy/theme_gate.sh" 2>&1)
THEME_RC=$?
if [ "$THEME_RC" -eq 0 ]; then
  log "$(echo "$THEME_OUT" | tail -1)"
else
  if [ "$THEME_RC" -eq 3 ]; then
    log "theme gate DISARMED (exit 3) — this release removed the checks, so it is"
    log "    refused rather than shipped unexamined — rolling back to $BEFORE"
  elif [ "$THEME_RC" -eq 2 ]; then
    log "theme gate COULD NOT RUN (exit 2) — this release is NOT contrast-checked,"
    log "    which is not a release to ship: rolling back to $BEFORE"
  else
    log "theme gate FAILED (exit $THEME_RC) — rolling back to $BEFORE"
  fi
  echo "$THEME_OUT" | sed 's/^/    /'
  FAIL_REASON="theme gate (exit $THEME_RC)"
  # The verdict first, then the failing assertion: the record is capped, and the
  # gate's own conclusion is the line that must never be the one that got cut.
  FAIL_DETAIL=$({ printf '%s\n' "$THEME_OUT" | grep -E '^theme gate:'
                  printf '%s\n' "$THEME_OUT" | grep -E '^FAILED |^E '; })
  quarantine_write
  as_owner git -C "$REPO" reset --hard --quiet "$BEFORE"
  exit 13
fi

# ── The schema gate: does production have what this release names? ───────────
# Every gate above reads the code or the box. None of them can see the failure
# this one exists for: a release whose *database* is behind its code — a table or
# column the app now names that no migration has put in production, because the
# migration was written and merged but never applied, or was applied to the wrong
# project. That release is perfect on the box and 500s the first time a teacher
# opens the page that reads it.
#
# `apply_migration.py --verify` is the one reader that can answer it: read-only,
# it holds the migration files against the live catalogue and exits 6 when a
# declared object exists nowhere. It runs before the reload, so a gap is caught
# while rolling back is still free, and it quarantines — a gap is a property of
# the commit, and the next tick must not re-pull it.
#
# Exit 1 is "the box could not measure": no DIRECT_URL, or a database it cannot
# reach. That is a property of the *box*, not of the release, and it must not roll
# a good release back — the armament check refuses a box with no DIRECT_URL before
# any release is fetched, so reaching here with exit 1 is a transient. It is
# logged loudly rather than swallowed, which is the difference between a gap and a
# box problem that no longer read the same.
# schema_gate:start
RUN_STEP="schema"
SCHEMA_OUT=$(as_owner env "$REPO/.venv/bin/python" "$REPO/deploy/apply_migration.py" \
    --verify --repo "$REPO" 2>&1)
SCHEMA_RC=$?
case "$SCHEMA_RC" in
  0)
    log "$(printf '%s\n' "$SCHEMA_OUT" | grep -m1 'Every declared object is present' \
        || echo 'schema gate: every declared object is present')" ;;
  6)
    log "schema gate FAILED — this release names objects the database does not have:"
    printf '%s\n' "$SCHEMA_OUT" | grep -E '^    MISSING  ' | sed 's/^/    /'
    log "    apply the migration first, then release this exact commit:"
    log "        $REPO/.venv/bin/python $REPO/deploy/apply_migration.py <file>.sql --commit"
    FAIL_REASON="schema gate (the release names objects no migration applied)"
    # The verdict first, then the objects themselves: the record is capped, and the
    # gate's own conclusion is the line that must never be the one that got cut.
    FAIL_DETAIL=$({ printf '%s\n' "$SCHEMA_OUT" | grep -E '^[0-9]+ file\(s\) declare objects the schema'
                    printf '%s\n' "$SCHEMA_OUT" | grep -E '^    MISSING  '; })
    quarantine_write
    as_owner git -C "$REPO" reset --hard --quiet "$BEFORE"
    exit 18 ;;
  *)
    log "schema gate COULD NOT RUN (exit $SCHEMA_RC) — this release was NOT held"
    log "    against the live schema. Not rolling back: an unreachable database is a"
    log "    property of the box, not of the commit, and the armament check refuses a"
    log "    box with no DIRECT_URL before any release is fetched."
    printf '%s\n' "$SCHEMA_OUT" | tail -n 3 | sed 's/^/    /' ;;
esac
# schema_gate:end

# ── Reload ───────────────────────────────────────────────────────────────────
# reload sends SIGHUP: gunicorn finishes in-flight requests (graceful_timeout=30)
# before retiring the old workers. A hard restart would cut off a student
# mid-exam, which is exactly what an auto-deploy must not do.
reload_app() {
  if systemctl reload "$SERVICE" 2>/dev/null; then
    log "reloaded $SERVICE gracefully (SIGHUP)"
  else
    log "reload unsupported — restarting $SERVICE"
    systemctl restart "$SERVICE"
  fi
}

probe_app() {
  local code=""
  for _ in $(seq 1 15); do
    code=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$APP_PORT/" || true)
    [ "$code" = "200" ] && return 0
    sleep 2
  done
  log "app answered '$code' on 127.0.0.1:$APP_PORT"
  return 1
}

# ── The other process running this checkout ──────────────────────────────────
# gunicorn is not the only thing holding this release's code. The Celery worker
# imports its task modules at start-up and keeps them in memory for the life of
# the process, so reloading the app leaves the worker answering with the previous
# release. That is not theoretical: `page_index` was added to the OMR task's
# signature and to its caller in one commit, the deploy reloaded gunicorn alone,
# and every scan then failed with
#
#     process_omr_scan() got an unexpected keyword argument 'page_index'
#
# — the *caller* new, the *worker* old, and nothing on the box saying so.
#
# Celery has no SIGHUP reload, so this one is a restart. It holds no
# student-facing request open the way gunicorn does, and the task config sets
# `task_acks_late = True`, so a scan in flight when the restart lands is
# redelivered and re-run rather than lost. The line is logged anyway: a worker
# that goes down quietly is a thing an unattended deploy should say out loud.
# A unit that is not installed is not a failure: async OMR simply queues.
WORKER_UNIT="scangrade-celery"

reload_worker() {
  if ! systemctl cat "$WORKER_UNIT" >/dev/null 2>&1; then
    log "$WORKER_UNIT is not installed — no worker to reload (async tasks will queue)"
    return 0
  fi
  if ! systemctl restart "$WORKER_UNIT" 2>/dev/null; then
    # Not a rollback: the app is healthy and rolling it back would turn a broken
    # worker into an outage. But it is not a footnote either — this is the exact
    # state that breaks scans — so it goes in the journal as a failure line.
    log "could not restart $WORKER_UNIT — it may still be running '$BEFORE' code"
    return 1
  fi
  log "restarted $WORKER_UNIT (it now holds the same revision as the app)"
}

WORKER_STALE=0
RUN_STEP="reload"
reload_app
reload_worker || WORKER_STALE=1
sleep 3

# Why a release is about to be rolled back — carried into the quarantine record
# so the journal says which gate judged it, not merely that something did.
FAIL_REASON="app did not come up after the reload"
HEALTHY=0
if systemctl is-active --quiet "$SERVICE" && probe_app; then
  HEALTHY=1
  FAIL_REASON=""
fi

# ── Did the reload actually take? Ask the app which commit it is serving ──────
#
# The probe above says gunicorn answers. It cannot say *whose code* answers, and
# every gate below rests on that: the smoke test signs in as each role against
# "this release", the claims gate re-reads the published table for "this release",
# the perf gate compares "this release" with the last one that passed. `systemctl
# reload` sends SIGHUP and gunicorn is expected to re-exec; when that quietly does
# nothing, all three go on measuring the previous release and report on a release
# nobody is being served. That is not hypothetical — this box was found four
# commits behind with 28 hours of uptime, every page answering and nothing saying
# so, which is why the served commit is now a reading the app publishes and a
# question this runner asks.
#
# Two design points, both in `deploy/served_commit_gate.py`:
#
# * **A graceful reload makes one probe ambiguous.** A worker retiring mid-request
#   can answer the first ask with the old commit, so the gate asks several times and
#   refuses only when *no* probe reported the merged commit — the perf gate's rule
#   about a divergence no second probe confirmed.
# * **Exit codes are chosen so a crash cannot mimic a refusal.** python exits 1 on
#   an uncaught exception, so the gate's refusal is 3 and everything else is "could
#   not measure". Rolling a healthy release back because a check crashed is the
#   false positive that would make this gate worse than none.
#
# Nothing here quarantines by itself: a refusal sets HEALTHY=0 and names the gate,
# and the shared rollback path at the end of the script writes the record, puts
# $BEFORE back and reloads it. The gate's own lines go in FAIL_DETAIL, because the
# record is what an operator without a shell reads.
# served-commit-gate:start
RUN_STEP="served"
if [ "$HEALTHY" != "1" ]; then
  : # already unhealthy; the rollback path owns it
else
  SERVED_OUT=$(as_owner "$REPO/.venv/bin/python" "$REPO/deploy/served_commit_gate.py" \
      --base "http://127.0.0.1:$APP_PORT" --commit "$AFTER_FULL" \
      --reporter "$REPO/app/__init__.py" 2>&1)
  SERVED_RC=$?
  # The gate's own verdict lines, or — when it produced none — the tail of its
  # output, because a traceback is the finding when there is no verdict.
  served_detail() {
    local detail
    detail=$(printf '%s\n' "$SERVED_OUT" | grep -E '^served commit:')
    [ -z "$detail" ] && detail=$(printf '%s\n' "$SERVED_OUT" | grep -vE '^[[:space:]]*$' | tail -n 6)
    printf '%s\n' "$detail"
  }
  case "$SERVED_RC" in
    0)
      log "$(printf '%s\n' "$SERVED_OUT" | grep -m1 '^served commit: OK' || echo 'served commit: OK')" ;;
    3)
      log "the app is serving a commit other than the one this run merged:"
      served_detail | sed 's/^/    /'
      log "    the reload did not take, so every gate below would report on the"
      log "    previous release — rolling back to $BEFORE"
      HEALTHY=0
      FAIL_REASON="served commit (the app reports serving a commit other than the one just merged)"
      FAIL_DETAIL=$(served_detail) ;;
    *)
      log "served-commit check COULD NOT MEASURE (exit $SERVED_RC) — this release is"
      log "    NOT confirmed as the code being served:"
      served_detail | sed 's/^/    /'
      log "    not rolling back: an app that cannot be asked is a property of the box,"
      log "    and the health probe above owns whether it answers at all." ;;
  esac
fi
# served-commit-gate:end

# ── Gate 4: sign in as each role and open the pages that matter ──────────────
# The port answering 200 only says gunicorn is up. It says nothing about whether
# login still works, whether a page 500s for one role, or whether an RBAC guard
# was loosened — and "the release is live but teachers cannot open anything" is
# exactly the failure that a reachability probe waves through.
#
# Credentials live outside the repo, in /etc/scangrade-smoke.conf, because they
# are deployment-specific and must never be committed. SMOKE_ENFORCE=true is
# what arms the rollback; install-auto-deploy.sh only sets it after confirming
# the accounts actually sign in, so a stale password cannot roll back good code.
RUN_STEP="verify"
SMOKE_CONF="/etc/scangrade-smoke.conf"
if [ "$HEALTHY" = "1" ] && [ -f "$SMOKE_CONF" ] && ! bash -n "$SMOKE_CONF" 2>/dev/null; then
  # Sourcing a broken file would take the whole deploy script down with it, so it
  # is not sourced — but a release that skips this gate is a release nobody
  # signed in as each role against, and that is the whole reason the gate exists.
  log "$SMOKE_CONF has a syntax error — the per-role smoke test cannot run, and a"
  log "    release without it is NOT signed in against: rolling back to $BEFORE"
  HEALTHY=0
  FAIL_REASON="smoke test (unarmed: $SMOKE_CONF does not parse)"
elif [ "$HEALTHY" = "1" ] && [ -f "$SMOKE_CONF" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$SMOKE_CONF"
  set +a

  SMOKE_ENV=()
  for v in SMOKE_BASE_URL SMOKE_INSECURE SMOKE_SUPER_ADMIN SMOKE_ADMIN_SEKOLAH SMOKE_GURU SMOKE_MURID; do
    [ -n "${!v:-}" ] && SMOKE_ENV+=("$v=${!v}")
  done

  # The one exam the smoke test is allowed to open. It opens a real exam as the
  # student to prove the fullscreen blocker and the away blur are still on the
  # page, and a check that opens a paper is only safe if the paper is *meant* to
  # be sat: assigned to the demo student's class, never closing, nothing to lose.
  # So the fixture is refreshed here, before the check reads it, on every release
  # — a fixture left to a one-off seed stops being sittable the first time
  # somebody submits it, and the check would then quietly warn on every deploy.
  #
  # Never fatal: a box whose demo data is gone must not roll a healthy release
  # back, and the smoke test reports the missing fixture itself. The schedulers
  # are switched off for the same reason Gate 2 switches them off — constructing
  # the app starts the retention loop, which purges.
  if [ -f "$REPO/manage.py" ]; then
    FIXTURE_OUT=$(as_owner env START_BACKGROUND_SCHEDULERS=false \
      "$REPO/.venv/bin/python" "$REPO/manage.py" demo-exam 2>&1)
    FIXTURE_RC=$?
    printf '%s\n' "$FIXTURE_OUT" | tail -n 3 | sed 's/^/    /'
    if [ "$FIXTURE_RC" = "0" ]; then
      log "demo exam fixture refreshed"
    else
      log "demo exam fixture could NOT be refreshed (exit $FIXTURE_RC) — the smoke"
      log "    test will report the sitting page as unchecked; that is not a rollback"
    fi
  fi

  # The smoke test prints each check as it makes it, and that stream to the journal
  # is worth keeping: an operator watching a deploy sees which role is being opened
  # rather than a silence that ends in a verdict. That was the reason this gate was
  # the one that quoted nothing into the quarantine record — the stream *was* its
  # output. The two are not exclusive: `tee` keeps the stream and leaves a copy, and
  # the copy is what a refusal quotes.
  #
  # Three details make the copy safe and honest. It is a fresh file outside the
  # checkout, because a refusal that also leaves the tree dirty is a second
  # refusal; the exit status comes from PIPESTATUS, because a pipeline reports
  # `tee`'s success as readily as the smoke test's failure; and stderr is folded in
  # because a traceback is the finding when there are no check lines.
  SMOKE_LOG=$(mktemp "${TMPDIR:-/tmp}/scangrade-smoke.XXXXXX")
  as_owner env "${SMOKE_ENV[@]}" "$REPO/.venv/bin/python" "$REPO/deploy/smoke_test.py" 2>&1 \
    | tee "$SMOKE_LOG"
  SMOKE_RC=${PIPESTATUS[0]}
  SMOKE_OUT=$(cat "$SMOKE_LOG" 2>/dev/null)
  rm -f "$SMOKE_LOG"

  # What a refusal quotes, in one place because both refusal arms below quote it.
  # The verdict and the per-role summary (one line per account that failed, with the
  # checks it failed) when the run produced them; when it produced neither — it died
  # before printing a check line — the traceback *is* the finding, and without it the
  # record names the gate and nothing else. stderr is folded into the tee, so the
  # traceback is in the copy. The last six lines, because the writer keeps the first
  # six it is handed and the exception is the *end* of a traceback: keeping the head
  # would file the boilerplate and drop the reason the process died.
  smoke_detail() {
    local detail
    detail=$({ printf '%s\n' "$SMOKE_OUT" | grep -E '^RESULT: '
               printf '%s\n' "$SMOKE_OUT" | grep -E '^   [a-z_]+ \([0-9]+\): '; })
    if [ -z "$detail" ]; then
      detail=$(printf '%s\n' "$SMOKE_OUT" | sed -n '/^Traceback /,$p' | tail -n 6)
      [ -z "$detail" ] && detail=$(printf '%s\n' "$SMOKE_OUT" | tail -n 6)
    fi
    printf '%s\n' "$detail"
  }

  case "$SMOKE_RC" in
    0)
      log "smoke test passed" ;;
    2)
      # Exit 2 is "nothing was testable": no accounts configured, an unknown role
      # in the conf, or a base URL this host cannot reach. The unreachable case is
      # not the release's fault and never was — but the other two mean this box
      # has nothing to sign in with, which the preflight already refuses. Either
      # way the release was not signed in against, so it does not get kept.
      log "smoke test COULD NOT RUN (exit 2) — no role was signed in against this"
      log "    release: rolling back to $BEFORE"
      HEALTHY=0
      FAIL_REASON="smoke test (exit 2: nothing was testable)"
      FAIL_DETAIL=$(smoke_detail) ;;
    *)
      if [ "${SMOKE_ENFORCE:-false}" = "true" ]; then
        log "smoke test FAILED (exit $SMOKE_RC) — rolling back"
        HEALTHY=0
        FAIL_REASON="smoke test (exit $SMOKE_RC)"
        FAIL_DETAIL=$(smoke_detail)
      else
        log "smoke test FAILED (exit $SMOKE_RC) but SMOKE_ENFORCE is not 'true' — keeping the release"
      fi ;;
  esac
elif [ ! -f "$SMOKE_CONF" ]; then
  log "no $SMOKE_CONF — the per-role smoke test cannot run, so this release is not"
  log "    signed in against: rolling back to $BEFORE (see docs/AUTO_DEPLOY.md)"
  HEALTHY=0
  FAIL_REASON="smoke test (unarmed: no $SMOKE_CONF)"
fi

# ── Gate 5: do the numbers on the landing page still describe this box? ──────
# The page publishes a capacity table (concurrent students -> p50, p95, errors)
# that was measured against this deployment once, by hand, and never again. A
# claim like that lives on regardless of what happens underneath it, which is
# how 46,698 and 74,923 requests sat on the page with nothing able to produce
# them. This gate re-measures the page's own lowest rung -- loading more at
# deploy time would cost the students the deploy is for -- and refuses the
# release when the box no longer behaves the way the page says.
#
# It runs here, after the reload, because the thing being measured is the code
# that is now serving: a probe before the reload would measure the old release.
# That also means a failure has to go through the shared rollback path below --
# resetting the checkout without reloading would leave the rejected release
# running, and the next tick would fail the same way forever.
#
# Exit 2 is "could not measure" — a box already busy with real students, a probe
# that did not complete, a divergence a second probe did not confirm. Never a
# rollback: an unconfirmed measurement is not evidence of a bad release, and a
# busy box must not refuse a good one. Exit 1 is a confirmed divergence and does.
#
# Exit 4 is the different answer, and the one this gate used to give as 2: the box
# is **not armed** to check the claim at all (no roster, a roster too small, no
# harness, no base URL, a claim above this gate's cap). That release was not
# measured, and shipping it means the published capacity table is no longer
# re-checked by anything. So it rolls back, and the preflight above normally
# refuses the whole run before this point — reaching 4 here means the box changed
# mid-release, where the rollback is the safe half of the race.
CLAIMS_CONF="/etc/scangrade-claims.conf"
if [ "$HEALTHY" != "1" ]; then
  : # already unhealthy; the rollback path owns it
elif [ ! -f "$CLAIMS_CONF" ]; then
  log "no $CLAIMS_CONF — the published capacity claims cannot be re-measured, so"
  log "    this release is NOT claim-checked: rolling back to $BEFORE"
  HEALTHY=0
  FAIL_REASON="claims gate (unarmed: no $CLAIMS_CONF)"
elif ! bash -n "$CLAIMS_CONF" 2>/dev/null; then
  log "$CLAIMS_CONF does not parse — the claims gate cannot run, so this release"
  log "    is NOT claim-checked: rolling back to $BEFORE"
  HEALTHY=0
  FAIL_REASON="claims gate (unarmed: $CLAIMS_CONF does not parse)"
else
  set -a
  # shellcheck disable=SC1090
  . "$CLAIMS_CONF"
  set +a

  CLAIMS_ENV=()
  for v in CLAIMS_BASE_URL CLAIMS_ROSTER CLAIMS_SESSIONS CLAIMS_DURATION \
           CLAIMS_MAX_SESSIONS CLAIMS_EVIDENCE; do
    [ -n "${!v:-}" ] && CLAIMS_ENV+=("$v=${!v}")
  done

  CLAIMS_ARGS=(--page "$REPO/app/templates/landing.html"
                --harness "$REPO/loadtest_concurrent.py")
  CLAIMS_OUT=$(as_owner env "${CLAIMS_ENV[@]}" "$REPO/.venv/bin/python" \
      "$REPO/deploy/claims_gate.py" "${CLAIMS_ARGS[@]}" 2>&1)
  CLAIMS_RC=$?

  case "$CLAIMS_RC" in
    0)
      log "$(echo "$CLAIMS_OUT" | head -1)" ;;
    2)
      log "claims gate could not measure (exit 2) — NOT re-verified this release:"
      echo "$CLAIMS_OUT" | head -3 | sed 's/^/    /' ;;
    4)
      log "claims gate NOT ARMED (exit 4) — this release was not measured against"
      log "    the published claims, so it is not kept: rolling back to $BEFORE"
      echo "$CLAIMS_OUT" | head -3 | sed 's/^/    /'
      HEALTHY=0
      FAIL_REASON="claims gate (not armed: exit 4)"
      FAIL_DETAIL=$(printf '%s\n' "$CLAIMS_OUT" | grep -E '^claims gate: NOT ARMED') ;;
    *)
      if [ "${CLAIMS_ENFORCE:-false}" = "true" ]; then
        log "claims gate FAILED — the page promises what this box no longer does:"
        echo "$CLAIMS_OUT" | sed 's/^/    /'
        log "rolling $AFTER back rather than publishing numbers we cannot deliver"
        HEALTHY=0
        FAIL_REASON="claims gate (the page promises what this box no longer does)"
        # The gate's own verdict and its divergent lines, which carry the ratio
        # between what the page advertises and what the box now answers. The verdict
        # comes first because the record is capped: a release with a wall of divergent
        # metrics must not lose the sentence that says it diverged.
        FAIL_DETAIL=$({ printf '%s\n' "$CLAIMS_OUT" | grep -E '^claims gate: (DIVERGED|CANNOT MEASURE)'
                        printf '%s\n' "$CLAIMS_OUT" | grep -E '^    - '; })
      else
        log "claims gate FAILED but CLAIMS_ENFORCE is not 'true' — keeping the release:"
        echo "$CLAIMS_OUT" | head -6 | sed 's/^/    /'
        log "    to enforce it: CLAIMS_ENFORCE=\"true\" in $CLAIMS_CONF"
      fi ;;
  esac
fi

# ── Gate 6: is this release slower than the last one at a reference load? ─────
# Gate 5 compares the box with a number written in an HTML file, at the rung that
# page advertises. That question is about the claim, and it cannot see a release
# that makes every page 40% slower while staying inside the published bound: five
# of those in a row are a box that no longer does what it did, and each one passes
# a claim check on its own.
#
# This gate asks the other question — did *this release* cost us response time? —
# by running a small fixed load (20 students, 20s: this is the box that serves
# students, so the deploy does not get to load it like a benchmark) and comparing
# the result with the last release that passed. The baseline is written only when
# a release passes, so a bad release cannot become the new normal.
#
# It runs after the reload for the same reason Gate 5 does: what is measured is
# the code that is now serving, and a failure therefore belongs to the shared
# rollback path below.
#
# Exit 2 is "could not measure" — a box already busy, a probe that did not
# complete, a divergence a second probe did not confirm. Never a rollback. The
# first run after installation has no baseline, so that release becomes one and
# passes: the gate arms itself rather than needing an operator to remember it.
#
# Exit 4 is "not armed": no roster, a roster too small, no harness, no base URL, or
# a baseline taken at a different reference load. Then this release was never
# compared with the last one that passed, which is the only thing the gate is for.
PERF_CONF="/etc/scangrade-perf.conf"
if [ "$HEALTHY" != "1" ]; then
  : # already unhealthy; the rollback path owns it
elif [ ! -f "$PERF_CONF" ]; then
  log "no $PERF_CONF — this release cannot be compared with the previous one, so"
  log "    it is not kept: rolling back to $BEFORE"
  HEALTHY=0
  FAIL_REASON="perf gate (unarmed: no $PERF_CONF)"
elif ! bash -n "$PERF_CONF" 2>/dev/null; then
  log "$PERF_CONF does not parse — the performance gate cannot run, so this"
  log "    release is not compared: rolling back to $BEFORE"
  HEALTHY=0
  FAIL_REASON="perf gate (unarmed: $PERF_CONF does not parse)"
else
  set -a
  # shellcheck disable=SC1090
  . "$PERF_CONF"
  set +a

  PERF_ENV=()
  for v in PERF_BASE_URL PERF_ROSTER PERF_SESSIONS PERF_TEACHERS PERF_DURATION \
           PERF_BASELINE PERF_EVIDENCE PERF_BYTES_SLACK PERF_ROUNDTRIPS_SLACK \
           PERF_BASELINE_MAX_AGE; do
    [ -n "${!v:-}" ] && PERF_ENV+=("$v=${!v}")
  done

  # An operator asked this tick to re-measure the box: hand the gate `--rebaseline`
  # so it rewrites the baseline from the box as it is now instead of comparing
  # against one that predates the change. Unarmed by default, so an ordinary tick is
  # byte-for-byte the command it always was.
  PERF_REBASELINE_ARGS=()
  if [ "${REBASELINE_REQUESTED:-0}" = "1" ]; then
    PERF_REBASELINE_ARGS=(--rebaseline)
  fi

  PERF_OUT=$(as_owner env "${PERF_ENV[@]}" "$REPO/.venv/bin/python" \
      "$REPO/deploy/perf_gate.py" --harness "$REPO/loadtest_concurrent.py" \
      --commit "$AFTER" "${PERF_REBASELINE_ARGS[@]}" 2>&1)
  PERF_RC=$?

  case "$PERF_RC" in
    0)
      log "$(echo "$PERF_OUT" | grep -m1 '^perf gate: OK' || echo 'perf gate: OK')" ;;
    2)
      log "perf gate could not measure (exit 2) — this release is NOT compared:"
      echo "$PERF_OUT" | grep -m2 '^perf gate' | sed 's/^/    /' ;;
    4)
      log "perf gate NOT ARMED (exit 4) — this release was never compared with the"
      log "    last one that passed, so it is not kept: rolling back to $BEFORE"
      echo "$PERF_OUT" | grep -m2 '^perf gate' | sed 's/^/    /'
      HEALTHY=0
      FAIL_REASON="perf gate (not armed: exit 4)"
      FAIL_DETAIL=$(printf '%s\n' "$PERF_OUT" | grep -E '^perf gate: NOT ARMED') ;;
    *)
      if [ "${PERF_ENFORCE:-false}" = "true" ]; then
        log "perf gate FAILED — this release is slower than the last one that passed:"
        echo "$PERF_OUT" | grep -E '^perf gate|^    -' | sed 's/^/    /'
        log "rolling $AFTER back rather than serving it"
        HEALTHY=0
        FAIL_REASON="perf gate (slower than the last release that passed)"
        # The numbers themselves: the gate's verdict line and the `    - ` lines it
        # refused on, each carrying a ratio against the baseline commit. This is the
        # reading that turns "slower" into something an operator can adjudicate. The
        # verdict is selected *first* — the progressed narration that the gate also
        # prints would otherwise fill the cap and cut the reasons off the bottom.
        FAIL_DETAIL=$({ printf '%s\n' "$PERF_OUT" | grep -E '^perf gate: (REGRESSED|CANNOT MEASURE)'
                        printf '%s\n' "$PERF_OUT" | grep -E '^    - '; })
      else
        log "perf gate FAILED but PERF_ENFORCE is not 'true' — keeping the release:"
        echo "$PERF_OUT" | grep -E '^perf gate|^    -' | head -8 | sed 's/^/    /'
        log "    to enforce it: PERF_ENFORCE=\"true\" in $PERF_CONF"
      fi ;;
  esac
fi

if [ "$HEALTHY" = "1" ]; then
  RUN_STEP="done"
  mkdir -p "$STATE_DIR"
  printf '%s\n%s\n%s\n' "$AFTER" "$(date -Is)" "$SNAPSHOT" > "$STATE_DIR/last-deploy"
  # A release that got all the way here is not a box in a loop, whatever it was
  # refusing before it started.
  refusal_streak_clear
  log "DEPLOY OK: $BEFORE -> $AFTER"
  # The release is verified and serving, so this is the moment the arrangement can
  # be brought back in step with the repo — a copy that has drifted, or a launcher
  # rendered from an older entrypoint.sh, heals here instead of waiting for
  # somebody to run the installer as root.
  refresh_installed_launchers
  if [ -n "$SNAPSHOT" ]; then
    log "recovery point kept: $SNAPSHOT"
  fi
  if [ "$WORKER_STALE" = "1" ]; then
    # The app is verified and the worker is not: a half-deployed release, and the
    # half that is wrong is the one nothing probes. Say so where the deploy ends.
    log "WARNING: $WORKER_UNIT was NOT restarted, so background work is still"
    log "         running $BEFORE code. Fix it before trusting scans:"
    log "             systemctl restart $WORKER_UNIT"
  fi
  exit 0
fi

# ── Failure: put the previous release back ───────────────────────────────────
log "$AFTER did not pass verification — rolling back to $BEFORE"
# Record the refusal before rolling back. Without this the next tick finds the
# same commit on the branch, merges it again, and fails the same way — forever.
quarantine_write
if [ -n "$SNAPSHOT" ]; then
  # Code goes back on its own; data does not. Name the recovery point here, where
  # someone is already looking, rather than leaving them to guess whether one
  # exists — that guess is the difference between a rollback and a loss.
  log "this release shipped migrations; the data as it was before it is in:"
  log "    $SNAPSHOT"
  log "to put the data back too:"
  log "    $REPO/.venv/bin/python $SNAPSHOT_CMD --repo $REPO --restore $SNAPSHOT"
fi
journalctl -u "$SERVICE" -n 30 --no-pager 2>/dev/null | sed 's/^/    /'
as_owner git -C "$REPO" reset --hard --quiet "$BEFORE"
reload_app
# The worker goes back with the app. Leaving it on the rejected revision is the
# same mismatch in the other direction — and this is the direction a rollback
# hides, because the site looks healthy while every background task fails.
reload_worker || true
sleep 3

if systemctl is-active --quiet "$SERVICE" && probe_app; then
  log "ROLLED BACK to $BEFORE — that release is serving."
  log "$AFTER is quarantined: the next tick skips it, so the box stops re-pulling it."
  log "Push a fix to $BRANCH (lifts it automatically), or release this exact commit:"
  log "    touch $RELEASE_FILE"
  mkdir -p "$STATE_DIR"
  printf '%s\n%s\n%s\n' "$BEFORE" "$(date -Is)" "$SNAPSHOT" > "$STATE_DIR/last-deploy"
  exit 10
fi

log "ROLLBACK ALSO UNHEALTHY — $BEFORE is not serving either. Manual attention required."
exit 11
