#!/usr/bin/env bash
# ─── ScanGrade — arm the auto-deploy, from an ordinary login ────────────────
#
#   bash deploy/arm-auto-deploy.sh            # arm it — asks for your password once
#   bash deploy/arm-auto-deploy.sh --check    # say what is armed; change nothing
#
# `install-auto-deploy.sh` has to run as root: it writes /usr/local/bin/
# scangrade-deploy, /usr/local/bin/scangrade-db-snapshot and the units under
# /etc/systemd/system. Run from a non-root shell it prints a banner and exits 2
# *before touching anything* — and in a screenful of output that reads as done.
# That is the shape of the attempt that was reported as complete while the box
# was still running a copy of the deploy script installed on 12 September: a file
# that knows no gate at all (no theme, claims, performance or quarantine block),
# with no /etc/scangrade-claims.conf, no /etc/scangrade-perf.conf and no roster.
#
# So this wrapper refuses to be ambiguous. It prints what the box is running
# before and after, elevates once when it has to ask for a password, places the
# load-test roster the two measuring gates need, runs the installer, and keeps
# every line of the installer in a log that can be read back over plain SSH.
#
# A box that is already armed does not need this: the deploy runner re-renders
# its own launchers on the next successful release. This is only the one step a
# file cannot take for itself — see docs/AUTO_DEPLOY.md.
#
# Exit codes: 0 armed (or --check found it armed) · 1 --check found it unarmed
#             2 refused: no way to elevate, or no terminal to ask for a password
#             3 the installer is missing, or the roster is not usable
#             anything else is the installer's own exit code, passed through
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail

# Every path is overridable so the tests can drive this against a scratch tree.
# The deploy runner itself takes no arguments and no environment; this wrapper
# only reads the box, so an override can change what --check reports and where a
# roster comes from, and nothing else.
REPO="${SG_REPO:-/opt/scangrade}"
INSTALLER="$REPO/deploy/install-auto-deploy.sh"
DEPLOY_BIN="${SG_DEPLOY_BIN:-/usr/local/bin/scangrade-deploy}"
SNAPSHOT_BIN="${SG_SNAPSHOT_BIN:-/usr/local/bin/scangrade-db-snapshot}"
CLAIMS_CONF="${SG_CLAIMS_CONF:-/etc/scangrade-claims.conf}"
PERF_CONF="${SG_PERF_CONF:-/etc/scangrade-perf.conf}"
ROSTER_SRC="${SG_ROSTER_SRC:-/tmp/lt_roster.json}"
ROSTER_DST="$REPO/.freebuff/lt_roster.json"
LOG="${SG_LOG:-/tmp/installer.log}"
PY="$REPO/.venv/bin/python"

# What a rendered launcher does — it execs the checkout's deploy script, so every
# gate in the repo is what actually runs. A copy has this line nowhere, which is
# what makes "the gates can run" a property of *this* line rather than of any
# particular gate name.
EXECS_CHECKOUT='TARGET="\$REPO/deploy/scangrade-deploy.sh"'
UNRENDERED='^[[:space:]]*REPO="@REPO@"'

# The blocks a deploy script written after this one carries. A copy that predates
# them runs every release without checking any of them, which is the thing worth
# saying out loud; the names match the fail reasons the status page reports.
GATE_BLOCKS="theme_gate claims_gate perf_gate quarantine"

CHECK=0
for arg in "$@"; do
  case "$arg" in
    --check) CHECK=1 ;;
    -h|--help)
      cat <<'USAGE'
usage: bash deploy/arm-auto-deploy.sh [--check]

  (no arguments)  install the launchers, the gate config and the roster; needs root
  --check         report what is armed and change nothing (safe as any user)
USAGE
      exit 0 ;;
    *)
      echo "!! unknown argument: $arg — this takes only --check" >&2
      exit 2 ;;
  esac
done

say() { echo; echo "── $* ────────────────────────────────────────"; }

# ── What the box is running ──────────────────────────────────────────────────
# Printed before anything changes and again as the receipt, so "before" and
# "after" are the same measurement rather than two different claims. The three
# answers are the same three the super-admin deploy-status page gives: a launcher
# that renders from the checkout, one that was never rendered, and a copy.
report_state() {
  local armed=1 blocks="" installed_blocks=""

  echo "   started as : $(id -un) (uid $(id -u)) on $(hostname)"
  echo "   checkout   : $REPO"

  if [ -f "$INSTALLER" ]; then
    echo "   installer  : $(stat -c '%s bytes, %y' "$INSTALLER" | cut -d. -f1)"
  else
    echo "   installer  : MISSING — $INSTALLER"
    armed=0
  fi

  if [ ! -f "$DEPLOY_BIN" ]; then
    echo "   runner     : MISSING — $DEPLOY_BIN"
    armed=0
  elif grep -qE "$UNRENDERED" "$DEPLOY_BIN" 2>/dev/null; then
    echo "   runner     : installed but never rendered (@REPO@ is still in it), so it"
    echo "                cannot run at all — no release has ever been deployed by it"
    armed=0
  elif grep -q "$EXECS_CHECKOUT" "$DEPLOY_BIN" 2>/dev/null; then
    echo "   runner     : renders from the checkout — what runs is"
    echo "                $REPO/deploy/scangrade-deploy.sh, the commit in the repo"
  else
    for m in $GATE_BLOCKS; do
      grep -q "$m" "$DEPLOY_BIN" 2>/dev/null || installed_blocks="$installed_blocks, no $m"
    done
    echo "   runner     : a COPY of the deploy script, $(stat -c '%y' "$DEPLOY_BIN" | cut -d. -f1)"
    if [ -n "$installed_blocks" ]; then
      echo "                It deploys, but it contains$installed_blocks block — so"
      echo "                every release it shipped went unchecked."
    else
      echo "                It carries the gate blocks; check whether it has drifted"
      echo "                from the repo rather than assuming it has not."
    fi
    armed=0
  fi

  for m in $GATE_BLOCKS; do
    if [ -f "$REPO/deploy/scangrade-deploy.sh" ] &&
       grep -q "$m" "$REPO/deploy/scangrade-deploy.sh" 2>/dev/null; then
      blocks="$blocks $m"
    fi
  done
  echo "   in the repo: the deploy script has${blocks:- no} gate block(s)"

  if [ -f "$SNAPSHOT_BIN" ]; then
    echo "   snapshot   : present ($SNAPSHOT_BIN)"
  else
    echo "   snapshot   : missing ($SNAPSHOT_BIN)"
    armed=0
  fi

  for conf in "$CLAIMS_CONF" "$PERF_CONF"; do
    local name; name=$(basename "$conf" | sed 's/scangrade-//; s/\.conf//')
    if [ -f "$conf" ]; then
      local enf; enf=$(sed -n 's/^[A-Z_]*ENFORCE=//p' "$conf" | tr -d '"' | head -1)
      printf '   %-10s : present — enforcement %s\n' "$name" "${enf:-unset}"
    else
      printf '   %-10s : MISSING (%s)\n' "$name" "$conf"
      armed=0
    fi
  done

  local count
  if [ -f "$ROSTER_DST" ] && [ -x "$PY" ]; then
    count=$("$PY" -c 'import json,collections,sys
d = json.load(open(sys.argv[1]))
c = collections.Counter(a.get("role") for a in d)
print(c["murid"], "murid /", c["guru"], "guru")' "$ROSTER_DST" 2>/dev/null)
    echo "   roster     : ${count:-unreadable} ($ROSTER_DST)"
    [ -n "$count" ] || armed=0
  else
    echo "   roster     : MISSING — the claims and performance gates cannot measure"
    echo "                without it, and would stay unenforced"
    armed=0
  fi

  [ "$armed" = "1" ]
}

if [ "$CHECK" = "1" ]; then
  say "What this box is running (--check changes nothing)"
  if report_state; then
    echo
    echo "ARMED — every gate has what it needs."
    exit 0
  fi
  echo
  echo "NOT ARMED — see the lines above. Arm it with:"
  echo "    bash $(readlink -f "$0")"
  exit 1
fi

# ── The roster is read *before* anything asks for a password ─────────────────
# A roster that cannot be parsed is the one failure worth reporting without
# elevation: otherwise the run asks for a password, installs nothing useful, and
# leaves two gates unenforced for reasons that are three screens up.
ROSTER_OK=0
ROSTER_COUNT=""
roster_count() {
  # (students / teachers) the harness will refuse to draw more than
  "$PY" -c 'import json,collections,sys
c = collections.Counter(a.get("role") for a in json.load(open(sys.argv[1])))
print(c["murid"], "murid /", c["guru"], "guru")' "$1" 2>/dev/null
}

if [ -f "$ROSTER_SRC" ]; then
  say "Checking the load-test roster ($ROSTER_SRC)"
  if ! "$PY" -c 'import json,sys
d = json.load(open(sys.argv[1]))
sys.exit(0 if isinstance(d, list) and d else 1)' "$ROSTER_SRC" 2>/dev/null; then
    echo "!! $ROSTER_SRC is not a non-empty JSON list of accounts." >&2
    echo "   Refusing to install it: the claims and performance gates would arm" >&2
    echo "   against a roster nothing can draw a session from." >&2
    exit 3
  fi
  ROSTER_OK=1
  ROSTER_COUNT=$(roster_count "$ROSTER_SRC")
  echo "   usable — $ROSTER_COUNT, one account per session"
else
  echo
  echo "!! no roster at $ROSTER_SRC — the claims and performance gates will report"
  echo "   'cannot measure' and stay unenforced. Provide one and re-run:"
  echo "       SG_ROSTER_SRC=/path/to/lt_roster.json bash $0"
fi

# ── Elevation, once, and only with a terminal to answer the prompt ───────────
if [ "$(id -u)" -ne 0 ]; then
  if [ "${SG_ELEVATED:-0}" = "1" ]; then
    echo "!! still uid $(id -u) after elevating — the privileged shell did not take." >&2
    echo "   Nothing was installed. Run it as root directly:" >&2
    echo "       bash $INSTALLER" >&2
    exit 2
  fi
  if [ ! -t 0 ]; then
    # Without this the prompt is answered by nobody and the run hangs — the other
    # way an attempt reads as "I ran it" when nothing happened.
    echo "!! not a terminal, so a password prompt cannot be answered — nothing was" >&2
    echo "   installed. Run this by hand, or as root:" >&2
    echo "       bash $INSTALLER" >&2
    exit 2
  fi
  SELF=$(readlink -f "$0")
  if command -v sudo >/dev/null 2>&1; then
    echo "   not root (uid $(id -u), $(id -un)) — elevating with sudo; it asks once"
    exec env SG_ELEVATED=1 sudo -- bash "$SELF" "$@"
  fi
  if command -v doas >/dev/null 2>&1; then
    echo "   not root (uid $(id -u), $(id -un)) — elevating with doas; it asks once"
    exec env SG_ELEVATED=1 doas bash "$SELF" "$@"
  fi
  echo "!! neither sudo nor doas is installed, so this shell cannot become root." >&2
  echo "   Log in as root (or use the provider console) and run:" >&2
  echo "       bash $INSTALLER" >&2
  exit 2
fi

[ -f "$INSTALLER" ] || { echo "!! $INSTALLER is missing — nothing to run" >&2; exit 3; }
OWNER=$(stat -c '%U' "$REPO")

say "Before"
report_state || true

# ── The roster the two measuring gates need ──────────────────────────────────
# One account per session: reusing logins turns per-identity rate limiting into
# errors that look like the server's fault. It lives outside git so a rollback
# cannot remove it, and it is owned by the service user because the gates run as
# that user (`as_owner` in the installer), not as root.
if [ "$ROSTER_OK" = "1" ]; then
  say "Placing the load-test roster"
  if [ -f "$ROSTER_DST" ] && cmp -s "$ROSTER_SRC" "$ROSTER_DST"; then
    echo "   already in place — unchanged ($ROSTER_COUNT)"
  else
    install -d -o "$OWNER" -g "$OWNER" "$REPO/.freebuff" ||
      { echo "!! cannot create $REPO/.freebuff" >&2; exit 3; }
    install -o "$OWNER" -g "$OWNER" -m 0600 "$ROSTER_SRC" "$ROSTER_DST" ||
      { echo "!! cannot write $ROSTER_DST" >&2; exit 3; }
    echo "   $ROSTER_SRC -> $ROSTER_DST ($ROSTER_COUNT, mode 0600, owner $OWNER)"
  fi
fi

# ── The installer, with its output kept ──────────────────────────────────────
say "Running the installer (every line is kept in $LOG)"
bash "$INSTALLER" 2>&1 | tee "$LOG"
rc=${PIPESTATUS[0]}
echo "   installer exit: $rc    log: $LOG"

say "After"
if report_state; then state=0; else state=1; fi

echo
if [ "$rc" != "0" ]; then
  echo "THE INSTALLER FAILED (exit $rc) — nothing was half-installed."
  echo "Its own codes: 4 a dirty checkout · 5 a non-fast-forward · 6 a file"
  echo "missing from the release · 7 the theme gate. The last lines of $LOG say which."
elif [ "$state" != "0" ]; then
  echo "INSTALLER OK, BUT THE BOX IS NOT FULLY ARMED — read the lines above."
  echo "A gate that says 'cannot measure' printed its own reason in $LOG."
else
  echo "ARMED — the runner renders from the checkout and all four gates have what"
  echo "they need. Prove it bites with the drill in docs/AUTO_DEPLOY.md."
fi
exit "$rc"
