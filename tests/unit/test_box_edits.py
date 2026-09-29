"""A box-local edit the release also writes is set aside, not refused forever.

What was wrong
--------------
`scangrade-deploy.sh` refused on *any* local change, **before** the fetch. On this
box that was not a warning, it was a deadlock: `M app/routes/admin_sekolah.py` meant
the checkout could never merge, so the box could never receive the very release that
added a way to clear it — eight commits behind, refusing every two minutes, with a
console as the only exit.

Refusing was the right instinct and the wrong rule. `git merge --ff-only` fails only
where the incoming commits write a path that is also changed here, so what matters is
not "is the tree dirty" but "would this merge clobber something".

What replaces it
----------------
`local_edits_heal` decides per path, against the release's own file list:

* **a path this release writes** — the box's version is set aside first, then the
  path is restored so the merge can proceed. A path HEAD has is preserved as a diff
  against HEAD; a path HEAD does not have is copied out whole, because there is no
  blob for a diff to apply to;
* **a path it does not write** — left exactly as it is, and reported. Nothing can
  clobber it, so it must not stop a release;
* **the set-aside cannot be written** — the one refusal left. A heal that cannot say
  what it moved is a silent loss.

These tests run the section itself, in a real temporary git repository, because the
property is what the function *does* to a tree: a source guard cannot tell a patch
that holds the box's version from one that holds an empty string. The harness is
written to a file rather than passed to `bash -c`, which is both how the runner is
actually started and the only form that survives Windows' command-line quoting for a
script this long.

The last few checks are source guards, for the two properties a single run cannot
show: that the heal happens after the release's file list is known, and that the
record is written before anything is restored.
"""
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DEPLOY_SH = ROOT / "deploy" / "scangrade-deploy.sh"
BASH = shutil.which("bash")

BOX_START = "# box-edits-logic:start"
BOX_END = "# box-edits-logic:end"
STREAK_START = "# refusal-streak-logic:start"
STREAK_END = "# refusal-streak-logic:end"

pytestmark = pytest.mark.skipif(BASH is None, reason="needs a bash to run the section")


def _box_block() -> str:
    script = DEPLOY_SH.read_text(encoding="utf-8")
    assert BOX_START in script and BOX_END in script, (
        "the section's delimiters are what let it be run on its own, the way the "
        "quarantine's, the preflight record's and the lock heal's are; keep them")
    return script.split(BOX_START, 1)[1].split(BOX_END, 1)[0]


def _streak_block() -> str:
    """The refusal memory, which the heal reads to decide whether it has been here
    before. Run alongside the box-edits section because that is how the runner runs
    them: one script, the definitions in it, the heal called for real."""
    script = DEPLOY_SH.read_text(encoding="utf-8")
    assert STREAK_START in script and STREAK_END in script, (
        "the streak section's delimiters are what let it be run on its own")
    return script.split(STREAK_START, 1)[1].split(STREAK_END, 1)[0]


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@example.com",
         "-c", "user.name=t", *args],
        capture_output=True, text=True, check=False)


def _repo(tmp_path: Path) -> Path:
    """A checkout with one committed file, the way the box's own looks."""
    repo = tmp_path / "repo"
    (repo / "app" / "routes").mkdir(parents=True)
    (repo / "app" / "routes" / "admin_sekolah.py").write_text("the release's line\n",
                                                              encoding="utf-8")
    (repo / "README.md").write_text("readme\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], capture_output=True, text=True)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


#: The smallest harness that can stand where the runner does: the section reads
#: `$REPO`, `$STATE_DIR` and `$AFTER_FULL`, heals as the repo's owner, and records
#: its refusals through `preflight_write`.
HARNESS = """set -uo pipefail
REPO=__REPO__
STATE_DIR=__STATE__
AFTER_FULL=__SHA__
as_owner() { "$@"; }
log() { printf 'LOG %s\\n' "$*"; }
PREFLIGHT_GATE=""; PREFLIGHT_EXIT=""; PREFLIGHT_DETAIL=""; PREFLIGHT_DIFF=""
preflight_write() { printf '%s\\n' "$PREFLIGHT_GATE" > __STATE__/preflight-gate; }
preflight_diff() { printf 'the box diff\\n'; }
"""


def _harness_text(repo: Path, state: Path, tail: str) -> str:
    header = (HARNESS
              .replace("__REPO__", shlex.quote(repo.as_posix()))
              .replace("__STATE__", shlex.quote(state.as_posix()))
              .replace("__SHA__", "c" * 40))
    return header + _streak_block() + _box_block() + tail


def _run(tmp_path: Path, repo: Path, state: Path, incoming: list[str],
         *, tail: str | None = None) -> subprocess.CompletedProcess:
    if tail is None:
        # The runner's own two globals, in the shape the runner sets them. Helpers in
        # this script take no arguments — that is what keeps it unsteerable from
        # outside — so the harness sets the same variables the guard sets.
        tail = ('DIRTY=$(git -C "$REPO" status --porcelain)\n'
                f'CHANGED={shlex.quote(chr(10).join(incoming))}\n'
                "local_edits_heal\n"
                "printf 'FINISHED\\n'\n")
    script = tmp_path / "harness.sh"
    script.write_text(_harness_text(repo, state, tail), encoding="utf-8", newline="\n")
    return subprocess.run([BASH, str(script)], capture_output=True, text=True, check=False)


def _records(state: Path) -> list[Path]:
    return sorted((state / "set-aside").glob("*.txt"))


# ── 1. the heal itself, against a real tree ─────────────────────────────────

def test_a_tracked_edit_the_release_writes_is_set_aside_and_the_tree_restored(tmp_path):
    """The reported case: `M app/routes/admin_sekolah.py`, on a release that writes it."""
    repo, state = _repo(tmp_path), tmp_path / "state"
    state.mkdir()
    target = repo / "app" / "routes" / "admin_sekolah.py"
    target.write_text("the box's hotfix\n", encoding="utf-8")

    done = _run(tmp_path, repo, state, ["app/routes/admin_sekolah.py"])

    assert done.returncode == 0, done.stderr
    assert "FINISHED" in done.stdout, f"the heal refused a case it can complete: {done.stdout}"
    assert target.read_text(encoding="utf-8") == "the release's line\n", (
        "the path the release writes was not restored, so the merge would still fail")
    assert _git(repo, "status", "--porcelain").stdout.strip() == "", "the tree is still dirty"

    patch = _records(state)[0].with_suffix(".patch")
    assert patch.is_file(), "the box's version was set aside without a patch"
    assert "the box's hotfix" in patch.read_text(encoding="utf-8"), (
        "the patch does not hold what the box had, so the edit is lost in every way "
        "that matters")


def test_the_record_says_which_commit_and_which_paths(tmp_path):
    repo, state = _repo(tmp_path), tmp_path / "state"
    state.mkdir()
    (repo / "app" / "routes" / "admin_sekolah.py").write_text("the box's hotfix\n",
                                                              encoding="utf-8")

    assert _run(tmp_path, repo, state, ["app/routes/admin_sekolah.py"]).returncode == 0
    record = _records(state)[0]

    lines = record.read_text(encoding="utf-8").splitlines()
    assert lines[1] == "c" * 40, "the record does not name the commit it set the edit aside for"
    assert "set-aside app/routes/admin_sekolah.py" in lines, (
        f"the record does not name the path it moved: {lines}")
    if os.name != "nt":
        # A mode is not a thing a Windows filesystem records, and this suite runs on
        # both. The line itself is held by a source guard in the section test.
        assert (os.stat(record).st_mode & 0o077) == 0, (
            "the set-aside record is a copy of a hand edit and can hold a credential; "
            "it must not be readable by anyone but root")


def test_a_path_head_does_not_have_is_copied_out_whole(tmp_path):
    """An added path has no blob, so a diff against HEAD would preserve nothing."""
    repo, state = _repo(tmp_path), tmp_path / "state"
    state.mkdir()
    (repo / "app" / "notes.py").write_text("a file the box made, that the release adds too\n",
                                           encoding="utf-8")

    done = _run(tmp_path, repo, state, ["app/notes.py"])

    assert done.returncode == 0, done.stderr
    assert not (repo / "app" / "notes.py").exists(), "the path still blocks the merge"
    kept = _records(state)[0].with_suffix(".files") / "app" / "notes.py"
    assert kept.read_text(encoding="utf-8") == \
        "a file the box made, that the release adds too\n", (
        "the copy does not hold the box's bytes, so the file is lost")


def test_an_edit_the_release_does_not_write_is_left_alone(tmp_path):
    """Nothing can clobber it, so it must not stop a release — and must not move."""
    repo, state = _repo(tmp_path), tmp_path / "state"
    state.mkdir()
    (repo / "README.md").write_text("the box's own note\n", encoding="utf-8")

    done = _run(tmp_path, repo, state, ["app/routes/admin_sekolah.py"])

    assert done.returncode == 0, done.stderr
    assert (repo / "README.md").read_text(encoding="utf-8") == "the box's own note\n", (
        "an edit the release does not write was moved")
    record = _records(state)[0].read_text(encoding="utf-8")
    assert "kept README.md" in record, (
        "the page cannot say the box still holds a local edit")
    assert "set-aside README.md" not in record


def test_a_clean_tree_leaves_no_record_at_all(tmp_path):
    repo, state = _repo(tmp_path), tmp_path / "state"
    state.mkdir()

    done = _run(tmp_path, repo, state, ["app/routes/admin_sekolah.py"])

    assert done.returncode == 0 and "FINISHED" in done.stdout, done.stdout
    assert not list((state / "set-aside").glob("*")), (
        "a clean checkout wrote a set-aside record, which reads as a heal that never "
        "happened")


def test_a_set_aside_that_cannot_be_written_refuses_with_the_dirty_gate(tmp_path):
    """The one refusal left, and the reason it is one."""
    repo, state = _repo(tmp_path), tmp_path / "state"
    state.mkdir()
    target = repo / "app" / "routes" / "admin_sekolah.py"
    target.write_text("the box's hotfix\n", encoding="utf-8")
    # `mkdir -p` onto a path that is a file fails, which is the shape of "the record
    # cannot be written" without needing a read-only filesystem.
    (state / "set-aside").write_text("in the way\n", encoding="utf-8")

    done = _run(tmp_path, repo, state, ["app/routes/admin_sekolah.py"])

    assert done.returncode == 4, done.stdout
    assert "FINISHED" not in done.stdout, "it refused and then merged anyway"
    assert (state / "preflight-gate").read_text(encoding="utf-8").strip() == "dirty_checkout"
    assert target.read_text(encoding="utf-8") == "the box's hotfix\n", (
        "it cleared a tree whose version it could not record")


def test_a_refusal_is_recorded_so_the_next_tick_knows_it_repeated(tmp_path):
    """The memory that makes a loop visible. One refusal, written down with the paths
    it was about — that is all the next tick needs to know it is the same problem."""
    repo, state = _repo(tmp_path), tmp_path / "state"
    state.mkdir()
    (repo / "app" / "routes" / "admin_sekolah.py").write_text("the box's hotfix\n",
                                                              encoding="utf-8")
    (state / "set-aside").write_text("in the way\n", encoding="utf-8")

    done = _run(tmp_path, repo, state, ["app/routes/admin_sekolah.py"])

    assert done.returncode == 4, done.stdout
    assert (state / "refusal-streak").read_text(encoding="utf-8").strip() == "1", (
        "the refusal was not counted, so the next tick cannot tell a loop from a "
        "first attempt")
    assert (state / "refusal-streak.paths").read_text(encoding="utf-8") == (
        "app/routes/admin_sekolah.py\n"), "the streak does not say what it is about"


def test_a_streak_file_with_carriage_returns_is_still_the_same_refusal(tmp_path):
    """The comparison is byte for byte, and these are files a person can look at: a
    `\r\n` would make every refusal a first refusal — a memory that is a no-op while
    looking like it works."""
    repo, state = _repo(tmp_path), tmp_path / "state"
    state.mkdir()
    target = repo / "app" / "routes" / "admin_sekolah.py"
    target.write_text("the box's hotfix\n", encoding="utf-8")
    (state / "set-aside").write_text("in the way\n", encoding="utf-8")
    (state / "refusal-streak").write_text("3\r\n", encoding="utf-8", newline="")
    (state / "refusal-streak.paths").write_text("app/routes/admin_sekolah.py\r\n",
                                                encoding="utf-8", newline="")
    fallback = tmp_path / "run" / "set-aside"

    done = _run(tmp_path, repo, state, ["app/routes/admin_sekolah.py"],
                tail=('REFUSAL_FALLBACK_DIR=' + shlex.quote(fallback.as_posix()) + '\n'
                      'DIRTY=$(git -C "$REPO" status --porcelain)\n'
                      f'CHANGED={shlex.quote("app/routes/admin_sekolah.py")}\n'
                      'local_edits_heal\n'
                      "printf 'FINISHED\\n'\n"))

    assert done.returncode == 0, done.stderr
    assert "FINISHED" in done.stdout, (
        f"a carriage return in the streak turned a loop into a first attempt: {done.stdout}")
    assert target.read_text(encoding="utf-8") == "the release's line\n"


def test_the_same_refusal_a_few_ticks_running_sets_the_edit_aside_anyway(tmp_path):
    """The point of the whole block: a refusal that has repeated is answered by doing
    the heal a different way, not by refusing again."""
    repo, state = _repo(tmp_path), tmp_path / "state"
    state.mkdir()
    target = repo / "app" / "routes" / "admin_sekolah.py"
    target.write_text("the box's hotfix\n", encoding="utf-8")
    # The preferred home is un-creatable, which is the shape of the reported stall,
    # and the streak says the same refusal has already been made the threshold.
    (state / "set-aside").write_text("in the way\n", encoding="utf-8")
    # `newline` because this models what the runner's own run wrote: the comparison
    # is byte for byte, and a fixture that silently carried `\r\n` would be testing
    # the harness's line endings rather than the code's.
    (state / "refusal-streak").write_text("3\n", encoding="utf-8", newline="\n")
    (state / "refusal-streak.paths").write_text("app/routes/admin_sekolah.py\n",
                                                encoding="utf-8", newline="\n")
    fallback = tmp_path / "run" / "set-aside"

    done = _run(tmp_path, repo, state, ["app/routes/admin_sekolah.py"],
                tail=('REFUSAL_FALLBACK_DIR=' + shlex.quote(fallback.as_posix()) + '\n'
                      'DIRTY=$(git -C "$REPO" status --porcelain)\n'
                      f'CHANGED={shlex.quote("app/routes/admin_sekolah.py")}\n'
                      'local_edits_heal\n'
                      "printf 'FINISHED\\n'\n"))

    assert done.returncode == 0, f"it refused the case the streak exists for: {done.stderr}"
    assert "FINISHED" in done.stdout, done.stdout
    assert target.read_text(encoding="utf-8") == "the release's line\n", (
        "the tree was not restored, so the merge would still fail")
    assert _git(repo, "status", "--porcelain").stdout.strip() == ""
    kept = list(fallback.glob("*.txt"))
    assert kept, "the edit was set aside with no record of where it went"
    assert "the box's hotfix" in "".join(
        p.read_text(encoding="utf-8", errors="replace")
        for p in fallback.rglob("*") if p.is_file()), (
        "the box's version is nowhere in the fallback home, so the edit was lost")


def test_a_completed_heal_forgets_the_refusal(tmp_path):
    """Otherwise an edit that was set aside once would count toward the next one's
    threshold, and an unrelated stall would inherit a loop it never had.

    The count starts at two rather than absent, because a streak that was never
    written cannot show whether the heal cleared it: the assertion has to be about a
    count the run *forgot*, not one it never had.
    """
    repo, state = _repo(tmp_path), tmp_path / "state"
    state.mkdir()
    (repo / "app" / "routes" / "admin_sekolah.py").write_text("the box's hotfix\n",
                                                              encoding="utf-8")
    (state / "refusal-streak").write_text("2\n", encoding="utf-8", newline="\n")
    (state / "refusal-streak.paths").write_text("app/routes/admin_sekolah.py\n",
                                                encoding="utf-8", newline="\n")

    done = _run(tmp_path, repo, state, ["app/routes/admin_sekolah.py"])

    assert done.returncode == 0, done.stderr
    assert "FINISHED" in done.stdout, done.stdout
    assert not (state / "refusal-streak").exists(), (
        "a heal that completed left its refusal counted, so the next stall inherits a "
        "threshold it never earned")
    assert not (state / "refusal-streak.paths").exists()


def test_the_degraded_home_is_chosen_before_anything_is_set_aside():
    """The choice cannot be made half-way: a set-aside that is part-done cannot be
    restarted, so the fallback has to be decided while the tree is still the box's."""
    block = DEPLOY_SH.read_text(encoding="utf-8").split(BOX_START, 1)[1].split(BOX_END, 1)[0]
    choose = block.index("\n  box_edits_choose_home\n")
    for late in ('mkdir -p "$BOX_EDITS_DIR_EFFECTIVE"', 'mv -f -- "$REPO/$path"'):
        assert block.index(late) > choose, (
            f"`{late}` can run before the home is chosen, so a half-done set-aside "
            "would be re-attempted somewhere else")


def test_only_the_newest_heals_keep_their_records(tmp_path):
    """A bounded record, like the refusal history: evidence, not an archive."""
    repo, state = _repo(tmp_path), tmp_path / "state"
    directory = state / "set-aside"
    directory.mkdir(parents=True)
    for index in range(8):
        stamp = f"2026090{index}T000000Z-{'a' * 12}-1"
        (directory / f"{stamp}.txt").write_text("a heal\n", encoding="utf-8")
        (directory / f"{stamp}.patch").write_text("a patch\n", encoding="utf-8")
        os.utime(directory / f"{stamp}.txt", (1_700_000_000 + index,) * 2)

    done = _run(tmp_path, repo, state, [], tail="box_edits_prune\nprintf 'FINISHED\\n'\n")

    assert done.returncode == 0, done.stderr
    assert len(list(directory.glob("*.txt"))) == 5, (
        f"the record grew without a bound: {len(list(directory.glob('*.txt')))} kept")
    assert not (directory / "20260900T000000Z-aaaaaaaaaaaa-1.patch").exists(), (
        "a pruned record left its patch behind")


# ── 1b. an edit nobody is coming back for ───────────────────────────────────
#
# The overlap rule answers "would this merge clobber it". It cannot answer the other
# question a dirty checkout raises — is anybody still working on it — and the box that
# reported this is the shape of that gap: one hand edit to a file no release writes,
# so nothing clobbers it, so nothing sets it aside, and nothing ever will, however
# long the box carries it. An edit old enough that no one is coming back for it is set
# aside too, and the record says that is why rather than letting it read as an overlap.

def _older_than_the_threshold(repo: Path, rel: str) -> None:
    """Back-date one path's own edit. The mtime is the clock the rule reads, so this is
    how a test makes an edit old without waiting a day for one to become old."""
    done = subprocess.run(["touch", "-d", "2020-01-01 00:00:00", str(repo / rel)],
                          capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr


def test_an_edit_older_than_the_threshold_is_set_aside_without_an_overlap(tmp_path):
    """The case the overlap rule cannot reach: the release writes nothing this box
    changed, so nothing would clobber it — and the box must still not carry an
    abandoned change forever."""
    repo, state = _repo(tmp_path), tmp_path / "state"
    state.mkdir()
    (repo / "README.md").write_text("the box's own note\n", encoding="utf-8")
    _older_than_the_threshold(repo, "README.md")

    done = _run(tmp_path, repo, state, [])

    assert done.returncode == 0, done.stderr
    assert "FINISHED" in done.stdout, done.stdout
    assert "stale" in done.stdout, (
        f"an old edit was set aside without the run saying why: {done.stdout}")
    record = _records(state)[0]
    text = record.read_text(encoding="utf-8")
    assert "set-aside README.md" in text, (
        "the box's version is not named, so the page cannot say where it went")
    assert "stale README.md" in text, (
        "the record does not say the edit was old rather than overlapping — those two "
        "need different answers from whoever reads the card")
    assert "kept README.md" not in text
    # Preserved and not lost: the diff holds the box's line, and the tree no longer does.
    patch = record.with_suffix(".patch")
    assert "the box's own note" in patch.read_text(encoding="utf-8"), (
        "nothing was preserved, so the edit exists nowhere")
    assert (repo / "README.md").read_text(encoding="utf-8") == "readme\n", (
        "the path was not restored, so the box is still dirty")
    assert _git(repo, "status", "--porcelain").stdout.strip() == "", (
        "the tree is still dirty, so this box has not fetched cleanly after the heal")


def test_an_edit_younger_than_the_threshold_is_still_left_alone(tmp_path):
    """A day is the point of the number: an edit somebody made this morning is not
    abandoned, and the runner must not clear a tree someone is working in."""
    repo, state = _repo(tmp_path), tmp_path / "state"
    state.mkdir()
    (repo / "README.md").write_text("this morning's note\n", encoding="utf-8")

    done = _run(tmp_path, repo, state, [])

    assert done.returncode == 0, done.stderr
    assert (repo / "README.md").read_text(encoding="utf-8") == "this morning's note\n", (
        "a fresh local edit was set aside")
    text = _records(state)[0].read_text(encoding="utf-8")
    assert "kept README.md" in text and "set-aside README.md" not in text
    assert "stale README.md" not in text


def test_a_path_with_nothing_to_date_is_not_stale(tmp_path):
    """A deletion leaves no file to ask, and an edit that cannot be dated is not an
    edit that is old: this block never acts on a reading that failed."""
    repo, state = _repo(tmp_path), tmp_path / "state"
    state.mkdir()
    (repo / "README.md").unlink()

    done = _run(tmp_path, repo, state, [])

    assert done.returncode == 0, done.stderr
    assert not (repo / "README.md").exists(), (
        "a deletion was restored on the strength of a date it does not have")
    assert "kept README.md" in _records(state)[0].read_text(encoding="utf-8"), (
        "the deletion was not reported at all")


def test_an_overlapping_edit_is_not_recorded_as_stale(tmp_path):
    """The release writing the path is the stronger reason, and naming both would make
    the record ambiguous about why the box's version is gone."""
    repo, state = _repo(tmp_path), tmp_path / "state"
    state.mkdir()
    (repo / "app" / "routes" / "admin_sekolah.py").write_text("the box's hotfix\n",
                                                              encoding="utf-8")
    _older_than_the_threshold(repo, "app/routes/admin_sekolah.py")

    done = _run(tmp_path, repo, state, ["app/routes/admin_sekolah.py"])

    assert done.returncode == 0, done.stderr
    text = _records(state)[0].read_text(encoding="utf-8")
    assert "set-aside app/routes/admin_sekolah.py" in text
    assert "stale app/routes/admin_sekolah.py" not in text, (
        "an overlapping path was recorded as stale as well")


def test_an_unchanged_dirty_set_is_not_noted_again_every_tick(tmp_path):
    """Otherwise a box with one local edit writes a record every two minutes, and
    within a day the newest few the page reads are all the same note — pushing the
    record of something that *was* preserved off the card, which is the opposite of
    what this directory is for."""
    repo, state = _repo(tmp_path), tmp_path / "state"
    state.mkdir()
    (repo / "README.md").write_text("this morning's note\n", encoding="utf-8")

    first = _run(tmp_path, repo, state, [])
    assert first.returncode == 0, first.stderr
    assert len(_records(state)) == 1, first.stdout

    second = _run(tmp_path, repo, state, [])

    assert second.returncode == 0, second.stderr
    assert len(_records(state)) == 1, (
        f"the same dirty set was noted {len(_records(state))} times, so the notes crowd "
        "out every record that has something preserved to name")


def test_the_staleness_threshold_is_one_declared_constant():
    """A day, declared once, and never shorter than a working session: the rule stops
    the box carrying an *abandoned* edit, so a shorter threshold would clear a tree
    somebody is still working in."""
    block = _box_block()
    match = re.search(r"^BOX_EDITS_STALE_SECONDS=(\d+)$", block, re.M)
    assert match, "the staleness threshold is not one declared constant"
    assert int(match.group(1)) >= 12 * 3600, (
        f"{match.group(1)} seconds mistakes an edit still being written for an "
        "abandoned one")
    assert re.search(r"\$BOX_EDITS_STALE_SECONDS", block), "the constant is never read"


# ── 2. the two properties a single run cannot show ──────────────────────────


def test_the_heal_runs_after_the_release_s_file_list_is_known():
    """The whole point: the decision needs `CHANGED`, which needs the fetch."""
    script = DEPLOY_SH.read_text(encoding="utf-8")
    changed = script.index('CHANGED=$(as_owner git -C "$REPO" diff --name-only')
    heal = script.index("\nlocal_edits_heal\n")
    assert heal > changed, (
        "the heal runs before the release's file list exists, so it cannot know what "
        "would be clobbered")


def test_a_local_change_no_longer_refuses_before_the_fetch():
    """The deadlock, stated as the two lines that must not come back."""
    script = DEPLOY_SH.read_text(encoding="utf-8")
    region = script[script.index("# ── Guard against clobbering hand edits"):
                    script.index("# ── Fetch")]
    assert "checkout_unreadable" in region, "an unreadable checkout must still refuse"
    assert "dirty_checkout" not in region, (
        "a local change still refuses before the fetch — which is the deadlock: the "
        "box cannot deploy, so it cannot receive the release that would let it")


def test_the_record_is_written_before_anything_is_restored():
    """A checkout that fails afterwards fails with the evidence already on disk.

    *Every* bare call has to precede the restore, not merely the first one. The block
    writes a record on two paths — nothing overlapped, and something did — and it was
    the first of those, the one taken when nothing moves, that let a restore placed
    before its own record read as a pass: the mutation survived the guard until this
    checked the last call rather than the earliest.
    """
    block = _box_block()
    # The bare call, not the `box_edits_record() {` definition above it: a helper here
    # takes no arguments, so the call is the line that is nothing but its own name.
    calls = list(re.finditer(r"^\s+box_edits_record\s*$", block, re.M))
    assert calls, "the heal never writes its record"
    restore = block.index('as_owner git -C "$REPO" checkout HEAD --')
    late = [match for match in calls if match.start() > restore]
    assert not late, (
        "a record is written after the paths are restored, so a checkout that fails "
        "in between fails with nothing on disk saying what was set aside")


def test_the_section_is_its_own_delimited_block():
    block = _box_block()
    for fn in ("local_edits_heal() {", "box_edits_record() {", "box_edits_prune() {",
               "box_edits_refuse() {"):
        assert fn in block, f"{fn} left the delimited block"
