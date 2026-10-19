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

And one rule about *permission*, because it is the failure that looks least like one:
git 2.35.2 refuses every command in a repository whose owner is not the reading user
("detected dubious ownership"), which this module reports as `unreadable` — the same
value an unreadable checkout produces — while the site answers normally. The fix is
not "add the checkout to a global `safe.directory`": that is a config file on a host
the app may have no shell on, and it is invisible to a process that starts as the
service user. :func:`_git_argv` makes the exemption itself, in the command scope,
which is protected config like the global file and writes nothing.

And one about *time*, for the same reason: a probe that did not answer within five
seconds is `timeout`, not `unreadable`, because the reading is frozen at import and
the two failures have opposite next steps. The checkout is fine and the retry is
worth one attempt; a checkout that is missing or withheld is an answer, and asking
again only delays the same sentence.
"""
from __future__ import annotations

import datetime as _dt
import functools
import os
import pathlib
import shutil
import subprocess
import time

from app.utils import commit_readers, import_safety

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

#: How long a probe may take before it is a reason rather than a reading. Five
#: seconds is generous for `rev-parse` on a healthy box and short enough that a
#: worker stuck on a cold page cache is not stuck for its whole startup.
PROBE_TIMEOUT = 5

#: The exit code for a probe git did not answer in time. Coreutils' `timeout` uses
#: the same number, so a log line or a quarantine record reads the way an operator
#: already expects it to, and it cannot be confused with python's own `1`.
EXIT_TIMEOUT = 124

#: How long the import-time reading waits before its one second attempt. The
#: contention that produces a timeout is the *other workers starting with it*, so
#: retrying inside the same instant would race the same window again; a quarter of
#: a second is long enough to leave it and short enough that boot does not notice.
RETRY_PAUSE_SECONDS = 0.25

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


def _git_argv(git: str, repo: pathlib.Path, *probe: str) -> list[str]:
    """A read-only probe that does not depend on anyone's global config.

    `safe.directory` exists because a repository can belong to a different user than
    the one reading it — here the checkout is the deploy owner's and gunicorn runs as
    the service user — and when it does, git 2.35.2 and later refuse *every* command
    with "detected dubious ownership" until the path is exempted. The documented
    remedy is `git config --global --add safe.directory <path>`: a file in the
    reading user's home, which is exactly what an app cannot arrange for itself and
    what an operator with no shell on the box cannot arrange at all. The symptom is
    not an error page — it is `/health` answering `unreadable`, and a served-commit
    gate that then reports *could not measure* instead of the commit.

    Git honours `safe.directory` from **protected** configuration, and the command
    scope (`-c`) is one of them, so passing it per invocation is the same exemption
    made by the process that needs it: no file is written, nothing global is changed,
    and a box whose checkout belongs to someone else reads its own commit normally.

    The path has to be the **resolved** one. Git compares against the directory it
    opened after following links, so an unresolved spelling is an exemption that is
    accepted and then never matches — a silent no, which is the shape this whole
    module exists to avoid.
    """
    exempt = import_safety.resolved(pathlib.Path(repo))
    return [git, "--no-optional-locks", "-c", f"safe.directory={exempt}",
            "-C", str(repo), *probe]


def _run(argv: list[str], timeout: int = PROBE_TIMEOUT) -> tuple[int, str, str]:
    """(returncode, stdout, stderr). Never raises: a command that cannot run is a reason.

    stderr is kept separately rather than merged: git's refusals are written there
    (`detected dubious ownership`, which is what this module's one silent failure
    looks like) while stdout carries the sha, and merging the two would let a
    warning become the commit.

    A timeout is `EXIT_TIMEOUT` rather than the catch-all 255, because it is the
    one failure that is about *when* the probe ran rather than about what it read —
    three workers importing at once on one vCPU can exceed five seconds on a probe
    that takes five milliseconds when the box is warm. Reported as `unreadable` it
    is indistinguishable from a broken checkout, and `read_own_commit` can do
    nothing sensible about it.
    """
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return EXIT_TIMEOUT, "", f"no answer within {timeout}s"
    except (OSError, subprocess.SubprocessError) as exc:
        return 255, "", f"{type(exc).__name__}: {exc}"
    return (done.returncode, (done.stdout or "").strip(),
            (done.stderr or "").strip())


def read_commit(repo: pathlib.Path | str = CODE_ROOT) -> dict:
    """The commit the code at `repo` is from, as data the page can render.

    `--no-optional-locks` for the same reason the checkout reader carries it: a
    status page must not be a writer, not even of an index, on the checkout the
    deploy is about to use. The ownership exemption is `_git_argv`'s: a checkout
    that belongs to the deploy user rather than to this process is still this
    process's own code, and being unable to say so is not a safe default.

    Every field is present in every outcome. A path that is not a checkout, a
    process that cannot run git, a repository that cannot be read and a probe that
    ran out of time are four different reasons and all four are `available: False`
    with the reason named — an empty sha would be indistinguishable from "no commit
    at all", and a deadline mistaken for a broken checkout is the one that gets
    frozen for the life of the process.
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

    rc, full, why = _run(_git_argv(git, repo, "rev-parse", "HEAD"))
    if rc != 0 or not full:
        out["reason_key"] = "timeout" if rc == EXIT_TIMEOUT else "unreadable"
        # Git's own sentence, because `unreadable` alone is the answer that sent an
        # operator looking for a broken checkout while the cause was an ownership
        # check — and it is the only place the reason is written down.
        out["detail"] = why or full or None
        return out
    out["full_commit"] = full

    rc, short, _why = _run(_git_argv(git, repo, "rev-parse", "--short", "HEAD"))
    out["commit"] = short if rc == 0 and short else full[:7]

    rc, line, _why = _run(_git_argv(git, repo, "log", "-1", "--format=%s|%cI"))
    if rc == 0 and "|" in line:
        subject, when = line.split("|", 1)
        out["subject"], out["committed_at"] = subject, when
    out["available"] = True
    return out


def read_own_commit(*, pause: float = RETRY_PAUSE_SECONDS) -> dict:
    """The commit whose code is in memory, read from where this code lives.

    Called exactly once, at import, by the line below. It is a named function rather
    than that call inlined so the two rules above stay testable: a test can call it
    with `SCANGRADE_REPO` pointed elsewhere and require that it is ignored, which is
    the guard that stops this reading from quietly becoming "the checkout the deploy
    is about to touch".

    **One second attempt, and only after a timeout.** The reading is frozen for the
    life of the process, so a worker that misses it is blind until it is replaced —
    measured on the box as one worker in three answering `unreadable` for two days
    while its neighbours read the same checkout normally, and that worker's cause was
    never settled (a missed deadline is the remaining suspect). A timeout is the one
    failure whose cause can be the *moment* rather than the checkout (three workers
    importing at once), so it is the one worth retrying; a missing `.git`, a missing
    git and a withheld checkout are answers, and asking again would only delay the
    same sentence. The pause exists because the contention retrying hopes to escape
    is the window the other workers started in.
    """
    reading = read_commit(CODE_ROOT)
    if reading["reason_key"] != "timeout":
        return reading
    if pause:
        time.sleep(pause)
    reading = read_commit(CODE_ROOT)
    if reading["reason_key"] == "timeout":
        # The reading does not know how many attempts produced it, so the detail
        # says so: "no answer within 5s" twice is the fact, and one sentence that
        # hides the retry would read as a box that tried once and gave up.
        reading["detail"] = "%s (twice at import)" % (reading["detail"] or "timeout")
    return reading


#: Resolved here, at import: see the module docstring. Everything downstream — the
#: deploy-status card, and the deploy's own served-commit check — is only as true as
#: this moment.
_READING_AT_LOAD = read_own_commit()

#: And published here, for the same reason it is read here. A reading this process
#: cannot re-take is exactly what `/health` could only ever report one worker at a
#: time; `commit_readers` collects one per worker, so a box whose workers disagree
#: — or one whose worker cannot read its own checkout — is a property of the box in
#: a single request instead of a coincidence across five samples. It is written
#: once, at import, because that is when the value above became true; a marker
#: written later would describe a process that had been serving something else in
#: between.
#:
#: Never raises: a box with no temp directory keeps no ledger, and `commit_readers`
#: says it could not measure rather than that every worker is fine.
commit_readers.record(_READING_AT_LOAD, loaded_at=_LOADED_AT, repo=CODE_ROOT)


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
