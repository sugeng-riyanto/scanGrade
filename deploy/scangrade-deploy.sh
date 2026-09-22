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
RELEASE_FILE="/etc/scangrade-deploy.release"
#: The same one-shot release, asked for from the app instead of a shell. The app
#: runs as the service user and cannot write /etc, so the installer creates this
#: directory owned by that user (mode 0750, so only it and root can reach it).
#: A file in it means exactly what RELEASE_FILE means, and only its *existence*
#: is read — nothing a web request can write is ever executed, so the request
#: cannot carry a command.
REQUEST_DIR="$STATE_DIR/requests"
RELEASE_REQUEST="$REQUEST_DIR/release"
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

# Record a refusal. Called only after a gate has judged a *merged* release, so a
# transient failure earlier in the run can never freeze a good commit.
quarantine_write() {
  local reason="${FAIL_REASON:-unknown gate}"
  mkdir -p "$STATE_DIR" 2>/dev/null || true
  printf '%s\n%s\n%s\n' "$AFTER_FULL" "$(date -Is)" "$reason" \
    > "$QUARANTINE_FILE" 2>/dev/null || true
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
  for path in "$RELEASE_FILE" "$RELEASE_REQUEST"; do
    [ -e "$path" ] || continue
    asked="${asked:+$asked, }$path"
  done
  [ -n "$asked" ] || return 0
  held=$(quarantine_sha) || held=""
  if [ -n "$held" ]; then
    log "explicit release requested ($asked) — clearing the quarantine on ${held:0:7}"
  fi
  rm -f "$QUARANTINE_FILE" "$RELEASE_FILE" "$RELEASE_REQUEST" 2>/dev/null || true
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

preflight_write() {
  mkdir -p "$STATE_DIR" 2>/dev/null || true
  {
    printf '%s\n%s\n%s\n%s\n' "${PREFLIGHT_GATE:-unknown}" "$(date -Is)" \
      "${PREFLIGHT_EXIT:-?}" "${AFTER_FULL:-}"
    printf '%s\n' "${PREFLIGHT_DETAIL:-}"
  } > "$PREFLIGHT_FILE" 2>/dev/null || true
  # Readable by the app (the status page shows this) and owned by root.
  chmod 0644 "$PREFLIGHT_FILE" 2>/dev/null || true
}

# A merge is the moment this stage stopped refusing, so a record about failing to
# get this far no longer describes the box. A later refusal rewrites it; a release
# that merges and is then rolled back is the *quarantine's* record, not this one's.
preflight_forget() {
  rm -f "$PREFLIGHT_FILE" 2>/dev/null || true
}
# preflight-logic:end

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
  [ -f "$REPO/deploy/entrypoint.sh" ] || return 0

  staged="$(dirname "$target")/.$(basename "$target").new.$$"
  if ! sed "s|@REPO@|$REPO|" "$REPO/deploy/entrypoint.sh" > "$staged" 2>/dev/null; then
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

# Both names the installer installs, because both are rendered from the same
# template and the snapshot command is the one that was silently broken by being
# a copy (it derived the checkout from its own location).
refresh_installed_launchers() {
  LAUNCHER_TARGET="$INSTALLED_RUNNER" LAUNCHER_LABEL="the deploy runner" refresh_launcher
  LAUNCHER_TARGET="$INSTALLED_SNAPSHOT" LAUNCHER_LABEL="the snapshot command" refresh_launcher
}
# refresh-launcher-logic:end

# ── Serialise runs ───────────────────────────────────────────────────────────
# The timer already skips while the unit is active, but a manual run can overlap
# a timer run. Exiting 0 keeps a skipped run from looking like a failed one.
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

# ── Guard against clobbering hand edits ──────────────────────────────────────
# A `git status` that *failed* used to be read as "no local changes": the command
# substitution came back empty either way, and empty is what a clean tree looks
# like. So a checkout git could not read was merged into, which then failed at the
# merge with a message about fast-forwards. "We could not tell" is not "it is
# fine" — the same rule `app/utils/armament.py` follows for the armament checker —
# so an unreadable checkout is its own refusal, with git's own error as the reason.
DIRTY=""
if ! DIRTY=$(as_owner git -C "$REPO" status --porcelain 2>&1); then
  log "could not read the checkout's state — NOT deploying:"
  printf '%s\n' "$DIRTY" | sed 's/^/    /'
  PREFLIGHT_GATE=checkout_unreadable PREFLIGHT_EXIT=4 \
    PREFLIGHT_DETAIL="${DIRTY:-git status exited non-zero with no output}" preflight_write
  exit 4
fi
if [ -n "$DIRTY" ]; then
  log "checkout has local changes — NOT deploying:"
  echo "$DIRTY" | sed 's/^/    /'
  PREFLIGHT_GATE=dirty_checkout PREFLIGHT_EXIT=4 \
    PREFLIGHT_DETAIL="$DIRTY" preflight_write
  exit 4
fi

BEFORE=$(as_owner git -C "$REPO" rev-parse --short HEAD)

# ── Fetch ────────────────────────────────────────────────────────────────────
# stderr is captured rather than left to the journal: this is the one refusal whose
# cause is entirely inside git's own message (credentials, DNS, a proxy), and the
# status page is for an operator with no shell to read that journal from.
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

# An operator's explicit release is consumed even when there is nothing to
# deploy, so a pending request cannot sit on the box and surprise a later tick.
quarantine_honour_release

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

# stderr captured for the same reason the fetch's is, and here it is the only thing
# that can name the cause: a stale `.git/index.lock` makes this fail while `git
# status` succeeds, so "not a fast-forward" is a guess — and it was the guess the
# runner printed for three hours while nothing deployed.
MERGE_OUT=""
if ! MERGE_OUT=$(as_owner git -C "$REPO" merge --ff-only --quiet "origin/$BRANCH" 2>&1); then
  log "could not merge origin/$BRANCH into $BEFORE — leaving $BEFORE in place"
  [ -n "$MERGE_OUT" ] && printf '%s\n' "$MERGE_OUT" | sed 's/^/    /'
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
if echo "$CHANGED" | grep -qx "requirements.txt"; then
  log "requirements.txt changed — installing"
  if ! as_owner "$REPO/.venv/bin/pip" install -q -r "$REPO/requirements.txt"; then
    log "pip install FAILED — rolling back"
    as_owner git -C "$REPO" reset --hard --quiet "$BEFORE"
    exit 7
  fi
fi

# ── Gate 1: does it compile? ─────────────────────────────────────────────────
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
  quarantine_write
  as_owner git -C "$REPO" reset --hard --quiet "$BEFORE"
  exit 13
fi

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

  as_owner env "${SMOKE_ENV[@]}" "$REPO/.venv/bin/python" "$REPO/deploy/smoke_test.py"
  SMOKE_RC=$?

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
      FAIL_REASON="smoke test (exit 2: nothing was testable)" ;;
    *)
      if [ "${SMOKE_ENFORCE:-false}" = "true" ]; then
        log "smoke test FAILED (exit $SMOKE_RC) — rolling back"
        HEALTHY=0
        FAIL_REASON="smoke test (exit $SMOKE_RC)"
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
      FAIL_REASON="claims gate (not armed: exit 4)" ;;
    *)
      if [ "${CLAIMS_ENFORCE:-false}" = "true" ]; then
        log "claims gate FAILED — the page promises what this box no longer does:"
        echo "$CLAIMS_OUT" | sed 's/^/    /'
        log "rolling $AFTER back rather than publishing numbers we cannot deliver"
        HEALTHY=0
        FAIL_REASON="claims gate (the page promises what this box no longer does)"
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
           PERF_BASELINE PERF_EVIDENCE PERF_BYTES_SLACK PERF_ROUNDTRIPS_SLACK; do
    [ -n "${!v:-}" ] && PERF_ENV+=("$v=${!v}")
  done

  PERF_OUT=$(as_owner env "${PERF_ENV[@]}" "$REPO/.venv/bin/python" \
      "$REPO/deploy/perf_gate.py" --harness "$REPO/loadtest_concurrent.py" \
      --commit "$AFTER" 2>&1)
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
      FAIL_REASON="perf gate (not armed: exit 4)" ;;
    *)
      if [ "${PERF_ENFORCE:-false}" = "true" ]; then
        log "perf gate FAILED — this release is slower than the last one that passed:"
        echo "$PERF_OUT" | grep -E '^perf gate|^    -' | sed 's/^/    /'
        log "rolling $AFTER back rather than serving it"
        HEALTHY=0
        FAIL_REASON="perf gate (slower than the last release that passed)"
      else
        log "perf gate FAILED but PERF_ENFORCE is not 'true' — keeping the release:"
        echo "$PERF_OUT" | grep -E '^perf gate|^    -' | head -8 | sed 's/^/    /'
        log "    to enforce it: PERF_ENFORCE=\"true\" in $PERF_CONF"
      fi ;;
  esac
fi

if [ "$HEALTHY" = "1" ]; then
  mkdir -p "$STATE_DIR"
  printf '%s\n%s\n%s\n' "$AFTER" "$(date -Is)" "$SNAPSHOT" > "$STATE_DIR/last-deploy"
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
