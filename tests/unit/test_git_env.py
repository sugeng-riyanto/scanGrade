"""A spawned ``git`` resolves the repository its caller meant — never one it inherited.

The failure this pins, measured rather than imagined: a pre-commit hook is handed an
absolute ``GIT_DIR`` and ``GIT_INDEX_FILE`` for the worktree, and a subprocess ``git``
honours those over its own ``cwd``. A test that built a scratch repository in
``tmp_path`` and ran ``git add`` there therefore wrote the fixture into *this
checkout's* index, leaving phantom entries that failed the very gate that had just
run the test. Two halves fix it, and this file proves both: the shared
:func:`tests.unit.git_env.git` scrubs the environment per call, and the session
fixture in :mod:`tests.conftest` clears the variables from ``os.environ`` so a spawn
that forgets ``env=`` cannot inherit them either.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.unit.git_env import GIT_REPO_ENV, git, git_env


def _repo(path: Path, filename: str = "seed.txt") -> Path:
    """A real checkout with one committed file, built with the scrubbed runner."""
    path.mkdir(parents=True, exist_ok=True)
    git("init", "-q", cwd=path)
    (path / filename).write_text("seed\n", encoding="utf-8")
    git("add", "-A", cwd=path)
    git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "seed",
        cwd=path)
    return path


class TestTheScrubList:
    def test_it_names_the_variables_git_sets_for_a_hook(self):
        # The ones measured under a linked worktree, plus the couple git documents
        # as repo redirection. A missing name is the whole bug, so it is spelled out.
        assert {"GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE", "GIT_COMMON_DIR"} <= set(
            GIT_REPO_ENV)

    def test_git_env_removes_them_and_keeps_everything_else(self):
        base = {name: "inherited" for name in GIT_REPO_ENV}
        base["PATH"] = "/kept"
        env = git_env(base)
        assert all(name not in env for name in GIT_REPO_ENV)
        assert env["PATH"] == "/kept"

    def test_the_run_itself_has_no_git_repository_variable_set(self):
        """The session fixture's property: the suite runs with them cleared."""
        leaked = sorted(set(GIT_REPO_ENV) & set(os.environ))
        assert not leaked, (
            f"{leaked} survived into the test process; a spawned git would resolve "
            "the checkout these point at instead of its own cwd")


class TestTheSharedRunnerIsScrubbed:
    def test_a_scratch_add_does_not_touch_an_inherited_index(self, tmp_path):
        """The exact incident: an inherited GIT_DIR must not capture the write."""
        victim = _repo(tmp_path / "victim", "v.txt")
        scratch = _repo(tmp_path / "scratch", "s.txt")
        (scratch / "new.txt").write_text("scratch\n", encoding="utf-8")
        index = victim / ".git" / "index"
        before = index.read_bytes()

        # Point the environment at the victim, then run the write the way the
        # incident did — through the shared runner, which is supposed to ignore it.
        inherited = dict(os.environ)
        inherited["GIT_DIR"] = str(victim / ".git")
        inherited["GIT_INDEX_FILE"] = str(index)
        done = git("add", "new.txt", cwd=scratch, env=inherited)
        assert done.returncode == 0, done.stderr

        assert index.read_bytes() == before, (
            "the scratch add wrote into the victim's index — the runner did not "
            "scrub GIT_DIR / GIT_INDEX_FILE")
        staged = git("ls-files", "--", "new.txt", cwd=scratch).stdout.split()
        assert staged == ["new.txt"], "the scratch add did not land in its own index"

    def test_the_runner_still_resolves_its_own_cwd(self, tmp_path):
        repo = _repo(tmp_path / "repo", "a.txt")
        head = git("rev-parse", "--is-inside-work-tree", cwd=repo)
        assert head.stdout.strip() == "true"
