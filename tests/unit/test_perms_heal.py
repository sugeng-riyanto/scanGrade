"""A root-owned file in `.git` must not stall every release behind a network story.

Every write the release makes goes through `git` *as the checkout's owner*: the
fetch writes refs and `FETCH_HEAD`, the merge writes the index. A file in `.git`
that root owns — left by a `git` a human ran as root, by a provider console, or by
an older runner — cannot be written by that owner, so the fetch dies with git's
`insufficient permission` or `Unable to create .../.git/index.lock`. The runner then
prints git's own words under **"git fetch failed (network or credentials)"**, and
there the box sits: the same sentence about the network, every two minutes, for a
file that is merely mis-owned. That is a silent stall wearing the costume of a
network fault, and it is the same shape `lock-heal-logic` closes for a lock.

What these tests hold:

* the heal sits in its own delimited block, before **every** fetch — the one in
  `branch_refs_read`, which the plan reader, the adoption and the release all share;
* it is a heal, not a gate: it takes no exit code and writes no preflight record,
  because a box refusing a release over a file a later tick could have healed is the
  deadlock this branch of work exists to remove;
* it sweeps with tools the box always has (`find`, `chown`) and by numeric uid, so a
  missing passwd entry cannot make it quietly stop working;
* and it behaves: a healthy checkout is left alone, and a drift is repaired, or is
  named and left for the fetch to report — never rounded to "fine".

The repair needs root, which a developer machine is not, so the block is *run* with
its sweep and its `chown` replaced — the same trick `lock-heal-logic` uses for its
process table. The real sweep (`find ... ! -uid`) and the real `chown -R` are pinned
structurally, where they cannot be simulated away.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "deploy" / "scangrade-deploy.sh"
BASH = shutil.which("bash")

needs_bash = pytest.mark.skipif(BASH is None, reason="needs bash to run the block")

START = "# perms-heal-logic:start"
END = "# perms-heal-logic:end"

#: What a heal must never be: a refusal. A heal that can stop a release is a heal
#: that can *cause* the stall it exists to remove, on a box whose only channel back
#: is the next tick.
FORBIDDEN = ("exit ", "PREFLIGHT_GATE", "PREFLIGHT_EXIT", "preflight_write")


def _script() -> str:
    return RUNNER.read_text(encoding="utf-8")


def _block() -> str:
    return _script().split(START, 1)[1].split(END, 1)[0]


def _code(block: str) -> str:
    """The block with its comments dropped, so a check reads the code alone — the
    comments deliberately name the tools and the shapes this step avoids."""
    return "\n".join(line for line in block.splitlines()
                     if line.strip() and not line.strip().startswith("#"))


# ── the block on its own ─────────────────────────────────────────────────────

def test_the_block_lives_between_its_own_delimiters():
    """The delimiters are what let the rest of this file run it in isolation."""
    script = _script()
    assert START in script and END in script, (
        "the perms-heal block's delimiters are gone; they are also how the block is "
        "tested, the way the lock heal's and the box-edits' are")
    block = _block()
    for fn in ("git_dir_path() {", "owner_uid() {", "git_dir_foreign() {",
               "git_ownership_heal() {"):
        assert fn in block, f"{fn} left the delimited block"


def test_it_runs_before_every_fetch_and_only_once():
    """One call site, and it is *before* the fetch that it exists to unblock.

    `branch_refs_read` is the single fetch in the script — the plan reader, the
    adoption and the release all share it — so a call inside it, ahead of the
    `git ... fetch`, is a call ahead of every round-trip to GitHub."""
    script = _script()
    calls = [m.start() for m in re.finditer(r"^\s*git_ownership_heal$", script, re.M)]
    assert len(calls) == 1, (
        f"{len(calls)} git_ownership_heal call(s): it belongs in the one reader that "
        "fetches, so every fetch path is covered and none pays it twice")
    fetch = script.index('fetch --quiet origin "$BRANCH"')
    assert calls[0] < fetch, (
        "the heal runs after the fetch it is there to unblock, so a root-owned file "
        "still fails the fetch and is still reported as a network fault")


def test_the_heal_is_inside_the_block_the_fetch_is_in():
    """A call in a block the refusal path does not compose is a call a stuck box
    never runs — and a stuck box is exactly the one whose `.git` has drifted."""
    block = _script().split("# branch-first-logic:start", 1)[1] \
        .split("# branch-first-logic:end", 1)[0]
    assert "git_ownership_heal" in block, (
        "the fetch moved out of the block the refusal path composes, so the heal no "
        "longer runs before the fetch that a stuck box needs to make")


def test_it_never_refuses_a_release():
    """It is a heal, not a gate. No exit, no preflight record: the fetch below still
    runs and still reports its own failure, and the next tick can try again."""
    code = _code(_block())
    for forbidden in FORBIDDEN:
        assert forbidden not in code, (
            f"the heal touches {forbidden!r} — a file it could not repair would then "
            "refuse a release, which is the deadlock this step removes")


def test_the_sweep_needs_no_package_and_resolves_no_names():
    """`find` and `chown` are coreutils; `-uid` is a number.

    A sweep that asked `find -user <name>` would depend on a passwd entry the box may
    not have — and a heal that quietly stops working because a lookup failed is the
    same silent stall in a new costume."""
    code = _code(_block())
    assert 'find "$OWNERSHIP_DIR" ! -uid "$OWNERSHIP_UID" -print' in code, (
        "the sweep does not list what the owner does not own, so it can find nothing "
        "to repair")
    assert "-user" not in code, (
        "the sweep resolves a username, so a missing passwd entry disables it")
    assert "chown -R" in code, (
        "the repair does not chown the tree recursively, so a directory left "
        "unwritable would survive it")


def test_the_path_is_asked_of_git_not_assumed():
    """A linked worktree keeps its gitdir elsewhere, where `.git` is a *file*.

    The ordinary checkout is `$REPO/.git`, so that is the fallback when git cannot
    describe the tree — not a second decision, just the one case that needs no git."""
    code = _code(_block())
    assert "rev-parse --absolute-git-dir" in code, (
        "the heal assumes `$REPO/.git` instead of asking git, so a worktree would be "
        "swept in the wrong place")
    assert '"$REPO/.git"' in code, (
        "there is no fallback for a checkout git cannot describe, so the heal would "
        "hold an empty path")


def test_a_healthy_checkout_is_left_alone_before_it_is_touched():
    """The common case must cost one sweep and print nothing.

    The repair is only reached once something is *known* to disagree, so a tick on a
    healthy box never runs `chown` — which on a large `.git` is the difference
    between a read and a rewrite of every object."""
    block = _block()
    sweep = block.index("foreign=$(git_dir_foreign)")
    guard = block.index('[ -n "$foreign" ] || return 0', sweep)
    repair = block.index("chown -R", guard)
    assert sweep < guard < repair, (
        "the repair is not guarded by the sweep, so a healthy `.git` is chowned on "
        "every tick")


# ── and it behaves ───────────────────────────────────────────────────────────

def _norm(path) -> str:
    return str(path).replace("\\", "/")


def _harness(tmp_path: Path, *, sweep: str, chown_body: str) -> tuple[str, Path, Path]:
    """The real block, with its sweep and its `chown` replaced.

    `git_dir_path` is replaced too, so the test needs no real repository and cannot
    wander into whatever checkout the temp directory happens to sit inside. The
    *defaults* of both replaced commands are pinned by the structural tests above.
    """
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True, exist_ok=True)
    sweep_file = tmp_path / "sweep"
    sweep_file.write_text(sweep, encoding="utf-8")
    chown_log = tmp_path / "chown.log"
    chown_log.write_text("", encoding="utf-8")

    program = "\n".join([
        "set -uo pipefail",
        f'REPO="{_norm(repo)}"',
        f'SWEEP_FILE="{_norm(sweep_file)}"',
        f'CHOWN_LOG="{_norm(chown_log)}"',
        'OWNER="$(id -un)"',
        'log() { echo "$*"; }',
        'as_owner() { "$@"; }',
        _block(),
        f'git_dir_path() {{ printf "%s\\n" "{_norm(repo / ".git")}"; }}',
        'git_dir_foreign() { cat "$SWEEP_FILE" 2>/dev/null; }',
        'chown() { echo "chown $*" >> "$CHOWN_LOG"; ' + chown_body + '; }',
    ])
    return program, sweep_file, chown_log


def _run(program: str, body: str, *, ceiling: Path):
    env = dict(os.environ)
    env["GIT_CEILING_DIRECTORIES"] = _norm(ceiling)
    env.pop("GIT_DIR", None)
    env.pop("GIT_INDEX_FILE", None)
    return subprocess.run([BASH, "-c", program + "\n" + body], capture_output=True,
                          text=True, timeout=60, env=env)


@needs_bash
def test_nothing_to_repair_says_nothing_and_never_chowns(tmp_path):
    program, _sweep, chown_log = _harness(tmp_path, sweep="",
                                          chown_body="return 0")
    run = _run(program, 'git_ownership_heal; echo "rc=$?"', ceiling=tmp_path)
    assert run.returncode == 0, run.stderr
    assert "rc=0" in run.stdout, run.stdout
    assert "holds" not in run.stdout, (
        "a healthy `.git` produces a line, which is how a journal fills with noise "
        "nobody reads")
    assert chown_log.read_text(encoding="utf-8") == "", (
        "the heal chowned a tree with nothing to repair")


@needs_bash
def test_a_drift_is_chowned_to_the_owner_and_reported(tmp_path):
    program, sweep, chown_log = _harness(
        tmp_path, sweep="/opt/scangrade/.git/index\n", chown_body=': > "$SWEEP_FILE"')
    run = _run(program, 'git_ownership_heal; echo "rc=$?"', ceiling=tmp_path)
    assert run.returncode == 0, run.stderr
    assert "rc=0" in run.stdout, run.stdout
    assert "1 path(s) not owned" in run.stdout, (
        "the heal repaired the drift without saying it found one, so a box that had "
        "been stalling looks exactly like one that never did")
    assert "/opt/scangrade/.git/index" in run.stdout, (
        "the offending path is not printed, so the journal cannot say what drifted")
    assert "repaired" in run.stdout, run.stdout
    log = chown_log.read_text(encoding="utf-8")
    assert log.startswith("chown -R "), (
        f"the repair did not chown the tree recursively: {log!r}")
    assert sweep.read_text(encoding="utf-8") == "", sweep.read_text(encoding="utf-8")


@needs_bash
def test_a_chown_that_fails_is_named_and_the_run_carries_on(tmp_path):
    """Fail open, loudly: the fetch still runs and will name git's own error, and the
    line above it says the ownership could not be fixed — not silence."""
    program, sweep, _log = _harness(
        tmp_path, sweep="/opt/scangrade/.git/FETCH_HEAD\n", chown_body="return 1")
    run = _run(program, 'git_ownership_heal; echo "rc=$?"; echo "carried on"',
               ceiling=tmp_path)
    assert run.returncode == 0, run.stderr
    assert "rc=0" in run.stdout, run.stdout
    assert "carried on" in run.stdout, (
        "the heal refused the run for a file it could not repair, which is the "
        "deadlock it exists to remove")
    assert "could not repair ownership" in run.stdout, (
        "a repair that failed said nothing, so the fetch failure below reads as a "
        "network fault again")
    assert sweep.read_text(encoding="utf-8").strip() != "", (
        "the sweep file must be left as found when the chown failed")


@needs_bash
def test_still_foreign_after_a_chown_is_reported_not_rounded_to_fixed(tmp_path):
    """A `chown` that returns 0 but changes nothing — a read-only filesystem, an
    immutable bit — must not be read as success. The re-check is the whole guard."""
    program, _sweep, _log = _harness(
        tmp_path, sweep="/opt/scangrade/.git/objects/pack/p.pack\n",
        chown_body="return 0")
    run = _run(program, 'git_ownership_heal; echo "rc=$?"; echo "carried on"',
               ceiling=tmp_path)
    assert run.returncode == 0, run.stderr
    assert "carried on" in run.stdout, run.stdout
    assert "still disagrees" in run.stdout, (
        "the heal believed a chown that changed nothing, so it reports a repair that "
        "did not happen and the fetch failure looks unexplained")
    assert "repaired" not in run.stdout, (
        "the heal claims a repair while the drift is still there")
