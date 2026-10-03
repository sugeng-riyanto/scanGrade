"""How far a process's commit is from the one this run merged, and what to do about it.

A served/worker/process refusal names the two shas and stops there — the worker
reports `bbbbbbb` — and the operator reading the quarantine card still has to answer
the question that decides their next move: *did the process merely miss the reload,
or is the box on the wrong history?* Those are different remedies, and the record
should say which one it is rather than leave the reader to reason it out from two
hashes.

The commit graph answers it, in three shapes:

* **behind** — the process's commit is an ancestor of the merged one. It holds an
  older release that is still on the merged line: the reload did not take. Remedy:
  **restart the unit** (what the runner already tried; if it refuses again, the unit
  is the problem).
* **ahead** — the merged commit is an ancestor of the process's commit. The box holds
  *newer* code than `origin/main`. Remedy: **re-baseline the box** — its checkout is
  on history the release does not have.
* **diverged** — neither is an ancestor of the other (a rewritten branch, a fetched
  head that is not this line). Remedy: **re-baseline the box.**

A git that cannot answer is **unknown**, and the sentence says so: an unmeasured gap
must never be read as either remedy. Every rule is a property of the graph rather
than of a clock, so the same two hashes always produce the same answer.
"""
from __future__ import annotations

import os
import shutil
import subprocess

#: Where git may be. The gates run under a service manager whose `PATH` is not a
#: login shell's, so git on `PATH` is not a safe assumption (the same list
#: `app/utils/build_info.py` and the deploy service carry).
GIT_FALLBACKS = ("/usr/bin/git", "/bin/git", "/usr/local/bin/git")

#: The graph shapes a reading can have.
SHAPE_SAME = "same"
SHAPE_BEHIND = "behind"
SHAPE_AHEAD = "ahead"
SHAPE_DIVERGED = "diverged"
SHAPE_UNKNOWN = "unknown"


def git_binary() -> str | None:
    found = shutil.which("git")
    if found:
        return found
    for path in GIT_FALLBACKS:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


def _default_runner(repo: str):
    """`run(args) -> (rc, stdout)`, never raising. `--no-optional-locks` so this is
    not a writer on the checkout the deploy is about to merge into."""
    git = git_binary()

    def run(args):
        if git is None:
            return 128, "no git"
        try:
            done = subprocess.run([git, "--no-optional-locks", "-C", str(repo), *args],
                                  capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.SubprocessError) as exc:
            return 255, "%s: %s" % (type(exc).__name__, exc)
        return done.returncode, (done.stdout or "").strip()

    return run


def _count(run, spec: str) -> int | None:
    rc, out = run(["rev-list", "--count", spec])
    if rc != 0:
        return None
    try:
        return int((out or "").strip() or "0")
    except ValueError:
        return None


def of(repo, served, merged, *, run=None) -> dict:
    """The shape of the gap between two commits, as data.

    `run` is injectable so the whole thing is testable without a repository: it takes
    a git argument list and returns `(returncode, stdout)`.
    """
    out = {"shape": SHAPE_UNKNOWN, "distance": None, "served": served, "merged": merged}
    if not served or not merged:
        return out
    if str(served) == str(merged):
        out["shape"] = SHAPE_SAME
        return out

    run = run or _default_runner(repo)
    behind_rc, _ = run(["merge-base", "--is-ancestor", str(served), str(merged)])
    if behind_rc == 0:
        out["shape"] = SHAPE_BEHIND
        out["distance"] = _count(run, "%s..%s" % (served, merged))
        return out
    ahead_rc, _ = run(["merge-base", "--is-ancestor", str(merged), str(served)])
    if ahead_rc == 0:
        out["shape"] = SHAPE_AHEAD
        out["distance"] = _count(run, "%s..%s" % (merged, served))
        return out
    # Both are 1 (neither is an ancestor of the other) only when git answered for
    # both objects: a 128 is an unreadable object, which is "unknown", not "diverged".
    if behind_rc == 1 and ahead_rc == 1:
        out["shape"] = SHAPE_DIVERGED
    return out


def _short(sha) -> str:
    return str(sha)[:7]


def describe(repo, served, merged, *, unit: str, run=None) -> str:
    """The clause an operator reads: which commit, how far, and what to do."""
    info = of(repo, served, merged, run=run)
    served_short, merged_short = _short(served), _short(merged)
    shape = info["shape"]

    if shape == SHAPE_SAME:
        return "running %s (the merged commit)" % served_short
    if shape == SHAPE_BEHIND:
        distance = info["distance"]
        gap = ("%s commit(s) behind" % distance) if distance is not None else "behind"
        return ("running %s, %s %s — the unit holds an older release that is still on "
                "the merged line, so restart %s" % (served_short, gap, merged_short, unit))
    if shape == SHAPE_AHEAD:
        distance = info["distance"]
        gap = ("%s commit(s) ahead of" % distance) if distance is not None else "ahead of"
        return ("running %s, %s %s — the box holds newer code than the merged commit, "
                "so re-baseline the box" % (served_short, gap, merged_short))
    if shape == SHAPE_DIVERGED:
        return ("running %s, which is not on the merged commit's history (%s) — the "
                "box's checkout is on the wrong history, so re-baseline the box"
                % (served_short, merged_short))
    return ("running %s, and how far it is from %s could not be measured from this "
            "checkout — decide by hand whether to restart %s or re-baseline the box"
            % (served_short, merged_short, unit))


def annotate(why: str, repo, served, merged, *, unit: str, run=None) -> str:
    """A gate's refusal sentence with the gap and the remedy appended.

    The original sentence is kept verbatim and only extended, so a reader who already
    knows which gate refused loses nothing and gains the number and the action.
    """
    return "%s — %s" % (why, describe(repo, served, merged, unit=unit, run=run))
