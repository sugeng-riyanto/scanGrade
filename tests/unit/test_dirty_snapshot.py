"""The suite must still be runnable when the checkout is too dirty to build.

`app/utils/checkout_integrity.py` refuses to construct the app once a checkout
holds more than its scan cap of modified tracked files, and that refusal is
correct for a box that serves. On a laptop with several features in flight it
means the suite cannot run at all — so the change that would prove "green"
cannot be measured, and the workaround was a hand-typed `git worktree add` plus a
`cp -f` that differed every time and quietly dropped a new test file.

`deploy/dirty_snapshot.py` materialises the working tree as one commit in a
linked scratch worktree. These tests hold it to the three properties that make
that useful and not merely convenient:

* **the tree it produces constructs** — the cap that refused the source does not
  refuse the snapshot, which is the whole point;
* **it is faithful** — an untracked file (the new guard) and a deletion both
  travel, because a snapshot that carries only tracked edits is the exact trap
  `git stash create` sets;
* **it only reads the source** — the checkout that was too dirty to build is
  exactly as dirty afterwards, so the tool cannot be the thing that loses the
  work it was invoked to preserve.

At HEAD this file does not import: `deploy/dirty_snapshot.py` does not exist.
"""

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.utils import checkout_integrity  # noqa: E402
from tests.unit import git_env as suite_git_env  # noqa: E402
from tests.unit.git_env import git_env  # noqa: E402

SCRIPT = ROOT / "deploy" / "dirty_snapshot.py"
GIT = shutil.which("git")


def _load_tool():
    """Import deploy/dirty_snapshot.py without making deploy/ a package."""
    spec = importlib.util.spec_from_file_location("dirty_snapshot", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["dirty_snapshot"] = module
    spec.loader.exec_module(module)
    return module


tool = _load_tool()

pytestmark = pytest.mark.skipif(GIT is None, reason="needs git to make a worktree")


def _git(repo: Path, *args: str, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@example.com", "-c",
         "user.name=t", *args],
        capture_output=True, check=check, text=True, env=git_env())


def _repo(tmp_path: Path, files: int = 3) -> Path:
    """A checkout whose every tracked file can be dirtied on demand."""
    repo = tmp_path / "repo"
    (repo / "app").mkdir(parents=True)
    (repo / "app" / "clean.py").write_text("x = 1\n", encoding="utf-8")
    for i in range(files):
        (repo / "app" / f"f{i}.py").write_text("v = 0\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], capture_output=True,
                   check=False, env=git_env())
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


def _dirty(repo: Path, files: int = 3) -> None:
    for i in range(files):
        (repo / "app" / f"f{i}.py").write_text("v = 1\n", encoding="utf-8")


def _repo_with_dependencies(tmp_path: Path) -> tuple[Path, Path]:
    """A checkout with an installed dependency tree, ignored the way the real
    `.gitignore` ignores it. The ignore rule is the point: it is what keeps
    `--exclude-standard` from *copying* `node_modules` into the snapshot, which is
    the case the link exists for."""
    repo = _repo(tmp_path)
    (repo / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
    nm = repo / "node_modules"
    nm.mkdir()
    (nm / "tailwind").write_text("#!" + os.linesep, encoding="utf-8")
    return repo, nm


# ── 1. the cap, lifted ───────────────────────────────────────────────────────

class TestItLiftsTheCapThatRefusedTheSource:
    def test_the_cap_refuses_before_and_the_snapshot_constructs(self, tmp_path):
        """The headline: what refused the source does not refuse the snapshot.

        The source is dirtied past the default cap so the refusal really is the
        cap (`more than 64`), not a measured blob — the state this tool exists
        for, and the one a hand-typed worktree had to be re-invented for.
        """
        repo = _repo(tmp_path, files=checkout_integrity.SCAN_LIMIT + 6)
        _dirty(repo, files=checkout_integrity.SCAN_LIMIT + 6)

        refused = checkout_integrity.unreproducible_reason(repo)
        assert refused and f"more than {checkout_integrity.SCAN_LIMIT}" in refused, (
            "the fixture does not reproduce the situation: the source is not over "
            f"the cap, so there is nothing for the snapshot to lift\n{refused}")

        snap, commit = tool.snapshot(repo, tmp_path / "snap")
        assert commit, "an over-cap source always has something to commit"
        assert checkout_integrity.unreproducible_reason(snap) is None, (
            "the snapshot is as dirty as the source, so it cannot stand in for a "
            "checkout that constructs — the tool did not do its one job")
        # The edits are really there, not merely "no longer counted".
        assert (snap / "app" / "f0.py").read_text(encoding="utf-8") == "v = 1\n"


# ── 2. faithful ──────────────────────────────────────────────────────────────

class TestTheSnapshotIsFaithful:
    def test_an_untracked_guard_travels(self, tmp_path):
        """`git stash create` is the trap this avoids: new files only live in the
        working tree, and a snapshot without them would run the old suite."""
        repo = _repo(tmp_path)
        (repo / "app" / "new_guard.py").write_text("guard = True\n", encoding="utf-8")

        snap, _ = tool.snapshot(repo, tmp_path / "snap")
        assert (snap / "app" / "new_guard.py").read_text(encoding="utf-8") == "guard = True\n"

    def test_a_deletion_travels(self, tmp_path):
        repo = _repo(tmp_path)
        (repo / "app" / "clean.py").unlink()

        snap, _ = tool.snapshot(repo, tmp_path / "snap")
        assert not (snap / "app" / "clean.py").exists(), (
            "a file the worktree deleted is still in the snapshot, so the suite "
            "would test code the checkout no longer has")

    def test_an_ignored_file_stays_behind(self, tmp_path):
        """.env/.venv are not the code; copying them would drag a box's secrets
        and its 400 MB virtualenv into every snapshot."""
        repo = _repo(tmp_path)
        (repo / ".gitignore").write_text(".env\n", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "ignore .env")
        (repo / ".env").write_text("SECRET=1\n", encoding="utf-8")

        snap, _ = tool.snapshot(repo, tmp_path / "snap")
        assert not (snap / ".env").exists()

    def test_the_source_is_only_read(self, tmp_path):
        """The checkout that was too dirty to build is just as dirty after.

        A tool for preserving work must not be the thing that loses it, and the
        temptation to `git stash` the source is exactly how it would.
        """
        repo = _repo(tmp_path, files=checkout_integrity.SCAN_LIMIT + 6)
        _dirty(repo, files=checkout_integrity.SCAN_LIMIT + 6)
        before = _git(repo, "status", "--porcelain", "--untracked-files=no").stdout

        tool.snapshot(repo, tmp_path / "snap")
        after = _git(repo, "status", "--porcelain", "--untracked-files=no").stdout
        assert sorted(before.splitlines()) == sorted(after.splitlines())
        assert checkout_integrity.unreproducible_reason(repo) is not None, (
            "the source still refuses to construct, as it must — the tool lifted "
            "the cap for the *snapshot*, not by mutating the checkout")


# ── 3. committed, and clean ──────────────────────────────────────────────────

class TestItCommits:
    def test_the_snapshot_is_clean_and_has_its_own_commit(self, tmp_path):
        repo = _repo(tmp_path)
        (repo / "app" / "new_guard.py").write_text("guard = True\n", encoding="utf-8")
        (repo / "app" / "f0.py").write_text("v = 2\n", encoding="utf-8")

        snap, commit = tool.snapshot(repo, tmp_path / "snap")
        assert commit, "the snapshot made no commit, so it is not a snapshot"
        assert _git(snap, "status", "--porcelain", "--untracked-files=no").stdout.strip() == "", (
            "the snapshot is dirty, which is the state it exists to avoid")
        subject = _git(snap, "log", "-1", "--format=%s").stdout
        assert subject.startswith("snapshot of the working tree"), subject
        # A machine capture must not depend on the local git identity.
        author = _git(snap, "log", "-1", "--format=%an <%ae>").stdout.strip()
        assert author == f"{tool.COMMIT_NAME} <{tool.COMMIT_EMAIL}>", author

    def test_a_clean_tree_keeps_head_with_no_commit(self, tmp_path):
        """Nothing to snapshot is not a failure — the worktree is HEAD, clean."""
        repo = _repo(tmp_path)
        head = _git(repo, "rev-parse", "HEAD").stdout.strip()

        snap, commit = tool.snapshot(repo, tmp_path / "snap")
        assert commit is None
        assert _git(snap, "rev-parse", "HEAD").stdout.strip() == head
        assert checkout_integrity.unreproducible_reason(snap) is None


# ── 4. refusals ──────────────────────────────────────────────────────────────

class TestRefusals:
    def test_a_path_that_is_not_a_checkout_is_refused(self, tmp_path):
        with pytest.raises(tool.SnapshotError):
            tool.snapshot(tmp_path / "nowhere", tmp_path / "snap")

    def test_the_checkout_itself_is_refused(self, tmp_path):
        repo = _repo(tmp_path)
        with pytest.raises(tool.SnapshotError):
            tool.snapshot(repo, repo)

    def test_a_directory_above_the_checkout_is_refused(self, tmp_path):
        """`--dir ..` would clear the tree it is meant to copy."""
        repo = _repo(tmp_path)
        with pytest.raises(tool.SnapshotError):
            tool.snapshot(repo, tmp_path)

    def test_a_foreign_directory_is_refused_rather_than_deleted(self, tmp_path):
        repo = _repo(tmp_path)
        foreign = tmp_path / "snap"
        foreign.mkdir()
        (foreign / "keep.txt").write_text("mine\n", encoding="utf-8")

        with pytest.raises(tool.SnapshotError):
            tool.snapshot(repo, foreign)
        assert (foreign / "keep.txt").exists(), (
            "the tool deleted a directory it did not create")


# ── 5. replacing and cleaning ────────────────────────────────────────────────

class TestReplaceAndClean:
    def test_it_replaces_its_own_snapshot(self, tmp_path):
        """Iterating on the suite is the use case, so a second run is the norm."""
        repo = _repo(tmp_path)
        dest = tmp_path / "snap"
        (repo / "app" / "f0.py").write_text("v = 1\n", encoding="utf-8")
        first, _ = tool.snapshot(repo, dest)

        (repo / "app" / "f0.py").write_text("v = 2\n", encoding="utf-8")
        second, _ = tool.snapshot(repo, dest)
        assert first == second
        assert (dest / "app" / "f0.py").read_text(encoding="utf-8") == "v = 2\n"

    def test_clean_removes_the_worktree_and_its_registration(self, tmp_path):
        repo = _repo(tmp_path)
        (repo / "app" / "f0.py").write_text("v = 1\n", encoding="utf-8")
        snap, _ = tool.snapshot(repo, tmp_path / "snap")

        tool.remove_snapshot(repo, snap)
        assert not snap.exists()
        assert not tool._is_our_worktree(repo, snap), (
            "the registration is stale, so the next `worktree add` refuses over it")

    def test_the_command_cleans_and_reports(self, tmp_path, monkeypatch, capsys):
        repo = _repo(tmp_path)
        (repo / "app" / "f0.py").write_text("v = 1\n", encoding="utf-8")
        monkeypatch.setattr(tool, "ROOT", repo)
        assert tool.main(["--dir", str(tmp_path / "snap")]) == 0
        assert (tmp_path / "snap").exists()
        assert tool.main(["--dir", str(tmp_path / "snap"), "--clean"]) == 0
        assert not (tmp_path / "snap").exists()
        assert "removed" in capsys.readouterr().out


# ── 6. the linked dependencies ──────────────────────────────────────────────

class TestTheLinkedDependencies:
    """A junction is a name, and `rmtree` is the thing that forgets that.

    `deploy/theme_gate.sh` runs the Tailwind CLI to prove the committed stylesheet
    is the one the templates produce. Without `node_modules` in the snapshot that
    check reports "could not measure" — not a pass — so the snapshot links the
    checkout's own. What went wrong the first time this was done by hand: the link
    was a Windows junction, and clearing the snapshot with `shutil.rmtree` walked
    *through* it and deleted the checkout's real `node_modules` (74 packages,
    restored with `npm install`). Half a gigabyte of dependencies is not a link's
    to remove, and these tests are the reason the next person cannot repeat it.
    """

    def test_the_source_dependencies_are_linked_not_copied(self, tmp_path):
        repo, nm = _repo_with_dependencies(tmp_path)
        snap, _ = tool.snapshot(repo, tmp_path / "snap")
        link = snap / "node_modules"
        assert link.is_symlink() or tool._is_junction(link), (
            "the snapshot copied the dependency tree instead of linking it, so a "
            "snapshot costs a full copy of node_modules")
        assert (link / "tailwind").exists(), "the link points at nothing"
        assert os.path.samefile(link / "tailwind", nm / "tailwind"), (
            "the snapshot's dependency tree is not the source's")

    def test_it_is_not_copied_when_the_source_has_none(self, tmp_path):
        """A checkout that never ran `npm install` gets the honest answer — the
        gate says it cannot measure — rather than a link to nothing."""
        repo = _repo(tmp_path)
        (repo / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
        snap, _ = tool.snapshot(repo, tmp_path / "snap")
        assert not (snap / "node_modules").exists()

    def test_removing_the_snapshot_leaves_the_dependencies_it_points_at(
            self, tmp_path):
        """The regression that cost a checkout its `node_modules`."""
        repo, nm = _repo_with_dependencies(tmp_path)
        snap, _ = tool.snapshot(repo, tmp_path / "snap")
        assert (snap / "node_modules" / "tailwind").exists()

        tool.remove_snapshot(repo, snap)

        assert not (snap / "node_modules").exists(), "the link is still there"
        assert (nm / "tailwind").exists(), (
            "removing a snapshot emptied the checkout's own node_modules: the link "
            "was followed instead of unlinked")

    def test_unlinking_follows_nothing(self, tmp_path):
        """Stated on the helper alone, because that is where the rule lives."""
        target = tmp_path / "real"
        target.mkdir()
        (target / "keep").write_text("x\n", encoding="utf-8")
        link = tmp_path / "link"
        tool._make_dir_link(target, link)
        assert (link / "keep").exists()

        tool._unlink_without_following(link)
        assert not link.exists()
        assert (target / "keep").read_text(encoding="utf-8") == "x\n", (
            "the target was emptied by an unlink")


# ── 7. the interpreter, and one env list ─────────────────────────────────────

class TestTheInterpreterAndTheEnv:
    def test_a_checkout_venv_is_preferred(self, tmp_path):
        """The snapshot carries no `.venv` (it is gitignored), so the suite has
        to run under the checkout's own interpreter rather than one it lacks."""
        root = tmp_path / "root"
        venv = root / ".venv" / "Scripts"
        venv.mkdir(parents=True)
        (venv / "python.exe").write_text("", encoding="utf-8")
        assert tool.runner_python(root) == str(venv / "python.exe")

    def test_without_a_venv_it_is_the_running_interpreter(self, tmp_path):
        assert tool.runner_python(tmp_path) == sys.executable

    def test_it_clears_the_same_repository_variables_as_the_suite(self):
        """Two copies of the list is how one of them stops covering a variable
        git adds — and this tool makes *linked worktrees*, whose absolute
        `GIT_DIR`/`GIT_INDEX_FILE` are what the list exists for."""
        assert set(tool.GIT_REPO_ENV) == set(suite_git_env.GIT_REPO_ENV)

    def test_the_snapshot_survives_a_hook_s_exported_repository(self, tmp_path,
                                                               monkeypatch):
        """Run as a hook, git has exported GIT_DIR et al. at the *outer* repo.

        The tool must still resolve the checkout from its own `cwd`, or it would
        read one repository's changes into another's snapshot.
        """
        repo = _repo(tmp_path)
        (repo / "app" / "f0.py").write_text("v = 7\n", encoding="utf-8")
        monkeypatch.setenv("GIT_DIR", str(ROOT / ".git"))
        monkeypatch.setenv("GIT_INDEX_FILE", str(ROOT / ".git" / "index"))

        snap, _ = tool.snapshot(repo, tmp_path / "snap")
        assert (snap / "app" / "f0.py").read_text(encoding="utf-8") == "v = 7\n"


if __name__ == "__main__":                                 # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
