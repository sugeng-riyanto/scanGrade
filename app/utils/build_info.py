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

Three rules, all of them the reason this is not a one-line helper elsewhere:

* **It reads where this code lives, never `SCANGRADE_REPO`.** That variable names
  the checkout the deploy will act on, and the two agree until a release has moved
  the checkout past the running process — which is the one moment the distinction
  is the whole answer.
* **It is resolved when this module is imported, not when it is first asked for.** A
  reading taken at call time describes the *checkout at that moment*, and a worker
  that was never asked before a release merged would take it afterwards: it would
  report the newly merged commit while running the code it loaded yesterday. That is
  not a nicety — it is the false negative that defeats the deploy's served-commit
  check, and it is permanent, because the memo is populated once. At import, python
  has just loaded these files, so the commit the checkout held then is the commit
  whose content is in memory. (`tests/unit/test_served_commit_gate.py` moves a
  checkout out from under an imported module and fails if the reading follows.)
* **`snapshot()` still hands out one object, and a failure is a reason rather than
  an exception.** A box whose git is missing, or whose code sits outside a checkout,
  still has to render a page; it says so instead of inventing a sha.
"""
from __future__ import annotations

import datetime as _dt
import functools
import os
import pathlib
import shutil
import subprocess

from app.utils import import_safety

#: Where this code was loaded from: the app package's own location, three levels up
#: from here (`app/utils/build_info.py` → the repository root). Resolved through
#: `import_safety.resolved`, because `.resolve()` is a probe that raises —
#: `RuntimeError` on a symlink loop, `OSError` on a link it cannot read — and this
#: runs at import, in the app *and* in the worker, to answer a question the deploy
#: gate asks about both. An unresolved path still names the checkout.
CODE_ROOT = import_safety.resolved(pathlib.Path(__file__)).parents[2]

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


def read_own_commit() -> dict:
    """The commit whose code is in memory, read from where this code lives.

    Called exactly once, at import, by the line below. It is a named function rather
    than that call inlined so the two rules above stay testable: a test can call it
    with `SCANGRADE_REPO` pointed elsewhere and require that it is ignored, which is
    the guard that stops this reading from quietly becoming "the checkout the deploy
    is about to touch".
    """
    return read_commit(CODE_ROOT)


#: Resolved here, at import: see the module docstring. Everything downstream — the
#: deploy-status card, and the deploy's own served-commit check — is only as true as
#: this moment.
_READING_AT_LOAD = read_own_commit()


@functools.cache
def snapshot() -> dict:
    """This process's own commit, sampled at load and handed out by identity.

    Not re-read per request, and not read at all here: re-reading would answer a
    different question — it would follow the checkout as it moves, which is exactly
    what the checkout reading already does — and reading it *late* would answer the
    wrong one, which is worse.
    """
    out = dict(_READING_AT_LOAD)
    out["loaded_at"] = _LOADED_AT
    out["pid"] = os.getpid()
    return out
