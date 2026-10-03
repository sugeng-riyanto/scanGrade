"""Whether the code being served can be reproduced from the commit it is on.

The strand this closes
----------------------
Commit `0afc68e` wrote **108 carriage returns** into the *blob*
`app/routes/admin_sekolah.py`, a path `.gitattributes` promises is `eol=lf`. Every
checkout of that commit smudges the blob down to LF, so `git status` reads the path
as modified however many times it is restored, and `git merge --ff-only` refuses
over it for ever — the release can neither land nor be explained, while the site
keeps answering `200` and nothing says why.

Two other layers know that state now: the deploy runner writes HEAD's own bytes in
verbatim and reads that one path without the filter, and the deploy-status page says
*which kind* of dirty it is holding. Neither is a gate. A box can be **serving**
code whose own commit it cannot reproduce, and until this check no part of the app
that is actually answering had an opinion about it — so the app asks at
construction and refuses.

Three rules, and each one is why this is a module rather than a line in `wsgi.py`:

* **The rule is the page's own.** `dirty_kinds_state` already answers which dirty
  paths a checkout cannot reproduce, and this calls it. A second implementation
  would be a second opinion, and the first thing two opinions do is disagree —
  about the box, on the page that exists to explain it.
* **Only positive evidence refuses.** A hand edit is replaceable (the runner sets it
  aside as a patch), an untracked file has no blob to blame, a path whose attribute
  *asks* for CRLF is fine, a directory that is not a checkout and a box with no git
  are not questions at all. This is deliberately more forgiving than
  `app/utils/armament.py`, and the reason is that it runs where the site is: a
  server is not taken down because git happened to be busy. The one thing that
  refuses is a *measured* blob — bytes the path's own filter would never write.
* **The journal can name the cause.** `MARKER` is what the deploy greps for, and
  the gate it quarantines under has a sentence on the status page — so the refusal
  never reads as "app did not construct" and never sends the next reader hunting
  for a Python fault.
"""

from __future__ import annotations

import datetime as _dt
import pathlib
import subprocess

from app.utils import build_info

#: The deploy greps for this to tell the app's refusal apart from a construct
#: error. Not a log level and not a return code: the probe captures stdout, and
#: this string is the only channel back.
MARKER = "SCANGRADE-UNREPRODUCIBLE"

#: Where this code was loaded from — the app package's own location, three levels
#: up from here, exactly as `app/utils/build_info.py` resolves it. Read at call
#: time rather than bound as a default argument so a test can point it at a
#: temporary checkout and build a real app over it.
REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

#: A `git status` and a handful of blob reads; no network, no network mounts. This
#: is a startup cost on a 1-vCPU box, so anything approaching it is a wedged git
#: rather than a slow one.
GIT_TIMEOUT = 8

#: How many modified tracked paths are measured. Each one is two git calls, so a
#: pathological tree is bounded — and a checkout with more uncommitted tracked files
#: than this is not one a release merges over either, which is why reaching the cap
#: refuses rather than passing.
SCAN_LIMIT = 64


def _run(argv: list[str], timeout: int = GIT_TIMEOUT) -> tuple[int, str]:
    """(returncode, stdout), with only the line ending off it.

    `.strip()` rather than `.rstrip("\\r\\n")` was the first version, and it ate a
    character of the first path in every porcelain listing: the status is three
    columns wide (` M <path>`), the first of which is a space, so a reader that
    trims the output and then slices `line[3:]` names `pp/cr.py` for `app/cr.py`.
    The suite caught it — the assertion is that the refusal names the file — and it
    is the class of defect this module exists to refuse: a reader normalising the
    very bytes it is measuring.
    """
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return 255, f"{type(exc).__name__}: {exc}"
    return done.returncode, (done.stdout or "").rstrip("\r\n")


def _modified_paths(git: str, repo: pathlib.Path) -> tuple[int, list[str]]:
    """Every **tracked** path this checkout has changed, or a non-zero exit code.

    `--untracked-files=no` on purpose: an untracked file has no blob for a filter to
    disagree with, so it is not this question, and counting it would let a developer
    with a scratch file spend the scan's budget on nothing. `--no-optional-locks`
    for the same reason the deploy-status reader carries it — a plain `git status`
    may refresh and rewrite the index, and this runs inside every construction,
    including the deploy's own probe.
    """
    rc, out = _run([git, "--no-optional-locks", "-C", str(repo),
                    "status", "--porcelain", "--untracked-files=no"])
    if rc != 0:
        return rc, []
    paths: list[str] = []
    for line in out.splitlines():
        if len(line) < 4:
            continue
        body = line[3:]
        if " -> " in body:                       # a rename: the path that changed
            body = body.split(" -> ", 1)[1]
        body = body.strip().strip('"')
        if body:
            paths.append(body)
    return 0, paths


def _head_short(git: str, repo: pathlib.Path) -> str:
    """The commit the refusal is about, or the word for not being able to say."""
    rc, out = _run([git, "--no-optional-locks", "-C", str(repo),
                    "rev-parse", "--short", "HEAD"])
    out = out.strip()
    return out if rc == 0 and out else "HEAD"


def _blob_sentence(git: str, repo: pathlib.Path, paths: list[str]) -> str:
    """The refusal, naming the files and the one remedy that works."""
    listed = "\n".join(f"    {path}" for path in paths)
    return (
        f"{len(paths)} path(s) in this checkout hold bytes no checkout of "
        f"{_head_short(git, repo)} can reproduce:\n"
        f"{listed}\n"
        f"Each stores a carriage return the path's own `.gitattributes` filter would "
        f"never write, so `git checkout`, `git restore` and `git stash` all write "
        f"*through* that filter: the path reads as modified however many times it is "
        f"restored, and `git merge --ff-only` refuses over it for ever. That is a "
        f"release which can neither land nor be explained, so this process refuses to "
        f"serve a checkout it was built from. The remedy is not a restore — HEAD's own "
        f"bytes go in verbatim and git is told to read that one path without the "
        f"filter (`deploy/scangrade-recover.sh` does both, and so does the deploy "
        f"runner's set-aside heal)."
    )


def unreproducible_reason(repo: pathlib.Path | str | None = None, *,
                          limit: int = SCAN_LIMIT) -> str | None:
    """Why this checkout cannot serve, or None when it can.

    None is the answer for every state that is not *measured* evidence of the
    defect — see the module docstring. The exception is the scan cap: a checkout
    with more modified tracked files than `limit` is one this cannot rule out, and
    one no release merges over either, so it says so instead of passing quietly.
    """
    repo = pathlib.Path(repo) if repo is not None else REPO_ROOT
    if not (repo / ".git").exists():
        return None                       # no commit to reproduce from: not this question
    git = build_info.git_binary()
    if git is None:
        return None                       # the defect is defined by git's filters

    rc, paths = _modified_paths(git, repo)
    if rc != 0 or not paths:
        return None

    # The one rule, and it is the page's: `dirty_kinds_state` classifies each dirty
    # path by the path's own attributes and the blob git stored. Imported here rather
    # than at module level so this module stays usable — and cheap to import — when
    # the service that owns the page is not wanted.
    from app.services import deploy_status_service as status

    reading = status.dirty_kinds_state(
        repo, now=_dt.datetime.now(_dt.timezone.utc), git=git,
        paths=paths, limit=max(1, limit))
    offenders = [entry["path"] for entry in reading["paths"]
                 if entry["kind"] == status.DIRTY_BLOB]
    if offenders:
        return _blob_sentence(git, repo, offenders)
    if reading["truncated"]:
        return (
            f"this checkout has more than {max(1, limit)} modified tracked file(s), so "
            f"this process cannot rule out a blob no checkout of "
            f"{_head_short(git, repo)} can reproduce among the rest — and a checkout "
            f"with that many uncommitted files is not one a release merges over "
            f"either. Measure the rest with the same rule by hand: `git status "
            f"--porcelain`, then for each path `git check-attr text eol -- <path>` and "
            f"`git cat-file blob HEAD:<path>`."
        )
    return None
