#!/bin/sh
# ─── ScanGrade — the installed entry point is a LAUNCHER, not a copy ─────────
#
# install-auto-deploy.sh renders this file (substituting @REPO@) to
# /usr/local/bin/scangrade-deploy and /usr/local/bin/scangrade-db-snapshot.
#
# It used to *copy* the two scripts there instead, and that is a snapshot: the
# moment it is installed it stops receiving fixes. Every later improvement to the
# deploy logic — a new gate, a new rollback path, a corrected probe — sat on
# GitHub while root kept running the version of the day it was installed, and
# nothing compared the two, so the drift was silent. The box then needed someone
# to remember to re-run the installer as root, which is exactly the manual step
# automatic deployment exists to remove.
#
# So the installed path execs the checkout's copy. What runs is always the
# commit the checkout is on; a script fix shipped through the normal flow is in
# effect on the next tick with nobody touching the console.
#
# It decides which script to run from its own name, and it is the only thing
# those two names ever mean.
# ─────────────────────────────────────────────────────────────────────────────

set -eu

# Substituted by install-auto-deploy.sh. The placeholder is spelled exactly like
# this in exactly one place, on purpose: the installer refuses to install a file
# where it is still present, so an unrendered launcher cannot reach root's PATH.
REPO="@REPO@"

case "$(basename "$0")" in
  scangrade-deploy)
    TARGET="$REPO/deploy/scangrade-deploy.sh"
    # No arguments get through, by design: the deploy is the one thing root runs
    # unattended and must not be steerable. `$@` is cleared here rather than
    # simply not forwarded, so a caller cannot smuggle a branch or a path in and
    # have it travel through this file at all.
    set -- ;;
  scangrade-db-snapshot)
    # This one *is* a command — `--list`, `--label`, `--restore` — and the wrapper
    # it execs validates its own flags, so arguments pass straight through.
    TARGET="$REPO/deploy/scangrade-db-snapshot.sh" ;;
  *)
    echo "!! $(basename "$0"): unknown launcher name." >&2
    echo "   This file is installed as scangrade-deploy or scangrade-db-snapshot;" >&2
    echo "   it picks its target by its own name." >&2
    exit 64 ;;
esac

if [ ! -f "$TARGET" ]; then
  echo "!! $TARGET is missing." >&2
  echo "   The launcher runs the checkout's own copy, so the checkout has to be" >&2
  echo "   there: pull the current commit (or fix the path this file was rendered" >&2
  echo "   with) and try again." >&2
  exit 3
fi

# ── the checkout has to still carry Gate 0 ──────────────────────────────────
#
# A launcher's whole promise is that what runs is the *checkout's* deploy logic.
# That is only a promise worth keeping if the logic can refuse a runner which is
# not the checkout's — Gate 0 — because that is what keeps a hand-installed copy
# from deploying yesterday's gates forever. A checkout that no longer carries it
# is reachable without anybody deciding to: roll back past the commit that added
# it, or edit the block out while debugging, and every tick after that would
# deploy with no gate able to notice the runner has drifted. So the launcher
# refuses rather than run it, and the exit code is its own so the journal does
# not read as a release that failed a gate.
#
# The marker is the block's own delimiters, which is also what the deploy's
# self-preservation check looks for. Both names must be there: a lone `start` is
# what a half-edited file looks like.
if [ "$(basename "$0")" = "scangrade-deploy" ]; then
  if ! grep -q '^# runner-identity:start$' "$TARGET" 2>/dev/null ||
     ! grep -q '^# runner-identity:end$' "$TARGET" 2>/dev/null; then
    echo "!! $TARGET does not carry Gate 0 — refusing to run it." >&2
    echo "   The launcher runs the checkout's deploy logic, and that logic has to be" >&2
    echo "   able to refuse a runner that is not the checkout's. Either the checkout" >&2
    echo "   is on a commit from before Gate 0 existed, or the block was removed from" >&2
    echo "   this file. Look at what the checkout is on, then look for the block:" >&2
    echo "       (the checkout's most recent commits, and grep $TARGET)" >&2
    echo "       for these two lines: # runner-identity:start / # runner-identity:end" >&2
    exit 15
  fi
fi

# Through `bash`, not by exec'ing the file: the scripts are committed 0644, so
# the executable bit is not part of the checkout, and depending on it would make
# this launcher fail on a fresh clone. bash sets $0 to the script path either
# way, which is what the deploy uses to recognise that it is running the
# checkout's copy and not an installed one.
exec bash "$TARGET" "$@"
