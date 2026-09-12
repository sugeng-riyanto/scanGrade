#!/usr/bin/env bash
# ─── ScanGrade auto-deploy ───────────────────────────────────────────────────
# Installed as /usr/local/bin/scangrade-deploy by deploy/install-auto-deploy.sh
# and run every couple of minutes by scangrade-deploy.timer.
#
# It takes NO arguments, on purpose: this is the one thing root runs unattended,
# so it must not be usable as a general-purpose command runner. Anything that
# could be passed in (a branch, a commit, a path) is fixed below instead.
#
# It also refuses to leave a broken release running. If the new code does not
# import, build its routes, or answer on the app port, it puts the previous
# commit back and restarts that. An unattended deploy that only knows how to
# move forward is worse than no automation at all.
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

# ── Gate 3: sign in as each role and open the pages that matter ──────────────
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
