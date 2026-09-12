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
as_owner() { runuser -u "$OWNER" -- env HOME="$REPO" GIT_TERMINAL_PROMPT=0 GIT_ASKPASS=/bin/true "$@"; }

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

for f in deploy/scangrade-deploy.sh deploy/scangrade-deploy.service \
         deploy/scangrade-deploy.timer deploy/scangrade.service; do
  [ -f "$REPO/$f" ] || { echo "!! missing $REPO/$f — is origin/$BRANCH the right commit?"; exit 6; }
done

# ── 2. The thing root will actually run.
say "Installing $DEPLOY_BIN"
install -m 0755 -o root -g root "$REPO/deploy/scangrade-deploy.sh" "$DEPLOY_BIN"
bash -n "$DEPLOY_BIN"
echo "   syntax ok"

# ── 3. Unit files. Back up anything we replace: this is the file that keeps the
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

# ── 4. Turn the timer on. start, not restart: an in-flight run is left alone.
say "Enabling the timer"
systemctl enable --now scangrade-deploy.timer >/dev/null
echo "   scangrade-deploy.timer is $(systemctl is-active scangrade-deploy.timer)"

# ── 5. Prove it works now rather than in two minutes, and report what it found.
say "Test run"
systemctl start scangrade-deploy.service || true
sleep 2
journalctl -u scangrade-deploy.service -n 20 --no-pager | sed 's/^/   /'

say "Done"
echo "   $SERVICE is $(systemctl is-active $SERVICE) on 127.0.0.1:8000"
echo "   deployed commit: $(as_owner git -C "$REPO" rev-parse --short HEAD)"
echo
echo "   watch deploys : journalctl -u scangrade-deploy.service -f"
echo "   next tick     : systemctl list-timers scangrade-deploy.timer"
echo "   deploy now    : systemctl start scangrade-deploy.service"
echo "   freeze/resume : touch /etc/scangrade-deploy.pause   (rm to resume)"
echo "   switch it off : systemctl disable --now scangrade-deploy.timer"
