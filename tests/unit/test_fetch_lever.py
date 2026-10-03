"""The recovery lever arrives by the fetch, not by a release.

`sgfix` is the one-word way out of a stuck box, and until this change the only thing
that installed it was `refresh_installed_launchers` — the last step of a release that
passed every gate. The box that needs the lever is the box whose release is being
refused, so the tool was reachable exactly where it was not needed. The heal that
teaches the runner to set a box-local edit aside travels the same way, which is how a
box can sit for a day refusing *before its own fetch*: the commit carrying the heal
is held by the refusal the heal exists to clear.

The fetch is the way out, and it is the only step with the property needed — it
writes refs and never the tree, so it succeeds on a checkout that is dirty, rolled
back, held by a quarantine or refused by a gate. `materialise_lever_from_origin`
reads the two files the lever is out of `origin/$BRANCH` and installs the name from
there, so a box that can never land a release can still obtain the tool that ends a
console session.

Two properties carry the whole thing, and both are *run* rather than read:

* the lever lands **without the checkout moving and without a merge** — the fixture's
  working tree is dirty in exactly the shape the journal shows (`M
  app/routes/admin_sekolah.py`), so a materialiser that touched the tree, or that
  needed a merge to have happened, fails here;
* a second tick **writes nothing** — the same rule the launcher refresh holds,
  because an mtime that moves while the content does not is a signal that lies.

The honesty guards are the rest, and each one is a way this block could be plausible
and wrong: the blobs must come from the *fetched commit* rather than the checkout's
working files (the fixture's origin carries a newer `entrypoint.sh` than the checkout,
and the checkout has no `scangrade-recover.sh` at all), both files are parsed before
either lands, the install is a rename inside the target's own directory so a running
lever keeps the inode it started with, and the block contains no merge, no reset, no
checkout and no service command — it is a read and two writes, which is why it is safe
to run on every tick before any gate has spoken.
"""
from __future__ import annotations

import pathlib
import shlex
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
RUNNER = ROOT / "deploy" / "scangrade-deploy.sh"
ENTRYPOINT = ROOT / "deploy" / "entrypoint.sh"
BASH = shutil.which("bash")

LEVER_START = "# fetch-lever-logic:start"
LEVER_END = "# fetch-lever-logic:end"
REFRESH_START = "# refresh-launcher-logic:start"
REFRESH_END = "# refresh-launcher-logic:end"
#: The read that runs the materialiser before every arrangement refusal, so a box
#: stuck ahead of its own fetch still obtains the lever.
BRANCH_FIRST_START = "# branch-first-logic:start"
BRANCH_FIRST_END = "# branch-first-logic:end"

#: The lever origin carries and the checkout does not, and the sentinel that tells
#: origin's `entrypoint.sh` apart from the checkout's. A materialiser that read the
#: working tree instead of the fetched commit would install the checkout's template
#: and this marker would be missing from the installed launcher's source.
LEVER_MARKER = "#!/usr/bin/env bash\n# the lever, as origin has it\necho \"LEVER RAN $*\"\n"
TEMPLATE_SENTINEL = "# origin's template, not the checkout's\n"

pytestmark = pytest.mark.skipif(BASH is None, reason="needs a bash to run the block")


def _text(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def _block(source: str, start: str, end: str) -> str:
    assert start in source and end in source, (
        "the block's delimiters are what let it be run on its own, the way the "
        "quarantine's, the lock heal's and the box-edit heal's are; keep them")
    return source.split(start, 1)[1].split(end, 1)[0]


def _lever_block() -> str:
    return _block(_text(RUNNER), LEVER_START, LEVER_END)


def _lever_code() -> str:
    """The block with its comments removed, which is what the guards below read.

    A guard that reads prose fires on a sentence: this block's own header says it
    "merges nothing and resets nothing", and `reset` is a word the forbidden list
    looks for — so a guard over the raw text would light up on the comment that
    explains the property it is testing.
    """
    return "\n".join(ln for ln in _lever_block().splitlines()
                     if ln.strip() and not ln.strip().startswith("#"))


def _git(repo: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@example.com",
         "-c", "user.name=t", *args],
        capture_output=True, text=True, check=False)


def _bare_git(cwd: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(cwd), "-c", "user.email=t@example.com",
         "-c", "user.name=t", *args],
        capture_output=True, text=True, check=False)


class Box:
    """A checkout of a branch, with a bare `origin` it does not hold a lever from."""

    def __init__(self, root: pathlib.Path) -> None:
        self.repo = root / "repo"
        self.origin = root / "origin.git"
        self.stage = root / "stage"
        self.lever = root / "lever"

    # ── the box's own checkout, pushed as the branch origin starts from ──────
    def make_checkout(self) -> None:
        (self.repo / "deploy").mkdir(parents=True)
        (self.repo / "app" / "routes").mkdir(parents=True)
        (self.repo / "deploy" / "entrypoint.sh").write_bytes(ENTRYPOINT.read_bytes())
        (self.repo / "app" / "routes" / "admin_sekolah.py").write_text(
            "the release's line\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.repo)],
                       capture_output=True, text=True)
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", "init")
        subprocess.run(["git", "init", "-q", "--bare", str(self.origin)],
                       capture_output=True, text=True)
        _git(self.repo, "remote", "add", "origin", str(self.origin))
        _git(self.repo, "push", "-q", "-u", "origin", "main")

    def push_lever(self, lever: str = LEVER_MARKER,
                   template_suffix: str = TEMPLATE_SENTINEL) -> None:
        """A commit on origin carrying the lever the checkout cannot have."""
        if not self.stage.exists():
            subprocess.run(["git", "clone", "-q", str(self.origin), str(self.stage)],
                           capture_output=True, text=True)
        (self.stage / "deploy").mkdir(parents=True, exist_ok=True)
        (self.stage / "deploy" / "scangrade-recover.sh").write_text(
            lever, encoding="utf-8", newline="\n")
        entry = self.stage / "deploy" / "entrypoint.sh"
        if template_suffix:
            entry.write_text(entry.read_text(encoding="utf-8") + template_suffix,
                             encoding="utf-8", newline="\n")
        _bare_git(self.stage, "add", "-A")
        _bare_git(self.stage, "commit", "-q", "-m", "the lever")
        _bare_git(self.stage, "push", "-q", "origin", "main")

    def fetch_and_dirty(self) -> str:
        """What a tick does: fetch, and be left dirty in a path the release writes."""
        _git(self.repo, "fetch", "-q", "origin", "main")
        (self.repo / "app" / "routes" / "admin_sekolah.py").write_text(
            "a hand edit the release also touches\n", encoding="utf-8")
        return _git(self.repo, "status", "--porcelain").stdout


def _harness(tmp_path: pathlib.Path, box: Box) -> str:
    bins = tmp_path / "installed"
    bins.mkdir(exist_ok=True)
    return (
        "set -uo pipefail\n"
        f"REPO={shlex.quote(box.repo.as_posix())}\n"
        "BRANCH=\"main\"\n"
        f"LEVER_DIR={shlex.quote(box.lever.as_posix())}\n"
        f"INSTALLED_BIN_DIR={shlex.quote(bins.as_posix())}\n"
        "INSTALLED_RUNNER=\"$INSTALLED_BIN_DIR/scangrade-deploy\"\n"
        "INSTALLED_SNAPSHOT=\"$INSTALLED_BIN_DIR/scangrade-db-snapshot\"\n"
        "INSTALLED_RECOVER=\"$INSTALLED_BIN_DIR/sgfix\"\n"
        # The runner acts as the checkout's owner through `as_owner`; the harness is
        # that owner.
        'as_owner() { "$@"; }\n'
        'log() { echo "$*"; }\n'
        + _block(_text(RUNNER), REFRESH_START, REFRESH_END)
        + _block(_text(RUNNER), LEVER_START, LEVER_END)
    )


def _run(tmp_path: pathlib.Path, box: Box,
         body: str = "materialise_lever_from_origin") -> subprocess.CompletedProcess:
    script = tmp_path / "harness.sh"
    script.write_text(_harness(tmp_path, box) + "\n" + body + "\n",
                      encoding="utf-8", newline="\n")
    return subprocess.run([BASH, str(script)], capture_output=True, text=True,
                          check=False)


def _box(tmp_path: pathlib.Path) -> tuple[Box, str]:
    box = Box(tmp_path)
    box.make_checkout()
    box.push_lever()
    dirty = box.fetch_and_dirty()
    return box, dirty


# ── 1. what it is for: a box that cannot release still gets the lever ────────

def test_a_dirty_checkout_whose_origin_carries_a_lever_gets_one(tmp_path):
    box, dirty = _box(tmp_path)
    assert dirty.strip(), "the fixture must be dirty, or it proves nothing"
    assert not (box.repo / "deploy" / "scangrade-recover.sh").exists(), (
        "the checkout must not already hold the lever: this is the box that "
        "predates it, and the point is that the fetch brings it")

    ran = _run(tmp_path, box)
    assert ran.returncode == 0, ran.stderr

    lever = box.lever / "deploy" / "scangrade-recover.sh"
    assert lever.exists(), (
        "a box that cannot merge did not get a lever — the fetch is the only step "
        "that succeeds on it, so this is where the lever has to come from")
    assert lever.read_text(encoding="utf-8") == LEVER_MARKER


def test_it_leaves_the_checkout_exactly_where_it_was(tmp_path):
    """A materialiser that needed a merge, or that wrote into the checkout, would
    either fail here or leave the tree dirtier than it found it — and the dirty file
    is precisely what the runner is about to set aside."""
    box, dirty = _box(tmp_path)
    _run(tmp_path, box)
    after = _git(box.repo, "status", "--porcelain").stdout
    assert after == dirty, "the materialiser moved the checkout"
    assert not (box.repo / "deploy" / "scangrade-recover.sh").exists(), (
        "the lever was written into the checkout, which dirties the very tree the "
        "release has to merge into")


def test_the_installed_word_runs_the_fetched_lever_not_the_checkouts(tmp_path):
    """End to end, and the assertion that matters on a real console: typing the
    installed name reaches the lever that came out of the fetched commit."""
    box, _ = _box(tmp_path)
    _run(tmp_path, box)
    sgfix = tmp_path / "installed" / "sgfix"
    assert sgfix.exists(), "the one-word name was not installed"
    assert "@REPO@" not in sgfix.read_text(encoding="utf-8"), (
        "an unrendered launcher reached the PATH")
    ran = subprocess.run([BASH, str(sgfix), "--dry-run"], capture_output=True,
                         text=True, check=False)
    assert "LEVER RAN --dry-run" in ran.stdout, (
        f"the installed name did not reach the fetched lever: {ran.stdout!r} "
        f"{ran.stderr!r}")


def test_the_blobs_come_from_the_fetched_commit_not_the_working_tree(tmp_path):
    """The checkout's `entrypoint.sh` and origin's differ here, so installing the
    checkout's would be visible — and the checkout has no lever to install at all."""
    box, _ = _box(tmp_path)
    _run(tmp_path, box)
    installed = (box.lever / "deploy" / "entrypoint.sh").read_text(encoding="utf-8")
    assert TEMPLATE_SENTINEL in installed, (
        "the installed template is the checkout's, not the fetched commit's")
    assert TEMPLATE_SENTINEL not in (
        box.repo / "deploy" / "entrypoint.sh").read_text(encoding="utf-8")


def test_it_replaces_a_lever_left_from_an_older_fetch(tmp_path):
    box, _ = _box(tmp_path)
    stale = box.lever / "deploy" / "scangrade-recover.sh"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text("#!/usr/bin/env bash\n# yesterday's lever\n", encoding="utf-8")
    _run(tmp_path, box)
    assert stale.read_text(encoding="utf-8") == LEVER_MARKER, (
        "a stale lever survived a fetch that carried a newer one")


def test_a_second_tick_writes_nothing(tmp_path):
    """The repo's rule for the launcher refresh, for the same reason: an mtime that
    moves while the content does not is a signal that lies."""
    box, _ = _box(tmp_path)
    _run(tmp_path, box)
    lever = box.lever / "deploy" / "scangrade-recover.sh"
    sgfix = tmp_path / "installed" / "sgfix"
    before = (lever.stat().st_mtime_ns, sgfix.stat().st_mtime_ns)

    again = _run(tmp_path, box)
    after = (lever.stat().st_mtime_ns, sgfix.stat().st_mtime_ns)
    assert after == before, "the second tick rewrote a lever that had not changed"
    assert "installed from origin" not in again.stdout, (
        "the second tick reported an install it did not make")


# ── 2. what it must refuse ───────────────────────────────────────────────────

def test_a_lever_that_does_not_parse_never_replaces_the_installed_one(tmp_path):
    """The lever runs as root on a box already in trouble, so a lever that cannot
    run is the one failure there is no room for."""
    box, _ = _box(tmp_path)
    _run(tmp_path, box)
    lever = box.lever / "deploy" / "scangrade-recover.sh"
    sgfix = tmp_path / "installed" / "sgfix"
    good = lever.read_bytes()

    box.push_lever(lever="#!/usr/bin/env bash\nif then fi\n", template_suffix="")
    fetched = _git(box.repo, "fetch", "-q", "origin", "main")
    assert fetched.returncode == 0, (
        f"the broken lever never reached origin, so nothing was tested: "
        f"{fetched.stderr}")
    assert _git(box.repo, "show", "origin/main:deploy/scangrade-recover.sh").stdout == (
        "#!/usr/bin/env bash\nif then fi\n"), "origin still carries the good lever"

    ran = _run(tmp_path, box)
    assert lever.read_bytes() == good, (
        "a lever that does not parse was installed over a working one")
    assert "does not parse" in ran.stdout, "the refusal was silent"
    reached = subprocess.run([BASH, str(sgfix), "--dry-run"], capture_output=True,
                             text=True, check=False)
    assert "LEVER RAN --dry-run" in reached.stdout, (
        "the installed word stopped working when origin carried a broken lever")


def test_a_fetch_without_the_lever_leaves_the_installed_one_alone(tmp_path):
    """Every box older than the lever is in exactly this state on its first tick."""
    box = Box(tmp_path)
    box.make_checkout()          # origin's only commit has no lever in it
    box.fetch_and_dirty()
    ran = _run(tmp_path, box)
    assert ran.returncode == 0
    assert not (box.lever / "deploy" / "scangrade-recover.sh").exists(), (
        "a lever was conjured out of a commit that does not carry one")
    assert not (tmp_path / "installed" / "sgfix").exists(), (
        "the name was installed pointing at a lever that is not there")


# ── 3. the wiring: the fetch carries it, and the release still refreshes it ──

def test_the_honesty_guards_hold_over_the_block_with_its_comments_removed():
    code = _lever_code()
    for banned in ("merge", "reset", "checkout", "pull", "systemctl"):
        assert banned not in code, (
            f"the materialiser talks about `{banned}` — it is a read of the fetched "
            "commit and two writes, and anything else belongs in a release")
    assert "bash -n" in code, "nothing parses the fetched lever before installing it"
    assert "mv -f" in code, "the install is not a rename"
    assert code.index("bash -n") < code.index("mv -f"), (
        "the install happens before the parse — a lever that cannot run would land")
    assert 'origin/$BRANCH:deploy/entrypoint.sh' in code
    assert 'origin/$BRANCH:deploy/scangrade-recover.sh' in code


def test_it_runs_after_the_fetch_and_before_anything_is_decided():
    """The position *is* the feature: after the fetch, because only then does
    `origin/$BRANCH` name anything; before the nothing-new exit, because an
    up-to-date box whose lever is missing is exactly a box that needs one; and long
    before the merge, which is the step a stuck box cannot reach.

    Three call sites now: the main one inside that window; the one in
    `branch_read_refs`, which materialises from a fetch that happens before every
    arrangement refusal; and the one a `recover` plan runs, which is how the pipeline
    reaches a box with no console and no release. The second is the whole point of
    branch-first-logic — a box whose runner refuses before its own fetch still ends up
    holding the newest lever — and it necessarily sits *before* the release's fetch. So
    is the third, which is why the release's own call is located from the fetch rather
    than from the top of the file.
    """
    script = _text(RUNNER)
    assert script.count("materialise_lever_from_origin") == 4, (
        "expected the definition and exactly three call sites (the branch read, the "
        "release's own, and the control plan's recover)")
    fetch = script.index('RUN_STEP="fetch"')
    call = script.index("\nmaterialise_lever_from_origin\n", fetch)
    nothing_new = script.index('if [ "$BEFORE" = "$AFTER" ]; then')
    merge = script.index('RUN_STEP="merge"')
    assert fetch < call < nothing_new < merge, (
        "the lever is materialised outside the window that makes it reachable on a "
        "box whose release is refused")
    # And the plan's copy, which is what the pipeline reaches a stuck box with.
    recover = script.index('      materialise_lever_from_origin\n')
    assert recover < fetch, (
        "`recover` materialises the lever after the release fetch, so a plan obeyed "
        "before the release decision would find no lever to run")
    # And the branch read's copy, ahead of the release fetch it is meant to survive.
    branch_call = script.index("  materialise_lever_from_origin\n",
                               script.index(BRANCH_FIRST_START))
    assert branch_call < script.index("REFUSING") < fetch, (
        "the branch read does not materialise before the arrangement refusals, so a "
        "box stuck before its own fetch still never receives the lever")


def test_the_installed_name_is_rendered_against_the_fetched_tree():
    """Rendered against `$LEVER_DIR`, not `$REPO`: the checkout's copy is the one
    thing a stuck box cannot update, so pointing the lever at it would hand back the
    stale tool the fetch had just beaten."""
    refresh = _block(_text(RUNNER), REFRESH_START, REFRESH_END)
    assert 'LAUNCHER_TEMPLATE="$LEVER_DIR/deploy/entrypoint.sh"' in refresh, (
        "the lever is not rendered from the tree the fetch materialised")
    assert 'LAUNCHER_ROOT="$LEVER_DIR"' in refresh
    assert ('LAUNCHER_TARGET="$INSTALLED_RUNNER" LAUNCHER_LABEL="the deploy runner"'
            in refresh), "the runner's own launcher no longer renders from the checkout"
    assert ('LAUNCHER_TARGET="$INSTALLED_SNAPSHOT" '
            'LAUNCHER_LABEL="the snapshot command"' in refresh)


