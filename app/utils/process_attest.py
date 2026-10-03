"""How a long-lived process that is *not* the app or the worker names its commit.

The deploy asks gunicorn (`deploy/served_commit_gate.py`) and the Celery worker
(`deploy/worker_commit_gate.py`) which commit they hold, because each keeps its code
in memory for the life of the process and a reload that misses one is a
half-deployed release. Those two are hand-wired, and `deploy/long_lived.py` is the
roster that keeps the arrangement closed: a unit running this checkout that the
roster does not carry is a refused release.

This module is how a *new* long-lived process answers the same question without a
third bespoke gate. It has no HTTP surface and no broker, so it leaves a line of JSON
under ``<state_dir>/processes/<name>.json`` at start-up: the commit whose code this
process loaded, its pid, and when it wrote the reading.

    from app.utils import process_attest

    def main():
        # process-attest:start
        process_attest.publish("sg-scheduler")
        # process-attest:end
        run_forever()

A caller marks the call site with ``process-attest:start`` so a roster entry can
prove the release ships its reading, exactly as the app's `/health` block is fenced
in `app/__init__.py`.

Two rules, both of them the reason this is not a one-line helper:

* **The reading is `build_info`'s, resolved at import.** It is the commit whose code
  this process is running, not whatever the checkout points at now — a reading taken
  later would follow the checkout as it moves and describe a release this process is
  not actually holding.
* **A failure is written down, not raised.** A process with no git, or outside a
  checkout, still has to start; it publishes a reason instead of inventing a sha, and
  the gate reports that as "cannot measure" rather than as a stale release.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import pathlib
import re

#: Where the reading is written by default. The same state directory the rest of the
#: deploy reads (`/var/lib/scangrade-deploy`); overridable so a test, or a process
#: running outside the service user, can publish somewhere it may write.
DEFAULT_STATE_DIR = "/var/lib/scangrade-deploy"

_SUBDIR = "processes"
_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def state_root() -> pathlib.Path:
    """The state directory, from the environment or the default the rest of the
    deploy uses. No argument, so a caller cannot shadow it with a parameter."""
    return pathlib.Path(os.environ.get("SCANGRADE_STATE_DIR") or DEFAULT_STATE_DIR)


def path_for(name: str, *, state_dir: str | os.PathLike | None = None) -> pathlib.Path:
    """Where this process's reading lives. The name is made filename-safe, so a
    helper cannot write outside the directory by choosing a name with a slash."""
    safe = _SAFE.sub("_", str(name)).strip("_") or "process"
    base = pathlib.Path(state_dir) if state_dir is not None else state_root()
    return base / _SUBDIR / (safe + ".json")


def publish(name: str, *, state_dir: str | os.PathLike | None = None,
            snapshot: dict | None = None) -> dict:
    """Write this process's commit where the deploy gate reads it.

    Never raises: a helper that cannot write its own reading must still run, and a
    missing reading is reported by the gate as "cannot measure", which is a box
    problem rather than a release defect. The write is atomic (a temp file moved into
    place) so a gate reading concurrently cannot see a half-written JSON.
    """
    if snapshot is None:
        try:
            from app.utils import build_info
            snapshot = build_info.snapshot()
        except Exception as exc:  # pragma: no cover - a broken install is a box
            snapshot = {"available": False, "reason_key": "no_build_info",
                        "detail": "%s: %s" % (type(exc).__name__, exc)}

    payload = {
        "name": str(name),
        "available": bool(snapshot.get("available")),
        "reason_key": snapshot.get("reason_key"),
        "detail": snapshot.get("detail"),
        "commit": snapshot.get("commit"),
        "full_commit": snapshot.get("full_commit"),
        "pid": os.getpid(),
        "written_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
    }

    target = path_for(name, state_dir=state_dir)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(temporary, target)
    except OSError:
        # The reading is lost, not the process. The gate will report "cannot
        # measure"; a helper that refused to start because it could not write a
        # status file would be worse than the staleness this exists to catch.
        pass
    return payload
