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

**The worker has no HTTP surface, so silence is read twice.** A worker that is
*down* and a worker *built before this reading* both answer nothing to `served_commit`,
and once they were indistinguishable — so a dead worker was "could not measure" and
slipped past a release that had, in fact, just killed it. They are told apart by a
second question: Celery's built-in `ping`, which every worker answers whatever code it
loaded, because it is not this reading at all. Nobody answering either question is a
worker that is not running — a release with no worker cannot process a single scan,
and the restart that was supposed to bring it back did not — so that **refuses**
(exit 4). An answered `ping` with an unanswered `served_commit` is a worker built
before the reading, which says nothing about *this* release and stays exit 2. And a
worker that **answers and names a different commit** refuses as before (exit 3): the
exact state that breaks scans.

(The `celery inspect` subcommand cannot be used: its argument parser collects command
names when Celery is imported, before any application command exists, so a custom
command is reachable from the Python API — `app.control.broadcast` — and not from the
CLI. This gate therefore imports the worker's own app and broadcasts.)

**Exit codes**, chosen so a crash cannot mimic a refusal: `0` pass, `3` a worker
answered with a different commit, `4` no worker is running at all, `2` could not
measure. Never `1`: python exits `1` on an uncaught exception.
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
#: No worker answered *either* question: the process is not running. A release with
#: no worker cannot process a single scan, so this refuses — it is a finding about
#: the release, not a box property. Kept distinct from EXIT_REFUSED so the record
#: names the right thing to restart.
EXIT_DOWN = 4
#: No worker could be asked, or answered in a way this gate cannot read. Never a
#: rollback: an alive worker built before the reading is a box property, not a
#: release defect.
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
#: No worker is running: neither the commit question nor the liveness probe was
#: answered. A distinct verdict because the remedy is "bring the worker back", not
#: "find the commit it is on".
VERDICT_DOWN = "down"
VERDICT_UNMEASURED = "unmeasured"

#: The fence in `app/celery_app.py` that registers the reading. Spelled in exactly
#: one place per side; the runner passes the file and the gate greps for this marker.
REPORTER_MARKER = "worker-commit:start"

#: The control command the worker registers and this gate broadcasts.
COMMAND = "served_commit"

#: Celery's built-in liveness command. Every worker answers it whatever commit it
#: loaded, because it is not this reading — which is the one property that lets a
#: *down* worker be told from one **built before the reading**. Spelled exactly as
#: Celery spells it.
COMMAND_ALIVE = "ping"

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


def judge(readings: list, merged: str, *, reporter_ships: bool,
          worker_alive: bool | None = None) -> tuple[str, str]:
    """What those answers mean for this release.

    Returns `(verdict, why)`, with `why` a sentence the runner quotes into the
    quarantine record. The order is the design: a readable disagreement is the
    finding; then a liveness probe that says nobody is running at all is a finding
    too (the release has no worker); everything else is "could not measure" — never
    a rollback. `worker_alive` is `True`/`False` from the `ping` probe, or `None`
    when that probe itself could not be read, which is never a refusal.
    """
    if not merged:
        return VERDICT_UNMEASURED, "no commit to compare against was given"

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

    # No worker named a commit. Now the liveness probe decides whether that silence
    # is a dead process or merely code older than this reading.
    if worker_alive is False:
        return VERDICT_DOWN, (
            "no Celery worker answered the liveness probe (%s) either: the worker is "
            "down, not merely running code from before this reading, so this release "
            "has no worker at all" % COMMAND_ALIVE)

    if not readings:
        return VERDICT_UNMEASURED, (
            "no worker answered over the broker; a worker that is down and one built "
            "before this reading are indistinguishable from here, so this is not read "
            "as a release defect" + ("" if reporter_ships else
                                     " (and this release does not ship the reading)"))

    # A worker is alive (or liveness could not be read): an unreadable or unnamed
    # answer is a box property, and a reply without the block is a worker that is not
    # running this reading — neither is evidence about *this* release.
    return VERDICT_UNMEASURED, readings[-1].why


def reporter_ships(path: str) -> bool:
    """Does the merged tree carry the reading? Silence is only evidence if it does."""
    try:
        text = open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        return False
    return REPORTER_MARKER in text


def _with_divergence(why: str, repo: str, served, merged: str, unit: str) -> str:
    """Append how far the worker is from the merged commit, and the remedy.

    The refusal this gate writes is the one an operator acts on, so it must say
    whether the worker merely missed the reload (restart it) or the box is on the
    wrong history (re-baseline it), not just name the two shas. Loaded here so the
    module still imports when read as text; a load failure leaves the sentence alone.
    """
    import importlib.util
    import pathlib
    import sys
    try:
        path = pathlib.Path(__file__).resolve().parent / "commit_divergence.py"
        spec = importlib.util.spec_from_file_location("commit_divergence", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules["commit_divergence"] = module
        spec.loader.exec_module(module)
        return module.annotate(why, repo, served, merged, unit=unit)
    except Exception:
        return why


def _ask(repo: str, command: str, *, timeout: float):
    """Broadcast one control command: the replies, or `None` if unaskable.

    The worker's own app is imported so the broadcast goes over the broker the box
    already uses. Importing it here is the same import the worker does at start-up;
    a failure (no broker address, a broken install) is `None` — "could not even
    ask" — which is deliberately different from "asked and nobody answered" (`[]`),
    because only the second is a statement about a worker.
    """
    if repo and repo not in sys.path:
        sys.path.insert(0, repo)
    try:
        from app.celery_app import celery_app
        replies = celery_app.control.broadcast(command, reply=True, timeout=timeout)
    except Exception:
        return None
    out: list = []
    for item in (replies or []):
        if isinstance(item, dict):
            for _worker, payload in item.items():
                out.append(payload)
        else:
            out.append(item)
    return out


def worker_replies(repo: str, *, timeout: float) -> list:
    """Every worker's answer to the commit command. `[]` when none answered.

    Never raises, and never `None`: this is the reading the rest of the gate treats
    as "which commit is each worker on", and "could not ask" and "nobody answered"
    are the same thing *for a commit* — neither names a commit. Liveness is where
    the two are told apart, in `workers_alive`.
    """
    replies = _ask(repo, COMMAND, timeout=timeout)
    return replies if replies is not None else []


def workers_alive(repo: str, *, timeout: float, attempts: int = DEFAULT_ATTEMPTS,
                  gap: float = DEFAULT_GAP) -> bool | None:
    """Is any worker answering *at all*? `True`/`False`, or `None` if unaskable.

    `ping` is built into every Celery worker, so it is the one question a worker can
    answer whatever commit it loaded — which is exactly what lets a genuinely dead
    worker be told apart from one built before the reading. Retried like the commit
    probe: a worker still coming up after a restart registers late. `None` (the ask
    failed — no broker address, a broken import) is "could not measure" and must
    never be read as "down".
    """
    for index in range(max(1, attempts)):
        replies = _ask(repo, COMMAND_ALIVE, timeout=timeout)
        if replies is None:
            return None
        if replies:
            return True
        if index + 1 < attempts:
            time.sleep(gap)
    return False


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
    ap.add_argument("--unit", default="scangrade-celery",
                    help="the unit the record should name in its remedy")
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
    # Only ask the second question when the first named nothing: a worker that named
    # the merged commit is alive by definition, and a worker that named *another*
    # commit is a stronger finding than "down".
    worker_alive: bool | None = None
    if not any(r.state == READING_OK for r in readings):
        worker_alive = workers_alive(args.repo, timeout=args.timeout,
                                     attempts=args.attempts, gap=args.gap)
    verdict, why = judge(readings, args.commit, reporter_ships=ships,
                         worker_alive=worker_alive)
    if verdict == VERDICT_MISMATCH:
        served = next((r.commit for r in readings if r.state == READING_OK), "")
        why = _with_divergence(why, args.repo, served, args.commit, args.unit)

    if args.json_out:
        try:
            with open(args.json_out, "w", encoding="utf-8") as handle:
                json.dump({"verdict": verdict, "why": why, "merged": args.commit,
                           "reporter_ships": ships, "worker_alive": worker_alive,
                           "readings": [r.__dict__ for r in readings]}, handle)
        except OSError:
            pass

    if verdict == VERDICT_OK:
        print("worker commit: OK — %s" % why)
        return EXIT_OK
    if verdict == VERDICT_MISMATCH:
        print("worker commit: REFUSED — %s" % why)
        return EXIT_REFUSED
    if verdict == VERDICT_DOWN:
        print("worker commit: REFUSED — %s" % why)
        return EXIT_DOWN
    print("worker commit: CANNOT MEASURE — %s" % why)
    return EXIT_UNMEASURED


if __name__ == "__main__":  # pragma: no cover - the runner calls main()
    sys.exit(main())
