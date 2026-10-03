#!/usr/bin/env bash
# ─── ScanGrade — give the box one order, from the pipeline. ───────────────────
#
#     bash deploy/scangrade-plan.sh recover "the migration gate is holding 2162e18"
#
# Why this exists: the box pulls, so the only thing that ever reaches it is a release
# — and a release is exactly what a stuck box refuses. `sgfix` solved that for a human
# standing at a console, which is not a channel: on the VPS the console is a noVNC
# window where a long command has to be typed by hand. This writes the order into the
# repository the box already trusts, and the box's own timer picks it up within one
# interval — no console, no ssh, no inbound connection of any kind.
#
# What it publishes, and where it is read:
#
#   * `deploy/control/plan`, committed and pushed. The runner reads it out of the
#     commit it has already fetched, before it decides anything about a release, so it
#     arrives on a box that is dirty, rolled back, quarantined or paused.
#   * The plan carries a fresh `issued:` stamp on every run. A plan is obeyed once per
#     exact content, so re-publishing identical bytes would be obeyed once and never
#     again — the stamp is what makes "do it again" mean something.
#
# What it will *not* do, and each refusal is a decision rather than an omission:
#
#   * **it refuses a command the runner does not know.** A typo would otherwise become
#     a plan the box refuses at the far end of a push, which reads as a box that
#     ignored you.
#   * **it refuses to publish from a branch the box does not read.** The runner reads
#     `main`; a plan pushed to a feature branch reaches nobody, and the operator gets
#     a green push and silence.
#   * **it does not force anything.** If the push is rejected, that is the answer: the
#     branch has moved and the plan must be re-issued on top of it, not forced over it.
#   * **it does not touch the box.** It cannot; the box has no inbound path. Printing
#     where to watch is the most it can honestly do.
#
# Modes:
#
#     bash deploy/scangrade-plan.sh <command> [note]   publish
#     bash deploy/scangrade-plan.sh --print <command>  write to stdout, touch nothing
#     bash deploy/scangrade-plan.sh --no-push <command>  commit, do not push
#     bash deploy/scangrade-plan.sh --help
set -uo pipefail

BRANCH="main"
PLAN_PATH="deploy/control/plan"
COMMANDS="none recover release rebaseline pause resume"

say() { printf '%s\n' "$*"; }
die() { printf 'refused: %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<EOF
usage: bash deploy/scangrade-plan.sh [--print|--no-push] <command> [note]

commands:
  recover     run the box's recovery lever (missing migration, stale baseline,
              a box-local edit) and end that tick
  release     ask for the held commit to be tried once more
  rebaseline  re-measure the box instead of comparing against a stale baseline
  pause       freeze deploys (exam week)
  resume      lift that freeze — the one state with no other way out
  none        ask for nothing

The box reads the plan on its next tick (at most one timer interval, 2 minutes),
before it decides anything about a release. Watch for it in the deploy journal or
on /super-admin/deploy-status.
EOF
}

MODE="publish"
case "${1:-}" in
  --help|-h) usage; exit 0 ;;
  --print) MODE="print"; shift ;;
  --no-push) MODE="commit"; shift ;;
esac

COMMAND="${1:-}"
[ -n "$COMMAND" ] || { usage >&2; exit 2; }
shift || true
NOTE="${*:-}"

# The one place the vocabulary is checked on this side. `case` rather than a substring
# test, so `--print re` does not look like a command.
case "$COMMAND" in
  none|recover|release|rebaseline|pause|resume) ;;
  *) die "\"$COMMAND\" is not a command the runner knows. Known commands: $COMMANDS" ;;
esac

# A note is one line: a newline in it would end the plan's `note:` field and let the
# rest of the text be read as further keys.
NOTE=$(printf '%s' "$NOTE" | tr '\n\r' '  ')
ISSUED=$(date -u +%Y-%m-%dT%H:%M:%SZ)

render() {
  cat <<EOF
# ScanGrade — the pipeline's way into a box that cannot deploy.
#
# Written by deploy/scangrade-plan.sh. Read by deploy/scangrade-deploy.sh on every
# tick, before it decides anything about a release. See deploy/control/plan in the
# repository for the full format, and docs/AUTO_DEPLOY.md for the channel itself.
#
# Obeyed once per exact content: the \`issued\` stamp below is what makes a
# re-issued command a new plan rather than one already obeyed.
command: $COMMAND
issued: $ISSUED
note: $NOTE
EOF
}

if [ "$MODE" = "print" ]; then
  render
  exit 0
fi

git rev-parse --git-dir >/dev/null 2>&1 || die "not inside a git checkout"

# Which branch the box reads. Publishing anywhere else is a green push and silence,
# so it is refused rather than discovered three days later.
CURRENT=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)
[ "$CURRENT" = "$BRANCH" ] || die "this checkout is on \"$CURRENT\"; the box reads \"$BRANCH\""

render > "$PLAN_PATH" || die "cannot write $PLAN_PATH"
say "plan written: $PLAN_PATH"
sed 's/^/    /' "$PLAN_PATH"

MESSAGE="control: $COMMAND"
[ -n "$NOTE" ] && MESSAGE="$MESSAGE — $NOTE"
git add "$PLAN_PATH" || die "cannot stage $PLAN_PATH"
git commit -q -m "$MESSAGE" || die "cannot commit the plan (nothing changed?)"
say "committed: $MESSAGE"

if [ "$MODE" = "commit" ]; then
  say "not pushed (--no-push). Publish it with: git push origin $BRANCH"
  exit 0
fi

if ! git push -q origin "$BRANCH"; then
  die "the push was refused. The branch has moved — re-run this so the plan sits on
       top of it. Nothing was forced: a forced plan is a plan written against a
       branch nobody else has seen."
fi

say "pushed to origin/$BRANCH"
say ""
say "The box reads it on its next tick (at most 2 minutes), before it decides"
say "anything about a release. To watch, from a shell on the box:"
say "    journalctl -u scangrade-deploy -n 40 --no-pager"
say "and the record of what was obeyed:  ls -t /var/lib/scangrade-deploy/control"
