#!/usr/bin/env bash
# ─── ScanGrade — the box's own recovery lever, in one word. ──────────────────
#
#     sgfix
#
# Why it exists: a box gets stuck in ways that need root and the checkout but not a
# decision from anybody, and every one of them has so far ended in a console session
# — on a VPS that is a noVNC window where a long command has to be typed by hand,
# which is the worst possible place to type `git -C /opt/scangrade …`. The deploy
# runner heals a box-local edit *by itself* now, but that heal arrives with a release
# and a release is exactly what a stuck box cannot fetch: the runner's dirty check
# runs before the fetch, so the commit carrying the heal is held by the refusal the
# heal exists to clear. This is the way out of that circle, and out of the three
# other shapes a stuck box has. It is deliberately the only new lever: one word on
# the console, no arguments to remember, and safe to run twice.
#
# A box that has no lever *installed* — a runner older than this file, or a checkout
# that was rolled back before it landed — can still run it. The same one line reads it
# out of the commit the box has already fetched and runs it: nothing to install, and
# `git show` touches nothing in the tree, so it works on a dirty, rolled-back, refused
# or quarantined checkout.
#
#     sudo bash -c 'runuser -u scangrade -- git -C /opt/scangrade show origin/main:deploy/scangrade-recover.sh > /tmp/sgfix.sh || { runuser -u scangrade -- git -C /opt/scangrade fetch -q origin && runuser -u scangrade -- git -C /opt/scangrade show origin/main:deploy/scangrade-recover.sh > /tmp/sgfix.sh; } && bash /tmp/sgfix.sh'
#
# Three things about that line are deliberate, because the box that needs it is the
# oldest box there is:
#
#   * it writes the lever to a file before running it. A *piped* script has no `$0`, so
#     a lever old enough to still carry the `sudo -E bash` hop — the one keyed on `$0` —
#     cannot re-run itself out of a pipe; a file gives it one, and run as root the hop is
#     not taken at all. The file form also lets the line check that it read something;
#     (the hop is keyed on `BASH_SOURCE` now, and `tests/unit/test_console_recover.py`
#     refuses the old spelling anywhere in this file — prose included) ;
#   * **it fetches `origin` itself when the fetched ref has no lever.** A box that
#     stalled before this file existed has a ref with no such path in it, so `git show`
#     comes up empty — and that used to be the one case answered only by a console
#     session and a hand-typed `git stash`. Now the same line fetches, as the deploy's
#     own user (the fetch writes refs into a checkout it does not own), and reads again;
#   * it fetches **only** in that case, and runs the file **only** if a read produced
#     one. `git show x > f` truncates `f` before git runs, so a line that ran the file
#     regardless would execute an empty script, exit 0 and report a recovery that
#     changed nothing — which is worse than an error, because nobody looks again.
#
# What it does, in order, and nothing else:
#
#   1. says why the box is stuck — read from the runner's own records and journal,
#      not guessed;
#   2. applies the migrations the live schema is missing, trial first. Before the
#      release and not after: the deploy's own schema gate refuses a release whose
#      database is behind its code, and it names this exact remedy when it does;
#   3. runs one release;
#   4. if the release was refused by a box-local edit, sets that edit aside — with a
#      patch and a record, never silently — and runs the release once more;
#   5. if it was refused by the *schema* quarantine that step 2 just answered, lifts
#      that quarantine once and runs the release once more;
#   6. verifies: the checkout moved, the app answers, and prints what it recorded.
#
# What it will *not* do, and each refusal is a decision rather than an omission:
#
#   * it never resets a branch or pushes anywhere — the checkout only ever moves by
#     the runner's own `git merge --ff-only`;
#   * it never discards an edit without writing it down first, and if the record
#     cannot be written it changes nothing about that path;
#   * it lifts a quarantine only when the record names the *schema* gate, because
#     that is the one gate this script can answer by doing something. A perf, theme,
#     smoke, claims or served-commit refusal is a statement about the release, and a
#     recovery lever that could clear those would be a bypass with a friendly name;
#     (the served-commit one is the closest call of the four — it *is* about this
#     box's reload — but a release was never served under that commit, so lifting it
#     would hand a quarantined release the one thing a gate refused to give it);
#   * it applies a migration only after that file's own trial run passed;
#   * it takes no argument that steers it (only --dry-run and --help), the same rule
#     the deploy runner holds: this is a thing root runs, not a thing anyone aims.
#
# Every run writes $STATE_DIR/recover/<stamp>.txt — the reason, what it set aside and
# where the patch is, which migrations it applied, what each release attempt did — and
# keeps the newest $SG_RECOVER_KEEP. A recovery that cannot be read back afterwards is
# indistinguishable from a box somebody broke by hand.

set -uo pipefail

REPO="${SG_REPO:-/opt/scangrade}"
BRANCH="${SG_BRANCH:-main}"
SERVICE="${SG_SERVICE:-scangrade}"
UNIT="${SG_DEPLOY_UNIT:-scangrade-deploy.service}"
STATE_DIR="${SG_STATE_DIR:-/var/lib/scangrade-deploy}"
RECOVER_DIR="$STATE_DIR/recover"
KEEP="${SG_RECOVER_KEEP:-5}"
RELEASE_FILE="${SG_RELEASE_FILE:-/etc/scangrade-deploy.release}"
#: The one-shot ask the status page's re-baseline button writes. Consulted here so a
#: stale-baseline refusal can be answered from a console with no browser and no shell
#: step: the runner re-measures the box, keeps that baseline, and retries once.
REBASELINE_REQUEST="${SG_REBASELINE_REQUEST:-$STATE_DIR/requests/rebaseline}"
HEALTH="${SG_HEALTH_URL:-http://127.0.0.1:8000/health}"
LEDGER="${SG_MIGRATION_LEDGER:-/var/lib/scangrade-migrations}"
PY="${SG_PYTHON:-$REPO/.venv/bin/python}"
MIGRATE="${SG_MIGRATE:-$REPO/deploy/apply_migration.py}"
PAUSE_FILE="${SG_PAUSE_FILE:-/etc/scangrade-deploy.pause}"

#: The one line an operator types on a box with no lever installed. It reads this
#: script out of the commit the box has fetched and runs it, so the whole of it is a
#: `git show` — a read of the object store, nothing installed and nothing touched in
#: the tree. Long, for a console with no clipboard, and long on purpose: every word of
#: it is the difference between a recovery and an error about the wrong thing, and the
#: one conditional in it exists because the box this line is *for* is the box whose
#: fetched ref predates this file.
#:
#: Read it as four steps:
#:
#:   1. read this script out of `origin/$BRANCH` into /tmp/sgfix.sh;
#:   2. if that failed — a ref from before this file existed — fetch `origin` as the
#:      deploy user and read it again;
#:   3. run the file, but *only* if step 1 or 2 produced one;
#:   4. nothing else: no merge, no checkout, no install. The lever itself is what
#:      decides the rest.
#:
#: Step 3's `&&` is the load-bearing one. `git show … > /tmp/sgfix.sh` truncates the
#: file before git runs, so a line that ran it regardless would execute an empty
#: script, exit 0, and report a recovery that changed nothing at all.
#:
#: One literal, printed by the refusal below and written in docs/AUTO_DEPLOY.md;
#: tests/unit/test_console_recover.py fails when the copies drift apart, and runs this
#: very command — with only the two box-specific paths substituted — against real
#: repositories to prove both branches.
GET_LEVER="runuser -u scangrade -- git -C /opt/scangrade show origin/main:deploy/scangrade-recover.sh > /tmp/sgfix.sh || { runuser -u scangrade -- git -C /opt/scangrade fetch -q origin && runuser -u scangrade -- git -C /opt/scangrade show origin/main:deploy/scangrade-recover.sh > /tmp/sgfix.sh; } && bash /tmp/sgfix.sh"

#: What a quarantine record's third line holds when the schema gate earned it. The
#: runner writes the gate's own name there, and the schema gate's name is the only
#: one this script may *lift*.
SCHEMA_GATE_WORD="schema gate"

#: The other gate that names an action. A perf refusal means the box's *baseline* was
#: measured before the box drifted, not that the code regressed — and the runner
#: already accepts a re-measurement request (the status page's own button) that makes
#: it measure the box again and judge the release against that. Asking for it is not a
#: bypass: the gate still runs, and still refuses a release that is genuinely slower.
PERF_GATE_WORD="perf gate"

#: The two pieces of the verifier's own text this reader stands on. Named once, and
#: held by a guard in tests/unit/test_console_recover.py: a reader whose markers no
#: longer match the tool's output would report "nothing to apply" for every gap,
#: which is the one way this file could be wrong and look right.
GAPS_HEADER="=== declared objects that exist nowhere"
MISSING_MARK="    MISSING  "

STAMP="$(date -u '+%Y%m%dT%H%M%SZ')"
RECORD="$RECOVER_DIR/$STAMP.txt"
HOLD="$RECOVER_DIR/$STAMP"
DRY=0

say()  { printf '\n== %s\n' "$*"; }
note() { printf '   %s\n' "$*"; }
# `$1`, and deliberately not `$*`: the second argument is the exit code, and a printer
# that joins its arguments puts it at the end of the sentence. One of these sentences
# *is* a command — the piped way in — and a console operator copying `… | bash' 1` gets
# a different command than the one that works. Held by a test in test_console_recover.py.
die()  { printf '\n!! %s\n' "$1"; exit "${2:-1}"; }

# ── the shape of a stuck box, read from its own records ──────────────────────
# recover-logic:start

recover_reason() {
  # Answers `<word>` on stdout for the record and for the reader at the top of the
  # run. Every source is a file the *runner* wrote, so this is the box's own answer
  # rather than a second opinion — the same rule the status page follows.
  if [ -f "$STATE_DIR/quarantined" ]; then
    printf 'quarantined:%s\n' "$(sed -n '3p' "$STATE_DIR/quarantined" 2>/dev/null)"
    return 0
  fi
  if [ -n "${PORCELAIN:-}" ]; then
    printf 'box-local edit\n'
    return 0
  fi
  if [ -f "$STATE_DIR/unarmed" ]; then
    printf 'not armed\n'
    return 0
  fi
  if [ -f "$STATE_DIR/refused-before-merge" ]; then
    printf 'refused:%s\n' "$(sed -n '1p' "$STATE_DIR/refused-before-merge" 2>/dev/null)"
    return 0
  fi
  printf 'behind\n'
}

recover_make_reproducible() {
  # A checkout writes *through* the filters, and the comparison that decides whether a
  # path is modified does not read both sides the same way: the worktree is normalised
  # (`text` turns CRLF into LF before comparing) while the blob is compared as stored.
  # A blob committed *around* the filters therefore reads as modified however many
  # times it is restored, and `git merge --ff-only` refuses for exactly that reason —
  # which is what made this box answer "NOT MOVED" while the restore reported success.
  #
  # Measured on the live box, 2026-09-30: commit `0afc68e` carries 108 carriage returns
  # in `app/routes/admin_sekolah.py`, a file the repo's `.gitattributes` promises is LF;
  # the box refused every two minutes for hours, twelve commits behind. The way out is
  # not another checkout, so it is the blob's own bytes — and, only if that is still
  # not enough, one line telling git not to read that one path through the filters.
  # Both are local: the bytes are HEAD's, and the line goes in this checkout's own
  # `info/attributes`, which is not committed and changes no other clone.
  #
  # Returns non-zero only when the path cannot be made clean; the caller records that
  # and leaves the file where it is, the same rule every other restore here follows.
  local path dir attr pattern line
  path="$1"
  [ -n "$(gitdo status --porcelain -- "$path" 2>/dev/null)" ] || return 0
  as_owner sh -c 'git -C "$1" cat-file blob "HEAD:$2" > "$1/$2"' _ "$REPO" "$path" 2>/dev/null \
    || return 1
  printf 'VERBATIM %s\n' "$path" >> "$RECORD"
  [ -n "$(gitdo status --porcelain -- "$path" 2>/dev/null)" ] || return 0
  dir="$(gitdo rev-parse --absolute-git-dir 2>/dev/null)"
  [ -n "$dir" ] || dir="$REPO/.git"
  attr="$dir/info/attributes"
  pattern="$(printf '%s' "$path" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g')"
  line="\"$pattern\" -text"
  as_owner sh -c 'grep -Fxq -- "$2" "$1" 2>/dev/null || printf "%s\n" "$2" >> "$1"' \
    _ "$attr" "$line" 2>/dev/null || return 1
  printf 'ATTRIBUTE %s\n' "$path" >> "$RECORD"
  [ -z "$(gitdo status --porcelain -- "$path" 2>/dev/null)" ]
}

recover_set_aside() {
  # Reads `git status --porcelain` lines and takes each entry out of the tree, in an
  # order that makes loss impossible at every step:
  #
  #   1. a durable copy of the entry goes into this run's hold directory — a diff
  #      against HEAD for a path HEAD has, and for a path it does not have there is
  #      no blob to diff, so the file itself is the copy;
  #   2. the record gains its line. If this write fails nothing else happens: the
  #      entry stays exactly where it was, and the caller is told;
  #   3. only then is the tree restored (or the copy removed, for an untracked path).
  #
  # The record is the point of the exercise — a heal that cannot say what it moved is
  # a silent loss — so an entry that cannot be written down is not moved, and the
  # return value says whether every line landed.
  local line code path target ok=0 moved=0 size=""
  mkdir -p "$HOLD/files" 2>/dev/null || true
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    code="${line:0:2}"
    path="${line:3}"
    # A rename arrives as `old -> new`; the path on disk is the right-hand one, and
    # it is the only one that can block a merge.
    case "$path" in *" -> "*) path="${path##* -> }" ;; esac
    target="$HOLD/files/$path"
    mkdir -p "$(dirname "$target")" 2>/dev/null || true

    if [ "$code" = "??" ]; then
      if ! cp -a -- "$REPO/$path" "$target" 2>/dev/null; then
        printf 'LEFT    %s (could not copy it into the record)\n' "$path" >> "$RECORD"
        printf '   could not record %s — it is untouched\n' "$path"
        ok=1
        continue
      fi
      if ! printf 'MOVED   %s  (whole file: HEAD has no blob to diff)\n' "$path" \
           >> "$RECORD"; then
        printf '   could not write the record for %s — it is untouched\n' "$path"
        ok=1
        continue
      fi
      rm -rf -- "$REPO/$path" 2>/dev/null || true
    else
      if ! gitdo diff HEAD -- "$path" > "$target.patch" 2>/dev/null; then
        printf 'LEFT    %s (could not write its patch)\n' "$path" >> "$RECORD"
        printf '   could not record %s — it is untouched\n' "$path"
        ok=1
        continue
      fi
      size="$(wc -c < "$target.patch" 2>/dev/null | tr -d ' ')"
      if ! printf 'SET     %s  (patch: %s.patch, %s bytes)\n' \
           "$path" "$path" "$size" >> "$RECORD"; then
        printf '   could not write the record for %s — it is untouched\n' "$path"
        ok=1
        continue
      fi
      if ! gitdo checkout HEAD -- "$path" 2>/dev/null; then
        printf 'FAILED  %s (recorded, but it could not be restored)\n' \
          "$path" >> "$RECORD"
        printf '   recorded %s but could not restore it — left in place\n' "$path"
        ok=1
        continue
      fi
      # A checkout writes through the filters, so for a blob those filters cannot
      # reproduce, that restore is not the end of it. This is the case the box was
      # actually stuck on, and it is why the lever used to report NOT MOVED with a
      # clean restore: the merge was still refused, on the same file.
      if ! recover_make_reproducible "$path"; then
        printf 'UNREPRODUCIBLE %s (recorded, but a checkout cannot match its blob)\n' \
          "$path" >> "$RECORD"
        printf '   recorded %s but could not make it reproducible — left in place\n' "$path"
        ok=1
        continue
      fi
    fi
    note "$path is set aside"
    moved=$((moved + 1))
  done
  printf 'SET ASIDE %s path(s) into %s\n' "$moved" "$(basename "$HOLD")" >> "$RECORD"
  return "$ok"
}

recover_pending() {
  # Reads `apply_migration.py --verify` output. A gap is announced as the file's own
  # name on a line by itself, inside the section the header below opens, and the
  # objects it is missing follow indented. Emitting exactly those names is what makes
  # this "the migrations the schema is missing" and not "every migration on disk".
  local line inside=0 name
  while IFS= read -r line; do
    case "$line" in
      "$GAPS_HEADER"*) inside=1; continue ;;
    esac
    [ "$inside" = "1" ] || continue
    case "$line" in
      "$MISSING_MARK"*) continue ;;
      *.sql)
        name="$(basename "$line")"
        # Bare names only: the report prints the file's name, and a path appearing
        # here would mean the format changed under us.
        [ "$name" = "$line" ] && printf '%s\n' "$line"
        ;;
    esac
  done
}

recover_may_lift() {
  # Reads a quarantine record. Lifting one says the gate was right about the box and
  # wrong about the commit; only the schema gate can be *answered* by this script,
  # because it is the one that names an action. Everything else is a statement about
  # the release, and the lever is not a bypass.
  local reason
  reason="$(sed -n '3p' 2>/dev/null)"
  case "$reason" in
    *"$SCHEMA_GATE_WORD"*) return 0 ;;
  esac
  return 1
}

recover_may_rebaseline() {
  # Reads a quarantine record and answers whether this is the perf gate's refusal.
  # Separate from `recover_may_lift` on purpose: the two do different things and one
  # of them is not a lift. The schema gate is answered by *doing the migration* the
  # release needs; the perf gate is answered by asking it to measure the box again.
  # Neither clears a refusal the gate would still make — the gate re-runs both times.
  local reason
  reason="$(sed -n '3p' 2>/dev/null)"
  case "$reason" in
    *"$PERF_GATE_WORD"*) return 0 ;;
  esac
  return 1
}

recover_name_refusal() {
  # Names whatever the runner wrote down when a release did not move. Read *after*
  # the attempt, never at the top of the run: a refusal is a statement about one
  # release, so a record from an earlier attempt names a gate that did not refuse
  # this one.
  #
  # This is the defect that made the lever useless on the live box (2026-09-30):
  # it set aside `app/routes/admin_sekolah.py`, ran the release a second time, and
  # printed only "the checkout did not move" — while the runner had already written
  # `perf gate (… p50 …)` into its own quarantine file. An operator reads NOT MOVED
  # as "nothing is happening", which is the opposite of "a gate measured the box and
  # said no": the first sentence sends them to the journal, the second to the
  # re-baseline button. The lift policy is unchanged — this only *says* what refused.
  #
  # The two records are the runner's shapes: a quarantine holds the gate's *name* on
  # line 3 (after the commit and its timestamp), and a pre-merge refusal holds the
  # refusing step's key on line 1. A preflight record is only this attempt's when it
  # differs from the one already there when the attempt started — otherwise a stale
  # `dirty_checkout` from the first attempt would be quoted as the second's reason.
  local gate now
  if [ -f "$STATE_DIR/quarantined" ]; then
    gate="$(sed -n '3p' "$STATE_DIR/quarantined" 2>/dev/null)"
    note "a gate is holding this commit: $gate"
    printf 'quarantine %s\n' "$gate" >> "$RECORD"
    return 0
  fi
  now="$(cat "$STATE_DIR/refused-before-merge" 2>/dev/null || true)"
  if [ -n "$now" ] && [ "$now" != "${PREFLIGHT_BEFORE:-}" ]; then
    gate="$(printf '%s\n' "$now" | sed -n '1p')"
    note "the release refused before it merged: $gate"
    printf 'preflight %s\n' "$gate" >> "$RECORD"
    return 0
  fi
  return 1
}
# recover-logic:end

# ── arguments: enough to look before you leap, not enough to aim ─────────────

for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY=1 ;;
    -h|--help)
      cat <<'USAGE'
usage: sgfix [--dry-run]

  (no arguments)  recover this box: apply the migrations its schema is missing, run
                  one release, set aside a box-local edit if that is what refused it,
                  and record why. Needs root.
  --dry-run       report why the box is stuck and what would be done; change nothing.

Everything it does is written to $STATE_DIR/recover/<stamp>.txt (default
/var/lib/scangrade-deploy/recover). It never resets a branch, never discards an edit
without recording it, and lifts a quarantine only when the schema gate earned it.

On a box that has no lever installed yet, this file can be read out of the commit the
box has already fetched and run — one line, which fetches `origin` itself if that
commit predates the lever and runs nothing at all if it cannot read one. Running this
script without root prints that line, and docs/AUTO_DEPLOY.md carries it too.
USAGE
      exit 0 ;;
    *)
      printf '!! unknown argument: %s — this takes only --dry-run\n' "$arg" >&2
      exit 2 ;;
  esac
done

# A console session is often root already; when it is not, the sudo hop is the
# difference between one word and a failed one. Refusing is still the fallback: this
# script restarts a unit and edits a checkout.
#
# The hop is skipped when this script did not come from a file, and that guard is
# load-bearing. The documented way in on a box with no lever is
#
#     git show origin/main:deploy/scangrade-recover.sh | bash
#
# and a piped script has nothing to re-exec: `$0` is the shell's own path — `bash`,
# or the shell's absolute path when the caller used one — so `exec sudo … "$0"` hands
# sudo either whatever `bash` happens to mean in the current directory or the shell's
# own binary, and the recovery does not happen either way. `BASH_SOURCE` is the one
# thing that tells the two apart: it is unset for input read from stdin and names the
# file otherwise. So the refusal prints the line that does work instead.
if [ "$(id -u)" -ne 0 ]; then
  if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ] && command -v sudo >/dev/null 2>&1; then
    printf 'not root — re-running under sudo\n'
    exec sudo -E bash "${BASH_SOURCE[0]}" "$@"
  fi
  die "run me as root. Installed, that is one word:

    sudo sgfix

Installed nowhere, read this out of the commit the box has already fetched and pipe
it into a root bash — git show touches nothing in the tree, so it works on a dirty,
rolled-back, refused or quarantined checkout:

    sudo bash -c '$GET_LEVER'" 1
fi

[ -d "$REPO/.git" ] || die "$REPO is not a git checkout (set SG_REPO=…)" 3
command -v runuser >/dev/null 2>&1 || die "runuser is missing — cannot act as the checkout's owner" 3

OWNER="$(stat -c '%U' "$REPO" 2>/dev/null || true)"
[ -n "$OWNER" ] || OWNER="scangrade"
if [ "$(id -un)" = "$OWNER" ]; then
  as_owner() { "$@"; }
else
  as_owner() { runuser -u "$OWNER" -- env HOME="$(getent passwd "$OWNER" | cut -d: -f6)" "$@"; }
fi
gitdo() { as_owner git -C "$REPO" "$@"; }

mkdir -p "$RECOVER_DIR" 2>/dev/null || die "cannot write $RECOVER_DIR" 3

# ── 1. why ───────────────────────────────────────────────────────────────────

say "1/6  why this box is stuck"
note "host       : $(hostname)   $STAMP"
BEFORE="$(gitdo rev-parse --short HEAD 2>/dev/null)"
PORCELAIN="$(gitdo status --porcelain 2>/dev/null || true)"
BEHIND="$(gitdo rev-list --count "HEAD..origin/$BRANCH" 2>/dev/null || echo '?')"
REASON="$(recover_reason)"
note "HEAD       : ${BEFORE:-?}  $(gitdo log -1 --format=%s 2>/dev/null)"
note "behind     : $BEHIND commit(s) on origin/$BRANCH"
note "reason     : $REASON"
if [ -n "$PORCELAIN" ]; then
  note "box-local edit(s):"
  printf '%s\n' "$PORCELAIN" | sed 's/^/   | /'
fi
for f in quarantined refused-before-merge unarmed last-stop; do
  [ -f "$STATE_DIR/$f" ] || continue
  note "$f:"
  sed -n '1,6p' "$STATE_DIR/$f" 2>/dev/null | sed 's/^/   | /'
done
if [ -e "$PAUSE_FILE" ]; then
  note "deploys are PAUSED by $PAUSE_FILE — removing it is the whole fix"
fi
note "last lines of the deploy journal:"
journalctl -u "$UNIT" -n 8 --no-pager 2>/dev/null | sed 's/^/   | /' || true

if [ "$DRY" = "1" ]; then
  say "dry run — nothing was changed"
  exit 0
fi

# ── the record, opened before anything is touched ────────────────────────────
# Written first and appended to as it goes, so a run that dies halfway still leaves
# the reason and whatever it had already set aside.

{
  printf 'ScanGrade recovery  %s\n' "$STAMP"
  printf 'host %s\n' "$(hostname)"
  printf 'reason %s\n' "$REASON"
  printf 'head.before %s\n' "${BEFORE:-?}"
  printf 'behind.before %s\n' "$BEHIND"
  printf 'pause %s\n' "$([ -e "$PAUSE_FILE" ] && echo present || echo absent)"
} > "$RECORD" 2>/dev/null || die "cannot write the recovery record at $RECORD" 1
chmod 0644 "$RECORD" 2>/dev/null || true

# ── 2. the migrations the live schema is missing ─────────────────────────────
# Before the release: the schema gate refuses a release whose database is behind its
# code, and applying them afterwards would only earn that refusal.

say "2/6  migrations the live schema is missing"
PENDING=""
VERIFY_OUT="$("$PY" "$MIGRATE" --verify --repo "$REPO" 2>&1)"
VERIFY_RC=$?
case "$VERIFY_RC" in
  0) note "none — every declared object is present, replaced, or transient" ;;
  6) PENDING="$(printf '%s\n' "$VERIFY_OUT" | recover_pending)"
     if [ -n "$PENDING" ]; then
       note "pending:"
       printf '%s\n' "$PENDING" | sed 's/^/   | /'
     else
       note "the schema gate named a gap but no file name could be read from it —"
       note "this is a format change, not an empty gap; the release below will say so"
     fi ;;
  *) note "could not measure (exit $VERIFY_RC) — skipping migrations, not failing"
     note "the schema gate treats an unmeasurable box the same way"
     printf 'migrations unmeasured (exit %s)\n' "$VERIFY_RC" >> "$RECORD" ;;
esac

APPLIED=0
if [ "$VERIFY_RC" = "6" ] && [ -n "$PENDING" ]; then
  while IFS= read -r migration; do
    [ -n "$migration" ] || continue
    file="$REPO/supabase/migrations/$migration"
    [ -f "$file" ] || { note "missing on disk: $migration"; printf 'MISSING %s\n' "$migration" >> "$RECORD"; continue; }
    note "trial: $migration"
    if ! "$PY" "$MIGRATE" "$file" --repo "$REPO" --ledger "$LEDGER" >> "$RECORD" 2>&1; then
      printf 'TRIAL FAILED %s\n' "$migration" >> "$RECORD"
      die "$migration failed its trial run — nothing was applied. Its output is in $RECORD" 1
    fi
    note "apply: $migration"
    if ! "$PY" "$MIGRATE" "$file" --commit --repo "$REPO" --ledger "$LEDGER" >> "$RECORD" 2>&1; then
      printf 'APPLY FAILED %s\n' "$migration" >> "$RECORD"
      die "$migration failed while applying — its output is in $RECORD" 1
    fi
    printf 'APPLIED %s\n' "$migration" >> "$RECORD"
    note "applied $migration"
    APPLIED=$((APPLIED + 1))
  done <<< "$PENDING"
fi

# ── 3-5. the release ladder ──────────────────────────────────────────────────

release_once() {
  # `systemctl start` on a oneshot waits for the unit. Returning the unit's own
  # verdict is what makes each rung below decide on evidence rather than on timing.
  systemctl start "$UNIT" >/dev/null 2>&1
}

new_head() { gitdo rev-parse --short HEAD 2>/dev/null; }

recover_try_again() {
  # One more release, then the runner's own answer when it *still* does not move.
  #
  # Two things about the read are deliberate. It happens *after* the attempt, so it
  # describes this release rather than the one before it. And it is only trusted for
  # a record this attempt wrote: `PREFLIGHT_BEFORE` is what `refused-before-merge`
  # already held, so a first attempt's `dirty_checkout` cannot be quoted as the
  # second attempt's reason. Naming the gate is the whole point — the live box's run
  # stopped at NOT MOVED while `perf gate (…)` sat unread in the quarantine file.
  PREFLIGHT_BEFORE="$(cat "$STATE_DIR/refused-before-merge" 2>/dev/null || true)"
  release_once
  AFTER="$(new_head)"
  if [ -n "$AFTER" ] && [ "$AFTER" != "$BEFORE" ]; then
    note "the checkout moved: $BEFORE -> $AFTER"
    printf 'release moved %s -> %s\n' "$BEFORE" "$AFTER" >> "$RECORD"
    return 0
  fi
  note "$1"
  recover_name_refusal \
    || note "    and the runner wrote no record — the journal above is where it says so"
  return 1
}

say "3/6  a release"
release_once
AFTER="$(new_head)"

if [ -n "$AFTER" ] && [ "$AFTER" != "$BEFORE" ]; then
  note "the checkout moved: $BEFORE -> $AFTER"
  printf 'release moved %s -> %s\n' "$BEFORE" "$AFTER" >> "$RECORD"
else
  note "the checkout did not move"
  # Read again rather than trusting the snapshot from step 1: the release that just
  # ran may have healed the edit itself (that is what the runner does now), and a
  # lever that set aside a tree that is already quiet would be inventing work.
  PORCELAIN="$(gitdo status --porcelain 2>/dev/null || true)"
  if [ -f "$STATE_DIR/quarantined" ]; then
    note "a gate is holding this commit:"
    sed -n '1,3p' "$STATE_DIR/quarantined" 2>/dev/null | sed 's/^/   | /'
    if recover_may_lift < "$STATE_DIR/quarantined"; then
      # The schema gate names an action and this script performs it: the migrations
      # it wants are applied above, so asking for one retry is what remains. The
      # runner consumes the file itself, so a second refusal re-arms the quarantine.
      say "4/6  the schema quarantine this run just answered"
      : > "$RELEASE_FILE" 2>/dev/null || die "could not ask the runner to retry ($RELEASE_FILE)" 1
      note "asked for one retry via $RELEASE_FILE"
      printf 'lifted the schema quarantine once\n' >> "$RECORD"
      recover_try_again "the retry did not move the checkout:"
    elif recover_may_rebaseline < "$STATE_DIR/quarantined"; then
      # The perf gate names an action too, and it is the one the status page offers:
      # re-measure the box, keep that baseline, and judge this release against it.
      # Not a bypass — the gate runs again and can still refuse a slower release —
      # but a stale baseline is not a code regression, and this is how the runner
      # already knows to tell them apart.
      say "4/6  the perf quarantine this run answers by re-measuring the box"
      : > "$REBASELINE_REQUEST" 2>/dev/null \
        || die "could not ask the runner to re-measure ($REBASELINE_REQUEST)" 1
      note "asked for one re-measurement via $REBASELINE_REQUEST"
      printf 'asked for a perf re-measurement\n' >> "$RECORD"
      recover_try_again "the re-measured box still did not let the release through:"
    else
      printf 'REFUSED another gate holds it: %s\n' "$(sed -n '3p' "$STATE_DIR/quarantined" 2>/dev/null)" >> "$RECORD"
      say "refusing"
      note "the gate holding this release is $(sed -n '3p' "$STATE_DIR/quarantined" 2>/dev/null)"
      note "that is a statement about the release, not about this box — this lever can"
      note "answer only a missing migration and a stale perf baseline. Read the journal."
      exit 1
    fi
  elif [ -n "$PORCELAIN" ]; then
    say "4/6  the box-local edit"
    note "setting aside $(printf '%s\n' "$PORCELAIN" | wc -l | tr -d ' ') path(s), recording each first"
    if printf '%s\n' "$PORCELAIN" | recover_set_aside; then
      note "every path was recorded in $RECORD"
    else
      note "at least one path could not be recorded and was left untouched"
    fi
    say "5/6  a release, on the restored tree"
    recover_try_again "the set-aside worked, but the checkout still did not move:"
  else
    note "no quarantine and no box-local edit — the refusal is something else,"
    note "and the journal above is where it says so"
    recover_name_refusal || true
    printf 'NOT MOVED: no quarantine, clean tree\n' >> "$RECORD"
    say "refusing"
    note "this lever answers two shapes: a box-local edit, and a schema quarantine."
    note "Read the journal above; the reason and this run's evidence are in $RECORD."
    exit 4
  fi
fi

# ── 6. did it land ───────────────────────────────────────────────────────────

say "6/6  verify"
FINAL="$(new_head)"
BEHIND_AFTER="$(gitdo rev-list --count "HEAD..origin/$BRANCH" 2>/dev/null || echo '?')"
note "HEAD now   : ${FINAL:-?}   (was ${BEFORE:-?})"
note "behind     : $BEHIND_AFTER commit(s)"
note "service    : $SERVICE=$(systemctl is-active "$SERVICE" 2>/dev/null)"
{
  printf 'head.after %s\n' "${FINAL:-?}"
  printf 'behind.after %s\n' "$BEHIND_AFTER"
  printf 'migrations.applied %s\n' "$APPLIED"
  printf 'record %s\n' "$RECORD"
} >> "$RECORD"

# Keep the newest few: a record is evidence about one recovery, and a directory that
# grows on every run is a directory nobody reads. The set-aside trees go with their
# records, or the oldest patch outlives the note that explains it.
ls -1dt "$RECOVER_DIR"/*/ 2>/dev/null | tail -n "+$((KEEP + 1))" | while IFS= read -r old; do
  [ "$old" = "$HOLD" ] && continue
  rm -rf -- "$old" 2>/dev/null || true
done
ls -1t "$RECOVER_DIR"/*.txt 2>/dev/null | tail -n "+$((KEEP + 1))" | while IFS= read -r old; do
  rm -f -- "$old" 2>/dev/null || true
done

ok=0
for _ in 1 2 3 4 5 6; do
  if curl -fsS --max-time 10 "$HEALTH" >/dev/null 2>&1; then ok=1; break; fi
  sleep 2
done
if [ "$ok" = "1" ]; then
  note "health     : OK ($HEALTH)"
else
  note "health     : no answer yet from $HEALTH — the app may still be starting"
fi

note "record     : $RECORD"
if [ -n "$FINAL" ] && [ "$FINAL" != "$BEFORE" ]; then
  printf '\n== RECOVERED — the box moved %s -> %s. The reason and everything set aside are in %s.\n\n' \
    "$BEFORE" "$FINAL" "$RECORD"
  exit 0
fi
printf '\n== NOT MOVED — HEAD is still %s. %s says why.\n\n' "${FINAL:-?}" "$RECORD"
exit 4
