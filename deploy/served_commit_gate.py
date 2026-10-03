"""Is the commit the app is serving the one this run just merged?

Every gate in `deploy/scangrade-deploy.sh` after the reload asks a question *about
the release*: the smoke test signs in as each role, the claims gate re-reads the
published capacity table, the perf gate compares a reference load with the last
release that passed. Each one rests on an unstated premise — that the code
answering is the code that was just merged. `systemctl reload` sends SIGHUP and
gunicorn is expected to re-exec; when that quietly does nothing, the previous
release keeps serving and every gate below goes on measuring it, describing a
release nobody is serving. From the outside the site is fine, which is why this
went unnoticed for a day and a half on this box: uptime climbing, every page
answering, `origin/main` fetched, and the process four commits behind.

The app publishes the commit it is serving on `/health` (see `app/__init__.py`,
`served_commit`), read once per process from where the code lives. This gate asks
one question of it, before any other post-reload gate is trusted: **is that commit
the one this run merged?**

Three answers, and the middle one is the whole design:

* **it is** — carry on;
* **it is not** — a measurement exists and it contradicts the merge. Exit 3 (see
  below), and the runner refuses and quarantines;
* **it did not answer** — no measurement. A box whose app cannot be asked is a box
  problem, and the health probe in the runner already owns reachability. Exit 2,
  never a rollback: an unconfirmed reading is not evidence of a bad release.

A gunicorn reload is graceful, so a request that arrives during the transition can
still be answered by a worker that is finishing an in-flight request. One probe
reporting the previous commit is therefore the transition, not a contradiction. The
gate asks `--attempts` times (default 5, two seconds apart — long enough to outlast
a worker retiring, short enough not to hold the deploy open) and refuses only when
**no** probe reported the merged commit. That is the same posture the perf gate
takes with a divergence a second probe did not confirm.

Two things make silence evidence, and both are needed:

* `--commit` — the commit this run merged. Without it there is nothing to compare,
  so that is exit 2 rather than a pass that means nothing.
* `--reporter` — the file in the *merged tree* that carries the reading (the fence
  `REPORTER_MARKER`). If that release ships the reading, then a process built from
  it can always name its commit, so an app answering without one is not this
  release: that is the reload that did not take, and it is exit 3. If the release
  *predates* the reading, silence is expected and the gate says so — which is what
  lets the very release that introduces this check land at all.

**Exit codes.** `1` is deliberately not used for the refusal: python exits `1` on an
uncaught exception, so a gate that crashed would be read by the runner as a gate
that refused. `0` pass, `3` the app is serving a different commit, `2` could not
measure.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

EXIT_OK = 0
#: A measurement that contradicts the merge. Not 1: python exits 1 on an uncaught
#: exception, and the runner must never read a crash as a refusal.
EXIT_REFUSED = 3
#: No measurement: the app could not be asked, or answered in a way this gate cannot
#: read. Never a rollback — a box that cannot be measured is not a bad release.
EXIT_UNMEASURED = 2

#: A reading came back and names a commit.
READING_OK = "ok"
#: The reporter answered and cannot place itself (`available: false`) — a box whose
#: git or checkout is missing, which is a box property, not a release defect.
READING_UNNAMED = "unnamed"
#: The payload has no commit block at all: the app serving it predates the reading.
READING_ABSENT = "absent"
#: Nothing readable came back: not JSON, or not the object this gate asks for.
READING_UNREADABLE = "unreadable"

VERDICT_OK = "ok"
VERDICT_MISMATCH = "mismatch"
VERDICT_UNMEASURED = "unmeasured"

#: The fence in the app file that publishes the reading. Spelled in exactly one
#: place per side: here, and around `served_commit` in `app/__init__.py`.
#: `tests/unit/test_served_commit_gate.py` fails if the two drift apart.
REPORTER_MARKER = "served-commit:start"

DEFAULT_HEALTH_PATH = "/health"
DEFAULT_ATTEMPTS = 5
DEFAULT_GAP = 2.0
DEFAULT_TIMEOUT = 5.0


@dataclass(frozen=True)
class Reading:
    """One answer from the app, as this gate understands it."""

    state: str
    commit: str | None
    why: str


def reading_of(body: str) -> Reading:
    """`/health` as a reading of what the process is serving.

    The four states are kept apart because they lead to different verdicts: a body
    that predates the reading is evidence when the merged release ships it, while a
    body that cannot be parsed is not evidence of anything.
    """
    try:
        doc = json.loads(body)
    except (ValueError, TypeError):
        return Reading(READING_UNREADABLE, None, "the answer was not JSON")
    if not isinstance(doc, dict):
        return Reading(READING_UNREADABLE, None, "the answer was not an object")
    block = doc.get("commit")
    if not isinstance(block, dict):
        return Reading(READING_ABSENT, None,
                       "the answer carried no commit block (a release from before "
                       "the reading)")
    if not block.get("available"):
        return Reading(READING_UNNAMED, None, "the app answered but cannot name the "
                       "commit it is serving (%s)" % (block.get("reason_key") or "no reason"))
    commit = block.get("full_commit") or block.get("commit")
    if not commit:
        # `available: true` with nothing in it is a body this gate cannot read: the
        # reporter says it has a commit and does not say which. Treating it as
        # "absent" would let a hollowed-out payload read as a release that predates
        # the check.
        return Reading(READING_UNREADABLE, None,
                       "the app says it knows its commit and named none")
    return Reading(READING_OK, str(commit), "the app named it")


def judge(readings: list[Reading], merged: str, *, reporter_ships: bool) -> tuple[str, str]:
    """What those answers mean for this release.

    Returns `(verdict, why)`, with `why` a sentence the runner quotes into the
    quarantine record. The order of the checks is the design: a readable
    disagreement is the finding, and only when there is no readable answer at all
    does the marker decide whether silence is evidence or an older release.
    """
    if not merged:
        return VERDICT_UNMEASURED, "no commit to compare against was given"
    if not readings:
        return VERDICT_UNMEASURED, "the app was never answered"

    named = [r for r in readings if r.state == READING_OK]
    for reading in named:
        if reading.commit == merged:
            return VERDICT_OK, "the app is serving %s" % merged[:7]

    if named:
        served = sorted({r.commit for r in named})
        seen = ", ".join(c[:7] for c in served)
        return VERDICT_MISMATCH, (
            "none of %d probe(s) reported %s; the app reports %s"
            % (len(readings), merged[:7], seen))

    if any(r.state == READING_UNREADABLE for r in readings):
        return VERDICT_UNMEASURED, readings[-1].why

    # No commit was named by any probe, and none of the answers was unreadable. Now
    # the marker decides: a release that ships the reading can always name its
    # commit, so silence from *this* release means the code answering is not it.
    if not reporter_ships:
        return VERDICT_UNMEASURED, (
            "the app does not publish the commit it serves, and this release does "
            "not ship the reading either, so there is nothing to compare (a release "
            "that predates %s)" % REPORTER_MARKER)
    if any(r.state == READING_UNNAMED for r in readings):
        # The reporter *is* answering — the reload took — and cannot read its own
        # checkout. That is the box's git, not the release.
        return VERDICT_UNMEASURED, readings[-1].why
    return VERDICT_MISMATCH, (
        "this release ships the reading (the marker is in the merged tree) and none "
        "of %d probe(s) named a commit: the app answering is not this release"
        % len(readings))


def reporter_ships(path: str) -> bool:
    """Does the merged tree carry the reading? Silence is only evidence if it does."""
    try:
        text = open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        return False
    return REPORTER_MARKER in text


def _with_divergence(why: str, repo: str, served, merged: str, unit: str) -> str:
    """Append how far the app is from the merged commit, and the remedy.

    Loaded here rather than at import so this module still loads when it is read as
    text by tests; a module that cannot be loaded leaves the sentence unchanged,
    because a detail line must never be the reason a gate crashes.
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


def fetch(url: str, timeout: float) -> str:
    """The body, or `""` when the box could not be asked. Never raises."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 - loopback
            if getattr(resp, "status", 200) != 200:
                return ""
            return resp.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError, ValueError):
        return ""


def probe_all(base: str, path: str, *, attempts: int, gap: float, timeout: float,
              merged: str) -> list[Reading]:
    """Ask repeatedly, stopping as soon as the merged commit is named.

    The early stop is not an optimization: it is what keeps the retry from costing
    the deploy time on the release that is fine. It is also the only reason one
    probe reporting the previous commit is not a contradiction — a retiring worker
    answers first and the next ask reaches the new code.
    """
    url = base.rstrip("/") + path
    readings: list[Reading] = []
    for index in range(max(1, attempts)):
        body = fetch(url, timeout)
        readings.append(reading_of(body) if body else
                        Reading(READING_UNREADABLE, None, "the app did not answer"))
        if readings[-1].state == READING_OK and readings[-1].commit == merged:
            return readings
        if index + 1 < attempts:
            time.sleep(gap)
    return readings


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Compare the commit the app reports serving with the one this "
                    "run merged.")
    ap.add_argument("--base", default=os.environ.get("SERVED_COMMIT_BASE")
                    or "http://127.0.0.1:8000",
                    help="the app's own address (loopback: nginx is not the process "
                         "whose reload is in question)")
    ap.add_argument("--path", default=DEFAULT_HEALTH_PATH,
                    help="where the reading is published")
    ap.add_argument("--commit", default="",
                    help="the commit this run merged — the one that must be serving")
    ap.add_argument("--reporter", default="",
                    help="the file in the merged tree that carries the reading")
    ap.add_argument("--attempts", type=int, default=DEFAULT_ATTEMPTS)
    ap.add_argument("--gap", type=float, default=DEFAULT_GAP,
                    help="seconds between probes (gunicorn's reload is graceful)")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    ap.add_argument("--repo", default=os.environ.get("SCANGRADE_REPO") or "/opt/scangrade",
                    help="the checkout the divergence is measured against")
    ap.add_argument("--unit", default="scangrade",
                    help="the unit the record should name in its remedy")
    ap.add_argument("--json-out", default="", help="write the readings here")
    return ap


def main() -> int:
    args = build_parser().parse_args()
    try:
        return _run(args)
    except Exception as exc:  # never exit 1: that is what a crash looks like, and
        # the runner must not read a crashed check as a refusal.
        print("served commit: CANNOT MEASURE — the check itself failed: %s: %s"
              % (type(exc).__name__, exc))
        return EXIT_UNMEASURED


def _run(args) -> int:
    ships = reporter_ships(args.reporter) if args.reporter else False
    readings = probe_all(args.base, args.path, attempts=args.attempts, gap=args.gap,
                         timeout=args.timeout, merged=args.commit)
    verdict, why = judge(readings, args.commit, reporter_ships=ships)
    if verdict == VERDICT_MISMATCH:
        served = next((r.commit for r in readings if r.state == READING_OK), "")
        why = _with_divergence(why, args.repo, served, args.commit, args.unit)

    if args.json_out:
        try:
            with open(args.json_out, "w", encoding="utf-8") as handle:
                json.dump({"verdict": verdict, "why": why, "merged": args.commit,
                           "reporter_ships": ships,
                           "readings": [r.__dict__ for r in readings]}, handle)
        except OSError:
            pass

    if verdict == VERDICT_OK:
        print("served commit: OK — %s" % why)
        return EXIT_OK
    if verdict == VERDICT_MISMATCH:
        print("served commit: REFUSED — %s" % why)
        return EXIT_REFUSED
    print("served commit: CANNOT MEASURE — %s" % why)
    return EXIT_UNMEASURED


if __name__ == "__main__":  # pragma: no cover - the runner calls main()
    sys.exit(main())
