"""The branch is read before the box refuses, so a stuck box is still reachable.

Measured on the box this was written for: its runner refused on a hand edit *before*
its own fetch, so `origin/main` never moved. The release carrying the fix — and the
recovery lever with it — could not reach a box whose only remaining channel was a
provider console that cannot paste. Every push, every release request and every page
needs the runner that is refusing, so a runner that refuses before it fetches is a
box that can only be moved by hand.

The refusals that happen before the fetch are all about the *box's arrangement*
rather than about a release: a drifted copy of the runner, an unarmed one, no
virtualenv, and a checkout git cannot read. Reading the branch first cannot make any
of them worse — a fetch writes refs and FETCH_HEAD and touches neither the working
tree nor the index — and it is what lets such a box hold the current lever, which is
the one thing that can fix it without a console.

What these tests hold:

* every arrangement refusal reads the branch *before* it exits;
* none of them loses its own exit code or its own record to that read — the read
  writes no record at all, because the record an operator needs is the reason the
  box is not deploying, not the reason it could not look;
* the read is not attempted where it cannot be made: not root, paused, or with no
  checkout at all — the first cannot fetch as the owner, the second is a freeze
  somebody asked for, and the third has nothing to read;
* and it is exercised for real, against a repository whose working tree is dirty:
  the branch is fetched and the lever is installed from it.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "deploy" / "scangrade-deploy.sh"

BASH = shutil.which("bash")

#: The blocks the harness composes, exactly as the runner composes them: the two that
#: define what the read installs, then the read itself.
BLOCKS = ("refresh-launcher-logic", "fetch-lever-logic", "branch-first-logic")

#: Each arrangement refusal, as (the line that names it, the exit it takes). The call
#: has to sit between the two.
SITES = (
    ("fix once, as root:  bash $REPO/deploy/install-auto-deploy.sh", "exit 14"),
    ("arm it once, as root:  bash $REPO/deploy/arm-auto-deploy.sh", "exit 15"),
    ("no virtualenv at $REPO/.venv — refusing", "exit 3"),
    ("could not read the checkout's state — NOT deploying", "exit 4"),
)

pytestmark = pytest.mark.skipif(BASH is None, reason="needs a bash to run the read")


def _script() -> str:
    return RUNNER.read_text(encoding="utf-8")


def _block(name: str) -> str:
    script = _script()
    start, end = f"# {name}:start", f"# {name}:end"
    assert start in script and end in script, (
        f"deploy/scangrade-deploy.sh no longer carries {name}; a box that refuses "
        "before it fetches has nothing left that can reach it")
    return script.split(start, 1)[1].split(end, 1)[0]


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True,
                   capture_output=True, text=True)


# ── where the read sits, and what it may not do ──────────────────────────────

def test_every_arrangement_refusal_reads_the_branch_first():
    script = _script()
    for names_it, exit_code in SITES:
        named = script.index(names_it)
        # Bounded by *this* refusal's own code: the window runs from the line that
        # names the refusal to the first exit after it. Searching past that would
        # let a call in a later block stand in for the missing one — which is how a
        # deleted call can look present.
        window = script[named:script.index(exit_code, named)]
        assert "branch_read_refs\n" in window, (
            f"the refusal at {names_it!r} exits without reading the branch: the box "
            "stays blind, `origin/$BRANCH` never moves, and the lever it needs can "
            "never arrive")


def test_the_read_writes_no_record_and_takes_no_exit():
    """The refusal that follows *is* the reason the box is not deploying. A record
    about the read would replace the one an operator needs with a footnote, and an
    exit here would pre-empt the refusal's own code."""
    block = _block("branch-first-logic")
    for forbidden in ("preflight_write", "PREFLIGHT_GATE", "UNARMED_FILE", "exit "):
        assert forbidden not in block, (
            f"the branch read touches {forbidden!r} — it exists to look, not to judge")


def test_it_is_not_read_where_it_cannot_be(tmp_path):
    script = _script()
    # Not root: the fetch runs as the checkout's owner, which needs root to drop to.
    root_check = script.index("must run as root")
    assert "branch_read_refs" not in script[root_check:script.index("exit 2", root_check)]
    # Paused: a freeze somebody asked for, and a frozen box fetches nothing today.
    pause = script.index('if [ -e "$PAUSE_FILE" ]')
    assert "branch_read_refs" not in script[pause:script.index("exit 0", pause)]
    # No checkout at all: there is nothing to read, and the guard says so first.
    # It lives in the shared reader now — three callers share one fetch — so the
    # guard is asserted where the fetch is, not where it used to be.
    block = _block("branch-first-logic")
    assert '[ -d "$REPO/.git" ] || return 1' in block, (
        "the read no longer checks for a checkout, so it would fetch in a directory "
        "that is not one")
    assert "branch_refs_read() {" in block, (
        "the fetch moved out of the block the refusal path composes, so a refusal "
        "would run a reader that is not defined")


def test_the_armament_sentence_stops_claiming_nothing_was_fetched():
    """It is checked before anything is *merged*, which is what it always meant: a
    release is not staged, nothing is reloaded — but the branch is read, and the
    sentence an operator reads has to say what actually happened."""
    script = _script()
    assert "Nothing was fetched and nothing was reloaded" not in script, (
        "the armament refusal still says nothing was fetched, and refs are read "
        "before it")
    assert "Nothing was merged and nothing was reloaded" in script


# ── and it is run, not just asserted ─────────────────────────────────────────

def _run(harness: str, script: Path) -> subprocess.CompletedProcess:
    """Run the composed harness from a *file*.

    Not `bash -c`: the harness is ~15 KB once the two lever blocks are in it, and
    Git Bash on Windows truncates an argument around 8 KB — silently, at exit 0,
    which turns "the lever was not installed" into a passing test. A file has no
    such ceiling, and every platform runs the same bytes.
    """
    script.write_text(harness, encoding="utf-8")
    return subprocess.run([BASH, str(script)], capture_output=True, text=True,
                          check=False)


def _harness(repo: Path, lever_dir: Path) -> str:
    return "\n".join([
        "set -uo pipefail",
        f'REPO="{repo.as_posix()}"',
        'BRANCH="main"',
        f'LEVER_DIR="{lever_dir.as_posix()}"',
        f'INSTALLED_BIN_DIR="{(lever_dir / "bin").as_posix()}"',
        'INSTALLED_RUNNER="$INSTALLED_BIN_DIR/scangrade-deploy"',
        'INSTALLED_SNAPSHOT="$INSTALLED_BIN_DIR/scangrade-db-snapshot"',
        'INSTALLED_RECOVER="$INSTALLED_BIN_DIR/sgfix"',
        'log() { echo "$*"; }',
        'as_owner() { "$@"; }',
        *[_block(name) for name in BLOCKS],
        "branch_read_refs",
        "echo DONE",
    ])


def _sandbox(tmp_path: Path, *, lever_body: str):
    """A bare origin and a clone of it, with the lever on the branch."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "-c", "init.defaultBranch=main", "init", "-q", "--bare",
                    str(origin)], check=True, capture_output=True, text=True)
    seed = tmp_path / "seed"
    (seed / "deploy").mkdir(parents=True)
    (seed / "deploy" / "scangrade-recover.sh").write_text("#!/usr/bin/env bash\n# old\n",
                                                          encoding="utf-8")
    (seed / "deploy" / "entrypoint.sh").write_text("#!/bin/sh\nREPO=\"@REPO@\"\n",
                                                   encoding="utf-8")
    subprocess.run(["git", "-c", "init.defaultBranch=main", "init", "-q", str(seed)],
                   check=True, capture_output=True, text=True)
    _git(seed, "add", "-A")
    _git(seed, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "old")
    _git(seed, "remote", "add", "origin", str(origin))
    _git(seed, "push", "-q", "-u", "origin", "main")

    checkout = tmp_path / "checkout"
    subprocess.run(["git", "clone", "-q", str(origin), str(checkout)],
                   check=True, capture_output=True, text=True)
    return origin, seed, checkout


def test_it_fetches_and_installs_the_lever_from_a_dirty_checkout(tmp_path):
    """The whole point: the tree git cannot merge into is not a reason to stay
    blind. The branch is read, and the lever the box has never run lands on it."""
    _origin, seed, checkout = _sandbox(tmp_path, lever_body="")
    (checkout / "somebody-was-editing.txt").write_text("hand edit\n", encoding="utf-8")
    assert subprocess.run(["git", "-C", str(checkout), "status", "--porcelain"],
                          capture_output=True, text=True).stdout.strip(), \
        "the harness is not in the dirty state the test is about"

    # The lever changes on the branch *after* this box last fetched.
    (seed / "deploy" / "scangrade-recover.sh").write_text(
        "#!/usr/bin/env bash\n# NEW-LEVER\n", encoding="utf-8")
    _git(seed, "add", "-A")
    _git(seed, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "new")
    _git(seed, "push", "-q", "origin", "main")

    lever_dir = tmp_path / "lever"
    done = _run(_harness(checkout, lever_dir), tmp_path / "harness.sh")

    assert done.returncode == 0, done.stderr
    assert "DONE" in done.stdout, done.stdout
    fetched = subprocess.run(["git", "-C", str(checkout), "rev-parse", "origin/main"],
                             capture_output=True, text=True).stdout.strip()
    branch = subprocess.run(["git", "-C", str(seed), "rev-parse", "main"],
                            capture_output=True, text=True).stdout.strip()
    assert fetched == branch, "the branch was not read — origin/main did not move"
    installed = lever_dir / "deploy" / "scangrade-recover.sh"
    assert installed.is_file(), (
        "the lever was not materialised: the box read the branch and still cannot be "
        "recovered from it")
    assert "NEW-LEVER" in installed.read_text(encoding="utf-8")


def test_a_missing_checkout_is_a_no_op(tmp_path):
    repo = tmp_path / "not-a-checkout"
    repo.mkdir()
    lever_dir = tmp_path / "lever"
    done = _run(_harness(repo, lever_dir), tmp_path / "harness.sh")

    assert done.returncode == 0, done.stderr
    assert "DONE" in done.stdout
    assert not (lever_dir / "deploy").exists(), (
        "the read created an installed lever out of nothing")
