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

The two refusals are not the same claim, and only one of them is evidence
----------------------------------------------------------------------------
Two of them come out of `unreproducible_reason`, and conflating them is how a
working checkout becomes un-verifiable:

* the **blob** refusal, above, is a *measurement* — a named path whose stored bytes
  the path's own filter could never write. It refuses everywhere, and
  `allow_unmeasured` cannot reach it.
* the **cap** refusal is the absence of a measurement. `SCAN_LIMIT` bounds the scan
  (two git calls per path), so a checkout with more modified tracked paths than the
  bound is one this process cannot *rule out* — and a box that serves must not guess.
  A laptop with four features in flight is exactly that checkout, and so is the
  checkout whose suite therefore cannot run at all: the change that would prove
  itself green cannot be measured, and the workaround was a hand-typed linked
  worktree with a `cp -f` of the files the feature touched. So the cap is the one
  refusal a development run may answer for itself, through `ALLOW_DIRTY_ENV` —
  explicitly, loudly, and never on a box that serves.
"""

from __future__ import annotations

import datetime as _dt
import os
import pathlib
import subprocess
import sys

from app.utils import build_info, import_safety

#: The deploy greps for this to tell the app's refusal apart from a construct
#: error. Not a log level and not a return code: the probe captures stdout, and
#: this string is the only channel back.
MARKER = "SCANGRADE-UNREPRODUCIBLE"

#: Where this code was loaded from — the app package's own location, three levels
#: up from here, exactly as `app/utils/build_info.py` resolves it. Read at call
#: time rather than bound as a default argument so a test can point it at a
#: temporary checkout and build a real app over it. Resolved through
#: `import_safety.resolved`: `.resolve()` probes the filesystem (an `lstat`, and a
#: `readlink` per link) and raises on a symlink loop or an unreadable link, and this
#: is a module body — the app would refuse to *construct*, which is the one failure
#: this gate exists to explain rather than to be.
REPO_ROOT = import_safety.resolved(pathlib.Path(__file__)).parents[2]

#: A `git status` and a handful of blob reads; no network, no network mounts. This
#: is a startup cost on a 1-vCPU box, so anything approaching it is a wedged git
#: rather than a slow one.
GIT_TIMEOUT = 8

#: How many modified tracked paths are measured. Each one is two git calls, so a
#: pathological tree is bounded — and a checkout with more uncommitted tracked files
#: than this is not one a release merges over either, which is why reaching the cap
#: refuses rather than passing.
SCAN_LIMIT = 64

#: The variable a **development** checkout may export to answer the cap question by
#: hand, and the only refusal it reaches. Named in the refusal itself, because the
#: reader who hits it is a developer mid-feature and the remedy has to be where the
#: obstacle is: the alternative they kept reaching for was a linked worktree with a
#: `cp -f` of the files in flight, which is different every time and quietly wrong
#: whenever a new test file is left behind (the guard then does not run, and the
#: suite reports green for a tree that was never measured).
ALLOW_DIRTY_ENV = "SCANGRADE_ALLOW_DIRTY_CHECKOUT"

#: What counts as yes. Spelled out rather than tested for truthiness, so an empty
#: value or `=0` — how a script that forwards its environment says no — cannot
#: switch it on by being non-empty, which is exactly the shape of an `env_bool`
#: whose default is read backwards.
ALLOW_DIRTY_WORDS = ("1", "true", "yes", "on")

#: Printed whenever the allowance is used, so no run that was permitted can be
#: mistaken for one that measured. Deliberately **not** `MARKER` and deliberately
#: not containing it: the deploy greps for `MARKER` to tell a refusal apart from a
#: construct error, and a waiver that printed it would quarantine a release for the
#: opposite of the reason it happened.
ALLOWED_MARKER = "SCANGRADE-DIRTY-CHECKOUT-ALLOWED"


def dev_allowance(config, environ=None) -> bool:
    """May this run leave the scan-cap question unanswered?

    Two independent conditions, and the second is the one that keeps this from
    being a hole in the gate rather than a development convenience:

    * the variable is exported *and* says yes (`ALLOW_DIRTY_WORDS`); and
    * the config proves this process is not the app that serves (or the deploy's
      probe, which is the release's own construction). `IS_PRODUCTION` is a class
      attribute on `ProductionConfig`, not an environment read, so a variable left
      in a shell profile on the box cannot travel here with the next `git pull`.

    `config` is a Flask `app.config` (any mapping). An object with no `get` — or no
    config at all — answers no, because "I could not tell where I am" is not
    permission to keep going; that direction is the whole point of the check.
    """
    get = getattr(config, "get", None)
    if get is None:
        return False
    if get("IS_PRODUCTION") or get("DEPLOY_PROBE"):
        return False
    value = (os.environ if environ is None else environ).get(ALLOW_DIRTY_ENV, "")
    return str(value).strip().lower() in ALLOW_DIRTY_WORDS


def _cap_sentence(git: str, repo: pathlib.Path, limit: int, read: int) -> str:
    """The cap refusal: what could not be ruled out, and the two ways to answer it.

    `read` is how many paths were measured before the bound — reported because the
    count is the difference between "some of this tree was measured and the rest was
    not" and a sentence about a number the reader cannot see.
    """
    return (
        f"this checkout has more than {limit} modified tracked file(s), so this "
        f"process cannot rule out a blob no checkout of {_head_short(git, repo)} can "
        f"reproduce among the rest — and a checkout with that many uncommitted files "
        f"is not one a release merges over either. Measure the rest with the same "
        f"rule by hand: `git status --porcelain`, then for each path `git check-attr "
        f"text eol -- <path>` and `git cat-file blob HEAD:<path>`. A development "
        f"checkout may answer it for itself instead — `{ALLOW_DIRTY_ENV}=1`, which "
        f"waives this refusal and nothing else; {read} path(s) were read before the "
        f"bound, and that permission is refused on a box that serves and in the "
        f"deploy's probe, so it can never answer the question for a release."
    )


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
                          limit: int = SCAN_LIMIT,
                          allow_unmeasured: bool = False) -> str | None:
    """Why this checkout cannot serve, or None when it can.

    None is the answer for every state that is not *measured* evidence of the
    defect — see the module docstring. Two of the exits below are refusals and they
    are different claims: a measured blob (never waived, in any environment) and the
    scan cap, which is the absence of a measurement and therefore the one a
    development run may answer for itself by passing `allow_unmeasured` — the value
    `dev_allowance` computes from the environment *and* the config. The waiver is
    printed when it is used, so a permitted run is never read as a clean one.
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
    # The measured refusal, first and unconditional: it is a named path, not a
    # budget, and none of the allowance's two conditions reaches it.
    if offenders:
        return _blob_sentence(git, repo, offenders)
    if reading["truncated"]:
        if allow_unmeasured:
            print(
                f"{ALLOWED_MARKER}: {len(paths)} modified tracked path(s) in this "
                f"checkout, more than the {max(1, limit)} this scan measures — the "
                f"rest were NOT measured ({ALLOW_DIRTY_ENV} is in force). This run is "
                f"not evidence that the checkout is reproducible; a box that serves "
                f"and the deploy's probe refuse the same permission.",
                file=sys.stderr)
            return None
        return _cap_sentence(git, repo, max(1, limit), len(paths))
    return None
