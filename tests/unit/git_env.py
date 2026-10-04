"""Spawn ``git`` against the repository the caller meant, never one it inherited.

Git exports its own repository-selection variables to the processes it starts for
a hook. Measured in a linked worktree, ``GIT_DIR`` and ``GIT_INDEX_FILE`` are
**absolute** (``…/.git/worktrees/<name>/index``), and a plain checkout at least
sets ``GIT_INDEX_FILE``. A subprocess ``git`` honours those over its own ``cwd``,
so a test that makes a scratch repository in ``tmp_path`` and runs ``git add``
there wrote the fixture into *this checkout's* index instead — leaving phantom
entries that then failed the very gate that had just run the test.

Two things fix it, and they are the same fix at two levels:

* :func:`git_env` / :func:`git` build the environment for a spawned ``git`` with
  those variables cleared, so every call this suite makes resolves the repository
  from its ``cwd`` — the only one the caller meant; and
* :mod:`tests.conftest` clears the same variables from ``os.environ`` for the
  whole run, so a spawn that forgets to pass ``env=`` cannot inherit them either.

The variable list is defined once, here, and imported by both — a second copy is
how the two halves would drift and one of them stop covering a new variable Git
adds.
"""
from __future__ import annotations

import os
import subprocess

#: The variables git sets to point a hook (and everything it spawns) at the
#: repository the hook is for. Cleared for every ``git`` this suite runs.
GIT_REPO_ENV = (
    "GIT_DIR",
    "GIT_INDEX_FILE",
    "GIT_WORK_TREE",
    "GIT_PREFIX",
    "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
)


def git_env(base: dict | None = None) -> dict:
    """The environment for a spawned ``git``, minus any inherited repo selection."""
    env = dict(os.environ if base is None else base)
    for name in GIT_REPO_ENV:
        env.pop(name, None)
    return env


def git(*args: str, cwd=None, check: bool = False, **kwargs) -> subprocess.CompletedProcess:
    """Run ``git`` with a scrubbed repository environment.

    A thin wrapper rather than a rule the callers have to remember: the argument
    list is the caller's, and only ``env`` is ours. Any ``env`` a caller passes is
    scrubbed too, so ``GIT_REPO_ENV`` cannot ride back in through a custom env.
    """
    kwargs.setdefault("capture_output", True)
    kwargs.setdefault("text", True)
    kwargs["env"] = git_env(kwargs.pop("env", None))
    return subprocess.run(["git", *args], cwd=cwd, check=check, **kwargs)
