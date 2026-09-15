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
BACKUP_DIR="/var/backups/scangrade"
BACKUP_KEEP=5
SNAPSHOT_CMD="$REPO/deploy/db_snapshot.py"
LOG_TAG="scangrade-deploy"

log() { echo "[$(date '+%F %T')] $*"; }

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
  exit 2
fi

# ── Maintenance window ───────────────────────────────────────────────────────
# Create the file to freeze deploys (e.g. during exam week) without touching the
# timer; remove it to let the next tick catch up.
if [ -e "$PAUSE_FILE" ]; then
  log "paused by $PAUSE_FILE — not deploying"
  exit 0
fi

# ── Gate 0: is this the checkout's runner, or a snapshot of an older one? ────
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

[ -d "$REPO/.git" ] || { log "$REPO is not a git checkout — refusing"; exit 3; }
[ -x "$REPO/.venv/bin/gunicorn" ] || { log "no virtualenv at $REPO/.venv — refusing"; exit 3; }

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

cd "$REPO" || exit 3

# ── Guard against clobbering hand edits ──────────────────────────────────────
DIRTY=$(as_owner git -C "$REPO" status --porcelain)
if [ -n "$DIRTY" ]; then
  log "checkout has local changes — NOT deploying:"
  echo "$DIRTY" | sed 's/^/    /'
  exit 4
fi

BEFORE=$(as_owner git -C "$REPO" rev-parse --short HEAD)

# ── Fetch ────────────────────────────────────────────────────────────────────
if ! as_owner git -C "$REPO" fetch --quiet origin "$BRANCH"; then
  log "git fetch failed (network or credentials) — will retry next tick"
  exit 5
fi

AFTER=$(as_owner git -C "$REPO" rev-parse --short "origin/$BRANCH")

if [ "$BEFORE" = "$AFTER" ]; then
  exit 0                      # nothing new; stay silent so the journal stays quiet
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
    exit 12
  fi
  if "$REPO/.venv/bin/python" "$SNAPSHOT_CMD" --repo "$REPO" --out "$BACKUP_DIR" \
       --keep "$BACKUP_KEEP" --label "$AFTER" --quiet 2>&1 | sed 's/^/    /'; then
    SNAPSHOT=$(ls -1t "$BACKUP_DIR"/scangrade-db-*.tar.gz 2>/dev/null | head -1)
    if [ -n "$SNAPSHOT" ]; then
      log "snapshot: $SNAPSHOT"
    else
      log "snapshot command succeeded but left no archive — refusing to deploy"
      exit 12
    fi
  else
    log "SNAPSHOT FAILED — not deploying a migration release without a recovery point"
    log "nothing has been merged; $BEFORE is untouched. Retry on the next tick."
    exit 12
  fi
else
  log "no migration in this release — no snapshot needed"
fi

if ! as_owner git -C "$REPO" merge --ff-only --quiet "origin/$BRANCH"; then
  log "not a fast-forward (history rewritten?) — leaving $BEFORE in place"
  exit 6
fi
log "pulled; $(echo "$CHANGED" | wc -l) file(s) changed"

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
  as_owner git -C "$REPO" reset --hard --quiet "$BEFORE"
  exit 8
fi

# ── Gate 2: does the app actually build, under the real configuration? ───────
# Constructing it registers every blueprint and route, and validates .env — so a
# bad import, a duplicate endpoint or a broken key is caught here instead of by
# whoever opens the site next. The schedulers are switched off explicitly: the
# retention loop runs a purge pass the moment it starts, and a deploy check has
# no business deleting anything.
if ! as_owner env START_BACKGROUND_SCHEDULERS=false "$REPO/.venv/bin/python" -c '
import sys
from app import create_app
app = create_app()          # the production configuration, from .env
rules = {r.rule for r in app.url_map.iter_rules()}
missing = {"/auth/login"} - rules
if missing:
    sys.exit("missing core routes: %s" % sorted(missing))
print("app constructs ok (%d routes)" % len(rules))
'; then
  log "app failed to construct — rolling back to $BEFORE"
  as_owner git -C "$REPO" reset --hard --quiet "$BEFORE"
  exit 9
fi

# ── Gate 3: is this release readable? ────────────────────────────────────────
# A template, or a colour utility one of them uses, can leave a page unreadable
# in dark mode while looking perfectly fine in the mode its author was working
# in — and nothing errors. It cannot be caught by any of the gates above, by the
# smoke test below (which checks that pages *answer*, not that they can be read),
# or by looking at a screenshot in light mode. So it is its own gate, and it runs
# before the app is reloaded, when rolling back is still free.
#
# Exit 2 is "the check could not run" — a missing interpreter, or pytest absent
# from the venv. That is a problem with the gate, not with the release, so it is
# logged loudly and does not roll back good code: a broken checker must never be
# able to take the site down. Exit 1 is a real finding and does.
THEME_OUT=$(as_owner bash "$REPO/deploy/theme_gate.sh" 2>&1)
THEME_RC=$?
if [ "$THEME_RC" -eq 0 ]; then
  log "$(echo "$THEME_OUT" | tail -1)"
elif [ "$THEME_RC" -eq 2 ]; then
  log "theme gate COULD NOT RUN (exit 2) — this release is NOT contrast-checked:"
  echo "$THEME_OUT" | sed 's/^/    /'
else
  log "theme gate FAILED (exit $THEME_RC) — rolling back to $BEFORE"
  echo "$THEME_OUT" | sed 's/^/    /'
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

reload_app
sleep 3

HEALTHY=0
if systemctl is-active --quiet "$SERVICE" && probe_app; then
  HEALTHY=1
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
  # Sourcing a broken file would take the whole deploy script down with it.
  log "$SMOKE_CONF has a syntax error — skipping the smoke test"
elif [ "$HEALTHY" = "1" ] && [ -f "$SMOKE_CONF" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$SMOKE_CONF"
  set +a

  SMOKE_ENV=()
  for v in SMOKE_BASE_URL SMOKE_INSECURE SMOKE_SUPER_ADMIN SMOKE_ADMIN_SEKOLAH SMOKE_GURU SMOKE_MURID; do
    [ -n "${!v:-}" ] && SMOKE_ENV+=("$v=${!v}")
  done

  as_owner env "${SMOKE_ENV[@]}" "$REPO/.venv/bin/python" "$REPO/deploy/smoke_test.py"
  SMOKE_RC=$?

  case "$SMOKE_RC" in
    0)
      log "smoke test passed" ;;
    2)
      # Nothing was testable: no accounts, or the base URL is unreachable from
      # this box. Neither is evidence that the release is bad.
      log "smoke test skipped (exit 2) — not treated as a failure" ;;
    *)
      if [ "${SMOKE_ENFORCE:-false}" = "true" ]; then
        log "smoke test FAILED (exit $SMOKE_RC) — rolling back"
        HEALTHY=0
      else
        log "smoke test FAILED (exit $SMOKE_RC) but SMOKE_ENFORCE is not 'true' — keeping the release"
      fi ;;
  esac
elif [ ! -f "$SMOKE_CONF" ]; then
  log "no $SMOKE_CONF — skipping the per-role smoke test (see docs/AUTO_DEPLOY.md)"
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
# Exit 2 is "could not measure" (no roster, an unreachable base URL, a box
# already busy with real students, a divergence that a second probe did not
# confirm). Never a rollback: an absent or unconfirmed measurement is not
# evidence of a bad release. Exit 1 is a confirmed divergence and does.
CLAIMS_CONF="/etc/scangrade-claims.conf"
if [ "$HEALTHY" != "1" ]; then
  : # already unhealthy; the rollback path owns it
elif [ ! -f "$CLAIMS_CONF" ]; then
  log "no $CLAIMS_CONF — the published capacity claims are NOT re-measured"
  log "    (see docs/AUTO_DEPLOY.md; install-auto-deploy.sh creates this file)"
elif ! bash -n "$CLAIMS_CONF" 2>/dev/null; then
  log "$CLAIMS_CONF has a syntax error — skipping the claims gate"
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
    *)
      if [ "${CLAIMS_ENFORCE:-false}" = "true" ]; then
        log "claims gate FAILED — the page promises what this box no longer does:"
        echo "$CLAIMS_OUT" | sed 's/^/    /'
        log "rolling $AFTER back rather than publishing numbers we cannot deliver"
        HEALTHY=0
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
# Exit 2 is "could not measure" again — no roster, no baseline yet, a reference
# load that changed, a box already busy, a divergence a second probe did not
# confirm. Never a rollback. The first run after installation has no baseline, so
# that release becomes one and passes: the gate arms itself rather than needing an
# operator to remember it.
PERF_CONF="/etc/scangrade-perf.conf"
if [ "$HEALTHY" != "1" ]; then
  : # already unhealthy; the rollback path owns it
elif [ ! -f "$PERF_CONF" ]; then
  log "no $PERF_CONF — this release is NOT compared with the previous one"
  log "    (see docs/AUTO_DEPLOY.md; install-auto-deploy.sh creates this file)"
elif ! bash -n "$PERF_CONF" 2>/dev/null; then
  log "$PERF_CONF has a syntax error — skipping the performance gate"
else
  set -a
  # shellcheck disable=SC1090
  . "$PERF_CONF"
  set +a

  PERF_ENV=()
  for v in PERF_BASE_URL PERF_ROSTER PERF_SESSIONS PERF_TEACHERS PERF_DURATION \
           PERF_BASELINE PERF_EVIDENCE; do
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
    *)
      if [ "${PERF_ENFORCE:-false}" = "true" ]; then
        log "perf gate FAILED — this release is slower than the last one that passed:"
        echo "$PERF_OUT" | grep -E '^perf gate|^    -' | sed 's/^/    /'
        log "rolling $AFTER back rather than serving it"
        HEALTHY=0
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
  if [ -n "$SNAPSHOT" ]; then
    log "recovery point kept: $SNAPSHOT"
  fi
  exit 0
fi

# ── Failure: put the previous release back ───────────────────────────────────
log "$AFTER did not pass verification — rolling back to $BEFORE"
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
sleep 3

if systemctl is-active --quiet "$SERVICE" && probe_app; then
  log "ROLLED BACK to $BEFORE — that release is serving. Fix origin/$BRANCH before the next tick."
  mkdir -p "$STATE_DIR"
  printf '%s\n%s\n%s\n' "$BEFORE" "$(date -Is)" "$SNAPSHOT" > "$STATE_DIR/last-deploy"
  exit 10
fi

log "ROLLBACK ALSO UNHEALTHY — $BEFORE is not serving either. Manual attention required."
exit 11
