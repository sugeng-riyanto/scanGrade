"""Every long-lived process this box runs from the checkout, and how each is asked.

The deploy asks gunicorn which commit it is serving (`served_commit_gate.py`) and the
Celery worker (`worker_commit_gate.py`). Both are *hand-wired*: each is its own gate,
its own fence in the runner, its own command line. That was true on the day it was
written and nothing kept it true. A third long-lived unit — a scheduler, a helper with
no HTTP surface — would import this release's code once at start-up and hold it across
every reload, and no gate would ask it, because no gate knows it exists.

This module is the roster, and it is deliberately **closed**. `discover()` reads the
box's own unit files and answers a question the runner never asked before: *which
units run this checkout?* Everything it finds must be either in `ROSTER` (and so
asked) or in `EXEMPT` with a reason a reader can act on. A unit in neither is a
refusal — `deploy/process_commit_gate.py` turns it into a refused release — so a
helper cannot be added to the box without the deploy noticing it.

Two things are deliberately one place and not two:

* **What runs here.** `ROSTER` is the single list; the runner's restart loop and the
  gate both read it, so a new unit is added once rather than remembered in three
  files that then drift.
* **How each is asked.** `ask` names the reporter — `http` (the app publishes on
  `/health`), `celery` (the worker answers a control command), or `attestation` (a
  generic helper writes `app/utils/process_attest.py`'s file). The two that exist
  today keep their own gates; the third is how a *future* process answers without a
  second bespoke gate.
"""
from __future__ import annotations

import argparse
import dataclasses
import os
import pathlib
import sys


@dataclasses.dataclass(frozen=True)
class Process:
    """One long-lived process: its unit, how it is asked, and how it is replaced."""

    name: str
    unit: str
    #: "http" | "celery" | "attestation"
    ask: str
    #: "reload" (graceful, for the request-serving app) | "restart" (everything else)
    action: str
    #: ask-specific: the address/path, the control command, the reporter file and the
    #: fence (`xxx:start`) that proves the release ships the reading.
    how: dict

    @property
    def reporter(self) -> str:
        return self.how.get("reporter", "")

    @property
    def marker(self) -> str:
        return self.how.get("marker", "")


#: The units this checkout runs, in the order the runner replaces them: the app
#: first (graceful reload, it serves students), then the worker (restart, it holds no
#: request open). A new long-lived process is added here, once.
ROSTER: tuple[Process, ...] = (
    Process("app", "scangrade", "http", "reload", {
        "base": "http://127.0.0.1:8000",
        "path": "/health",
        "reporter": "app/__init__.py",
        "marker": "served-commit:start",
    }),
    Process("worker", "scangrade-celery", "celery", "restart", {
        "command": "served_commit",
        "reporter": "app/celery_app.py",
        "marker": "worker-commit:start",
    }),
)

#: Units that run the checkout and are deliberately **not** asked, each with the
#: reason an operator can read. An exemption with no sentence is the same as a
#: forgotten gate, so every entry here must say why.
EXEMPT: dict[str, str] = {
    "scangrade-deploy": (
        "the oneshot runner is the process asking this very question — it exits "
        "rather than holding a release, so there is no commit for it to be stale on"),
}


def _norm(path: str) -> str:
    """A path comparable on either platform: separators unified, `.`/`..` folded."""
    return os.path.normpath(str(path).replace("\\", "/")).replace("\\", "/")


def unit_runs_checkout(text: str, repo: str) -> bool:
    """Does this unit file start a process inside `repo`?

    Read line by line and never sourced: a unit file is data an operator (or a
    package) can put anything into, so the question is answered by the two keys that
    actually place a process — `WorkingDirectory=` and the `Exec*=` command lines —
    rather than by a substring search over the whole file.
    """
    repo_n = _norm(repo)
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().lower()
        value = value.strip().lstrip("-+!@").strip().strip('"')
        if key == "workingdirectory":
            if _norm(value) == repo_n:
                return True
        elif key in ("execstart", "execreload", "execstartpre"):
            if repo_n + "/" in _norm(value):
                return True
    return False


def discover(units_dir: pathlib.Path | str, repo: str) -> list[str]:
    """The names of installed `.service` units that run this checkout.

    A directory that cannot be read is an empty list rather than an exception: the
    gate that calls this owns the distinction between "nothing runs this checkout"
    and "the box could not be read", and neither of those is a release defect.
    """
    found: list[str] = []
    directory = pathlib.Path(units_dir)
    try:
        entries = sorted(directory.glob("*.service"))
    except OSError:
        return found
    for path in entries:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if unit_runs_checkout(text, repo):
            found.append(path.name[: -len(".service")])
    return found


def uncovered(discovered: list[str], *, roster: tuple[Process, ...] = ROSTER,
              exempt: dict[str, str] = EXEMPT) -> list[str]:
    """Discovered units that no gate asks — the ones a helper hides in."""
    known = {proc.unit for proc in roster} | set(exempt)
    return sorted({unit for unit in discovered if unit not in known})


def roster_units(*, roster: tuple[Process, ...] = ROSTER) -> list[str]:
    """The units the runner must replace, in order. One list, read everywhere."""
    return [proc.unit for proc in roster]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="The roster of long-lived processes this checkout runs.")
    ap.add_argument("--units", action="store_true",
                    help="print the rostered units, for the runner's restart loop")
    ap.add_argument("--discover", action="store_true",
                    help="print the installed units that run this checkout")
    ap.add_argument("--uncovered", action="store_true",
                    help="print discovered units no gate asks (empty is good)")
    ap.add_argument("--repo", default=os.environ.get("SCANGRADE_REPO") or "/opt/scangrade")
    ap.add_argument("--units-dir", default="/etc/systemd/system")
    args = ap.parse_args(argv)

    if args.units:
        for unit in roster_units():
            print(unit)
        return 0
    if args.discover:
        for unit in discover(args.units_dir, args.repo):
            print(unit)
        return 0
    if args.uncovered:
        for unit in uncovered(discover(args.units_dir, args.repo)):
            print(unit)
        return 0
    ap.error("nothing to do: pass --units, --discover or --uncovered")
    return 2


if __name__ == "__main__":  # pragma: no cover - the runner calls main()
    sys.exit(main())
