"""Does *every* long-lived process on this box hold the commit this run merged?

`served_commit_gate.py` asks the app and `worker_commit_gate.py` asks the Celery
worker — each a hand-wired gate for one process. The moment a third long-lived unit
runs this checkout, both are answering an incomplete question: the release renames a
task signature, the helper that imports it keeps the old module in memory, and every
call fails while the box reports success. That is the `page_index` failure again,
one process further out.

This gate is the general form. It reads `deploy/long_lived.py` — the **roster** — and
asks three things:

1. **Coverage.** Which installed units run this checkout (`long_lived.discover`), and
   is every one of them either rostered or exempted with a reason? A discovered unit
   nobody asks is a refusal. This is the clause that makes the arrangement closed: a
   helper added to the box cannot be silent, because the box's own unit files name it.
2. **The ask.** Each rostered process is asked by its own reporter — `http` for the
   app (its `/health` body, reused from `served_commit_gate.py`), `celery` for the
   worker (its control command, reused from `worker_commit_gate.py`), and
   `attestation` for a generic helper (`app/utils/process_attest.py`'s file).
3. **The verdict.** A process that answers with another commit refuses the release; a
   process that cannot be asked at all is "cannot measure", never a rollback — a
   helper that is down and one built before the reading look the same from here, and
   only a *readable disagreement* is evidence about this release.

**Exit codes**, chosen so a crash cannot mimic a refusal: `0` pass, `3` a refused
release (an uncovered unit, or a process on another commit), `2` could not measure.
Never `1`: python exits `1` on an uncaught exception.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent

EXIT_OK = 0
#: A measurement that contradicts the merge (an uncovered unit, or a process running
#: another commit). Not 1: python exits 1 on an uncaught exception.
EXIT_REFUSED = 3
#: No measurement: nothing could be asked, or the answers cannot be read. Never a
#: rollback — a process that is down is not a bad release.
EXIT_UNMEASURED = 2

VERDICT_OK = "ok"
VERDICT_MISMATCH = "mismatch"
VERDICT_UNMEASURED = "unmeasured"

READING_OK = "ok"
READING_UNMEASURED = "unmeasured"


def _load(name: str, filename: str):
    """Import a sibling module by path (these are scripts, not a package).

    Registered in `sys.modules` before execution because `long_lived.py` defines a
    dataclass: `dataclasses` resolves its string annotations through
    `sys.modules[cls.__module__]`, and a module that exists only as a local name
    resolves to None and raises.
    """
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# ── the generic reading, for a process that writes an attestation file ────────

def read_attestation(path, *, pid: int | None) -> tuple[str, str | None, str]:
    """`(state, commit, why)` for one helper's attestation file.

    `pid` is the process the *unit* is running right now (systemd's MainPID), and it
    is the whole reason this is not a file read: the reading is written once, at
    start-up, so an old file from a previous run looks exactly like a current one.
    The pid is what makes it a statement about *this* process — if the file was
    written by a different pid, the process restarted and has not attested yet, and
    that is "cannot measure", not a stale release.
    """
    try:
        text = pathlib.Path(path).read_text(encoding="utf-8")
    except OSError:
        return READING_UNMEASURED, None, "no attestation was written for this process"
    try:
        doc = json.loads(text)
    except ValueError:
        return READING_UNMEASURED, None, "the attestation was not JSON"
    if not isinstance(doc, dict):
        return READING_UNMEASURED, None, "the attestation was not an object"
    if not doc.get("available"):
        return (READING_UNMEASURED, None,
                "the process cannot name its commit (%s)"
                % (doc.get("reason_key") or "no reason"))
    commit = doc.get("full_commit") or doc.get("commit")
    if not commit:
        return READING_UNMEASURED, None, "the attestation says it has a commit and " \
            "named none"
    written_pid = doc.get("pid")
    if pid is None:
        return READING_UNMEASURED, None, (
            "the running process id of this unit could not be read, so the "
            "attestation cannot be tied to it")
    if written_pid != pid:
        return READING_UNMEASURED, None, (
            "the attestation was written by pid %s, not the running process (pid %s)"
            % (written_pid, pid))
    return READING_OK, str(commit), "the process named it"


# ── the verdict over everything ──────────────────────────────────────────────

def judge_all(*, uncovered: list[str], per_process: list[tuple[str, str, str]],
              merged: str) -> tuple[str, str]:
    """`(verdict, why)` across the coverage rule and every process's answer.

    The order is the design. An uncovered unit refuses first: a helper nobody asks is
    a release the deploy cannot guarantee, whatever the processes it *does* ask say.
    Only then does a readable disagreement matter, and only when there is none does
    the remaining "could not measure" become the answer.
    """
    if uncovered:
        return VERDICT_MISMATCH, (
            "%d long-lived unit(s) run this checkout and no gate asks them: %s — add "
            "each to deploy/long_lived.py's roster, or exempt it there with a reason"
            % (len(uncovered), ", ".join(uncovered)))
    if not merged:
        return VERDICT_UNMEASURED, "no commit to compare against was given"
    if not per_process:
        return VERDICT_UNMEASURED, "no long-lived process could be asked"

    for name, verdict, why in per_process:
        if verdict == VERDICT_MISMATCH:
            return VERDICT_MISMATCH, "%s: %s" % (name, why)

    unmeasured = [name for name, verdict, _ in per_process
                  if verdict == VERDICT_UNMEASURED]
    if unmeasured:
        return VERDICT_UNMEASURED, (
            "could not be asked: %s — a process that is down and one built before "
            "this reading are indistinguishable, so this is not read as a release "
            "defect" % ", ".join(unmeasured))
    return VERDICT_OK, ", ".join("%s ok" % name for name, _, _ in per_process)


# ── asking each reporter ─────────────────────────────────────────────────────

def _divergence():
    """The shared gap/remedy sentence, loaded once. A load failure is not fatal:
    a detail line must never be the reason a gate crashes."""
    return _load("commit_divergence", "commit_divergence.py")


def _named(readings) -> str:
    """The first commit any probe named, for measuring the gap."""
    return next((r.commit for r in readings if r.state == READING_OK), "")


def _http_verdict(proc, served, *, repo: str, base: str, merged: str, attempts: int,
                  gap: float, timeout: float) -> tuple[str, str, str]:
    how = proc.how
    readings = served.probe_all(base or how["base"], how["path"], attempts=attempts,
                                gap=gap, timeout=timeout, merged=merged)
    ships = served.reporter_ships(proc.reporter)
    verdict, why = served.judge(readings, merged, reporter_ships=ships)
    if verdict == VERDICT_MISMATCH:
        why = _divergence().annotate(why, repo, _named(readings), merged, unit=proc.unit)
    return proc.name, verdict, why


def _celery_verdict(proc, worker, *, repo: str, merged: str, attempts: int, gap: float,
                    timeout: float) -> tuple[str, str, str]:
    readings = worker.probe_all(lambda: worker.worker_replies(repo, timeout=timeout),
                                attempts=attempts, gap=gap, merged=merged)
    ships = worker.reporter_ships(proc.reporter)
    verdict, why = worker.judge(readings, merged, reporter_ships=ships)
    if verdict == VERDICT_MISMATCH:
        why = _divergence().annotate(why, repo, _named(readings), merged, unit=proc.unit)
    return proc.name, verdict, why


def _attest_verdict(proc, *, repo: str, state_dir: str, merged: str, pid: int | None
                    ) -> tuple[str, str, str]:
    from pathlib import Path
    path = Path(state_dir) / "processes" / (proc.name + ".json")
    state, commit, why = read_attestation(path, pid=pid)
    if state != READING_OK or not commit:
        return proc.name, VERDICT_UNMEASURED, why
    if commit == merged:
        return proc.name, VERDICT_OK, "the process is holding %s" % merged[:7]
    why = _divergence().annotate("the process is holding %s" % commit[:7], repo,
                                 commit, merged, unit=proc.unit)
    return proc.name, VERDICT_MISMATCH, why


def systemd_main_pid(unit: str) -> int | None:
    """"The pid systemd is running for this unit, or None. Never raises."""
    try:
        done = subprocess.run(
            ["systemctl", "show", "-p", "MainPID", "--value", unit],
            capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    value = (done.stdout or "").strip()
    try:
        pid = int(value)
    except ValueError:
        return None
    return pid or None


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Ask every long-lived process which commit it holds.")
    ap.add_argument("--repo", default=os.environ.get("SCANGRADE_REPO") or "/opt/scangrade")
    ap.add_argument("--units-dir", default="/etc/systemd/system")
    ap.add_argument("--state-dir",
                    default=os.environ.get("SCANGRADE_STATE_DIR")
                    or "/var/lib/scangrade-deploy")
    ap.add_argument("--commit", default="", help="the commit this run merged")
    ap.add_argument("--app-base", default="", help="override the app's base address")
    ap.add_argument("--attempts", type=int, default=5)
    ap.add_argument("--gap", type=float, default=2.0)
    ap.add_argument("--timeout", type=float, default=5.0)
    ap.add_argument("--json-out", default="", help="write the verdicts here")
    return ap


def _run(args) -> int:
    roster = _load("long_lived", "long_lived.py")
    served = _load("served_commit_gate", "served_commit_gate.py")
    worker = _load("worker_commit_gate", "worker_commit_gate.py")

    discovered = roster.discover(args.units_dir, args.repo)
    uncovered = roster.uncovered(discovered)

    per_process: list[tuple[str, str, str]] = []
    for proc in roster.ROSTER:
        try:
            if proc.ask == "http":
                per_process.append(_http_verdict(
                    proc, served, repo=args.repo, base=args.app_base,
                    merged=args.commit, attempts=args.attempts, gap=args.gap,
                    timeout=args.timeout))
            elif proc.ask == "celery":
                per_process.append(_celery_verdict(
                    proc, worker, repo=args.repo, merged=args.commit,
                    attempts=args.attempts, gap=args.gap, timeout=args.timeout))
            else:
                per_process.append(_attest_verdict(
                    proc, repo=args.repo, state_dir=args.state_dir, merged=args.commit,
                    pid=systemd_main_pid(proc.unit)))
        except Exception as exc:  # an ask that crashes is "cannot measure", never a
            # refusal: a broken check must not roll back a good release.
            per_process.append((proc.name, VERDICT_UNMEASURED,
                                "the check failed: %s: %s" % (type(exc).__name__, exc)))

    verdict, why = judge_all(uncovered=uncovered, per_process=per_process,
                             merged=args.commit)

    if args.json_out:
        try:
            with open(args.json_out, "w", encoding="utf-8") as handle:
                json.dump({"verdict": verdict, "why": why, "merged": args.commit,
                           "uncovered": uncovered,
                           "processes": [{"name": n, "verdict": v, "why": w}
                                         for n, v, w in per_process]}, handle)
        except OSError:
            pass

    for proc in roster.ROSTER:
        for name, pv, pwhy in per_process:
            if name == proc.name:
                print("process commit: %-8s %-9s %s" % (name, pv, pwhy))

    if verdict == VERDICT_OK:
        print("process commit: OK — %s" % why)
        return EXIT_OK
    if verdict == VERDICT_MISMATCH:
        print("process commit: REFUSED — %s" % why)
        return EXIT_REFUSED
    print("process commit: CANNOT MEASURE — %s" % why)
    return EXIT_UNMEASURED


def main() -> int:
    args = build_parser().parse_args()
    try:
        return _run(args)
    except Exception as exc:  # never exit 1: that is what a crash looks like.
        print("process commit: CANNOT MEASURE — the check itself failed: %s: %s"
              % (type(exc).__name__, exc))
        return EXIT_UNMEASURED


if __name__ == "__main__":  # pragma: no cover - the runner calls main()
    sys.exit(main())
