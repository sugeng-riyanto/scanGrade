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
# that knows no gate at all (no theme, claims, performance, quarantine or SEB-door
# block), with no /etc/scangrade-claims.conf, no /etc/scangrade-perf.conf and no
# roster.
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
RECOVER_BIN="${SG_RECOVER_BIN:-/usr/local/bin/sgfix}"
CLAIMS_CONF="${SG_CLAIMS_CONF:-/etc/scangrade-claims.conf}"
PERF_CONF="${SG_PERF_CONF:-/etc/scangrade-perf.conf}"
SMOKE_CONF="${SG_SMOKE_CONF:-/etc/scangrade-smoke.conf}"
#: The box's own .env, where DIRECT_URL lives. The schema gate is the one gate that
#: needs a database credential, so this is read to judge whether the box can run it
#: at all — the key's *presence* is the whole question, never its value.
ENV_FILE="${SG_ENV_FILE:-$REPO/.env}"
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
# `seb_door_gate` is the newest of them, and it is the one whose absence is easiest
# to live with: a copy without it deploys every release having never opened a gated
# paper, so "SEB is enforced" would be asserted on that box and measured on none.
GATE_BLOCKS="theme_gate claims_gate perf_gate quarantine schema_gate seb_door_gate"

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

# ── Reading the gate config, whichever user is asking ────────────────────────
#
# The three confs are mode 0600 root:root — they hold passwords, and the installer
# pins that. This checker, meanwhile, is run by three callers: an operator at a
# console, the deploy runner's own preflight (as root), and the app's construct
# probe, which the deploy runs through `as_owner` and so as the service user.
#
# A reading that changes with the caller is not a reading. Asked as the service
# user, `sed` and `grep` failed on the confs with "Permission denied"; the failure
# was indistinguishable from absence, and the SEB line below read a *present*
# SMOKE_MURID as missing. A fully armed box was then reported unarmed by its own
# app, refused every tick of every release, and could never merge the fix — while
# the console, running the same file as root, said ARMED about the same box in the
# same minute. Two callers, one file, two verdicts: the box is held by that, not by
# the gate.
#
# So a conf has three states and the middle one is not absence:
#
#   value      — this caller read it, and this is what it says
#   unreadable — it is there, but this caller cannot read it. Presence is all this
#                caller can establish, and presence is all the *copy* question below
#                needs; the content questions are answered by the caller that can
#                read it, which is the runner's preflight running as root
#   absent     — not there at all, which is the one state that disarms
#
# The state is decided by attempting the read rather than by `test -r`, because a
# read that failed is a fact and a permission bit is a guess.
CONF_STATE="absent"
CONF_VALUE=""

conf_read() {  # conf_read <file> <sed script>; the answer lands in CONF_STATE/CONF_VALUE
  local file="$1" program="$2" out=""
  CONF_STATE="absent"; CONF_VALUE=""
  [ -e "$file" ] || return 0
  out=$(sed -n "$program" "$file" 2>/dev/null) || { CONF_STATE="unreadable"; return 0; }
  CONF_STATE="value"
  CONF_VALUE=$(printf '%s\n' "$out" | head -1)
}

conf_has() {  # 0 the conf says it · 1 it does not · 2 this caller could not read it
  local file="$1" pattern="$2" rc=0
  [ -e "$file" ] || return 1
  grep -qE "$pattern" "$file" 2>/dev/null; rc=$?
  case "$rc" in
    0) return 0 ;;
    1) return 1 ;;
    # grep's own third answer: it could not read the file. Not a match and not a
    # miss — the caller that can read it decides.
    *) return 2 ;;
  esac
}

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
      if ! grep -q "$m" "$DEPLOY_BIN" 2>/dev/null; then
        installed_blocks="${installed_blocks:+$installed_blocks, }no $m"
      fi
    done
    echo "   runner     : a COPY of the deploy script, $(stat -c '%y' "$DEPLOY_BIN" | cut -d. -f1)"
    if [ -n "$installed_blocks" ]; then
      echo "                It deploys, but it contains $installed_blocks block — so"
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
      blocks="${blocks:+$blocks, }$m"
    fi
  done
  echo "   in the repo: deploy/scangrade-deploy.sh has ${blocks:-no} gate block(s)"

  if [ -f "$SNAPSHOT_BIN" ]; then
    echo "   snapshot   : present ($SNAPSHOT_BIN)"
  else
    echo "   snapshot   : missing ($SNAPSHOT_BIN)"
    armed=0
  fi

  # The recovery lever is reported and deliberately NOT part of `armed`. Being armed
  # means "this box can run the gates a release has to pass"; the lever is what gets a
  # box back to the console-free state, not a gate. Folding it into `armed` would
  # refuse every release on every box installed before the lever existed — including
  # the release that installs it — which is the same trap this check was written to
  # avoid, one level down.
  if [ -f "$RECOVER_BIN" ]; then
    echo "   recover    : present ($RECOVER_BIN) — one word, no console ritual"
  else
    echo "   recover    : missing ($RECOVER_BIN) — the next stuck box needs a shell"
    echo "                install it: bash $INSTALLER"
  fi

  # The schema gate holds a release against the live catalogue through DIRECT_URL,
  # a credential no other gate needs — so a box without it can run every other check
  # and none of this one, and would ship a release whose database is behind its code
  # with nothing having looked. Presence is the whole question; --check must not open
  # a connection to answer it, and it never prints the value.
  if grep -qE '^(DIRECT_URL|DATABASE_URL)=' "$ENV_FILE" 2>/dev/null &&
     ! grep -qE '^(DIRECT_URL|DATABASE_URL)=.*\[YOUR-PASSWORD\]' "$ENV_FILE" 2>/dev/null; then
    printf '   %-10s : present — the schema gate can hold a release against the catalogue\n' \
      "schema"
  else
    printf '   %-10s : MISSING — no DIRECT_URL in %s, so the schema gate cannot\n' \
      "schema" "$ENV_FILE"
    echo  "                verify a release, and a box that cannot check a release"
    echo  "                deploys none. Put the session-mode pooler URL there"
    echo  "                (Project Settings → Database) and re-run --check."
    armed=0
  fi

  # The smoke test is a gate too, and one of the loudest silently-skipped ones:
  # without its conf the deploy logs "skipping the per-role smoke test" and keeps
  # the release, so "every role still works" stops being checked by anything. It
  # belongs in the same list, and its enforcement is reported by the same rule.
  for conf in "$SMOKE_CONF" "$CLAIMS_CONF" "$PERF_CONF"; do
    local name enf; name=$(basename "$conf" | sed 's/scangrade-//; s/\.conf//')
    conf_read "$conf" 's/^[A-Z_]*ENFORCE=//p'
    case "$CONF_STATE" in
      value)
        enf=$(printf '%s' "$CONF_VALUE" | tr -d '"')
        printf '   %-10s : present — enforcement %s\n' "$name" "${enf:-unset}" ;;
      unreadable)
        printf '   %-10s : present (%s — mode 0600 root:root, so this caller (%s)\n' \
          "$name" "$conf" "$(id -un)"
        echo  "                cannot read it; the runner's preflight runs as root and does)" ;;
      *)
        printf '   %-10s : MISSING (%s)\n' "$name" "$conf"
        armed=0 ;;
    esac
  done

  # The SEB door gate is the one gate whose verdict cannot be read anywhere else in
  # this report. The panel can render, the file can download and the toggle can be
  # stored while a plain browser opens the paper — the claim that gate exists to
  # measure instead of assert. It needs two things the box already has a place for:
  # the gate itself, and the smoke conf's `murid` account, because the pupil whose
  # paper it opens is the one the smoke test signs in as. A conf without
  # SMOKE_MURID is a skip, not a pass — and it is named on its own line because the
  # smoke test can pass on the other five roles while the SEB gate measures nothing
  # at all. Nothing else is needed: it brings its own throwaway paper, and the browser
  # its client half drives the handshake page in is the same one the two DOM gates
  # require below — a box with no browser already refuses there, so a browser is not
  # counted twice. That half's arrival is why this line no longer says the SEB gate
  # measures in no browser: it did, until the page's own script was measured too.
  # CONF_STATE now describes the smoke conf: the SEB gate's two content questions
  # (is the pupil there, is the gate enforced) are the only ones this report asks of
  # a conf, and both are asked of the caller that can read it.
  local seb_enf seb_has
  conf_read "$SMOKE_CONF" 's/^SEB_ENFORCE=//p'
  seb_enf=$(printf '%s' "$CONF_VALUE" | tr -d '"')
  if [ ! -f "$REPO/deploy/seb_door_gate.py" ]; then
    printf '   %-10s : MISSING — %s/deploy/seb_door_gate.py, so "SEB is\n' \
      "seb" "$REPO"
    echo  '                enforced" would be asserted on every release rather than'
    echo  '                measured. Pull the checkout, or re-run the installer.'
    armed=0
  elif [ "$CONF_STATE" = "absent" ]; then
    printf '   %-10s : MISSING (%s) — the gate signs in as its SMOKE_MURID\n' \
      "seb" "$SMOKE_CONF"
    armed=0
  else
    conf_has "$SMOKE_CONF" '^SMOKE_MURID='; seb_has=$?
    case "$seb_has" in
      0)
        printf '   %-10s : present — one throwaway paper as the smoke pupil, then the handshake page in a browser; enforcement %s\n' \
          "seb" "${seb_enf:-unset}" ;;
      1)
        printf '   %-10s : MISSING — no SMOKE_MURID in %s, so the gate has no\n' \
          "seb" "$SMOKE_CONF"
        echo  "                pupil to open a paper as and reports \"could not"
        echo  "                measure\" on every release. Add the demo pupil as"
        echo  "                email:password (the smoke test already signs in as it)."
        armed=0 ;;
      *)
        # Not this caller's question to answer, and answering it badly is what held
        # a whole release: a conf it cannot read is present, not absent.
        printf '   %-10s : present (%s is root-only, so SMOKE_MURID cannot be read\n' \
          "seb" "$SMOKE_CONF"
        echo  "                from here — the deploy preflight reads it as root and"
        echo  "                refuses the run when the pupil is not in it)" ;;
    esac
  fi

  # A browser is what the two DOM gates measure in — the touch gate's finger floor
  # and the render gate's blank-exam check — and, since the SEB door gate grew its
  # client half, the one the shipped handshake page is driven in as well. All three
  # answer "could not measure" and keep the release without one, so a box with no
  # browser ships every release having laid out no page and with nobody having
  # measured what that page's own script does, exactly the silent skip the confs
  # above are counted for. The gates'
  # own `locate_browser` is imported rather than restated, so a box this check calls
  # armed is armed by their own definition — `SG_CHROME` authoritative, then the
  # usual names and install paths.
  local browser=""
  if [ -x "$PY" ] && [ -f "$REPO/deploy/touch_gate.py" ]; then
    # `-B`: the import must not leave a __pycache__ in the checkout, which is a
    # write `--check` promises not to make anywhere.
    browser=$("$PY" -B -c 'import sys; sys.path.insert(0, sys.argv[1]);
from touch_gate import locate_browser
print(locate_browser() or "")' "$REPO/deploy" 2>/dev/null)
  fi
  if [ -n "$browser" ]; then
    printf '   %-10s : present (%s) — the DOM gates can lay a page out\n' \
      "browser" "$browser"
  else
    printf '   %-10s : MISSING — no Chrome/Chromium, so the finger-floor and exam\n' \
      "browser"
    echo "                render gates skip every release without laying out a page,"
    echo "                and the SEB door gate's client half goes unmeasured."
    echo "                install one, or set SG_CHROME"
    armed=0
  fi

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
# The *checkout's* checker, not this process's copy of `report_state`. The installer
# above pulled origin/main, and "armed" can have grown in that pull: measured on a box
# whose "After" printed "ARMED — … all four gates" (the four-gate rules this process
# started with) while the very next deploy tick printed "NOT ARMED" (the pulled rules,
# which add the schema/DIRECT_URL requirement). One of those two sentences described the
# same box wrongly, and an After report is the last place that may happen. Running the
# file now on disk is what makes "After" describe the box as its own runner will.
state=1
if [ -f "$REPO/deploy/arm-auto-deploy.sh" ]; then
  bash "$REPO/deploy/arm-auto-deploy.sh" --check && state=0
elif report_state; then
  state=0
fi

echo
if [ "$rc" != "0" ]; then
  echo "THE INSTALLER FAILED (exit $rc) — nothing was half-installed."
  echo "Its own codes: 4 a dirty checkout · 5 a non-fast-forward · 6 a file"
  echo "missing from the release · 7 the theme gate. The last lines of $LOG say which."
elif [ "$state" != "0" ]; then
  echo "INSTALLER OK, BUT THE BOX IS NOT FULLY ARMED — read the lines above."
  echo "A gate that says 'cannot measure' printed its own reason in $LOG."
else
  # "Every gate above", not a count: the number has been wrong twice (four, then
  # five) and a sentence that has to be re-typed for each new gate is one that will
  # describe the box wrongly on the release that adds it.
  echo "ARMED — the runner renders from the checkout and every gate above has what"
  echo "it needs. Prove it bites with the drill in docs/AUTO_DEPLOY.md."
fi
exit "$rc"
