"""Which commit this process is serving.

The deploy status page answers a question about the *box*: what the runner will
do, what the checkout holds, how far behind `origin/main` it is. Every one of
those readings comes out of a file on disk, and all of them can be perfectly
happy while the process answering the page is still running older code — the
merge landed, the gunicorn reload did not, and from the outside the site looks
exactly like a box with nothing waiting.

That gap is what this module closes, and it is deliberately a *different reading*
from the checkout's: the commit here is the one the code at `CODE_ROOT` was when
this process imported it. It cannot drift, because it is not read again; it is
what is being served until the process is replaced.

Two rules, both of them the reason this is not a one-line helper elsewhere:

* **It reads where this code lives, never `SCANGRADE_REPO`.** That variable names
  the checkout the deploy will act on, and the two agree until a release has moved
  the checkout past the running process — which is the one moment the distinction
  is the whole answer.
* **It is read once, and a failure is a reason rather than an exception.** A box
  whose git is missing, or whose code sits outside a checkout, still has to render
  a page; it says so instead of inventing a sha.
"""
from __future__ import annotations

import datetime as _dt
import functools
import os
import pathlib
import shutil
import subprocess

#: Where this code was loaded from: the app package's own location, three levels up
#: from here (`app/utils/build_info.py` → the repository root).
CODE_ROOT = pathlib.Path(__file__).resolve().parents[2]

#: Where the git that answers may be. The deploy service carries the same list for
#: the same reason: the app runs under a service manager whose `PATH` is not a
#: login shell's, so `git` on `PATH` is not a safe assumption.
GIT_FALLBACKS = ("/usr/bin/git", "/bin/git", "/usr/local/bin/git")

#: When this process loaded the app — the honest age of the code being served, and
#: the second half of "did a release land": a checkout that moved *after* this is a
#: release that has not been loaded.
_LOADED_AT = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def git_binary() -> str | None:
    """`git` if this process can run it, else None — never an exception."""
    found = shutil.which("git")
    if found:
        return found
    for path in GIT_FALLBACKS:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


def _run(argv: list[str], timeout: int = 5) -> tuple[int, str]:
    """(returncode, stdout). Never raises: a command that cannot run is a reason."""
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return 255, f"{type(exc).__name__}: {exc}"
    return done.returncode, (done.stdout or "").strip()


def read_commit(repo: pathlib.Path | str = CODE_ROOT) -> dict:
    """The commit the code at `repo` is from, as data the page can render.

    `--no-optional-locks` for the same reason the checkout reader carries it: a
    status page must not be a writer, not even of an index, on the checkout the
    deploy is about to use.

    Every field is present in every outcome. A path that is not a checkout, a
    process that cannot run git and a repository that cannot be read are three
    different reasons and all three are `available: False` with the reason named —
    an empty sha would be indistinguishable from "no commit at all".
    """
    repo = pathlib.Path(repo)
    out: dict = {
        "available": False, "reason_key": None, "detail": None, "repo": str(repo),
        "commit": None, "full_commit": None, "subject": None, "committed_at": None,
    }
    if not (repo / ".git").exists():
        out["reason_key"] = "not_a_checkout"
        return out
    git = git_binary()
    if git is None:
        out["reason_key"] = "no_git"
        return out

    rc, full = _run([git, "--no-optional-locks", "-C", str(repo), "rev-parse", "HEAD"])
    if rc != 0 or not full:
        out["reason_key"] = "unreadable"
        out["detail"] = full or None
        return out
    out["full_commit"] = full

    rc, short = _run([git, "--no-optional-locks", "-C", str(repo),
                      "rev-parse", "--short", "HEAD"])
    out["commit"] = short if rc == 0 and short else full[:7]

    rc, line = _run([git, "--no-optional-locks", "-C", str(repo),
                     "log", "-1", "--format=%s|%cI"])
    if rc == 0 and "|" in line:
        subject, when = line.split("|", 1)
        out["subject"], out["committed_at"] = subject, when
    out["available"] = True
    return out


@functools.cache
def snapshot() -> dict:
    """This process's own commit, read once and handed out by identity.

    Memoised rather than re-read per request because re-reading it would answer a
    different question: it would follow the checkout as it moves, which is exactly
    what the checkout reading already does. This one is fixed until the process is
    replaced — that is the fact the page needs.
    """
    out = read_commit(CODE_ROOT)
    out["loaded_at"] = _LOADED_AT
    out["pid"] = os.getpid()
    return out
