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

if systemctl is-active --quiet "$SERVICE" && probe_app; then
  mkdir -p "$STATE_DIR"
  printf '%s\n%s\n' "$AFTER" "$(date -Is)" > "$STATE_DIR/last-deploy"
  log "DEPLOY OK: $BEFORE -> $AFTER"
  exit 0
fi

# ── Failure: put the previous release back ───────────────────────────────────
log "app is not healthy on $AFTER — rolling back to $BEFORE"
journalctl -u "$SERVICE" -n 30 --no-pager 2>/dev/null | sed 's/^/    /'
as_owner git -C "$REPO" reset --hard --quiet "$BEFORE"
reload_app
sleep 3

if systemctl is-active --quiet "$SERVICE" && probe_app; then
  log "ROLLED BACK to $BEFORE — that release is serving. Fix origin/$BRANCH before the next tick."
  printf '%s\n%s\n' "$BEFORE" "$(date -Is)" > "$STATE_DIR/last-deploy"
  exit 10
fi

log "ROLLBACK ALSO UNHEALTHY — $BEFORE is not serving either. Manual attention required."
exit 11
