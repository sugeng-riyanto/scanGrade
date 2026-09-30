"""Does the read that precedes every refusal actually happen, and stay weightless?

The feature is one function and four call sites, and every way it can look present
while doing none of its job is a mutation below:

* **a call is deleted** — at the copy check, the armament check, the missing
  virtualenv, or an unreadable checkout. Each is the same failure with a different
  face: that refusal exits without reading the branch, so `origin/$BRANCH` never
  moves and the lever the box needs can never arrive;
* **the read writes a record** — it takes a `PREFLIGHT_GATE` of its own, so the
  sentence an operator needs ("this box is not armed") is replaced by a footnote
  about the *read*;
* **the read takes a code** — an `exit` inside it, which pre-empts the refusal that
  follows and hides its own code and preflight line;
* **the read happens where it cannot** — inside the root check (the fetch drops to
  the owner with `runuser`, which needs root) and behind the pause file (a freeze
  somebody asked for);
* **the guard that keeps it out of a non-checkout goes**, so a box with no `.git`
  is asked to fetch in a directory that is not one;
* **the lever is not materialised**, so the box reads the branch and still holds
  nothing from it;
* **the fetch is neutered**, so the read "succeeds" and `origin/$BRANCH` never
  moves;
* **the armament sentence reverts to claiming nothing was fetched**, when a read
  now precedes it.

The harness — bytes in and out, an anchor that must match exactly once, and a `-k`
filter that matches no test counted as a harness error rather than a catch — is
`mutate_console_recover.py`'s, imported rather than copied. The suite is overridden,
because the default is the lever's and a selector that ran the wrong file would
report every mutation caught.

    .venv/Scripts/python.exe .freebuff/mutate_branch_first.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

import mutate_console_recover as base  # noqa: E402
from mutate_console_recover import purge_bytecode, restore  # noqa: E402

RUNNER = ROOT / "deploy" / "scangrade-deploy.sh"

#: The suite these guards live in. The harness's own choice is the lever's, so it is
#: replaced rather than inherited.
base.SUITES = ["tests/unit/test_branch_first.py"]


def run_tests(only: str):
    return base.run_tests(only)


CALL_DRIFT = ("# newest lever — is installed from it. See branch-first-logic.\n"
              "  branch_read_refs\n")
CALL_ARMAMENT = ("# cannot release. See branch-first-logic.\n"
                 "  branch_read_refs\n")
CALL_NOVENV = '  log "no virtualenv at $REPO/.venv — refusing"\n  branch_read_refs\n'
CALL_UNREADABLE = ("  printf '%s\\n' \"$DIRTY\" | sed 's/^/    /'\n"
                   "  branch_read_refs\n")

HEAD = 'branch_read_refs() {\n  [ -d "$REPO/.git" ] || return 0\n'
GUARD = '[ -d "$REPO/.git" ] || return 0\n'
FETCH = '  if ! out=$(as_owner git -C "$REPO" fetch --quiet origin "$BRANCH" 2>&1); then\n'
MATERIALISE = ('  materialise_lever_from_origin\n  return 0\n}\n'
               '# branch-first-logic:end\n')
ROOT_LOG = '  log "must run as root (it restarts $SERVICE) — current uid $(id -u)"\n'
PAUSE = '  log "paused by $PAUSE_FILE — not deploying"\n  exit 0\n'

#: (target, name, [(old, new), ...], the -k selector the failure must land in)
MUTATIONS = [
    (RUNNER, "the copy check refuses without reading the branch",
     [(CALL_DRIFT, CALL_DRIFT.replace("  branch_read_refs\n", ""))],
     "arrangement_refusal_reads_the_branch_first"),
    (RUNNER, "the armament check refuses without reading the branch",
     [(CALL_ARMAMENT, CALL_ARMAMENT.replace("  branch_read_refs\n", ""))],
     "arrangement_refusal_reads_the_branch_first"),
    (RUNNER, "the missing virtualenv refuses without reading the branch",
     [(CALL_NOVENV, CALL_NOVENV.replace("  branch_read_refs\n", ""))],
     "arrangement_refusal_reads_the_branch_first"),
    (RUNNER, "an unreadable checkout refuses without reading the branch",
     [(CALL_UNREADABLE, CALL_UNREADABLE.replace("  branch_read_refs\n", ""))],
     "arrangement_refusal_reads_the_branch_first"),
    (RUNNER, "the read writes a preflight record of its own",
     [(HEAD, HEAD + "  PREFLIGHT_GATE=branch_read\n")],
     "writes_no_record"),
    (RUNNER, "the read takes an exit code of its own",
     [(HEAD, HEAD + "  exit 0\n")],
     "writes_no_record"),
    (RUNNER, "the read runs inside the root check, before it can drop to the owner",
     [(ROOT_LOG, ROOT_LOG + "  branch_read_refs\n")],
     "not_read_where_it_cannot_be"),
    (RUNNER, "the read runs inside the pause block",
     [(PAUSE, PAUSE.replace("  exit 0\n", "  branch_read_refs\n  exit 0\n"))],
     "not_read_where_it_cannot_be"),
    (RUNNER, "the no-checkout guard goes, so a non-repo is asked to fetch",
     [(GUARD, "")],
     "not_read_where_it_cannot_be"),
    (RUNNER, "the branch is read but the lever is never materialised",
     [(MATERIALISE, MATERIALISE.replace("  materialise_lever_from_origin\n", ""))],
     "installs_the_lever_from_a_dirty_checkout"),
    (RUNNER, "the fetch is neutered, so origin/$BRANCH never moves",
     [(FETCH, "  if ! out=$(false); then\n")],
     "fetches_and_installs_the_lever_from_a_dirty_checkout"),
    (RUNNER, "the armament sentence claims nothing was fetched again",
     [("Nothing was merged and nothing was reloaded",
       "Nothing was fetched and nothing was reloaded")],
     "armament_sentence"),
]


def main() -> int:
    targets = (RUNNER,)
    originals = {path: path.read_bytes() for path in targets}
    caught = survivors = harness_errors = 0

    for target, name, replacements, only in MUTATIONS:
        text = originals[target].decode("utf-8")
        for old, _new in replacements:
            if text.count(old) != 1:
                print(f"SKIP      {name}: anchor matched {text.count(old)} time(s)")
                harness_errors += 1
                break
        else:
            broken = text
            for old, new in replacements:
                broken = broken.replace(old, new, 1)
            target.write_bytes(broken.encode("utf-8"))
            try:
                passed = run_tests(only)
            finally:
                restore(target, originals[target])
                purge_bytecode([target])
            if passed is None:
                print(f"HARNESS   {name}: the -k filter {only!r} matched no test")
                harness_errors += 1
            elif passed:
                survivors += 1
                print(f"SURVIVED  {name}  <-- the guard does not bite")
            else:
                caught += 1
                print(f"caught    {name}")

    print(f"\n{caught}/{len(MUTATIONS)} injected defects caught"
          + (f", {survivors} survived" if survivors else "")
          + (f", {harness_errors} harness error(s)" if harness_errors else ""))
    return 1 if survivors or harness_errors else 0


if __name__ == "__main__":
    sys.exit(main())
