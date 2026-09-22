"""Is the runner that will deploy the next release the checkout's launcher?

Why this exists
---------------
The launcher change is deliberately invisible: `/usr/local/bin/scangrade-deploy`
became a file that *execs* `deploy/scangrade-deploy.sh` in the checkout instead of
being a copy of it, so every later fix to the gates takes effect on the next timer
tick with nobody opening a console. The whole point is that you never think about
it again — and that is also what makes its failure mode silent.

Three things can be true of that path, and only one of them is correct:

* **a launcher** rendered from this commit — the arrangement, and no action;
* **a launcher** rendered from an *older* `deploy/entrypoint.sh` — still runs the
  checkout, so nothing is broken, but whatever the newer file changed is not in
  effect until the installer runs once more;
* **a copy** — the old arrangement. While it still matches the checkout
  byte-for-byte Gate 0 lets it through and it deploys today; the moment it differs,
  Gate 0 refuses every release with exit 14 and **nothing deploys again** while the
  site keeps serving happily. There is no symptom on any page; the only way to
  notice is to look, and until now looking meant SSH.

So this reports it from outside: whether the installed runner is the checkout's
launcher or a copy, whether the checkout is behind `origin/main`, and how far. It
reads the same two facts Gate 0 compares (`SELF` and `REPO_RUNNER`), so the page
and the gate cannot disagree about the arrangement.

A copy is *named*, not merely called stale
------------------------------------------
"A copy" is not yet an answer to "which fixes are missing", and the number that
answers it is the one nobody could get without a shell: how far behind the
*runner* is. Measured on production, the first version of this page said the box
was five days stale — true — and then added the wrong reason:*"Gate 0 refuses it with exit 14"*. The installed copy was the revision from
before Gate 0 existed, so there was no check in the running file to refuse
anything, and releases had been going out all week with the deploy logic of 45
commits ago — every gate built since simply absent, and the site perfectly
healthy. The alarming sentence was wrong in the direction of being *reassuring*
about the consequences.

So the consequence is derived rather than assumed: a copy that carries no
`runner-identity` block cannot refuse, whatever the checkout says (`copy_predates_gate`),
and only a copy that does carry it is one Gate 0 turns away (`copy_drifted`).
And the installed file's bytes are matched against every committed revision of
the file it came from — exactly, by asking git for the commit whose blob has that
hash, so the answer is a commit and a distance rather than an impression.

No copy lives here
------------------
Every reading is a stable **key** plus the **data** that goes with it — a path, a
git error, a commit count. The sentence a reader sees is written in the template,
in both languages, where the i18n sweep can check it; a reason spelled out in
Python would be English copy that the toggle cannot reach and the sweep cannot
see. Data (a path, a sha, an error string from git) is not translated, on purpose.

The copy is compared as BYTES, because Gate 0 compares bytes
------------------------------------------------------------
Gate 0 runs `cmp -s "$SELF" "$REPO_RUNNER"`, and `cmp` does not normalise line
endings. Comparing the two files as text does: `\r\n` and `\n` read as the same
character, so a copy that Gate 0 refuses is one this page would call a match — two
answers to one question, which is the exact drift this module exists to prevent.
(The first version did that, and the test that runs Gate 0 over the same files
caught it.) The launcher half stays a text comparison on purpose: there the
question is "is this what `entrypoint.sh` renders for this commit", not "are these
two files on disk the same".

Read-only, and honest about what it cannot see
---------------------------------------------
Every command is a read — `rev-parse`, `rev-list`, `log`, `status` — all with
`--no-optional-locks`, because a plain `git status` may refresh and rewrite the
index and a *status page* must never take a lock on the checkout the deploy is
about to use. Nothing fetches: `origin/main` is reported as *this checkout knows
it*, with the age of that knowledge, since the deploy already fetches every two
minutes and a page that fetched would be changing what it reports.

A part that cannot be measured says so and says why — the path is absent, there is
no git, the file is unreadable — rather than reporting a zero. A zero here reads
as "up to date", which is the one answer this must never invent.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import os
import pathlib
import re
import shutil
import stat
import subprocess

#: Names, not secrets. Each is overridable so the checks can be pointed at a
#: temporary tree in a test, and at a different layout on a box that has one.
DEFAULT_REPO = "/opt/scangrade"
DEFAULT_RUNNER = "/usr/local/bin/scangrade-deploy"
DEFAULT_SNAPSHOT_RUNNER = "/usr/local/bin/scangrade-db-snapshot"
DEFAULT_PAUSE_FILE = "/etc/scangrade-deploy.pause"

#: The runner's own quarantine record and the two one-shot release files, as
#: `deploy/scangrade-deploy.sh` names them. Overridable for the same reason the
#: paths above are: the checks point them at a temporary tree.
DEFAULT_STATE_DIR = "/var/lib/scangrade-deploy"
DEFAULT_QUARANTINE_FILE = DEFAULT_STATE_DIR + "/quarantined"
DEFAULT_RELEASE_FILE = "/etc/scangrade-deploy.release"
DEFAULT_REQUEST_DIR = DEFAULT_STATE_DIR + "/requests"
#: The runner's record of a refusal that is about the *box* rather than about a
#: commit: `armament_preflight` writes the checker's own report here and deletes it
#: the moment the box is armed again. Same directory as the quarantine record, and
#: the opposite answer — "no commit is held" and "no release will deploy at all"
#: were being told apart by nothing.
DEFAULT_UNARMED_FILE = DEFAULT_STATE_DIR + "/unarmed"

#: Where git lives when the service user's PATH does not carry it. The unit runs
#: the app without a login shell, so `which` is the first guess and these are the
#: fallbacks rather than the other way round.
GIT_FALLBACKS = ("/usr/bin/git", "/bin/git", "/usr/local/bin/git")

#: `deploy/entrypoint.sh`'s signature — three parts, all of which must hold for
#: the installed file to be the launcher rather than something that resembles one.
PLACEHOLDER = "@REPO@"
#: The unrendered launcher is recognised by its *assignment*, not by the bare token.
#: `deploy/scangrade-deploy.sh` names `@REPO@` three times while re-rendering the
#: launcher from the checkout, so a bare-token test read a **copy of the runner** as
#: an unrendered launcher and returned before it ever compared the bytes — the one
#: reading this page exists to give. A file that merely mentions the token is not an
#: unrendered launcher; a file that assigns it is.
UNRENDERED = f'REPO="{PLACEHOLDER}"'
_DISPATCH = 'case "$(basename "$0")" in'
_EXEC = re.compile(r'^\s*exec bash "\$TARGET"', re.M)
_RENDERED_REPO = re.compile(r'^REPO="([^"]*)"', re.M)

#: Levels, in the order a reader should care about. `broken` means no release can
#: deploy at all; `warn` means it works today but something needs doing; `fresh`
#: means nothing to do; `unknown` means this could not look, which is never the
#: same answer as `fresh`.
BROKEN, WARN, FRESH, UNKNOWN = "broken", "warn", "fresh", "unknown"

#: Gate 0's own delimiter. A copy that does not carry it cannot refuse anything:
#: the check it would need is not in the file that runs. Measured the hard way —
#: production's runner was the revision from before Gate 0 existed, so a page that
#: announced "Gate 0 refuses it, nothing can deploy" was describing a refusal that
#: could not happen while releases went out all week.
IDENTITY_MARKER = "runner-identity:start"

#: The two arrangements, as `gate0` spells them. Strings rather than booleans
#: because the template branches on them and the vocabulary test reads them.
GATE0_PASSES = "passes"
GATE0_COPY_MATCHES = "passes (the copy still matches)"
GATE0_REFUSES = "refuses (exit 14)"
GATE0_CANNOT = "cannot refuse (no Gate 0)"

#: How far back the launcher search looks for the revision it was rendered from.
#: Small on purpose: this runs inside a page request, and a launcher that matches
#: nothing in the last 25 revisions of `deploy/entrypoint.sh` is stale by any
#: measure a reader needs.
LAUNCHER_SCAN_LIMIT = 25

#: The directory whose movement is reported as "how much of the gate code is not
#: in effect here": a commit count is honest but says nothing about *what*.
DEPLOY_DIR = "deploy"

#: How the installed file's provenance reads. `current` means it is what this
#: commit renders; `named` means it is a real commit, just an older one;
#: `unmatched` means no committed revision has those bytes; `unreadable` means git
#: or the checkout could not be read, which is never the same answer as `named`.
ORIGIN_CURRENT, ORIGIN_NAMED, ORIGIN_UNMATCHED, ORIGIN_UNREADABLE = (
    "current", "named", "unmatched", "unreadable")
ORIGIN_KEYS = frozenset({ORIGIN_CURRENT, ORIGIN_NAMED, ORIGIN_UNMATCHED, ORIGIN_UNREADABLE})


# ── the commit a gate refused ────────────────────────────────────────────────
#
# The runner quarantines a refused commit so the next tick does not walk into the
# same gate — see `deploy/scangrade-deploy.sh`. It is correct, and it is invisible:
# the *previous* release serves throughout, no page changes, and the only record
# is a three-line file on the box. So "why has nothing deployed for an hour" is a
# question that could only be answered with a shell — the same gap this page
# already closes for the runner itself, and the same answer: read it and say it.

#: `deploy/scangrade-deploy.sh` sets `FAIL_REASON` to one of these before it
#: quarantines a release. The sentence goes on the page as data — it is the
#: runner's own words — but "which gate refused this" is something an operator
#: reads, so it is *also* classified into a key the template can say in either
#: language. `tests/unit/test_deploy_status.py` lifts every `FAIL_REASON=` out of
#: the runner and fails if one of them lands on a key with no sentence, so the two
#: cannot drift apart.
GATE_UNKNOWN = "unknown"
GATE_KEYS = frozenset({
    "gate_0",
    "python_compileall",
    "app_did_not_construct",
    "app_did_not_come_up_after_the_reload",
    "runner_not_armed",
    "theme_gate",
    "smoke_test",
    "claims_gate",
    "perf_gate",
})

#: Why the quarantine record itself could not be read. `held` carries no key of
#: its own — "a commit is held" and "no commit is held" are the two ordinary
#: answers, and the second one has a sentence the same way the first does.
QUARANTINE_REASON_KEYS = frozenset({"unreadable", "malformed"})

#: What a release request can answer.
RELEASE_WRITTEN = "written"
RELEASE_NOTHING_HELD = "nothing_held"
RELEASE_DIR_MISSING = "dir_missing"
RELEASE_NOT_WRITABLE = "not_writable"
RELEASE_FAILED = "failed"
RELEASE_KEYS = frozenset({RELEASE_WRITTEN, RELEASE_NOTHING_HELD,
                          RELEASE_DIR_MISSING, RELEASE_NOT_WRITABLE, RELEASE_FAILED})


def gate_key(reason: str | None) -> str:
    """Which gate refused this, as a stable key rather than a sentence.

    The runner writes `theme gate (exit 3)`, `perf gate (slower than the last
    release that passed)`, and so on. Everything from the first `(` is its own
    explanation and is shown as it stands; the name in front of it is the part
    that has to be said in the reader's language.
    """
    if not reason:
        return GATE_UNKNOWN
    head = reason.split("(", 1)[0]
    slug = re.sub(r"[^a-z0-9]+", "_", head.strip().lower()).strip("_")
    return slug if slug in GATE_KEYS else GATE_UNKNOWN


def _commit_subject(repo: pathlib.Path, sha: str) -> str | None:
    """What the refused commit says it does, from this checkout's own history.

    Read-only, and it may simply be absent: a rollback moves HEAD, so the refused
    commit is in the object database rather than on a branch. Not being able to say
    is one of the answers here, never a blank that reads as "nothing".
    """
    git = _git()
    if git is None:
        return None
    rc, line = _git_out(git, repo, "log", "-1", "--format=%s", sha)
    return line if rc == 0 and line else None


def quarantine_state(path: pathlib.Path, repo: pathlib.Path, *,
                     now: _dt.datetime) -> dict:
    """The commit the runner is holding, by which gate, since when.

    The runner writes three lines — the full sha, the time it was refused, and the
    gate's own sentence — so this reads the runner's record instead of keeping a
    second copy of the same fact, and a page cannot disagree with the box.

    Anything unreadable or malformed is *reported*, not treated as "nothing held":
    a blank here would read as "no release is stuck", which is the one wrong answer
    that looks exactly like the right one.
    """
    state: dict = {
        "path": str(path), "held": False, "reason_key": None, "detail": None,
        "sha": None, "short": None, "subject": None, "refused_at": None,
        "age_seconds": None, "gate": None, "gate_key": None,
    }
    text, detail = _read(path)
    if text is None:
        if detail != "absent":
            state["reason_key"] = "unreadable"
            state["detail"] = detail
        return state

    lines = text.splitlines()
    sha = lines[0].strip() if lines else ""
    when = lines[1].strip() if len(lines) > 1 else ""
    reason = lines[2].strip() if len(lines) > 2 else ""
    if not re.fullmatch(r"[0-9a-fA-F]{40}", sha):
        state["reason_key"] = "malformed"
        state["detail"] = sha or None
        return state

    state["held"] = True
    state["sha"] = sha.lower()
    state["short"] = sha[:7]
    state["refused_at"] = when or None
    state["age_seconds"] = _age_seconds(when or None, now)
    state["gate"] = reason or None
    state["gate_key"] = gate_key(reason)
    state["subject"] = _commit_subject(repo, sha)
    return state


# ── the refusal that is about the box, not about a commit ────────────────────
#
# The runner refuses a whole run before it fetches anything when this box is not
# armed to check a release: no launcher but a copy, no gate config, no roster. It
# then writes the checker's own report here and exits 15. Nothing is deployed and
# nothing is fetched, so there is no quarantine record to look at, no checkout
# movement, and no symptom on the site — the box simply stops taking releases,
# which from outside is indistinguishable from "no new commits". Hence this card.
#
# The vocabulary matches the quarantine card's on purpose. `present` and
# `unreadable` are different keys because they need different remedies, and a
# blank must never stand for either: absent is the ordinary answer (nothing has
# refused), and it is the one that has to be said out loud rather than shown by
# omission.
UNARMED_NONE = "none"
UNARMED_PRESENT = "present"
UNARMED_UNREADABLE = "unreadable"
UNARMED_KEYS = frozenset({UNARMED_NONE, UNARMED_PRESENT, UNARMED_UNREADABLE})


def unarmed_state(path: pathlib.Path, *, now: _dt.datetime) -> dict:
    """The last box-side refusal, as the runner recorded it.

    `deploy/scangrade-deploy.sh` writes two things into the file: the ISO time it
    refused, then the checker's own multi-line report. The report is shown as it
    stands — it is the runner's own words, and rewriting it here would put a
    second, weaker copy of `arm-auto-deploy.sh --check` in this module.
    """
    state: dict = {
        "path": str(path), "present": False, "key": UNARMED_NONE, "at": None,
        "age_seconds": None, "detail": None, "reason": None,
    }
    text, why = _read(path)
    if text is None:
        if why != "absent":
            state["key"] = UNARMED_UNREADABLE
            state["reason"] = why
        return state

    lines = text.splitlines()
    when = lines[0].strip() if lines else ""
    # The report keeps its own indentation: the checker aligns its readings in a
    # column, and `.strip()` on the whole block eats the first line's, which is
    # the one a reader scans. Blank lines before it go; nothing else does.
    body = lines[1:]
    while body and not body[0].strip():
        body.pop(0)
    report = "\n".join(body).rstrip()
    state["present"] = True
    state["key"] = UNARMED_PRESENT
    state["at"] = when or None
    state["age_seconds"] = _age_seconds(when or None, now)
    # A record with nothing after the timestamp is still a refusal on record; the
    # gap is the checker's silence, not this page's licence to say "absent".
    state["detail"] = report or None
    return state


def request_release(*, request_file=None, quarantine_file=None, repo=None,
                    now: _dt.datetime | None = None) -> dict:
    """Ask the runner, from the page, to retry the refused commit exactly once.

    Why a file: this is the only channel a web request has to a script that runs as
    root, and it is deliberately one bit wide. The runner reads whether
    `requests/release` **exists** and ignores what is in it, so nothing a page can
    write ever becomes something root executes.

    Why nothing is written when no commit is held: a request that outlived its
    quarantine would sit in that directory and release the *next* refusal — the
    standing override the runner's own design refuses to have. So the honest answer
    is "there is nothing to release", never a file waiting for something to release.
    """
    now = now or _dt.datetime.now(_dt.timezone.utc)
    repo = pathlib.Path(repo or os.environ.get("SCANGRADE_REPO") or DEFAULT_REPO)
    quarantine_file = pathlib.Path(
        quarantine_file or os.environ.get("SCANGRADE_QUARANTINE_FILE")
        or DEFAULT_QUARANTINE_FILE)
    request_file = pathlib.Path(
        request_file or os.environ.get("SCANGRADE_RELEASE_REQUEST")
        or (DEFAULT_REQUEST_DIR + "/release"))

    held = quarantine_state(quarantine_file, repo, now=now)
    result = {"key": None, "written": False, "path": str(request_file),
              "detail": None, "held": held["sha"], "gate": held["gate"]}

    if not held["held"]:
        result["key"] = RELEASE_NOTHING_HELD
        return result
    if not request_file.parent.is_dir():
        result["key"] = RELEASE_DIR_MISSING
        result["detail"] = str(request_file.parent)
        return result

    try:
        request_file.write_text(
            f"{held['sha']}\n{now.isoformat(timespec='seconds')}\n"
            f"requested from /super-admin/deploy-status\n",
            encoding="utf-8")
    except PermissionError as exc:
        result["key"] = RELEASE_NOT_WRITABLE
        result["detail"] = str(exc)
        return result
    except OSError as exc:
        result["key"] = RELEASE_FAILED
        result["detail"] = f"{type(exc).__name__}: {exc}"
        return result

    result["key"] = RELEASE_WRITTEN
    result["written"] = True
    return result


def _dir_writable(path: pathlib.Path) -> bool | None:
    """Whether this process could drop a request here.

    `None` is not "no": there is no directory, which needs the installer run once,
    while `False` is a directory whose permissions need looking at. Two different
    remedies, so they are two different answers.

    The temptation is `if not path.is_dir(): return None`, and it is wrong: `is_dir`
    answers False for *any* OSError, including the permission error this process
    gets when it cannot traverse a parent directory. That turned "the app cannot
    reach its own state directory" into "the installer has never been run here" —
    a remedy that had already been applied. A stat that is allowed to raise keeps
    the two apart.
    """
    try:
        mode = path.stat().st_mode
    except FileNotFoundError:
        return None
    except OSError:
        # It exists (or something on the path does) and we are not allowed to
        # find out. That is False's job, not None's.
        return False
    if not stat.S_ISDIR(mode):
        return False
    return os.access(path, os.W_OK)


def _exists(path: pathlib.Path) -> bool:
    try:
        return path.exists()
    except OSError:
        return False


# ── running a command, read-only ─────────────────────────────────────────────

def _git() -> str | None:
    found = shutil.which("git")
    if found:
        return found
    for candidate in GIT_FALLBACKS:
        if os.path.exists(candidate):
            return candidate
    return None


def _run(argv: list[str], timeout: int = 10) -> tuple[int, str]:
    """(returncode, stdout). Never raises: a command that cannot run is a reason."""
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return 255, f"{type(exc).__name__}: {exc}"
    return done.returncode, (done.stdout or "").strip()


def _git_out(git: str, repo: pathlib.Path, *args: str) -> tuple[int, str]:
    """`git --no-optional-locks -C <repo> …`.

    The flag is not decoration: a plain `git status` may refresh and rewrite the
    index, so a status page would be a writer — on the checkout the deploy is
    about to use, which is the last place to take a lock from a web request.
    """
    return _run([git, "--no-optional-locks", "-C", str(repo), *args])


def _git_raw(git: str, repo: pathlib.Path, *args: str) -> tuple[int, str]:
    """The same call, *without* the `.strip()`.

    `_git_out` trims because every other caller wants a line. `cat-file` hands back
    a whole file, and trimming it drops the trailing newline the installed file
    has — so a launcher could never match the revision it was rendered from, and
    every stale launcher would read as *unmatchable* rather than merely old. The
    test that names a stale launcher found this.
    """
    try:
        done = subprocess.run([git, "--no-optional-locks", "-C", str(repo), *args],
                              capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        return 255, f"{type(exc).__name__}: {exc}"
    return done.returncode, (done.stdout or "")


def _git_bytes(git: str, repo: pathlib.Path, *args: str) -> tuple[int, bytes]:
    """Undecoded, so a blob can be compared with the bytes on disk.

    Comparing a blob through `text=True` would normalise line endings, and the
    question here is exactly which bytes the installed file has.
    """
    try:
        done = subprocess.run([git, "--no-optional-locks", "-C", str(repo), *args],
                              capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        return 255, b""
    return done.returncode, (done.stdout or b"")


def _read(path: pathlib.Path) -> tuple[str | None, str | None]:
    """(text, detail of why it could not be read)."""
    try:
        return path.read_text(encoding="utf-8", errors="replace"), None
    except FileNotFoundError:
        return None, "absent"
    except OSError as exc:
        return None, f"{type(exc).__name__}: {exc}"


def _read_bytes(path: pathlib.Path) -> tuple[bytes | None, str | None]:
    """The same read, undecoded — what `cmp -s` in Gate 0 actually sees."""
    try:
        return path.read_bytes(), None
    except FileNotFoundError:
        return None, "absent"
    except OSError as exc:
        return None, f"{type(exc).__name__}: {exc}"


def _blob_sha(data: bytes) -> str:
    """The sha1 git would give these bytes as a blob, so history can be searched.

    `git log --find-object` takes that hash and returns the commits carrying it —
    one command, exact, and it does not need the file to exist in the index.
    """
    return hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()


def _head_sha(git: str, repo: pathlib.Path) -> str | None:
    rc, sha = _git_out(git, repo, "rev-parse", "HEAD")
    return sha if rc == 0 and sha else None


def _origin_facts(git: str, repo: pathlib.Path, commit: str | None, *,
                  head: str | None, key: str = ORIGIN_NAMED) -> dict:
    """The commit the installed file came from, and what has moved since.

    `origin_stale_files` is the count of files under `deploy/` that differ between
    that commit and HEAD, which is the number behind "every gate added since is
    not in effect" — a commit count is honest but says nothing about *what*.
    """
    facts = {
        "origin_key": key, "origin_commit": None, "origin_short": None,
        "origin_date": None, "origin_subject": None, "origin_behind": None,
        "origin_stale_files": None,
    }
    if not commit:
        return facts
    facts["origin_commit"] = commit
    rc, line = _git_out(git, repo, "log", "-1", "--format=%h|%cI|%s", commit)
    if rc == 0 and "|" in line:
        short, date, subject = line.split("|", 2)
        facts["origin_short"], facts["origin_date"], facts["origin_subject"] = short, date, subject
    if head:
        rc, count = _git_out(git, repo, "rev-list", "--count", f"{commit}..{head}")
        if rc == 0 and count.isdigit():
            facts["origin_behind"] = int(count)
    rc, names = _git_out(git, repo, "diff", "--name-only", commit,
                         head or "HEAD", "--", DEPLOY_DIR)
    if rc == 0:
        facts["origin_stale_files"] = len([n for n in names.splitlines() if n.strip()])
    return facts


def _origin_of_copy(git: str, repo: pathlib.Path, data: bytes, copy_of: str, *,
                    head: str | None) -> dict:
    """Which commit an installed *copy* was taken from — by its bytes.

    A copy is normally installed from a clean checkout, so its bytes are one of
    the committed blobs; `--all` so a force-pushed or side-branch install is still
    nameable. When nothing matches, the answer is `unmatched` rather than an
    invented distance.
    """
    rc, out = _git_out(git, repo, "log", "--all", "--format=%H",
                       f"--find-object={_blob_sha(data)}", "--",
                       f"{DEPLOY_DIR}/{copy_of}")
    if rc != 0:
        return _origin_facts(git, repo, None, head=head, key=ORIGIN_UNREADABLE)
    # `--find-object` reports every commit whose diff *changed the count* of that
    # object — which includes the commit that **removed** it, i.e. the one where a
    # later version replaced the file. Taking the first line therefore named the
    # newest commit, and the distance came out as zero for a runner that was a
    # commit behind. So each candidate is checked: which commit actually holds
    # these bytes at this path.
    for rev in out.splitlines():
        rc, blob = _git_bytes(git, repo, "cat-file", "blob",
                              f"{rev}:{DEPLOY_DIR}/{copy_of}")
        if rc == 0 and blob == data:
            return _origin_facts(git, repo, rev, head=head)
    return _origin_facts(git, repo, None, head=head, key=ORIGIN_UNMATCHED)


def _origin_of_launcher(git: str, repo: pathlib.Path, text: str, *,
                       head: str | None) -> dict:
    """Which committed `entrypoint.sh` renders to the installed launcher.

    The rendered file is not a blob git ever stored (the placeholder is
    substituted), so the search renders each recent revision and compares — which
    is why it is bounded: the page must not walk all of history on a request.
    """
    rc, revs = _git_out(git, repo, "log", "-n", str(LAUNCHER_SCAN_LIMIT),
                        "--format=%H", "--", f"{DEPLOY_DIR}/entrypoint.sh")
    if rc != 0:
        return _origin_facts(git, repo, None, head=head, key=ORIGIN_UNREADABLE)
    for rev in revs.splitlines():
        rc, blob = _git_raw(git, repo, "cat-file", "blob",
                            f"{rev}:{DEPLOY_DIR}/entrypoint.sh")
        if rc == 0 and blob.replace(PLACEHOLDER, str(repo)) == text:
            return _origin_facts(git, repo, rev, head=head)
    return _origin_facts(git, repo, None, head=head, key=ORIGIN_UNMATCHED)


def _mtime(path: pathlib.Path) -> str | None:
    try:
        return _dt.datetime.fromtimestamp(
            path.stat().st_mtime, _dt.timezone.utc).isoformat(timespec="seconds")
    except OSError:
        return None


def _age_seconds(iso: str | None, now: _dt.datetime) -> int | None:
    if not iso:
        return None
    try:
        return max(0, int((now - _dt.datetime.fromisoformat(iso)).total_seconds()))
    except ValueError:
        return None


# ── the installed runner ─────────────────────────────────────────────────────

def expected_launcher(repo: pathlib.Path) -> tuple[str | None, dict | None]:
    """What `install-auto-deploy.sh` would write for this checkout, or why not.

    The comparison is against the *rendered* file rather than either mtime: a
    `git pull` rewrites every mtime in the checkout, so a launcher installed
    before a pull would look fresh while holding older text.
    """
    text, detail = _read(repo / "deploy" / "entrypoint.sh")
    if text is None:
        return None, {"reason_key": "entrypoint_unreadable", "detail": detail}
    return text.replace(PLACEHOLDER, str(repo)), None


def runner_state(path: pathlib.Path, repo: pathlib.Path, *,
                 expect: str | None = None, copy_of: str = "scangrade-deploy.sh") -> dict:
    """What is installed at `path`: a launcher, a copy, or nothing to read.

    `expect` is the rendered launcher for this checkout, so the caller renders it
    once and uses it for both installed paths. `copy_of` names the checkout file a
    *copy* would have been taken from — `scangrade-deploy.sh` for the deploy,
    `scangrade-db-snapshot.sh` for the snapshot command, which was the one that was
    silently broken while installed as a copy.
    """
    state: dict = {
        "path": str(path), "exists": False, "size": None, "installed_at": None,
        "sha256": None, "kind": UNKNOWN, "reason_key": None, "detail": None,
        "gate0": UNKNOWN, "differs_from_checkout": None, "rendered_repo": None,
        "matches_this_commit": None, "expected_paths": None,
        "has_identity_check": None,
        "origin_key": ORIGIN_UNREADABLE, "origin_commit": None, "origin_short": None,
        "origin_date": None, "origin_subject": None, "origin_behind": None,
        "origin_stale_files": None,
    }

    text, detail = _read(path)
    if text is None:
        state["reason_key"] = ("absent" if detail == "absent" else "unreadable")
        state["detail"] = None if detail == "absent" else detail
        return state

    state["exists"] = True
    try:
        state["size"] = path.stat().st_size
    except OSError:
        pass
    state["installed_at"] = _mtime(path)
    state["sha256"] = hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:12]

    is_launcher = (UNRENDERED not in text
                   and _DISPATCH in text
                   and bool(_EXEC.search(text)))
    rendered = _RENDERED_REPO.search(text)
    state["rendered_repo"] = rendered.group(1) if rendered else None

    if is_launcher:
        state["kind"] = "launcher"
        if not rendered:
            state["reason_key"] = "no_repo_line"
            return state
        if rendered.group(1) != str(repo):
            state["reason_key"] = "other_path"
            state["detail"] = rendered.group(1)
            return state
        # Gate 0's second branch: SELF is not the checkout's file but the file it
        # execs is — which is the arrangement, and it passes.
        state["gate0"] = GATE0_PASSES
        if expect is None:
            state["reason_key"] = "entrypoint_unreadable"
            return state
        state["matches_this_commit"] = text == expect
        state["expected_paths"] = [
            target for target in re.findall(r'TARGET="\$REPO/([^"]+)"', text)]
        git = _git()
        head = _head_sha(git, repo) if git else None
        if git is None or head is None:
            return state
        if state["matches_this_commit"]:
            state.update(_origin_facts(git, repo, head, head=head, key=ORIGIN_CURRENT))
        else:
            # A launcher that is not what this commit renders still runs the
            # checkout — so the interesting number is which revision it was
            # rendered from, not whether it matches. (An installed launcher never
            # reaches Gate 0's byte comparison at all; see the test that pins it.)
            state.update(_origin_of_launcher(git, repo, text, head=head))
            state["reason_key"] = "launcher_stale"
        return state

    if UNRENDERED in text:
        state["kind"] = "copy"
        state["reason_key"] = "unrendered"
        return state

    # The old arrangement: a snapshot. Gate 0 allows one that still matches the
    # checkout and refuses one that has drifted — and it compares `cmp -s`, i.e.
    # bytes. Comparing text here would normalise line endings and call a copy a
    # match that the gate then refuses with exit 14.
    state["kind"] = "copy"
    checkout_file = repo / "deploy" / copy_of
    mine, _ = _read_bytes(path)
    theirs, why = _read_bytes(checkout_file)
    if theirs is None or mine is None:
        state["reason_key"] = "checkout_file_unreadable"
        state["detail"] = f"deploy/{copy_of} ({why})"
        return state
    state["differs_from_checkout"] = mine != theirs
    state["matches_this_commit"] = not state["differs_from_checkout"]
    # Whether anything *refuses* this copy is a property of the copy, not of the
    # gate: Gate 0 lives in the file that runs, so an installed revision from
    # before it existed compares itself with nothing and deploys regardless. The
    # first version of this page assumed the gate was there and told a box that was
    # deploying all week that nothing could deploy.
    state["has_identity_check"] = IDENTITY_MARKER in text
    if not state["differs_from_checkout"]:
        state["gate0"] = GATE0_COPY_MATCHES
    elif state["has_identity_check"]:
        state["gate0"] = GATE0_REFUSES
    else:
        state["gate0"] = GATE0_CANNOT
    if state["differs_from_checkout"]:
        state["reason_key"] = "drifted"
        state["detail"] = f"deploy/{copy_of}"

    git = _git()
    head = _head_sha(git, repo) if git else None
    if git is None or head is None:
        return state
    if state["matches_this_commit"]:
        state.update(_origin_facts(git, repo, head, head=head, key=ORIGIN_CURRENT))
    else:
        state.update(_origin_of_copy(git, repo, mine, copy_of, head=head))
    return state


# ── the checkout ─────────────────────────────────────────────────────────────

ORIGIN_REFS = ("refs/remotes/origin/main", "refs/remotes/origin/HEAD")


def checkout_state(repo: pathlib.Path, *, now: _dt.datetime) -> dict:
    """Where the checkout is relative to the `origin/main` it last fetched.

    Nothing here fetches. `behind` is therefore "behind the remote-tracking ref as
    this checkout last saw it", and the age of that ref travels with the number so
    a stale figure cannot read as a live one.
    """
    state: dict = {
        "path": str(repo), "available": False, "reason_key": None, "detail": None,
        "git": None, "branch": None, "head": None, "head_subject": None,
        "head_date": None, "origin": None, "behind": None, "ahead": None,
        "dirty": None, "origin_updated_at": None, "origin_age_seconds": None,
        "detached": False,
    }

    if not (repo / ".git").exists():
        state["reason_key"] = "not_a_checkout"
        return state

    git = _git()
    if git is None:
        state["reason_key"] = "no_git"
        return state
    state["git"] = git

    rc, branch = _git_out(git, repo, "rev-parse", "--abbrev-ref", "HEAD")
    if rc != 0:
        state["reason_key"] = "branch_unreadable"
        state["detail"] = branch
        return state
    state["available"] = True
    state["detached"] = branch == "HEAD"
    state["branch"] = None if state["detached"] else branch

    rc, head = _git_out(git, repo, "rev-parse", "--short", "HEAD")
    state["head"] = head if rc == 0 else None

    rc, line = _git_out(git, repo, "log", "-1", "--format=%s|%cI")
    if rc == 0 and "|" in line:
        subject, when = line.split("|", 1)
        state["head_subject"], state["head_date"] = subject, when

    # The branch this deploys is the ref the timer fetches. Read it rather than
    # assuming `main` is what is configured.
    rc, origin = _git_out(git, repo, "rev-parse", "--short", "origin/main")
    state["origin"] = origin if rc == 0 else None

    if state["origin"] and state["head"]:
        rc, count = _git_out(git, repo, "rev-list", "--count", "HEAD..origin/main")
        state["behind"] = int(count) if rc == 0 and count.isdigit() else None
        rc, count = _git_out(git, repo, "rev-list", "--count", "origin/main..HEAD")
        state["ahead"] = int(count) if rc == 0 and count.isdigit() else None

    # The deploy refuses a dirty checkout, so "why did nothing deploy" has to be
    # answerable from here too.
    rc, porcelain = _git_out(git, repo, "status", "--porcelain")
    if rc == 0:
        state["dirty"] = len([ln for ln in porcelain.splitlines() if ln.strip()])

    for ref in ORIGIN_REFS:
        ref_file = repo / ".git" / ref
        stamp = _mtime(ref_file) if ref_file.exists() else None
        if stamp:
            state["origin_updated_at"] = stamp
            break
    else:
        # A packed ref has no file of its own; FETCH_HEAD is when the deploy last
        # fetched, which is the honest proxy.
        fetch_head = repo / ".git" / "FETCH_HEAD"
        packed = repo / ".git" / "packed-refs"
        state["origin_updated_at"] = (
            _mtime(fetch_head) if fetch_head.exists()
            else _mtime(packed) if packed.exists() else None)
    state["origin_age_seconds"] = _age_seconds(state["origin_updated_at"], now)
    return state


# ── the verdict ──────────────────────────────────────────────────────────────

def verdict(runner: dict, checkout: dict, *, paused: bool,
            unarmed: dict | None = None) -> dict:
    """One level and one reason key, in the order the failures actually bite.

    A runner that cannot pass Gate 0 is the headline even when the checkout is
    also behind: it is the one state where *nothing* will deploy, however much is
    waiting. `paused` is reported separately rather than folded in, because a
    frozen box is somebody's decision and a broken runner is not.

    `unarmed` is the runner's own record of refusing a whole run because this box
    cannot check a release, and it sits above everything below it: the arrangement
    can be perfect and a stale launcher can be a warning, but a box the deploy
    refuses deploys nothing at all. Reporting `fresh` beside that record would be
    the page telling an operator the opposite of what the box is doing.
    """
    out = {"level": UNKNOWN, "key": None, "detail": None, "behind": None,
           "runner_behind": None, "runner_from": None}

    if runner["kind"] == UNKNOWN:
        return {**out, "key": runner["reason_key"] or "unreadable",
                "detail": runner["detail"]}

    if runner["kind"] == "copy":
        if runner["gate0"] == GATE0_REFUSES:
            return {**out, "level": BROKEN, "key": "copy_drifted",
                    "detail": runner["detail"], "behind": checkout.get("behind")}
        if runner["gate0"] == GATE0_CANNOT:
            # Worse than the refusal, and quieter: nothing stops this copy, so
            # releases keep going out with the deploy logic of the commit it was
            # taken from while the site looks healthy. It is a `broken` level
            # because the gates this repository now relies on are simply not in
            # the file that runs them.
            return {**out, "level": BROKEN, "key": "copy_predates_gate",
                    "detail": runner["detail"], "behind": checkout.get("behind"),
                    "runner_behind": runner.get("origin_behind"),
                    "runner_from": runner.get("origin_short")}
        if runner["reason_key"]:
            return {**out, "level": WARN, "key": runner["reason_key"],
                    "detail": runner["detail"]}
        return {**out, "level": WARN, "key": "copy_matches"}

    if runner["gate0"] != GATE0_PASSES:
        return {**out, "level": WARN,
                "key": runner["reason_key"] or "launcher_unreadable",
                "detail": runner["detail"]}
    if unarmed and unarmed.get("present"):
        return {**out, "level": BROKEN, "key": "unarmed",
                "detail": unarmed.get("at")}
    if runner["matches_this_commit"] is False:
        return {**out, "level": WARN, "key": "launcher_stale"}
    if paused:
        return {**out, "level": WARN, "key": "paused"}
    if not checkout["available"]:
        return {**out, "level": UNKNOWN, "key": checkout["reason_key"] or "checkout_unreadable",
                "detail": checkout["detail"]}
    if checkout.get("dirty"):
        return {**out, "level": WARN, "key": "dirty", "detail": str(checkout["dirty"])}
    behind = checkout.get("behind")
    if isinstance(behind, int) and behind > 0:
        return {**out, "level": WARN, "key": "behind", "detail": str(behind),
                "behind": behind}
    return {**out, "level": FRESH, "key": "fresh"}


#: Every key `runner_state`, `checkout_state` and `verdict` can emit. The template
#: has a sentence for each and `tests/unit/test_deploy_status.py` fails if one is
#: missing, so a new reading cannot ship as a blank line.
REASON_KEYS = frozenset({
    # the installed runner
    "absent", "unreadable", "unrendered", "no_repo_line", "other_path",
    "entrypoint_unreadable", "launcher_stale", "checkout_file_unreadable",
    "drifted", "copy_matches", "copy_drifted", "launcher_unreadable",
    "copy_predates_gate",
    # the checkout
    "not_a_checkout", "no_git", "branch_unreadable", "checkout_unreadable",
    # the verdict
    "paused", "dirty", "behind", "fresh",
    # the runner's record of refusing a whole run (the box, not a commit)
    "unarmed",
})


def report(*, repo=None, runner=None, snapshot_runner=None, pause_file=None,
           quarantine_file=None, unarmed_file=None, request_dir=None,
           release_request=None, now: _dt.datetime | None = None) -> dict:
    """Everything the page shows. Any single part may be `unknown` with a reason."""
    now = now or _dt.datetime.now(_dt.timezone.utc)
    repo = pathlib.Path(repo or os.environ.get("SCANGRADE_REPO") or DEFAULT_REPO)
    runner = pathlib.Path(runner or os.environ.get("SCANGRADE_RUNNER") or DEFAULT_RUNNER)
    snapshot_runner = pathlib.Path(
        snapshot_runner or os.environ.get("SCANGRADE_SNAPSHOT_RUNNER")
        or DEFAULT_SNAPSHOT_RUNNER)
    pause_file = pathlib.Path(
        pause_file or os.environ.get("SCANGRADE_PAUSE_FILE") or DEFAULT_PAUSE_FILE)
    quarantine_file = pathlib.Path(
        quarantine_file or os.environ.get("SCANGRADE_QUARANTINE_FILE")
        or DEFAULT_QUARANTINE_FILE)
    unarmed_file = pathlib.Path(
        unarmed_file or os.environ.get("SCANGRADE_UNARMED_FILE")
        or DEFAULT_UNARMED_FILE)
    unarmed = unarmed_state(unarmed_file, now=now)
    request_dir = pathlib.Path(
        request_dir or os.environ.get("SCANGRADE_REQUEST_DIR") or DEFAULT_REQUEST_DIR)
    release_request = pathlib.Path(
        release_request or os.environ.get("SCANGRADE_RELEASE_REQUEST")
        or str(request_dir / "release"))

    expect, expect_reason = expected_launcher(repo)
    main = runner_state(runner, repo, expect=expect)
    snapshot = runner_state(snapshot_runner, repo, expect=expect,
                            copy_of="scangrade-db-snapshot.sh")
    checkout = checkout_state(repo, now=now)
    try:
        paused = pause_file.exists()
    except OSError:
        paused = False

    return {
        "measured_at": now.isoformat(timespec="seconds"),
        "repo": str(repo),
        "runner": main,
        "snapshot_runner": snapshot,
        "checkout": checkout,
        "paused": paused,
        "pause_file": str(pause_file),
        "launcher": expect_reason,
        "verdict": verdict(main, checkout, paused=paused, unarmed=unarmed),
        "quarantine": quarantine_state(quarantine_file, repo, now=now),
        "quarantine_file": str(quarantine_file),
        "unarmed": unarmed_state(unarmed_file, now=now),
        "unarmed_file": str(unarmed_file),
        "release_file": DEFAULT_RELEASE_FILE,
        "release_file_present": _exists(pathlib.Path(DEFAULT_RELEASE_FILE)),
        "request_dir": str(request_dir),
        "request_path": str(release_request),
        "request_pending": _exists(release_request),
        "request_dir_writable": _dir_writable(request_dir),
    }


if __name__ == "__main__":                                   # pragma: no cover
    # Same reason `schema_contract.py` has one: the answer has to be reachable on
    # the box, where the page is not the only reader.
    import json
    print(json.dumps(report(), indent=2))
