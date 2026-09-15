#!/usr/bin/env bash
# ─── ScanGrade — install automated deployment (run ONCE, as root) ───────────
#
#   bash deploy/install-auto-deploy.sh
#
# After this, a push to origin/main reaches production by itself: the VPS pulls
# every two minutes, verifies the new code, reloads the app gracefully, and puts
# the previous commit back if the new one does not come up. Nothing on the
# GitHub side needs a credential, and nobody has to open the console again.
#
# Safe to re-run: it re-reads the automation from the checkout each time, so you
# can use it to upgrade the logic after pulling a newer commit.
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

REPO="/opt/scangrade"
SERVICE="scangrade"
BRANCH="main"
DEPLOY_BIN="/usr/local/bin/scangrade-deploy"
SNAPSHOT_BIN="/usr/local/bin/scangrade-db-snapshot"
BACKUP_DIR="/var/backups/scangrade"
BACKUP_KEEP=5
STAMP=$(date +%Y%m%d_%H%M%S)

say() { echo; echo "── $* ────────────────────────────────────────"; }

if [ "$(id -u)" -ne 0 ]; then
  cat <<EOF

!! Run this as ROOT — it installs a unit into /etc/systemd/system and restarts
   $SERVICE. You are $(id -un) (uid $(id -u)).

   From the root console:   bash $0

EOF
  exit 2
fi

[ -d "$REPO/.git" ] || { echo "!! $REPO is not a git checkout"; exit 3; }
OWNER=$(stat -c '%U' "$REPO")
# The owner's real home — not $REPO — so git looks for credentials where they
# actually live and pip's cache never lands inside the checkout (an untracked
# $REPO/.cache would trip the deploy script's own dirty-checkout guard).
OWNER_HOME=$(getent passwd "$OWNER" | cut -d: -f6)
[ -n "$OWNER_HOME" ] || OWNER_HOME=/tmp
as_owner() { runuser -u "$OWNER" -- env HOME="$OWNER_HOME" GIT_TERMINAL_PROMPT=0 GIT_ASKPASS=/bin/true "$@"; }

# ── 1. Bring the checkout up to date, so the automation we install is the
#       version in the repo rather than whatever happens to be lying around.
say "Updating $REPO from origin/$BRANCH"
cd "$REPO"

DIRTY=$(as_owner git -C "$REPO" status --porcelain)
if [ -n "$DIRTY" ]; then
  echo "$DIRTY"
  echo "!! $REPO has local changes — commit or discard them first, then re-run."
  exit 4
fi

BEFORE=$(as_owner git -C "$REPO" rev-parse --short HEAD)
as_owner git -C "$REPO" fetch --quiet origin "$BRANCH"
as_owner git -C "$REPO" merge --ff-only --quiet "origin/$BRANCH" || {
  echo "!! not a fast-forward — resolve $REPO manually, then re-run."; exit 5; }
AFTER=$(as_owner git -C "$REPO" rev-parse --short HEAD)
echo "   $BEFORE -> $AFTER"

for f in deploy/scangrade-deploy.sh deploy/entrypoint.sh deploy/smoke_test.py \
         deploy/db_snapshot.py deploy/scangrade-db-snapshot.sh deploy/theme_gate.sh \
         deploy/claims_gate.py deploy/perf_gate.py \
         tests/unit/test_dark_theme_contrast.py tests/unit/test_theme_stylesheet.py \
         deploy/scangrade-deploy.service deploy/scangrade-deploy.timer \
         deploy/scangrade.service; do
  [ -f "$REPO/$f" ] || { echo "!! missing $REPO/$f — is origin/$BRANCH the right commit?"; exit 6; }
done

# The deploy runs the theme gate against every release, and treats "the gate
# cannot run" as a warning rather than a rollback — so a VPS where pytest is
# missing would keep shipping uncheckable releases and only whisper about it.
# Proving it here means that warning never has a reason to appear.
say "Checking the dark-mode readability gate"
if bash "$REPO/deploy/theme_gate.sh" >/dev/null 2>&1; then
  echo "   gate runs and passes"
else
  rc=$?
  if [ "$rc" -eq 2 ]; then
    echo "!! the gate cannot run (exit 2) — the deploy will warn on every release instead of"
    echo "   checking anything. Usually a missing pytest:"
    echo "       $REPO/.venv/bin/pip install -r $REPO/requirements.txt"
    exit 7
  fi
  # Exit 1: the gate ran and found something. That is exactly what the deploy
  # would refuse to ship, so it has to be fixed before this installer is useful.
  echo "!! the gate found unreadable templates (exit $rc) — run it to see them:"
  echo "       bash $REPO/deploy/theme_gate.sh"
  exit 7
fi

# ── 2. The thing root will actually run: a LAUNCHER, not a copy.
#
#       This step used to be `install … "$REPO/deploy/scangrade-deploy.sh"
#       "$DEPLOY_BIN"`, which installs a snapshot. A snapshot stops receiving
#       fixes the moment it lands: every later change to the deploy logic sat on
#       GitHub while the timer kept deploying with the version of the day it was
#       installed, and nothing compared the two — so the only way to deliver a
#       script fix was for a human to re-run this installer as root, the very
#       step automatic deployment exists to remove. Worse, the snapshot for
#       `scangrade-db-snapshot` was silently broken when run from
#       /usr/local/bin: the wrapper derives the checkout from its own location,
#       so it looked for /usr/local/.venv/bin/python and refused to take the
#       snapshot at exactly the moment one was wanted.
#
#       deploy/entrypoint.sh execs the checkout's own copy instead, choosing the
#       target by the name it was installed under. What root runs is therefore
#       always the commit the checkout is on, and re-running this installer is
#       needed only to change the arrangement itself (a new unit, a new path),
#       never to deliver a script fix.
install_launcher() {
  local bin="$1" tmp
  tmp=$(mktemp)
  sed "s|@REPO@|$REPO|" "$REPO/deploy/entrypoint.sh" > "$tmp"
  if grep -q '@REPO@' "$tmp"; then
    rm -f "$tmp"
    echo "!! the launcher still contains the @REPO@ placeholder after rendering"
    exit 8
  fi
  install -m 0755 -o root -g root "$tmp" "$bin"
  rm -f "$tmp"
  bash -n "$bin"
}

say "Installing $DEPLOY_BIN and $SNAPSHOT_BIN (launchers, not copies)"
install_launcher "$DEPLOY_BIN"
install_launcher "$SNAPSHOT_BIN"
echo "   both exec $REPO/deploy/*.sh, so the runner cannot lag behind the repo"
echo "   (a copy that differs from the checkout now refuses to run: exit 14)"

# ── 2b. Where the snapshots go.
#       The deploy captures a release that changes supabase/migrations — but
#       migrations here are pasted into the Supabase SQL editor by hand, and that
#       changes no file, so nothing can detect it. This is the command to run
#       before doing that, and the reason it exists as a command at all.
#
#       The archives hold personal data (names, phone numbers, exam answers), so
#       they live in a root-only directory and the rotation keeps the newest
#       $BACKUP_KEEP. UU PDP treats an unsecured copy as a breach of its own;
#       docs/AUTO_DEPLOY.md records the retention.
say "Snapshot directory"
mkdir -p "$BACKUP_DIR"
chmod 0700 "$BACKUP_DIR"
echo "   $BACKUP_DIR (mode 0700, newest $BACKUP_KEEP)"
echo "   $SNAPSHOT_BIN execs $REPO/deploy/scangrade-db-snapshot.sh"

# ── 3. Smoke-test credentials.
#       They cannot live in the repo (deployment-specific, and passwords), so
#       they live here and the repo ships the logic instead. Seeded with the
#       accounts manage.py creates so the gate is useful immediately.
SMOKE_CONF=/etc/scangrade-smoke.conf
say "Smoke-test credentials ($SMOKE_CONF)"
if [ -f "$SMOKE_CONF" ]; then
  echo "   already exists — left untouched"
else
  # Prefer the app's own APP_URL. It must be https:// — production sets
  # SESSION_COOKIE_SECURE, so over plain HTTP the session cookie is dropped and
  # every login would look broken.
  BASE_DEFAULT=$(grep -E '^APP_URL=' "$REPO/.env" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '\"' | sed 's/[[:space:]]*#.*//')
  case "$BASE_DEFAULT" in
    https://*) ;;
    *) BASE_DEFAULT="https://scangrade.web.id" ;;
  esac

  cat > "$SMOKE_CONF" <<EOF
# Read by scangrade-deploy. Root-only: it holds passwords.
#
# SMOKE_BASE_URL is what the smoke test talks to. It must be HTTPS: production
# sets SESSION_COOKIE_SECURE, so over plain HTTP the browser-like client would
# drop the session cookie and every login would look broken.
#
# Replace the accounts below with dedicated read-only ones when you can; these
# are the demo accounts manage.py seeds.
SMOKE_BASE_URL="$BASE_DEFAULT"

# SMOKE_ENFORCE=true makes a failed smoke test roll the release back.
# install-auto-deploy.sh sets it once the logins below are proven to work.
SMOKE_ENFORCE="false"

SMOKE_SUPER_ADMIN="superadmin@scan-grade.app:superadmin123"
SMOKE_ADMIN_SEKOLAH="admin_smp@scan-grade.app:demo123"
SMOKE_GURU="guru_mtk_smp@scan-grade.app:demo123"
SMOKE_MURID="siswa2_smp@scan-grade.app:demo123"
EOF
  chmod 0600 "$SMOKE_CONF"
  chown root:root "$SMOKE_CONF"
  echo "   created (mode 0600)"
fi

# Prove the accounts before arming the gate. A config whose passwords no longer
# work must never be able to reject a good release.
say "Checking the smoke-test credentials"
SMOKE_ENV=()
while IFS='=' read -r key value; do
  case "$key" in SMOKE_SUPER_ADMIN|SMOKE_ADMIN_SEKOLAH|SMOKE_GURU|SMOKE_MURID|SMOKE_BASE_URL|SMOKE_INSECURE)
    SMOKE_ENV+=("$key=$value");; esac
done < <(grep -E '^SMOKE_[A-Z_]+=' "$SMOKE_CONF" | tr -d '"')

if as_owner env "${SMOKE_ENV[@]}" "$REPO/.venv/bin/python" \
     "$REPO/deploy/smoke_test.py" --check-credentials; then
  sed -i 's/^SMOKE_ENFORCE=.*/SMOKE_ENFORCE="true"/' "$SMOKE_CONF"
  echo "   every configured account signed in -> SMOKE_ENFORCE=true"
else
  echo "   NOT all accounts signed in -> SMOKE_ENFORCE stays false."
  echo "   Fix the accounts in $SMOKE_CONF, then re-run this installer to arm the gate."
fi

# ── 3b. The published capacity claims.
#       The landing page publishes a capacity table measured against this
#       deployment once. The deploy re-measures the page's lowest rung on every
#       release and compares it with what the page says. This is the config for
#       that gate, plus the proof that it can run and passes before it is armed
#       — a gate armed against a stale roster would reject good releases, which
#       is exactly what the smoke-test section above is careful to avoid.
CLAIMS_CONF=/etc/scangrade-claims.conf
say "Published capacity claims ($CLAIMS_CONF)"
if [ -f "$CLAIMS_CONF" ]; then
  echo "   already exists — left untouched"
else
  BASE_DEFAULT=$(grep -E '^APP_URL=' "$REPO/.env" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '\"' | sed 's/[[:space:]]*#.*//')
  case "$BASE_DEFAULT" in
    https://*) ;;
    *) BASE_DEFAULT="https://scangrade.web.id" ;;
  esac

  cat > "$CLAIMS_CONF" <<EOF
# Read by scangrade-deploy. Runs the claims gate on every release.
#
# The gate re-measures the landing page's own lowest advertised rung and
# compares it with the numbers the page publishes, so a claim cannot outlive the
# box that was measured.
#
# It must be HTTPS: production sets SESSION_COOKIE_SECURE, so over plain HTTP the
# session cookie is dropped and every probe would look like a failed login.
CLAIMS_BASE_URL="$BASE_DEFAULT"

# One account per session is required -- reusing logins turns per-identity rate
# limiting into errors that look like the server's fault. The roster is written
# by provision_loadtest.py and lives outside git, so a rollback cannot remove it.
CLAIMS_ROSTER="$REPO/.freebuff/lt_roster.json"

# Empty = probe the rung the page advertises. Set a number to probe fewer
# (a lower-bound check, and the gate says so when you do).
# 30s, not 12: a shorter probe is dominated by the login burst at the start and
# measures the wrong thing. The published rows are 60-second runs.
CLAIMS_SESSIONS=""
CLAIMS_DURATION="30"

# Never load more than this at deploy time, whatever the page claims. Raising it
# is a deliberate choice: this runs on the box that is serving students.
CLAIMS_MAX_SESSIONS="60"

# One JSON line per run, so a published number has a history and not a memory.
CLAIMS_EVIDENCE="/var/lib/scangrade-deploy/claims/history.jsonl"

# CLAIMS_ENFORCE=true lets a confirmed divergence roll the release back.
# install-auto-deploy.sh sets it only after a probe has actually passed.
CLAIMS_ENFORCE="false"
EOF
  chmod 0600 "$CLAIMS_CONF"
  chown root:root "$CLAIMS_CONF"
  echo "   created (mode 0600)"
fi

mkdir -p /var/lib/scangrade-deploy/claims
chown "$OWNER":"$OWNER" /var/lib/scangrade-deploy/claims
chmod 0750 /var/lib/scangrade-deploy/claims

say "Checking the claims gate"
if ! as_owner "$REPO/.venv/bin/python" "$REPO/deploy/claims_gate.py" --check \
     --page "$REPO/app/templates/landing.html"; then
  echo "   the gate cannot run yet — see the reason above."
  echo "   It needs a roster holding one account per session:"
  echo "       cd $REPO && .venv/bin/python provision_loadtest.py 60 2"
  echo "   then re-run this installer to arm it."
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

  # One real probe, before anything is armed. It costs a few seconds of load on
  # the box you are standing on, and it is the only way to know whether the page
  # describes this deployment at all.
  set +e
  CLAIMS_OUT=$(as_owner env "${CLAIMS_ENV[@]}" "$REPO/.venv/bin/python" \
      "$REPO/deploy/claims_gate.py" --page "$REPO/app/templates/landing.html" \
      --harness "$REPO/loadtest_concurrent.py" 2>&1)
  CLAIMS_RC=$?
  set -e
  echo "$CLAIMS_OUT" | sed 's/^/   /'

  case "$CLAIMS_RC" in
    0)
      sed -i 's/^CLAIMS_ENFORCE=.*/CLAIMS_ENFORCE="true"/' "$CLAIMS_CONF"
      echo "   the page matches this deployment -> CLAIMS_ENFORCE=true" ;;
    2)
      echo "   the gate could not measure (exit 2) -> CLAIMS_ENFORCE stays false."
      echo "   That is not a verdict on the page; fix the measurement and re-run." ;;
    *)
      echo "   the page does NOT describe this deployment -> CLAIMS_ENFORCE stays false."
      echo "   Fix one of the two, then re-run this installer to arm the gate:"
      echo "       publish the numbers you measure, or find what made this box slower" ;;
  esac
fi

# ── 3c. The release-to-release performance gate.
#       Gate 5 (above) compares this box with a number written on the landing
#       page, at the rung the page advertises. It cannot see a release that makes
#       every page 40% slower while staying inside that bound. This gate compares
#       the release with the last release that passed, at a small fixed load, and
#       refuses one whose response time regressed. Config for that gate:
PERF_CONF=/etc/scangrade-perf.conf
say "Release-to-release performance ($PERF_CONF)"
if [ -f "$PERF_CONF" ]; then
  echo "   already exists — left untouched"
else
  BASE_DEFAULT=$(grep -E '^APP_URL=' "$REPO/.env" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"' | sed 's/[[:space:]]*#.*//')
  case "$BASE_DEFAULT" in
    https://*) ;;
    *) BASE_DEFAULT="https://scangrade.web.id" ;;
  esac

  cat > "$PERF_CONF" <<EOF
# Read by scangrade-deploy. Runs the performance gate on every release.
#
# The gate runs a SMALL reference load -- 20 concurrent students for 20 seconds --
# and compares the result with the last release that passed. It is deliberately
# small: this is the 1 vCPU that serves students, and the deploy does not get to
# load it the way a benchmark would. At 20 sessions the box is far from saturated,
# so latency tracks per-request cost, which is the thing a release can change.
# The advertised rung stays the claims gate's job (Gate 5).
#
# It must be HTTPS: production sets SESSION_COOKIE_SECURE, so over plain HTTP the
# session cookie is dropped and every probe would look like a failed login.
PERF_BASE_URL="$BASE_DEFAULT"

# One account per session is required -- reusing logins turns per-identity rate
# limiting into errors that look like the server's fault. Written by
# provision_loadtest.py, outside git, so a rollback cannot remove it.
PERF_ROSTER="$REPO/.freebuff/lt_roster.json"
PERF_SESSIONS="20"
PERF_TEACHERS="2"
PERF_DURATION="20"

# The reference measurement. Written only when a release PASSES, so a slow
# release cannot become the thing the next one is judged against. Delete it, or
# run the gate with --rebaseline, when a slower release is a deliberate trade.
PERF_BASELINE="/var/lib/scangrade-deploy/perf/baseline.json"

# One JSON line per run, so "it got slower" has a history and not a memory.
PERF_EVIDENCE="/var/lib/scangrade-deploy/perf/history.jsonl"

# True from the start, unlike CLAIMS_ENFORCE, and that difference is the point:
# with no baseline yet the gate writes one and passes, so arming it cannot reject
# anything. From the second release on, a confirmed regression rolls the release
# back.
PERF_ENFORCE="true"
EOF
  chmod 0600 "$PERF_CONF"
  chown root:root "$PERF_CONF"
  echo "   created (mode 0600)"
fi

mkdir -p /var/lib/scangrade-deploy/perf
chown "$OWNER":"$OWNER" /var/lib/scangrade-deploy/perf
chmod 0750 /var/lib/scangrade-deploy/perf

say "Checking the performance gate"
# The conf is read and passed the same way scangrade-deploy reads it, so "the gate
# can run" is established with the settings that will actually be used rather than
# with the gate's defaults. (Reading the conf and then not passing it is exactly
# how the claims gate shipped unable to measure anything — see env_default() in
# deploy/claims_gate.py.)
set -a
# shellcheck disable=SC1090
. "$PERF_CONF"
set +a
set +e
PERF_OUT=$(as_owner env PERF_BASE_URL="${PERF_BASE_URL:-}" PERF_ROSTER="${PERF_ROSTER:-}" \
    PERF_SESSIONS="${PERF_SESSIONS:-}" PERF_TEACHERS="${PERF_TEACHERS:-}" \
    PERF_DURATION="${PERF_DURATION:-}" PERF_BASELINE="${PERF_BASELINE:-}" \
    PERF_EVIDENCE="${PERF_EVIDENCE:-}" \
    "$REPO/.venv/bin/python" "$REPO/deploy/perf_gate.py" --check 2>&1)
PERF_RC=$?
echo "$PERF_OUT" | sed 's/^/   /'
if [ "$PERF_RC" -ne 0 ]; then
  echo "   the gate cannot run yet — see the reason above."
  echo "   It needs a roster holding one account per session:"
  echo "       cd $REPO && .venv/bin/python provision_loadtest.py 25 3"
  echo "   The first release after this one writes the baseline and passes;"
  echo "   from then on a confirmed regression rolls the release back."
fi

# ── 4. Unit files. Back up anything we replace: this is the file that keeps the
#       site up, and a silent overwrite with a wrong copy would be unrecoverable.
say "Installing systemd units"
for unit in scangrade.service scangrade-deploy.service scangrade-deploy.timer; do
  dest="/etc/systemd/system/$unit"
  src="$REPO/deploy/$unit"
  if [ -f "$dest" ] && cmp -s "$src" "$dest"; then
    echo "   $unit — unchanged"
    continue
  fi
  if [ -f "$dest" ]; then
    cp -a "$dest" "$dest.bak-$STAMP"
    echo "   $unit — replaced (previous kept as $unit.bak-$STAMP)"
  else
    echo "   $unit — installed"
  fi
  install -m 0644 -o root -g root "$src" "$dest"
done

systemctl daemon-reload

# ── 4b. Reload the app, because step 1 already pulled new code and gunicorn is
#        still serving the old copy. Without this the install would leave
#        production on stale code *forever*: the next timer tick finds the
#        checkout already at origin/main and exits without deploying anything.
say "Reloading $SERVICE so the pulled code is actually running"
if systemctl reload "$SERVICE" 2>/dev/null; then
  echo "   reloaded gracefully (SIGHUP)"
else
  echo "   reload unsupported — restarting"
  systemctl restart "$SERVICE"
fi
sleep 3

if [ "$(systemctl is-active "$SERVICE")" != "active" ]; then
  journalctl -u "$SERVICE" -n 25 --no-pager
  echo "!! $SERVICE did not come back up — the units are installed, but fix this first."
  exit 7
fi

PROBE=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:8000/" || true)
echo "   app answered $PROBE on 127.0.0.1:8000"
if [ "$PROBE" != "200" ]; then
  echo "!! the app is not serving; check: journalctl -u $SERVICE -n 50"
  exit 7
fi

# ── 5. Turn the timer on. start, not restart: an in-flight run is left alone.
say "Enabling the timer"
systemctl enable --now scangrade-deploy.timer >/dev/null
echo "   scangrade-deploy.timer is $(systemctl is-active scangrade-deploy.timer)"

# ── 6. Prove the unit can run at all. It will find nothing to deploy (step 1
#       already pulled), so this checks the mechanics and not the rollback path:
#       the next real release is the first full end-to-end run.
say "Test run of the deploy unit"
systemctl start scangrade-deploy.service || true
sleep 2
journalctl -u scangrade-deploy.service -n 20 --no-pager | sed 's/^/   /'

say "Done"
echo "   $SERVICE is $(systemctl is-active $SERVICE) on 127.0.0.1:8000"
echo "   deployed commit: $(as_owner git -C "$REPO" rev-parse --short HEAD)"
echo "   runner        : $DEPLOY_BIN execs this checkout, so a fix to the deploy"
echo "                   script is live on the next tick — no install step needed"
echo
echo "   watch deploys : journalctl -u scangrade-deploy.service -f"
echo "   next tick     : systemctl list-timers scangrade-deploy.timer"
echo "   test it live  : push a commit to main, then watch the journal above"
echo "   deploy now    : systemctl start scangrade-deploy.service"
echo "   freeze/resume : touch /etc/scangrade-deploy.pause   (rm to resume)"
echo "   switch it off : systemctl disable --now scangrade-deploy.timer"
echo
echo "   Releases that change supabase/migrations get a data snapshot first, and"
echo "   the recovery point is named in the journal. A migration applied by hand in"
echo "   the SQL editor is NOT visible to that check — run this before doing it:"
echo "       scangrade-db-snapshot --label before-<migration>"
echo "   then, if it goes wrong:"
echo "       scangrade-db-snapshot --restore /var/backups/scangrade/<archive>"
echo
echo "   Both also run straight from the checkout, with nothing installed:"
echo "       bash $REPO/deploy/scangrade-db-snapshot.sh --help"
