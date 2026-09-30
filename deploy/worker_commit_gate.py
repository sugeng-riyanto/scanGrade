"""Is the commit the Celery worker is running the one this run just merged?

`served_commit_gate.py` checks gunicorn. The Celery worker is the *other* process
holding the same checkout: it imports its task modules once, at start-up, and keeps
them in memory for the life of the process, so reloading the app leaves it answering
with the previous release. That is a **half-deployed release**, and it is not
theoretical — `page_index` was added to the OMR task's signature and to its caller in
one commit, the deploy restarted gunicorn alone, and every scan then failed with
`process_omr_scan() got an unexpected keyword argument 'page_index'`: the caller new,
the worker old, and nothing on the box saying so.

So the worker answers the same question the app answers, over the broker it already
uses. `app/celery_app.py` registers a `served_commit` control command that returns
`build_info.snapshot()` — the commit whose code the worker loaded, resolved at import.
This gate broadcasts that command and compares the answers with the merged commit.

**Where it deliberately differs from the app gate: silence is never a refusal.**
The app always has an HTTP surface, so a release that ships the reading can always
name its commit when asked, and a body without one means the code answering is not
this release. The worker has no such floor. A worker that is *down* and a worker
*built before the reading* both answer nothing over the broker, and from here they
are indistinguishable — so "no worker answered" is exit 2, not a rollback. The gate
refuses only when a worker **answers and names a different commit**, which is exactly
the state that breaks scans.

(The `celery inspect` subcommand cannot be used: its argument parser collects command
names when Celery is imported, before any application command exists, so a custom
command is reachable from the Python API — `app.control.broadcast` — and not from the
CLI. This gate therefore imports the worker's own app and broadcasts.)

**Exit codes**, chosen so a crash cannot mimic a refusal: `0` pass, `3` a worker
answered with a different commit, `2` could not measure. Never `1`: python exits `1`
on an uncaught exception.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass

EXIT_OK = 0
#: A worker answered and named a commit other than the merged one. Not 1: python
#: exits 1 on an uncaught exception, and the runner must never read a crash as a
#: refusal.
EXIT_REFUSED = 3
#: No worker could be asked, or answered in a way this gate cannot read. Never a
#: rollback: a down worker and a worker built before the reading are the same answer
#: from here.
EXIT_UNMEASURED = 2

READING_OK = "ok"
#: The worker answered but cannot place itself (`available: false`) — its checkout
#: or git is missing, a box property.
READING_UNNAMED = "unnamed"
#: The worker answered without the commit block at all: a reply from a command that
#: is not this reading.
READING_ABSENT = "absent"
#: Nothing readable came back: not a mapping, or a mapping this gate cannot read.
READING_UNREADABLE = "unreadable"

VERDICT_OK = "ok"
VERDICT_MISMATCH = "mismatch"
VERDICT_UNMEASURED = "unmeasured"

#: The fence in `app/celery_app.py` that registers the reading. Spelled in exactly
#: one place per side; the runner passes the file and the gate greps for this marker.
REPORTER_MARKER = "worker-commit:start"

#: The control command the worker registers and this gate broadcasts.
COMMAND = "served_commit"

DEFAULT_REPO = os.environ.get("SCANGRADE_REPO") or "/opt/scangrade"
DEFAULT_ATTEMPTS = 3
DEFAULT_GAP = 2.0
DEFAULT_TIMEOUT = 3.0


@dataclass(frozen=True)
class Reading:
    """One worker's answer, as this gate understands it."""

    state: str
    commit: str | None
    why: str


def reading_of(payload) -> Reading:
    """One broadcast reply, read as "which commit is this worker running".

    The four states are kept apart because they lead to different verdicts: a reply
    that cannot be read must not be counted as a worker on the previous commit.
    """
    if not isinstance(payload, dict):
        return Reading(READING_UNREADABLE, None,
                       "a worker answered with something that is not a commit block")
    if payload.get("error"):
        return Reading(READING_UNREADABLE, None,
                       "a worker reported an error: %s" % str(payload["error"])[:80])
    if "available" not in payload:
        return Reading(READING_ABSENT, None,
                       "a worker answered without the reading (a reply from a "
                       "command that is not this one)")
    if not payload.get("available"):
        return Reading(READING_UNNAMED, None,
                       "a worker answered but cannot name the commit it is running "
                       "(%s)" % (payload.get("reason_key") or "no reason"))
    commit = payload.get("full_commit") or payload.get("commit")
    if not commit:
        # `available: true` with nothing in it is a reply this gate cannot read:
        # counting it as "absent" would let a hollowed-out payload read as a worker
        # that predates the check.
        return Reading(READING_UNREADABLE, None,
                       "a worker says it knows its commit and named none")
    return Reading(READING_OK, str(commit), "the worker named it")


def judge(readings: list, merged: str, *, reporter_ships: bool) -> tuple[str, str]:
    """What those answers mean for this release.

    Returns `(verdict, why)`, with `why` a sentence the runner quotes into the
    quarantine record. The order is the design: a readable disagreement is the
    finding, and everything else is "could not measure" — never a rollback.
    """
    if not merged:
        return VERDICT_UNMEASURED, "no commit to compare against was given"
    if not readings:
        return VERDICT_UNMEASURED, (
            "no worker answered over the broker; a worker that is down and one built "
            "before this reading are indistinguishable from here, so this is not read "
            "as a release defect" + ("" if reporter_ships else
                                     " (and this release does not ship the reading)"))

    named = [r for r in readings if r.state == READING_OK]
    for reading in named:
        if reading.commit == merged:
            return VERDICT_OK, "the worker is running %s" % merged[:7]
    if named:
        served = sorted({r.commit for r in named})
        seen = ", ".join(c[:7] for c in served)
        return VERDICT_MISMATCH, (
            "none of %d worker(s) reported %s; the worker reports %s"
            % (len(readings), merged[:7], seen))

    # No worker named a commit. An unreadable or unnamed answer is a box property,
    # and a reply without the block is a worker that is not running this reading —
    # neither is evidence about *this* release, so both are "could not measure".
    return VERDICT_UNMEASURED, readings[-1].why


def reporter_ships(path: str) -> bool:
    """Does the merged tree carry the reading? Silence is only evidence if it does."""
    try:
        text = open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        return False
    return REPORTER_MARKER in text


def worker_replies(repo: str, *, timeout: float) -> list:
    """Every worker's answer to the command. `[]` when none answered. Never raises.

    The worker's own app is imported so the broadcast goes over the broker the box
    already uses. Importing it here is the same import the worker does at start-up;
    a failure (no broker address, a broken install) is "no answer", not a crash.
    """
    if repo and repo not in sys.path:
        sys.path.insert(0, repo)
    try:
        from app.celery_app import celery_app
        replies = celery_app.control.broadcast(COMMAND, reply=True, timeout=timeout)
    except Exception:
        return []
    out: list = []
    for item in (replies or []):
        if isinstance(item, dict):
            for _worker, payload in item.items():
                out.append(payload)
        else:
            out.append(item)
    return out


def probe_all(ask, *, attempts: int, gap: float, merged: str) -> list[Reading]:
    """Ask repeatedly, stopping as soon as a worker names the merged commit.

    The retry covers the window in which a restarted worker has not yet registered
    its control command: the first broadcast can reach nobody and the next one can.
    """
    readings: list[Reading] = []
    for index in range(max(1, attempts)):
        for payload in ask():
            readings.append(reading_of(payload))
        if any(r.state == READING_OK and r.commit == merged for r in readings):
            return readings
        if index + 1 < attempts:
            time.sleep(gap)
    return readings


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Compare the commit the Celery worker reports running with the "
                    "one this run merged.")
    ap.add_argument("--repo", default=DEFAULT_REPO,
                    help="the checkout to import the worker's app from")
    ap.add_argument("--commit", default="",
                    help="the commit this run merged — the one the worker must run")
    ap.add_argument("--reporter", default="",
                    help="the file in the merged tree that registers the reading")
    ap.add_argument("--attempts", type=int, default=DEFAULT_ATTEMPTS)
    ap.add_argument("--gap", type=float, default=DEFAULT_GAP,
                    help="seconds between broadcasts (a restarted worker registers late)")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT,
                    help="seconds to wait for each broadcast's replies")
    ap.add_argument("--json-out", default="", help="write the readings here")
    return ap


def main() -> int:
    args = build_parser().parse_args()
    try:
        return _run(args)
    except Exception as exc:  # never exit 1: that is what a crash looks like, and
        # the runner must not read a crashed check as a refusal.
        print("worker commit: CANNOT MEASURE — the check itself failed: %s: %s"
              % (type(exc).__name__, exc))
        return EXIT_UNMEASURED


def _run(args) -> int:
    ships = reporter_ships(args.reporter) if args.reporter else False
    readings = probe_all(lambda: worker_replies(args.repo, timeout=args.timeout),
                         attempts=args.attempts, gap=args.gap, merged=args.commit)
    verdict, why = judge(readings, args.commit, reporter_ships=ships)

    if args.json_out:
        try:
            with open(args.json_out, "w", encoding="utf-8") as handle:
                json.dump({"verdict": verdict, "why": why, "merged": args.commit,
                           "reporter_ships": ships,
                           "readings": [r.__dict__ for r in readings]}, handle)
        except OSError:
            pass

    if verdict == VERDICT_OK:
        print("worker commit: OK — %s" % why)
        return EXIT_OK
    if verdict == VERDICT_MISMATCH:
        print("worker commit: REFUSED — %s" % why)
        return EXIT_REFUSED
    print("worker commit: CANNOT MEASURE — %s" % why)
    return EXIT_UNMEASURED


if __name__ == "__main__":  # pragma: no cover - the runner calls main()
    sys.exit(main())
