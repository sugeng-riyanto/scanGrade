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

for f in deploy/scangrade-deploy.sh deploy/smoke_test.py deploy/db_snapshot.py \
         deploy/scangrade-db-snapshot.sh \
         deploy/scangrade-deploy.service deploy/scangrade-deploy.timer \
         deploy/scangrade.service; do
  [ -f "$REPO/$f" ] || { echo "!! missing $REPO/$f — is origin/$BRANCH the right commit?"; exit 6; }
done

# ── 2. The thing root will actually run.
say "Installing $DEPLOY_BIN"
install -m 0755 -o root -g root "$REPO/deploy/scangrade-deploy.sh" "$DEPLOY_BIN"
bash -n "$DEPLOY_BIN"
echo "   syntax ok"

# ── 2b. Snapshots, and the one the deploy cannot take by itself.
#       The deploy captures a release that changes supabase/migrations — but
#       migrations here are pasted into the Supabase SQL editor by hand, and that
#       changes no file, so nothing can detect it. This is the command to run
#       before doing that, and the reason it exists as a command at all.
#
#       The archives hold personal data (names, phone numbers, exam answers), so
#       they live in a root-only directory and the rotation keeps the newest
#       $BACKUP_KEEP. UU PDP treats an unsecured copy as a breach of its own;
#       docs/AUTO_DEPLOY.md records the retention.
say "Installing $SNAPSHOT_BIN"
mkdir -p "$BACKUP_DIR"
chmod 0700 "$BACKUP_DIR"
# Copied out of the checkout, not generated here. This step used to write its own
# inline copy of the wrapper, which is two versions of one script: whatever the
# repo version gains, the installed one silently lacks. The repo copy is the
# source of truth, and it also runs in place — from the checkout — so the command
# works even on a host where nobody has run this installer.
install -m 0755 -o root -g root "$REPO/deploy/scangrade-db-snapshot.sh" "$SNAPSHOT_BIN"
bash -n "$SNAPSHOT_BIN"
echo "   installed; archives in $BACKUP_DIR (mode 0700, newest $BACKUP_KEEP)"

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
