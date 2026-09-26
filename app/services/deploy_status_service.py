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
import json
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
#: The runner's bounded history of past refusals, beside the quarantine record — see
#: `REFUSALS_DIR` in `deploy/scangrade-deploy.sh`. A quarantine answers "what is held
#: now"; it is replaced by the next refusal and deleted by the release that lifts it,
#: so the numbers behind a refusal left the page at the moment a later release passed
#: and somebody came to look. This answers "what has been refused lately" from the
#: records the runner already wrote.
DEFAULT_REFUSALS_DIR = DEFAULT_STATE_DIR + "/refusals"
DEFAULT_RELEASE_FILE = "/etc/scangrade-deploy.release"
DEFAULT_REQUEST_DIR = DEFAULT_STATE_DIR + "/requests"
#: The runner's record of a refusal that is about the *box* rather than about a
#: commit: `armament_preflight` writes the checker's own report here and deletes it
#: the moment the box is armed again. Same directory as the quarantine record, and
#: the opposite answer — "no commit is held" and "no release will deploy at all"
#: were being told apart by nothing.
DEFAULT_UNARMED_FILE = DEFAULT_STATE_DIR + "/unarmed"
#: The runner's record of a refusal that happens before a release is merged — the
#: half of "why is nothing deploying?" that had no record at all. A quarantine is
#: about a commit; this is about the step that stopped the run getting to one.
DEFAULT_PREFLIGHT_FILE = DEFAULT_STATE_DIR + "/refused-before-merge"
#: The runner's record of the last run that ended non-zero, whatever stopped it.
#: Written by its `EXIT` trap and deleted by the next run that finishes, so its
#: presence means the last tick stopped. The named refusals already had records;
#: this is for the ones that did not — `pip install` failing after a merge, or a
#: path nobody has written yet — which is why the trap names the *step* as well as
#: the code: "exit 7" answers nothing on its own.
DEFAULT_LAST_STOP_FILE = DEFAULT_STATE_DIR + "/last-stop"
#: What the *performance* gate measured, in its own words. `deploy/perf_gate.py`
#: appends one JSON line per judgement to the history file — the commit under
#: judgement, the verdict, every number, and on a regression the `reasons` it
#: printed — and writes the baseline it compared against beside it. Both are named
#: by the gate (`DEFAULT_EVIDENCE`, `DEFAULT_BASELINE`), and `install-auto-deploy.sh`
#: writes those same two paths into `/etc/scangrade-perf.conf` (`PERF_EVIDENCE`,
#: `PERF_BASELINE`) instead of inventing its own — so the three agree by
#: construction, and when this page cannot find the history it says so rather than
#: reporting that nothing was ever refused.
#:
#: The page already names the gate and quotes the runner's one-line reason, which is
#: a *which* and not a *what*: "slower than the last release that passed" cannot be
#: told from two noisy probes, or acted on, without the numbers underneath it. Those
#: numbers were being written down and then read by nobody but `journalctl`.
DEFAULT_PERF_HISTORY_FILE = DEFAULT_STATE_DIR + "/perf/history.jsonl"
DEFAULT_PERF_BASELINE_FILE = DEFAULT_STATE_DIR + "/perf/baseline.json"

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
#: A release asked for one commit when a *different* one is under judgement. The
#: runner deploys `origin/$BRANCH`, so it can retry a refused commit only while the
#: branch still points at it; a request naming an older commit is not one the runner
#: could honour, and writing the file anyway would clear a quarantine for the wrong
#: release. This is the answer for that row.
RELEASE_NOT_HELD = "not_held"
RELEASE_DIR_MISSING = "dir_missing"
RELEASE_NOT_WRITABLE = "not_writable"
RELEASE_FAILED = "failed"
RELEASE_KEYS = frozenset({RELEASE_WRITTEN, RELEASE_NOTHING_HELD, RELEASE_NOT_HELD,
                          RELEASE_DIR_MISSING, RELEASE_NOT_WRITABLE, RELEASE_FAILED})

#: What a re-baseline request can answer. A release request retries a refused commit;
#: this one asks the runner to re-describe the *box*, so it shares the write answers
#: and the "nothing is pending" one, and has no per-commit objection of its own.
REBASELINE_WRITTEN = "written"
REBASELINE_NOTHING_HELD = "nothing_held"
REBASELINE_DIR_MISSING = "dir_missing"
REBASELINE_NOT_WRITABLE = "not_writable"
REBASELINE_FAILED = "failed"
REBASELINE_KEYS = frozenset({REBASELINE_WRITTEN, REBASELINE_NOTHING_HELD,
                             REBASELINE_DIR_MISSING, REBASELINE_NOT_WRITABLE,
                             REBASELINE_FAILED})


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
    return line if rc == 0 and line else None#: How many of the refusing gate's own lines the card shows, and how wide each one.
#: The runner bounds what it *writes*; this bounds what the page *renders*, because
#: the record is a file an operator — or an older runner — can put anything into,
#: and the card is a page a browser has to lay out.
QUARANTINE_DETAIL_SHOWN = 6
QUARANTINE_DETAIL_WIDTH = 400

#: How many past refusals the page lists, and the prefix the runner names each record
#: with. The bound is the *page's* own rather than the runner's: the directory is a
#: file tree an operator — or an older runner — can put anything into, so the page
#: renders at most this many and reports how many it found.
REFUSALS_SHOWN = 5
REFUSAL_PREFIX = "refused-"
#: What the history reader can answer. `none` is the ordinary answer (nothing has
#: been refused, or the runner predates the history), and it is said out loud rather
#: than shown by a blank; `unreadable` is the different answer that must not be read
#: as `none`. `present` carries the records themselves.
REFUSALS_NONE = "none"
REFUSALS_PRESENT = "present"
REFUSALS_UNREADABLE = "unreadable"
REFUSALS_KEYS = frozenset({REFUSALS_NONE, REFUSALS_PRESENT, REFUSALS_UNREADABLE})
#: Control bytes that must never reach the page. A gate's output is text, but a
#: file is not obliged to be: a card a reader cannot read is worse than no card.
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def _gate_lines(raw: list[str]) -> list[str]:
    """The gate's own lines, cleaned and bounded for a page to render."""
    out = []
    for line in raw:
        cleaned = _CONTROL.sub("", line).strip()
        if cleaned:
            out.append(cleaned[:QUARANTINE_DETAIL_WIDTH])
    return out


def quarantine_state(path: pathlib.Path, repo: pathlib.Path, *, 
                     now: _dt.datetime) -> dict:
    """The commit the runner is holding, by which gate, since when, and why.

    The runner writes the full sha, the time it was refused, and the gate's own
    sentence as the first three lines — so this reads the runner's record instead of
    keeping a second copy of the same fact, and a page cannot disagree with the box.
    Everything from line 4 on is the refusing gate's own output, written there for
    the refusals that *were* a measurement: "slower than the last release that
    passed" is a which, and the ratio that makes it actionable was reaching nobody
    without a shell.

    Anything unreadable or malformed is *reported*, not treated as "nothing held":
    a blank here would read as "no release is stuck", which is the one wrong answer
    that looks exactly like the right one.
    """
    state: dict = {
        "path": str(path), "held": False, "reason_key": None, "detail": None,
        "sha": None, "short": None, "subject": None, "refused_at": None,
        "age_seconds": None, "gate": None, "gate_key": None,
        "reasons": [], "reasons_total": 0,
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
        # A record whose sha is not one is not the record this card describes, so the
        # lines under it are not findings about a held release — they are whatever
        # that file happens to say.
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
    quoted = _gate_lines(lines[3:])
    state["reasons_total"] = len(quoted)
    state["reasons"] = quoted[:QUARANTINE_DETAIL_SHOWN]
    return state


def _refusal_record(text: str, *, now: _dt.datetime,
                    name: str | None = None) -> dict:
    """One record as the runner wrote it, without the commit's subject resolved.

    The same three positional lines as the quarantine file, on purpose: a record the
    runner keeps in its history is byte-for-byte the quarantine file, so a reader
    that could parse one and not the other would be two answers to one question.
    """
    record: dict = {
        "name": name, "held": False, "sha": None, "short": None, "subject": None,
        "refused_at": None, "age_seconds": None, "gate": None, "gate_key": None,
        "reasons": [], "reasons_total": 0, "reason_key": None, "detail": None,
    }
    lines = text.splitlines()
    sha = lines[0].strip() if lines else ""
    when = lines[1].strip() if len(lines) > 1 else ""
    reason = lines[2].strip() if len(lines) > 2 else ""
    if not re.fullmatch(r"[0-9a-fA-F]{40}", sha):
        # A record whose first line is not a sha is not this card's kind of record;
        # it is reported rather than dropped, because a gap in the history is how
        # "nothing was refused" starts reading as the truth.
        record["reason_key"] = "malformed"
        record["detail"] = sha or None
        return record
    record["held"] = True
    record["sha"] = sha.lower()
    record["short"] = sha[:7]
    record["refused_at"] = when or None
    record["age_seconds"] = _age_seconds(when or None, now)
    record["gate"] = reason or None
    record["gate_key"] = gate_key(reason)
    quoted = _gate_lines(lines[3:])
    record["reasons_total"] = len(quoted)
    record["reasons"] = quoted[:QUARANTINE_DETAIL_SHOWN]
    return record


def refusal_history_state(directory: pathlib.Path, repo: pathlib.Path, *,
                          now: _dt.datetime,
                          limit: int = REFUSALS_SHOWN) -> dict:
    """The last few refusals the runner kept, newest first.

    A quarantine is replaced by the next refusal and removed by the release that
    lifts it — both correct for the tick, and both erasing the evidence exactly when
    a later release has passed and somebody comes looking. This reads the copies the
    runner keeps for that reason, so the numbers behind a refusal outlive the release
    that replaces it.

    `none`, `present` and `unreadable` are three different answers, and the empty
    directory is `none` rather than an error: a box that has never had a refusal, or
    one whose runner predates the history, is not a box whose history could not be
    read. Only being unable to list a directory that is there is `unreadable`.
    """
    state: dict = {
        "path": str(directory), "key": REFUSALS_NONE, "records": [],
        "total": 0, "shown": limit, "detail": None,
    }
    try:
        entries = [entry for entry in directory.iterdir()
                   if entry.name.startswith(REFUSAL_PREFIX)]
    except FileNotFoundError:
        return state
    except OSError as exc:                                 # pragma: no cover - rare
        state["key"] = REFUSALS_UNREADABLE
        state["detail"] = str(exc)
        return state
    # The name is `refused-<epoch>-<short sha>`, so sorting by name is sorting by
    # time and this needs no timestamp to stat. Newest first, because the newest
    # refusal is the one a reader is asking about.
    entries.sort(key=lambda entry: entry.name, reverse=True)
    state["total"] = len(entries)
    if not entries:
        return state
    state["key"] = REFUSALS_PRESENT
    subjects: dict = {}
    for entry in entries[:limit]:
        text, detail = _read(entry)
        if text is None:
            state["records"].append({
                "name": entry.name, "held": False, "sha": None, "short": None,
                "subject": None, "refused_at": None, "age_seconds": None,
                "gate": None, "gate_key": None, "reasons": [], "reasons_total": 0,
                "reason_key": "unreadable", "detail": detail,
            })
            continue
        record = _refusal_record(text, now=now, name=entry.name)
        sha = record["sha"]
        if record["held"] and sha is not None:
            # Resolved once per commit: a retried refusal names the same commit, and
            # a page request should not ask git the same question per record.
            if sha not in subjects:
                subjects[sha] = _commit_subject(repo, sha)
            record["subject"] = subjects[sha]
        state["records"].append(record)
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


# ── the refusal that happens before the release moves ────────────────────────
#
# The quarantine card covers a release a gate refused *after* merging it, and the
# unarmed card covers a run refused before it fetched anything. Between them sits
# the failure this page was blind to: a run that fetches fine and then stops before
# the merge — a dirty checkout, a fetch with no network, a migration release with no
# recovery point, a merge that cannot happen. Nothing is merged, so nothing is
# quarantined; nothing is reloaded, so no uptime moves; and the checkout's own
# readings look healthy, because a status reading and a *writer* disagree about what
# a stale `.git/index.lock` means. A box in that state fetches every two minutes and
# deploys nothing, indefinitely, with `No Held Release` on this page.
#
# So the runner records which step refused, when, the exit code, the commit under
# judgement and the command's own words. This reads that record and classifies the
# step into a key the template can say in either language, the way `gate_key` does
# for the quarantine. `tests/unit/test_deploy_status.py` lifts every
# `PREFLIGHT_GATE=` out of the runner and fails if one has no sentence, so a step
# added to the deploy cannot arrive here as a blank.
PREFLIGHT_NONE = "none"
PREFLIGHT_PRESENT = "present"
PREFLIGHT_UNREADABLE = "unreadable"
PREFLIGHT_MALFORMED = "malformed"
PREFLIGHT_KEYS = frozenset({PREFLIGHT_NONE, PREFLIGHT_PRESENT, PREFLIGHT_UNREADABLE,
                            PREFLIGHT_MALFORMED})

#: The steps that can refuse a release before it moves, as the runner names them.
PREFLIGHT_GATES = frozenset({
    "not_root",
    "no_checkout",
    "no_virtualenv",
    "checkout_unreadable",
    "dirty_checkout",
    "fetch_failed",
    "snapshot_refused",
    "lock_refused",
    "merge_refused",
})

#: The one step whose refusal is about the world rather than the box: a fetch that
#: could not reach GitHub retries by itself on the next tick. Warning, not breakage
#: — this is the only gate here that has ever been right about a healthy box.
PREFLIGHT_TRANSIENT = frozenset({"fetch_failed"})

#: A step this page does not know (a newer runner wrote the record). Its own name is
#: shown as it stands; the key only exists so the card has one sentence to give.
PREFLIGHT_UNKNOWN_GATE = "unknown_gate"

# ── what the runner can end with ─────────────────────────────────────────────
# `systemctl status scangrade-deploy` reports one number and no words, so a reader
# with no shell had a journal they could not read. Every code the runner can leave
# through is listed here with the *reading* it deserves, and the page writes the
# sentence in both languages.
#
# It is a contract in three directions: the runner's own `exit N` statements are
# parsed and checked against these keys, the page is checked to have a sentence for
# each, and the third direction is the one that matters to a reader — the row whose
# code this box last stopped with is marked, so "why is nothing deploying" is
# answered by the table rather than by a shell.
#
#   quiet        nothing was waiting; a tick with no work is not a failure
#   refused      a release was turned away before it moved; the previous keeps serving
#   rolled_back  it was merged, judged, and put back; the previous keeps serving
#   blocked      nothing can deploy at all until somebody acts on the box
#   attention    the box needs a human now: even the rollback is not serving
EXIT_TONES = ("quiet", "refused", "rolled_back", "blocked", "attention")

EXIT_CODES: dict[int, str] = {
    0: "quiet",         # nothing new, already current, another run holds the lock
    2: "blocked",       # not root: this run cannot reload the service
    3: "blocked",       # no checkout, or nothing in it that could serve a release
    4: "refused",       # the checkout could not be read, or carries hand edits
    5: "refused",       # the fetch could not reach GitHub (network or credentials)
    6: "refused",       # the release could not be merged into the checkout
    7: "rolled_back",   # dependencies failed to install, so it went back
    8: "rolled_back",   # the code did not compile
    9: "rolled_back",   # the app did not construct with its blueprints and routes
    10: "rolled_back",  # it reloaded, then failed the verification after it
    11: "attention",    # the rollback is not serving either
    12: "refused",      # a migration release with no recovery point
    13: "rolled_back",  # a template was unreadable in one of the themes
    14: "blocked",      # the installed runner is a copy that has drifted
    15: "blocked",      # the box is not armed to check a release
    16: "rolled_back",  # the release removed Gate 0
    17: "refused",      # the checkout's index is locked and will not be forced
}

#: The phases a run passes through, as the runner names them in its record. A step
#: is what turns "exit 7" into "it stopped installing dependencies", so the page
#: needs a sentence for each — and a name the runner invents without being added
#: here shows as itself rather than as a guess.
RUN_STEPS = frozenset({
    "start", "lock", "identity", "armament", "checkout", "fetch", "snapshot",
    "merge", "dependencies", "compile", "construct", "theme", "reload",
    "verify", "done",
})

#: A step from a newer runner. Shown as it stands, like the pre-merge gates.
RUN_STEP_UNKNOWN = "unknown_step"

#: The four answers the last-stop record can give, named the way the pre-merge
#: record's are: absent, present, unreadable, and not a record at all.
LAST_STOP_NONE = "none"
LAST_STOP_PRESENT = "present"
LAST_STOP_UNREADABLE = "unreadable"
LAST_STOP_MALFORMED = "malformed"
LAST_STOP_KEYS = frozenset({LAST_STOP_NONE, LAST_STOP_PRESENT,
                            LAST_STOP_UNREADABLE, LAST_STOP_MALFORMED})

#: The performance gate's reading, named the same way the three records above are.
PERF_NONE = "none"
PERF_PRESENT = "present"
PERF_UNREADABLE = "unreadable"
PERF_MALFORMED = "malformed"
PERF_KEYS = frozenset({PERF_NONE, PERF_PRESENT, PERF_UNREADABLE, PERF_MALFORMED})

#: Every verdict `deploy/perf_gate.py` can write, and the word for one this page has
#: not been taught. The page must have a sentence per key *and* must not reach for
#: the nearest one: a gate that grows a fifth verdict would otherwise have its
#: refusal — or its pass — rendered as whichever of these four it was closest to.
PERF_VERDICTS = frozenset({"baseline", "pass", "unconfirmed", "regressed"})
PERF_VERDICT_UNKNOWN = "unknown"

#: How many of the gate's `reasons` the page shows. A refusal is a handful of lines;
#: `reasons_total` carries the count so a truncated list cannot read as the whole
#: finding, and the cap is what keeps a pathological one off the page.
PERF_REASONS_SHOWN = 6

#: How much of the end of the history file is read. It is append-only and grows by a
#: line per deploy for the life of the box, so a page render reads a bounded tail
#: rather than however many years of judgements happen to have accumulated.
PERF_TAIL_BYTES = 64 * 1024

#: The held commit's own judgement, looked up in the same history by its sha. Named
#: the way the four readings above are, and the point of the split is that "this gate
#: never judged the held commit" and "its judgement is older than the window this
#: page reads" are different answers with different remedies: only the second is
#: fixed by looking at the file, and reporting a lookup that ran out of window as a
#: gate that never ran sends an operator to the wrong place.
PERF_HELD_NONE = "none"
PERF_HELD_PRESENT = "present"
PERF_HELD_NO_JUDGEMENT = "no_judgement"
PERF_HELD_OUT_OF_WINDOW = "out_of_window"
PERF_HELD_UNREADABLE = "unreadable"
PERF_HELD_KEYS = frozenset({PERF_HELD_NONE, PERF_HELD_PRESENT,
                            PERF_HELD_NO_JUDGEMENT, PERF_HELD_OUT_OF_WINDOW,
                            PERF_HELD_UNREADABLE})


def preflight_state(path: pathlib.Path, *, now: _dt.datetime) -> dict:
    """The last refusal to get a release merged, as the runner recorded it.

    Five positional lines, the first four being the record's header: the step, the
    ISO time, the exit code, and the commit the attempt was about (empty when the
    run refused before a commit was under judgement). Everything after them is the
    runner's own words and is shown as it stands.

    A record that exists but cannot be read is *reported*, never treated as absent.
    That distinction is the whole reason this card exists: "nothing has refused" and
    "a refusal I cannot read" look identical from outside, and only one of them
    means the box is deploying.
    """
    state: dict = {
        "path": str(path), "present": False, "key": PREFLIGHT_NONE,
        "gate": None, "gate_key": None, "at": None, "age_seconds": None,
        "exit_code": None, "commit": None, "short": None, "detail": None,
        "reason": None,
    }
    text, why = _read(path)
    if text is None:
        if why != "absent":
            state["key"] = PREFLIGHT_UNREADABLE
            state["reason"] = why
        return state

    lines = text.splitlines()
    gate = lines[0].strip() if lines else ""
    when = lines[1].strip() if len(lines) > 1 else ""
    code = lines[2].strip() if len(lines) > 2 else ""
    commit = lines[3].strip() if len(lines) > 3 else ""
    body = "\n".join(lines[4:]).rstrip() if len(lines) > 4 else ""
    if not re.fullmatch(r"[a-z0-9_]{1,40}", gate):
        state["key"] = PREFLIGHT_MALFORMED
        state["reason"] = gate or None
        return state

    state["present"] = True
    state["key"] = PREFLIGHT_PRESENT
    state["gate"] = gate
    state["gate_key"] = gate if gate in PREFLIGHT_GATES else PREFLIGHT_UNKNOWN_GATE
    state["at"] = when or None
    state["age_seconds"] = _age_seconds(when or None, now)
    state["exit_code"] = code if code.isdigit() else None
    if re.fullmatch(r"[0-9a-fA-F]{40}", commit):
        state["commit"] = commit.lower()
        state["short"] = commit[:7]
    # A header with nothing after it is still a refusal on record: the silence is
    # the command's, not this page's licence to say nothing was refused.
    state["detail"] = body or None
    return state


def last_stop_state(path: pathlib.Path, *, now: _dt.datetime) -> dict:
    """How the last run that ended non-zero ended, as the runner recorded it.

    Four positional lines: the step it was in, when, the exit code, and the commit
    under judgement (empty when the run stopped before there was one).

    A run that finishes deletes the record, so what is here describes a box that is
    *still* stopping on that step rather than one that stopped once and recovered —
    the same reason `preflight_forget` clears its own record on a merge. And a
    record that cannot be read is reported rather than treated as absent, which is
    the rule all four of these answers follow.
    """
    state: dict = {
        "path": str(path), "present": False, "key": LAST_STOP_NONE, "step": None,
        "step_key": None, "at": None, "age_seconds": None, "exit_code": None,
        "commit": None, "short": None, "tone": None, "reason": None,
    }
    text, why = _read(path)
    if text is None:
        if why != "absent":
            state["key"] = LAST_STOP_UNREADABLE
            state["reason"] = why
        return state

    lines = text.splitlines()
    step = lines[0].strip() if lines else ""
    when = lines[1].strip() if len(lines) > 1 else ""
    code = lines[2].strip() if len(lines) > 2 else ""
    commit = lines[3].strip() if len(lines) > 3 else ""
    if not re.fullmatch(r"[a-z0-9_]{1,40}", step):
        state["key"] = LAST_STOP_MALFORMED
        state["reason"] = step or None
        return state

    state["present"] = True
    state["key"] = LAST_STOP_PRESENT
    state["step"] = step
    state["step_key"] = step if step in RUN_STEPS else RUN_STEP_UNKNOWN
    state["at"] = when or None
    state["age_seconds"] = _age_seconds(when or None, now)
    if code.isdigit():
        state["exit_code"] = int(code)
        # A code this page has no row for is left with no tone: the table shows it
        # as a code without a reading rather than inventing one.
        state["tone"] = EXIT_CODES.get(int(code))
    if re.fullmatch(r"[0-9a-fA-F]{40}", commit):
        state["commit"] = commit.lower()
        state["short"] = commit[:7]
    return state


def perf_state(path: pathlib.Path, *, baseline_path: pathlib.Path | None = None,
               repo: pathlib.Path | None = None,
               now: _dt.datetime | None = None,
               held_commit: str | None = None,
               limit: int = PERF_REASONS_SHOWN) -> dict:
    """What the performance gate measured, from the line it wrote itself.

    `report()`'s rule holds here as everywhere else: a part that cannot be read is
    reported with a reason rather than treated as absent, and a number that was not
    measured is `None` rather than zero. The one thing this adds is that the *gate's
    own sentence* is carried through unedited — `reasons` is the answer to "what got
    slower", and a paraphrase would drop the numbers that make it checkable.
    """
    now = now or _dt.datetime.now(_dt.timezone.utc)
    state: dict = {
        "path": str(path), "present": False, "key": PERF_NONE, "reason": None,
        "verdict": None, "verdict_key": None,
        "commit": None, "short": None, "subject": None,
        "measured_at": None, "age_seconds": None,
        "p50_ms": None, "p95_ms": None, "endpoints": [], "samples": None,
        "error_pct": None, "server_errors_5xx": None,
        "bytes": None, "bytes_endpoint": None,
        "roundtrips": None, "roundtrips_endpoint": None,
        "reasons": [], "reasons_total": 0, "shown": limit,
        "baseline": {"path": str(baseline_path) if baseline_path else None,
                     "present": False, "key": PERF_NONE,
                     "commit": None, "short": None, "p50_ms": None, "p95_ms": None},
        "held": {"key": PERF_HELD_NONE, "commit": None, "short": None,
                 "subject": None, "verdict": None, "verdict_key": None,
                 "measured_at": None, "p50_ms": None, "p95_ms": None,
                 "endpoints": [], "samples": None, "reasons": [],
                 "reasons_total": 0, "skipped": 0, "same_as_latest": False},
    }

    # The held commit's lookup does not depend on the latest line's, so it runs even
    # when that line is absent or unreadable: the two are different questions and the
    # page needs both answers.
    state["held"] = _held_judgement(path, held_commit, repo=repo, limit=limit)

    line, why = _read_tail(path)
    if line is None:
        # A file that is not there and a file with no judgements in it are the same
        # reading — "the gate has recorded nothing for this page to show" — while a
        # file that exists and cannot be read is a different one with a different
        # remedy, and is reported as such.
        if why not in ("absent", "no_lines"):
            state["key"] = PERF_UNREADABLE
            state["reason"] = why
        return state

    try:
        record = json.loads(line)
    except (ValueError, TypeError):
        state["key"] = PERF_MALFORMED
        state["reason"] = line[:200]
        return state
    if not isinstance(record, dict):
        state["key"] = PERF_MALFORMED
        state["reason"] = line[:200]
        return state

    state["present"] = True
    state["key"] = PERF_PRESENT

    verdict = record.get("verdict")
    if isinstance(verdict, str) and verdict:
        state["verdict"] = verdict
        state["verdict_key"] = (verdict if verdict in PERF_VERDICTS
                                else PERF_VERDICT_UNKNOWN)

    sha = record.get("commit")
    if isinstance(sha, str) and re.fullmatch(r"[0-9a-fA-F]{40}", sha):
        state["commit"] = sha.lower()
        state["short"] = sha[:7]
        if repo is not None:
            state["subject"] = _commit_subject(pathlib.Path(repo), sha)

    when = record.get("measured_at")
    if isinstance(when, str) and when:
        state["measured_at"] = when
        state["age_seconds"] = _age_seconds(when, now)

    latency = record.get("latency")
    if isinstance(latency, dict):
        state["p50_ms"] = _number(latency.get("p50_ms"))
        state["p95_ms"] = _number(latency.get("p95_ms"))
        state["samples"] = _number(latency.get("samples"))
        ends = latency.get("endpoints")
        if isinstance(ends, list):
            state["endpoints"] = [e for e in ends if isinstance(e, str)]

    cost = record.get("page_cost")
    if isinstance(cost, dict):
        state["bytes"] = _number(cost.get("bytes"))
        state["bytes_endpoint"] = cost.get("bytes_endpoint") or None
        state["roundtrips"] = _number(cost.get("roundtrips"))
        state["roundtrips_endpoint"] = cost.get("roundtrips_endpoint") or None

    state["error_pct"] = _number(record.get("error_pct"))
    state["server_errors_5xx"] = _number(record.get("server_errors_5xx"))

    reasons = record.get("reasons")
    if isinstance(reasons, list):
        kept = [r for r in reasons if isinstance(r, str) and r.strip()]
        state["reasons_total"] = len(kept)
        state["reasons"] = kept[:limit]

    # "the held commit is also the latest judgement" is the one case where the page
    # would otherwise print the same numbers twice, once as the latest and once as
    # the refusal — so the lookup is told which record it landed on.
    if (state["held"]["key"] == PERF_HELD_PRESENT and state["commit"]
            and state["held"]["commit"] == state["commit"]):
        state["held"]["same_as_latest"] = True

    if baseline_path is not None:
        state["baseline"] = _perf_baseline(pathlib.Path(baseline_path), repo=repo)
    return state


def _judgement_fields(out: dict, record: dict, *, limit: int) -> None:
    """One history record's own fields, into a judgement of the shape the page reads."""
    verdict = record.get("verdict")
    if isinstance(verdict, str) and verdict:
        out["verdict"] = verdict
        out["verdict_key"] = (verdict if verdict in PERF_VERDICTS
                              else PERF_VERDICT_UNKNOWN)
    when = record.get("measured_at")
    if isinstance(when, str) and when:
        out["measured_at"] = when
    latency = record.get("latency")
    if isinstance(latency, dict):
        out["p50_ms"] = _number(latency.get("p50_ms"))
        out["p95_ms"] = _number(latency.get("p95_ms"))
        out["samples"] = _number(latency.get("samples"))
        ends = latency.get("endpoints")
        if isinstance(ends, list):
            out["endpoints"] = [e for e in ends if isinstance(e, str)]
    cost = record.get("page_cost")
    if isinstance(cost, dict):
        out["bytes"] = _number(cost.get("bytes"))
        out["bytes_endpoint"] = cost.get("bytes_endpoint") or None
    reasons = record.get("reasons")
    if isinstance(reasons, list):
        kept = [r for r in reasons if isinstance(r, str) and r.strip()]
        out["reasons_total"] = len(kept)
        out["reasons"] = kept[:limit]


def _empty_judgement(*, commit: str | None = None) -> dict:
    """The reading before a lookup: every answer `None`/absent, never a zero."""
    return {
        "key": PERF_HELD_NONE, "commit": commit,
        "short": commit[:7] if commit else None, "subject": None,
        "verdict": None, "verdict_key": None, "measured_at": None,
        "p50_ms": None, "p95_ms": None, "endpoints": [], "samples": None,
        "bytes": None, "bytes_endpoint": None,
        "reasons": [], "reasons_total": 0, "skipped": 0, "same_as_latest": False,
    }


def _perf_tail_records(path: pathlib.Path) -> tuple[dict[str, dict], bool, int, str | None]:
    """Every judgement in the bounded tail, keyed by the sha it is about.

    Read once for however many commits a page needs, because the histories it
    serves — the held commit and every commit in the refusal history — are the same
    file, and a reader that opened it per commit would read the tail N times to
    answer one question. Returns `(records, partial, skipped, error)`, where `error`
    is `absent`, `unreadable`, or `None`, and `partial` says whether the window cut
    the file so a commit that was not found may be older rather than never judged.

    The window starts mid-record whenever one record is larger than the tail, and
    that leading fragment is *counted* as a line this reader could not read rather
    than dropped: a dropped line is how "not found" starts reading as "never
    judged".
    """
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            partial = size > PERF_TAIL_BYTES
            if partial:
                # One byte of context before the window, so a boundary that lands on
                # a line ending is recognised and the record after it read whole.
                fh.seek(size - PERF_TAIL_BYTES - 1)
            raw = fh.read()
    except FileNotFoundError:
        return {}, False, 0, "absent"
    except OSError:
        return {}, False, 0, "unreadable"

    text = raw.decode("utf-8", errors="replace")
    skipped = 0
    if partial:
        head, _, rest = text.partition("\n")
        if head.strip():
            skipped += 1
        text = rest

    records: dict[str, dict] = {}
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except (ValueError, TypeError):
            skipped += 1
            continue
        if not isinstance(rec, dict):
            skipped += 1
            continue
        rec_sha = rec.get("commit")
        if not (isinstance(rec_sha, str)
                and re.fullmatch(r"[0-9a-fA-F]{40}", rec_sha)):
            continue
        # Last judgement for a sha wins: the gate appends, so a later line is the
        # newer measurement of the same commit.
        records[rec_sha.lower()] = rec
    return records, partial, skipped, None


def _perf_index_path(history: pathlib.Path) -> pathlib.Path:
    """The index the gate keeps beside its history.

    Derived with the same rule `deploy/perf_gate.py::index_path()` writes it with, so
    the writer and this reader cannot be pointed at different files.
    """
    return pathlib.Path(str(history) + ".index")


def _perf_index(history: pathlib.Path) -> dict[str, int]:
    """sha -> byte offset, from the small index beside the history.

    Read *whole*, on purpose: it is one short line per judgement, so it stays cheap
    to read long after the history it points into has outgrown the bounded tail this
    page reads — which is exactly the reason it exists. An absent or unreadable index
    is `{}` rather than an error: a gate from before it wrote none, and the tail scan
    is the fallback.
    """
    try:
        text = _perf_index_path(history).read_text(encoding="utf-8",
                                                   errors="replace")
    except OSError:
        return {}
    offsets: dict[str, int] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(record, dict):
            continue
        sha = record.get("commit")
        offset = record.get("offset")
        if not (isinstance(sha, str) and re.fullmatch(r"[0-9a-fA-F]{40}", sha)):
            continue
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            continue
        # Last entry for a commit wins: the gate appends, so the last line is the
        # newest judgement of that commit.
        offsets[sha.lower()] = offset
    return offsets


def _record_at(history: pathlib.Path, offset: int) -> dict | None:
    """The one record starting at `offset`, or None when it is not a record."""
    try:
        with history.open("rb") as fh:
            fh.seek(offset)
            raw = fh.readline()
    except OSError:
        return None
    try:
        record = json.loads(raw.decode("utf-8"))
    except (ValueError, TypeError, UnicodeDecodeError):
        return None
    return record if isinstance(record, dict) else None


def _perf_records(path: pathlib.Path, shas):
    """Records for the wanted commits: the tail, plus the index beyond it.

    The tail scan alone stops answering once a judgement falls outside the read
    window, which for a held commit is exactly when somebody comes looking. So the
    index is consulted for whatever the tail did not have, and its offsets are read
    directly — the file may be arbitrarily long; the seek is not.

    The tail wins any commit it *does* hold, because a later append sits nearer the
    end of the file: if the tail has the commit at all, it has the newest judgement.
    Returns `(records, partial, skipped, error)` with the same meanings the tail
    reader gives, so a commit found by neither is reported the way it always was.
    """
    wanted = [sha.lower() for sha in shas
              if isinstance(sha, str) and re.fullmatch(r"[0-9a-fA-F]{40}", sha)]
    records, partial, skipped, error = _perf_tail_records(path)
    if error is not None:
        return {}, partial, skipped, error
    found = {sha: records[sha] for sha in wanted if sha in records}
    missing = [sha for sha in wanted if sha not in found]
    if missing:
        offsets = _perf_index(path)
        for sha in missing:
            offset = offsets.get(sha)
            if offset is None:
                continue
            record = _record_at(path, offset)
            if record is None:
                continue
            rec_sha = record.get("commit")
            # A stale offset points at another commit's line (the history was
            # rotated or rewritten), and that is not this commit's judgement.
            if (isinstance(rec_sha, str)
                    and re.fullmatch(r"[0-9a-fA-F]{40}", rec_sha)
                    and rec_sha.lower() == sha):
                found[sha] = record
    return found, partial, skipped, None


def _held_judgement(path: pathlib.Path, sha: str | None, *,
                    repo: pathlib.Path | None = None,
                    limit: int = PERF_REASONS_SHOWN) -> dict:
    """The held commit's own judgement, found in the same history by its sha.

    The perf card shows the gate's *last* judgement, and that is exactly what a held
    commit loses: a refused commit is quarantined and stops being judged, so the next
    line belongs to a later release that passed, and the numbers behind the refusal —
    the ones the quarantine file quotes only in prose — leave the page just as
    somebody finally comes looking.

    So the held commit is looked up in the same file, by the sha parsed out of each
    record rather than matched as text (the window starts mid-record whenever one is
    bigger than the tail, and a fragment that happens to contain the sha is not a
    judgement of it), and "not found" is split two ways: a page that read only a
    bounded tail cannot say "this gate never judged it" when the truth is "its
    judgement is older than the window" — different facts, different next steps.
    """
    out = _empty_judgement()
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", sha):
        return out
    sha = sha.lower()
    out["commit"] = sha
    out["short"] = sha[:7]
    if repo is not None:
        out["subject"] = _commit_subject(pathlib.Path(repo), sha)

    records, partial, skipped, error = _perf_records(path, [sha])
    out["skipped"] = skipped
    if error == "absent":
        out["key"] = PERF_HELD_NO_JUDGEMENT
        return out
    if error == "unreadable":
        out["key"] = PERF_HELD_UNREADABLE
        return out

    found = records.get(sha)
    if found is None:
        out["key"] = PERF_HELD_OUT_OF_WINDOW if partial else PERF_HELD_NO_JUDGEMENT
        return out
    out["key"] = PERF_HELD_PRESENT
    _judgement_fields(out, found, limit=limit)
    return out


def perf_judgements(path: pathlib.Path, shas, *,
                    limit: int = PERF_REASONS_SHOWN) -> dict[str, dict]:
    """Each given commit's own judgement, from one read of the history.

    The refusal card shows the gate's *last* judgement (and the held commit's in its
    own block), which leaves the older quarantined commits with nothing but the
    runner's prose: their measurement is in the same history and was reaching only a
    shell. So every commit in the refusal history is looked up here, by the sha
    parsed out of each record, and each keeps the same four answers a single lookup
    does — present, no judgement, older than the window, unreadable — because a
    commit whose evidence is merely outside the tail must not read as one the gate
    never judged.
    """
    wanted = []
    for sha in shas:
        if isinstance(sha, str) and re.fullmatch(r"[0-9a-fA-F]{40}", sha):
            wanted.append(sha.lower())
    out: dict[str, dict] = {}
    for sha in wanted:
        out.setdefault(sha, _empty_judgement())
    if not out:
        return out

    records, partial, skipped, error = _perf_records(path, wanted)
    for sha, judgement in out.items():
        judgement["commit"] = sha
        judgement["short"] = sha[:7]
        judgement["skipped"] = skipped
        if error == "absent":
            judgement["key"] = PERF_HELD_NO_JUDGEMENT
            continue
        if error == "unreadable":
            judgement["key"] = PERF_HELD_UNREADABLE
            continue
        found = records.get(sha)
        if found is None:
            judgement["key"] = (PERF_HELD_OUT_OF_WINDOW if partial
                                else PERF_HELD_NO_JUDGEMENT)
            continue
        judgement["key"] = PERF_HELD_PRESENT
        _judgement_fields(judgement, found, limit=limit)
    return out


#: How much of the gate's evidence history one download may carry. The file grows by
#: a line per deploy for the life of the box, and the bounded tail the page reads is
#: exactly what this route exists to see past — but an unbounded diagnostic download is
#: one nobody can open on a box that is already the thing under investigation. Past this
#: the *newest* bytes are served, cut back to a line boundary, and the answer says so in
#: a header so a truncated download cannot read as the whole history.
PERF_DOWNLOAD_MAX_BYTES = 4 * 1024 * 1024

#: What each downloadable file is: its default path, the environment variable the page
#: resolves the same path through, the name the browser is handed, and the type. One
#: table so the route, the resolver and the page's links cannot drift apart — the
#: download must be the file that was judged, not a second guess at where it lives.
PERF_DOWNLOADS = {
    "evidence": (DEFAULT_PERF_HISTORY_FILE, "SCANGRADE_PERF_HISTORY_FILE",
                 "history.jsonl", "application/x-ndjson"),
    "baseline": (DEFAULT_PERF_BASELINE_FILE, "SCANGRADE_PERF_BASELINE_FILE",
                 "baseline.json", "application/json"),
}


def perf_download(which: str, *, path=None, limit: int | None = None) -> dict:
    """One of the gate's own files, for a browser rather than a shell.

    The page reads both files already — a bounded tail of the history and the baseline
    beside it — and the tail is the thing a quarantined box needs to see past: the
    judgement that refused the held commit can be older than the window, and the numbers
    an operator wants to check are all in the file. This hands the file over, resolved
    through the same environment variables the page reads, so what is downloaded is what
    was judged rather than a second guess at the path.

    A part that cannot be read is *reported*, never served as empty: `absent` and
    `unreadable` are different states with different remedies, the same rule the page
    follows everywhere. Nothing here is parsed — the gate's own bytes are the evidence,
    and a re-serialised copy would be a different document.
    """
    if which not in PERF_DOWNLOADS:
        raise KeyError(which)
    default, env, name, content_type = PERF_DOWNLOADS[which]
    target = pathlib.Path(path or os.environ.get(env) or default)
    result = {"which": which, "path": str(target), "name": name,
              "content_type": content_type, "key": "absent", "reason": None,
              "data": b"", "size": None, "truncated": False}
    cap = PERF_DOWNLOAD_MAX_BYTES if limit is None else limit
    try:
        size = target.stat().st_size
        with target.open("rb") as fh:
            if cap and size > cap:
                fh.seek(size - cap)
                data = fh.read(cap)
                # Cut back to a line boundary: half a JSON line is not evidence, and a
                # reader who pastes it into `jq` deserves an error that is about the
                # file rather than about the cut.
                cut = data.find(b"\n")
                if cut != -1:
                    data = data[cut + 1:]
                result["truncated"] = True
            else:
                data = fh.read()
    except FileNotFoundError:
        # "the gate judged nothing" and "this page cannot read the file" are different
        # states with different remedies; a missing file is the first, and it is the
        # default the result already carries.
        return result
    except OSError as exc:
        result["key"] = "unreadable"
        result["reason"] = f"{type(exc).__name__}: {exc}"
        return result
    result.update(key="present", data=data, size=size)
    return result


def _perf_baseline(path: pathlib.Path, *, repo: pathlib.Path | None = None) -> dict:
    """The release the gate compared against — named, not just implied.

    "the last release that passed" is a commit, and a reader deciding whether a
    refusal is real needs to see the pair: what this release measured against what
    the previous one did.
    """
    out: dict = {"path": str(path), "present": False, "key": PERF_NONE,
                 "commit": None, "short": None, "p50_ms": None, "p95_ms": None,
                 "measured_at": None, "reason": None}
    text, why = _read(path)
    if text is None:
        if why != "absent":
            out["key"] = PERF_UNREADABLE
            out["reason"] = why
        return out
    try:
        record = json.loads(text)
    except (ValueError, TypeError):
        out["key"] = PERF_MALFORMED
        return out
    if not isinstance(record, dict):
        out["key"] = PERF_MALFORMED
        return out
    out["present"] = True
    out["key"] = PERF_PRESENT
    sha = record.get("commit")
    if isinstance(sha, str) and re.fullmatch(r"[0-9a-fA-F]{40}", sha):
        out["commit"] = sha.lower()
        out["short"] = sha[:7]
    if isinstance(record.get("measured_at"), str):
        out["measured_at"] = record["measured_at"] or None
    latency = record.get("latency")
    if isinstance(latency, dict):
        out["p50_ms"] = _number(latency.get("p50_ms"))
        out["p95_ms"] = _number(latency.get("p95_ms"))
    return out


def _number(value) -> float | None:
    """A measured number, or `None` — which is not the same reading as zero."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def request_release(*, request_file=None, quarantine_file=None, repo=None,
                    expect_sha: str | None = None,
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

    `expect_sha` is the commit a per-row button asked for. The runner deploys the
    branch head, so it can retry a refusal only while that commit *is* the head; a
    request naming any other commit is refused here rather than written, because the
    file it would leave behind would silently release whatever is held instead.
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
    if expect_sha is not None:
        # `held["sha"]` is already a lowercased 40-char sha — `quarantine_state`
        # refuses to call a record held otherwise — so the only question left is
        # whether the row named the commit that is actually under judgement.
        wanted = expect_sha.strip().lower()
        if wanted != held["sha"]:
            result["key"] = RELEASE_NOT_HELD
            result["expected"] = expect_sha.strip() or None
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


def request_rebaseline(*, request_file=None, quarantine_file=None, repo=None,
                       now: _dt.datetime | None = None) -> dict:
    """Ask the runner, from the page, to re-measure the box on its next attempt.

    A release request retries a refused commit; this one also tells the perf gate to
    rewrite its baseline from the box as it is now (`--rebaseline`). They are two
    different asks: retrying with the same yardstick re-refuses a release the box's
    drift, not the code, made slow, which is the deadlock an operator could only
    leave from a shell.

    Written only while a commit is held, for the reason the release request is: the
    runner consumes the file on its next tick whatever it finds, so a request left
    behind with nothing to deploy would be spent on a tick that measured nothing
    instead of on the release the operator meant. "Nothing is pending" is the honest
    answer then, never a file waiting for something to spend it on.

    One bit wide, like the release request: only the file's *existence* is read, so
    nothing a page can write is ever executed.
    """
    now = now or _dt.datetime.now(_dt.timezone.utc)
    repo = pathlib.Path(repo or os.environ.get("SCANGRADE_REPO") or DEFAULT_REPO)
    quarantine_file = pathlib.Path(
        quarantine_file or os.environ.get("SCANGRADE_QUARANTINE_FILE")
        or DEFAULT_QUARANTINE_FILE)
    request_file = pathlib.Path(
        request_file or os.environ.get("SCANGRADE_REBASELINE_REQUEST")
        or (DEFAULT_REQUEST_DIR + "/rebaseline"))

    held = quarantine_state(quarantine_file, repo, now=now)
    result = {"key": None, "written": False, "path": str(request_file),
              "detail": None, "held": held["sha"], "gate": held["gate"]}

    if not held["held"]:
        result["key"] = REBASELINE_NOTHING_HELD
        return result
    if not request_file.parent.is_dir():
        result["key"] = REBASELINE_DIR_MISSING
        result["detail"] = str(request_file.parent)
        return result

    try:
        request_file.write_text(
            f"{now.isoformat(timespec='seconds')}\n"
            f"re-baseline requested from /super-admin/deploy-status\n",
            encoding="utf-8")
    except PermissionError as exc:
        result["key"] = REBASELINE_NOT_WRITABLE
        result["detail"] = str(exc)
        return result
    except OSError as exc:
        result["key"] = REBASELINE_FAILED
        result["detail"] = f"{type(exc).__name__}: {exc}"
        return result

    result["key"] = REBASELINE_WRITTEN
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


def _read_tail(path: pathlib.Path, limit: int = PERF_TAIL_BYTES) -> tuple[str | None, str | None]:
    """(the last line, why it could not be read).

    For a file that is appended to once per deploy and never rotated: what a reader
    wants is the *last* judgement, and reading the whole history to find it would
    make a page render slower every month the box stays up. So the tail is read and
    the last complete line taken.

    The line is returned even when it is not complete — when the last record itself
    is larger than the window, the truncated text is what the caller gets, and the
    JSON parse fails on it. That is the honest answer ("there is a record and this
    page cannot read it") where returning nothing would say the gate had never
    judged anything, and returning the *previous* line would present an older pass
    as this box's latest judgement.
    """
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            partial = size > limit
            if partial:
                # One byte of context before the window, so a boundary that happens
                # to be a line ending is recognised and the whole line kept.
                fh.seek(size - limit - 1)
            raw = fh.read()
    except FileNotFoundError:
        return None, "absent"
    except OSError as exc:
        return None, f"{type(exc).__name__}: {exc}"

    if not raw.strip():
        return None, "no_lines"

    tail = raw.decode("utf-8", errors="replace")
    text = tail
    if partial:
        # A first segment with no newline behind it cannot be a whole record; the
        # one byte read for context tells the two cases apart.
        text = text[1:] if text.startswith("\n") else text.split("\n", 1)[-1]
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    if not lines:
        # The window landed entirely inside one (enormous) record, so there is no
        # whole line to return. The truncated record is returned rather than
        # nothing: the caller's verdict is then "there is a record and this page
        # cannot read it", which is true, where "nothing was recorded" is not.
        return tail.strip()[:4000], None
    return lines[-1], None


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
            unarmed: dict | None = None, preflight: dict | None = None) -> dict:
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

    `preflight` is the runner's record of a run that fetched and then refused before
    merging. It sits above the readings that *infer* the same fact — `dirty`,
    `behind` — because it says which step refused and what it said, where those only
    say that something is off. It sits below `paused`, which is somebody's decision
    rather than a fault, and below `unarmed`, which stops the run even earlier. Only
    the transient arm (`fetch_failed`) is a warning: that one retries by itself, and
    crying breakage for a network blip would be wrong the next tick.
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
    if preflight and preflight.get("present"):
        gate = preflight.get("gate_key")
        level = WARN if gate in PREFLIGHT_TRANSIENT else BROKEN
        return {**out, "level": level, "key": "refused",
                "detail": preflight.get("gate") or gate,
                "behind": checkout.get("behind")}
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
    # the runner's record of refusing a release *before* it merged it
    "refused",
})


def report(*, repo=None, runner=None, snapshot_runner=None, pause_file=None,
           quarantine_file=None, unarmed_file=None, preflight_file=None,
           last_stop_file=None, request_dir=None, release_request=None,
           perf_history_file=None, perf_baseline_file=None, refusals_dir=None,
           now: _dt.datetime | None = None) -> dict:
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
    preflight_file = pathlib.Path(
        preflight_file or os.environ.get("SCANGRADE_PREFLIGHT_FILE")
        or DEFAULT_PREFLIGHT_FILE)
    preflight = preflight_state(preflight_file, now=now)
    last_stop_file = pathlib.Path(
        last_stop_file or os.environ.get("SCANGRADE_LAST_STOP_FILE")
        or DEFAULT_LAST_STOP_FILE)
    last_stop = last_stop_state(last_stop_file, now=now)
    perf_history_file = pathlib.Path(
        perf_history_file or os.environ.get("SCANGRADE_PERF_HISTORY_FILE")
        or DEFAULT_PERF_HISTORY_FILE)
    perf_baseline_file = pathlib.Path(
        perf_baseline_file or os.environ.get("SCANGRADE_PERF_BASELINE_FILE")
        or DEFAULT_PERF_BASELINE_FILE)
    # Read once: `perf_state` needs the held sha to look up the refusal's own
    # numbers, and the quarantine card below needs the same record.
    quarantine = quarantine_state(quarantine_file, repo, now=now)
    perf = perf_state(perf_history_file, baseline_path=perf_baseline_file,
                      repo=repo, now=now, held_commit=quarantine["sha"])
    refusals_dir = pathlib.Path(
        refusals_dir or os.environ.get("SCANGRADE_REFUSALS_DIR")
        or DEFAULT_REFUSALS_DIR)
    refusals = refusal_history_state(refusals_dir, repo, now=now)
    # Every quarantined commit's own measurement, from one read of the same history
    # the perf card reads. Without this only the held commit has numbers: the older
    # refusals keep the runner's prose and lose the evidence behind it.
    judgements = perf_judgements(
        perf_history_file,
        [record["sha"] for record in refusals["records"] if record["sha"]],
    )
    for record in refusals["records"]:
        record["perf"] = judgements.get(record["sha"]) if record["sha"] else None
        # The held commit's numbers are already on this page (its own block, or the
        # latest judgement when it is that too), so its inline copy is suppressed to
        # keep the page from printing the same measurement twice.
        record["is_current"] = bool(record["sha"] and record["sha"] == quarantine["sha"])
    request_dir = pathlib.Path(
        request_dir or os.environ.get("SCANGRADE_REQUEST_DIR") or DEFAULT_REQUEST_DIR)
    release_request = pathlib.Path(
        release_request or os.environ.get("SCANGRADE_RELEASE_REQUEST")
        or str(request_dir / "release"))
    rebaseline_request = pathlib.Path(
        os.environ.get("SCANGRADE_REBASELINE_REQUEST")
        or str(request_dir / "rebaseline"))

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
        "verdict": verdict(main, checkout, paused=paused, unarmed=unarmed,
                           preflight=preflight),
        "quarantine": quarantine,
        "quarantine_file": str(quarantine_file),
        # The same refusals, kept past the lift: what the quarantine card must
        # forget so the tick can act, this remembers so a reader can.
        "refusals": refusals,
        "refusals_dir": str(refusals_dir),
        "unarmed": unarmed,
        "unarmed_file": str(unarmed_file),
        "preflight": preflight,
        "preflight_file": str(preflight_file),
        # The vocabulary the two records are read against, and the table the page
        # renders: one source for the codes, one for the steps a record can name.
        "exit_codes": EXIT_CODES,
        "exit_tones": EXIT_TONES,
        "run_steps": RUN_STEPS,
        "last_stop": last_stop,
        "last_stop_file": str(last_stop_file),
        # The perf gate's own last judgement: what it measured, against which
        # release, and — when it refused one — the lines it refused on.
        "perf": perf,
        "perf_file": str(perf_history_file),
        "perf_baseline_file": str(perf_baseline_file),
        "perf_verdicts": PERF_VERDICTS,
        "release_file": DEFAULT_RELEASE_FILE,
        "release_file_present": _exists(pathlib.Path(DEFAULT_RELEASE_FILE)),
        "request_dir": str(request_dir),
        "request_path": str(release_request),
        "request_pending": _exists(release_request),
        "rebaseline_path": str(rebaseline_request),
        "rebaseline_pending": _exists(rebaseline_request),
        "request_dir_writable": _dir_writable(request_dir),
    }


if __name__ == "__main__":                                   # pragma: no cover
    # Same reason `schema_contract.py` has one: the answer has to be reachable on
    # the box, where the page is not the only reader.
    import json
    print(json.dumps(report(), indent=2))
