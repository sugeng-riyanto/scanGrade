#!/usr/bin/env bash
# unstick-deploy.sh — bring a box whose auto-deploy has stalled back up to the
# commit that is already on GitHub.
#
# One command, as root on the VPS:
#
#     curl -fsSL https://raw.githubusercontent.com/sugeng-riyanto/scanGrade/main/deploy/unstick-deploy.sh | sudo bash
#
# Why it exists: production fetched `origin/main` every two minutes and merged
# nothing for days. `git status` reported a clean tree while `git merge` was
# refused, because a stale `.git/index.lock` sat in the git directory — the
# failure this repository documents. The heal for it is in the deploy runner,
# and the runner is itself one of the commits that could not deploy, so the box
# could never have recovered on its own. This script is that heal, by hand.
#
# What it does, in order, and nothing else:
#   1. reports what the box is doing now (HEAD, origin/main, the journal, the
#      runner's own refusal records);
#   2. removes a stale `.git/index.lock` — only when no git process holds it;
#   3. runs the deploy unit once, so the release is merged and gated normally;
#   4. verifies the checkout moved and the app answers.
#
# It never runs `git reset`, never force-pushes, and removes nothing but a stale
# index lock. Safe to run twice. Exit 0 = unstuck, 1 = refused (with the reason),
# 2 = still behind.
#
#     --manual-merge   fast-forward by hand and reload, skipping the deploy
#                      gates. Refused while a gate holds a commit. For when the
#                      runner cannot run at all.

set -uo pipefail

REPO="${SG_REPO:-/opt/scangrade}"
BRANCH="${SG_BRANCH:-main}"
SERVICE="${SG_SERVICE:-scangrade}"
UNIT="${SG_DEPLOY_UNIT:-scangrade-deploy.service}"
STATE_DIR="${SG_STATE_DIR:-/var/lib/scangrade-deploy}"
HEALTH="${SG_HEALTH_URL:-http://127.0.0.1:8000/health}"
PROC_ROOT="${SG_PROC_ROOT:-/proc}"
MANUAL_MERGE=0
[ "${1:-}" = "--manual-merge" ] && MANUAL_MERGE=1

step() { printf '\n== %s\n' "$*"; }
note() { printf '   %s\n' "$*"; }
die()  { printf '\n!! %s\n' "$*"; exit 1; }

if [ "$(id -u)" -ne 0 ]; then
    if [ -r "$0" ] && command -v sudo >/dev/null 2>&1; then
        printf 'not root — re-running under sudo\n'
        exec sudo -E bash "$0" "$@"
    fi
    die "run me as root:  curl -fsSL <url> | sudo bash"
fi

[ -d "$REPO/.git" ] || die "$REPO is not a git checkout (set SG_REPO=...)"
command -v runuser >/dev/null 2>&1 || die "runuser is missing — cannot act as the checkout's owner"

OWNER="$(stat -c '%U' "$REPO" 2>/dev/null || true)"
[ -n "$OWNER" ] || OWNER="scangrade"

as_owner() {
    if [ "$(id -un)" = "$OWNER" ]; then "$@"; else runuser -u "$OWNER" -- "$@"; fi
}
gitdo() { as_owner git -C "$REPO" "$@"; }

# ── 1. what the box is doing now ─────────────────────────────────────────────
step "1/6  the box as it stands"
note "host          : $(hostname)   $(date -Is)"

HEAD_SHA="$(gitdo rev-parse --short HEAD 2>/dev/null)"
ORIGIN_SHA="$(gitdo rev-parse --short "origin/$BRANCH" 2>/dev/null)"
BEHIND="$(gitdo rev-list --count "HEAD..origin/$BRANCH" 2>/dev/null)"
note "HEAD          : ${HEAD_SHA:-?}  $(gitdo log -1 --format=%s 2>/dev/null)"
note "origin/$BRANCH : ${ORIGIN_SHA:-?}"
note "behind        : ${BEHIND:-?} commit(s)"
note "services      : $SERVICE=$(systemctl is-active "$SERVICE" 2>/dev/null)  $UNIT=$(systemctl is-active "$UNIT" 2>/dev/null)  ${UNIT%.service}.timer=$(systemctl is-active "${UNIT%.service}.timer" 2>/dev/null)"

git_count=0
for pid in "$PROC_ROOT"/[0-9]*; do
    [ -r "$pid/comm" ] || continue
    read -r comm < "$pid/comm" 2>/dev/null || continue
    case "$comm" in git|git-*) git_count=$((git_count + 1)) ;; esac
done
note "git processes : $git_count"
note ""
note "last lines of the deploy journal (the reason is usually here):"
journalctl -u "$UNIT" -n 20 --no-pager 2>/dev/null | sed 's/^/   | /' || note "(no journal for $UNIT)"

for f in quarantined refused-before-merge last-stop unarmed last-deploy; do
    if [ -f "$STATE_DIR/$f" ]; then
        note ""
        note "$STATE_DIR/$f:"
        sed 's/^/   | /' "$STATE_DIR/$f" 2>/dev/null || note "   (declared but unreadable)"
    fi
done

if [ "${BEHIND:-0}" = "0" ] && [ -n "$HEAD_SHA" ] && [ "$HEAD_SHA" = "$ORIGIN_SHA" ]; then
    step "already up to date — nothing to unstick"
    exit 0
fi

# ── 2. the stale index lock ──────────────────────────────────────────────────
step "2/6  the index lock"

GITDIR="$(gitdo rev-parse --absolute-git-dir 2>/dev/null)"
[ -n "$GITDIR" ] || GITDIR="$REPO/.git"
LOCK="$GITDIR/index.lock"

process_table_readable() {
    local pid
    for pid in "$PROC_ROOT"/[0-9]*; do
        [ -d "$pid" ] || continue
        return 0
    done
    return 1
}

lock_holders() {
    local pid comm
    for pid in "$PROC_ROOT"/[0-9]*; do
        [ -r "$pid/comm" ] || continue
        read -r comm < "$pid/comm" 2>/dev/null || continue
        case "$comm" in
            git|git-*) printf '%s %s\n' "${pid##*/}" "$comm" ;;
        esac
    done
}

if [ ! -e "$LOCK" ]; then
    note "no index lock at $LOCK"
else
    if ! process_table_readable; then
        die "there is a lock at $LOCK and $PROC_ROOT cannot be read — refusing to remove a lock that may have a live owner"
    fi
    HOLDERS="$(lock_holders)"
    if [ -n "$HOLDERS" ]; then
        note "a git process is running — leaving $LOCK alone:"
        printf '%s\n' "$HOLDERS" | sed 's/^/   | /'
        die "a git holds the index lock. Nothing was changed. Re-run in a minute, or check: ps -eo pid,comm | grep git"
    fi
    if ! rm -f "$LOCK"; then
        die "could not remove $LOCK"
    fi
    note "removed the stale lock at $LOCK (no git process held it)"
fi

# ── 3. run the deploy unit, so gates run normally ────────────────────────────
step "3/6  running $UNIT once"
if systemctl start "$UNIT"; then
    note "the runner exited 0"
else
    note "the runner exited non-zero ($?) — its output follows"
fi
journalctl -u "$UNIT" -n 40 --no-pager 2>/dev/null | sed 's/^/   | /'

NEW_SHA="$(gitdo rev-parse --short HEAD 2>/dev/null)"
ORIGIN_SHA="$(gitdo rev-parse --short "origin/$BRANCH" 2>/dev/null)"

# ── 4. did it move? ──────────────────────────────────────────────────────────
step "4/6  did the checkout move?"
note "HEAD now      : ${NEW_SHA:-?}   (was ${HEAD_SHA:-?})"
note "origin/$BRANCH : ${ORIGIN_SHA:-?}"

if [ -n "$NEW_SHA" ] && [ "$NEW_SHA" = "$ORIGIN_SHA" ]; then
    note "the release landed on its own."
else
    note "the checkout did NOT move."
    if [ -f "$STATE_DIR/quarantined" ]; then
        note "a gate refused this release and is holding it:"
        sed 's/^/   | /' "$STATE_DIR/quarantined"
        die "fix what that gate names, then re-run — or release it once: touch /etc/scangrade-deploy.release"
    fi
    if [ "$MANUAL_MERGE" != "1" ]; then
        note "No quarantine and no move means the merge itself is still refused."
        note "Read the journal above, then re-run with --manual-merge to fast-forward by hand."
        exit 2
    fi
    note "--manual-merge: fast-forwarding by hand (the deploy gates will NOT run for this release)"
    gitdo fetch --prune origin || die "git fetch failed"
    if ! gitdo merge --ff-only "origin/$BRANCH"; then
        die "git merge --ff-only refused — read git's own words above (a real divergence, or a file in the way)"
    fi
    systemctl reload "$SERVICE" 2>/dev/null || systemctl restart "$SERVICE" || die "could not reload $SERVICE"
    note "merged and reloaded $SERVICE"
fi

# ── 5. is this box armed? ────────────────────────────────────────────────────
# The runner that this move puts in place refuses a release on a box that cannot
# run its gates (exit 15), so an unarmed box would be unstuck once and then stuck
# again, silently. Worth saying now rather than in two days.
step "5/6  is this box armed?"
if [ -f "$REPO/deploy/arm-auto-deploy.sh" ]; then
    if bash "$REPO/deploy/arm-auto-deploy.sh" --check >/tmp/unstick-armament.log 2>&1; then
        note "armed — the four gates have what they need"
    else
        note "NOT armed. This release is fine, but the NEXT one will be refused"
        note "until this is fixed:"
        sed 's/^/   | /' /tmp/unstick-armament.log
        note "fix once, as root:  bash $REPO/deploy/install-auto-deploy.sh"
    fi
else
    note "no arm-auto-deploy.sh in this checkout yet — once it is there:"
    note "   bash $REPO/deploy/install-auto-deploy.sh"
fi

# ── 6. verify ────────────────────────────────────────────────────────────────
step "6/6  verify"
note "HEAD          : $(gitdo rev-parse --short HEAD 2>/dev/null)  $(gitdo log -1 --format=%s 2>/dev/null)"
note "behind        : $(gitdo rev-list --count "HEAD..origin/$BRANCH" 2>/dev/null) commit(s)"
note "service       : $SERVICE=$(systemctl is-active "$SERVICE" 2>/dev/null)"

ok=0
for _ in 1 2 3 4 5 6; do
    if curl -fsS --max-time 10 "$HEALTH" >/dev/null 2>&1; then ok=1; break; fi
    sleep 2
done
if [ "$ok" = "1" ]; then
    note "health        : OK ($HEALTH)"
    printf '\n== UNSTUCK — the box is on %s and answering.\n\n' "$(gitdo rev-parse --short HEAD 2>/dev/null)"
    exit 0
fi

note "health        : no answer yet from $HEALTH"
note "the app may still be starting — check: systemctl status $SERVICE"
exit 2
