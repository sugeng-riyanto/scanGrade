"""How many of this box's workers can name the commit they are running.

`app/utils/build_info.py` freezes a process's reading at import, so on a box with
three gevent workers the answer to "what commit is being served" is not one value:
it is one value *per worker*, and `/health` is answered by whichever worker the
connection happened to reach. That is why a single probe cannot tell an honest box
from a half-broken one. It was measured here: **one worker in three answered
`unreadable` for two days** while its neighbours read the same checkout normally,
and every individual `/health` sample looked like a coincidence — the reading was
only visible to someone asking five times and tallying the answers by hand — which
is what the box report's fifteen samples are for, and why it needs that many.

This module is what makes one request enough. Each worker leaves its own reading in
a marker file at import, and any worker can list the directory and count:

* **how many workers can name their commit** (`seen`, `blind`, and the reasons),
* **whether they agree** (`commits`: the same shas the served-commit gate compares,
  so a reload that landed in two workers out of three is a *reading*, not a guess),
* **how many are missing from the ledger** (`unregistered`, against
  `GUNICORN_WORKERS`), because a worker whose marker was never written is a worker
  whose reading is unknown — and unknown must not be counted as healthy.

The shape is deliberately *a directory of markers* rather than the single marker
`lock_health` / `auth_health` use: there, the fact is "an outage happened" and the
earliest writer is the evidence; here the fact is **per worker**, so first-writer-wins
would erase the very thing being counted.

Three rules, each of them a way this could otherwise lie:

* **A marker is counted only while its process is alive.** The file outlives a
  recycled worker (`max_requests` cycles them), and a dead worker's reading is not
  a worker. `os.kill(pid, 0)` decides that on POSIX, and the process's own start
  time from `/proc/<pid>/stat` is compared as well, because a pid is **reused**: a
  stale marker whose pid now belongs to an unrelated process would otherwise be
  counted as a live worker with a stale reading. Dead markers are removed as they
  are found, so the directory cannot grow for the life of the box.
* **Only HTTP workers are counted.** This directory is shared by every process that
  imports the app, and the Celery worker does too — it has its own gate
  (`deploy/worker_commit_gate.py`) and is reloaded on its own schedule, so counting
  it here would report a mixed box on every release while the app workers agree.
  The role is decided from `GUNICORN_WORKERS`, which gunicorn's master exports to
  the workers it forks.
* **Nothing here raises.** This runs at import, in every process, including a box
  with a full disk, a read-only `/tmp` or no temp directory at all (see
  `import_safety`). A marker that cannot be written costs a count, never a worker —
  and the reading says it could not measure rather than reporting zero blind
  workers, because zero is a claim and `unmeasured` is not.

The markers must never live inside the checkout: the deploy runner refuses a dirty
tree, so a file written there would leave the box permanently dirty. They go in the
system temp directory, like the other two marker modules, and hold a sha and a
reason — never the commit's subject, which `/health` does not publish either.
"""

from __future__ import annotations

import json
import os
import pathlib
from datetime import datetime, timezone

from app.utils import import_safety

# ── the three keys this reading headlines ────────────────────────────────────

#: Every live worker named the same commit.
UNIFORM = "uniform"
#: At least one live worker could not name a commit, one worker could not be read,
#: or the workers named different commits. The amber case, and the one this module
#: exists for: it is a property of the *box*, so no single sample can see it.
MIXED = "mixed"
#: There was nowhere to look, or nothing had recorded a reading. Never rendered as
#: `uniform`: an unmeasured box is not an agreeing one.
UNMEASURED = "unmeasured"

#: The directory the markers live in, under the temp directory. Read at import
#: through `import_safety`, because `tempfile.gettempdir()` *opens files* to prove a
#: directory is writable and raises when none of them is, and at module scope that
#: is an import-time death for the app, the worker and `manage.py` at once. `""`
#: means there is nowhere to keep them, which `state_dir()` turns into None.
DIR_NAME = "scangrade-commit-readers"
DEFAULT_DIR = import_safety.default_state_file(DIR_NAME)

#: Why there is no default directory, or ``""``. Published so a page, a probe or a
#: test can tell "nothing recorded" from "nowhere to record it".
TEMP_DIR_ERROR = import_safety.TEMP_DIR_ERROR

#: The env var that moves the directory, for a box whose temp directory is not
#: shared between workers or a suite that wants a scratch one.
ENV_DIR = "SCANGRADE_COMMIT_READERS_DIR"

#: gunicorn's master exports this to the workers it forks (see `gunicorn.conf.py`),
#: which is what distinguishes the processes that answer `/health` from the Celery
#: worker that imports the same app without a request surface.
WORKER_COUNT_ENV = "GUNICORN_WORKERS"

#: The marker's `role` for a process that answers requests, and for one that does
#: not (Celery, `manage.py`, a one-off script). Only the first is counted.
HTTP_ROLE = "http"
OTHER_ROLE = "other"

#: A reason in a marker is a short sentence, not a page of traceback.
REASON_MAX = 160


def _trim(text) -> str | None:
    out = " ".join(str(text or "").split())
    return out[:REASON_MAX] or None


def state_dir(directory=None) -> pathlib.Path | None:
    """Where the markers live, or None when this box has nowhere to keep them.

    The argument and the environment variable always win; otherwise the temp
    directory decides, and `""` from `DEFAULT_DIR` becomes None rather than
    `Path("")` — which is `.`, a directory that would be *written to* and then read
    back as an empty ledger, reporting a box with no workers instead of a box with
    no temp directory.
    """
    if directory is not None:
        return pathlib.Path(directory)
    chosen = os.environ.get(ENV_DIR) or DEFAULT_DIR
    return pathlib.Path(chosen) if chosen else None


# ── is that pid this worker, another one, or a stranger? ─────────────────────

def _proc_start(pid) -> int | None:
    """When `pid` started, in kernel clock ticks — or None when it cannot be read.

    Field 22 of `/proc/<pid>/stat`, and it is the only thing that makes a pid an
    *identity*: Linux reuses pids, so a marker for a recycled worker would otherwise
    be counted as a live one carrying a stale reading. Read after the `)` that
    closes the command name, because that name is the one field a process can put
    spaces and parentheses in.
    """
    try:
        with open("/proc/%d/stat" % int(pid), encoding="ascii", errors="replace") as handle:
            raw = handle.read()
    except (OSError, TypeError, ValueError):
        return None
    head = raw.rsplit(")", 1)
    if len(head) != 2:
        return None
    fields = head[1].split()
    if len(fields) < 20:
        return None
    try:
        return int(fields[19])
    except ValueError:
        return None


def _process_alive(pid, start=None):
    """True / False / None — alive, gone, or this box cannot say.

    `None` is a real answer and the caller must not fold it into either: a pid this
    process may not signal belongs to *somebody*, and a platform without `/proc` and
    `os.kill`'s POSIX meaning cannot be asked at all. Windows is the sharp edge
    here — `os.kill(pid, 0)` is not a probe there, it **terminates** the process —
    so the platform check comes first and there is no fallback for it.
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    if pid == os.getpid():
        # This process is, by definition, running this code.
        return True
    if os.name != "posix":
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # It exists and is not ours: the deploy owner's worker, most likely.
        pass
    except OSError:
        return None
    started = _proc_start(pid)
    if started is not None and start is not None:
        try:
            if int(start) != started:
                # The pid was recycled: this marker belongs to a process that is
                # gone, and its reading is not this box's current answer.
                return False
        except (TypeError, ValueError):
            pass
    return True


# ── this process's own marker ────────────────────────────────────────────────

def _write_atomic(target: pathlib.Path, payload: dict) -> None:
    """Write beside the target and rename, so a reader never sees half a marker.

    `/health` can be answered while another worker is starting up — that is exactly
    the moment a mixed box is being observed — and a partially written JSON file
    would be reported as an unreadable worker, which is a worker with a problem it
    does not have.
    """
    staged = target.with_name(".%s.%d.tmp" % (target.name, os.getpid()))
    try:
        with open(staged, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
            handle.write("\n")
        os.replace(staged, target)
    except (OSError, TypeError, ValueError):
        # Whatever went wrong, the staged file is not the caller's problem: it is a
        # half-written marker that would be counted as a worker whose reading could
        # not be read, on every `/health` for the life of the box.
        try:
            staged.unlink()
        except OSError:
            pass
        raise


def record(reading, *, loaded_at=None, repo=None, directory=None,
           role=None, pid=None, start=None):
    """Leave this process's reading where `/health` can count it. Never raises.

    Called once, at import, from `build_info` — the reading is frozen there, so it is
    also the only honest moment to publish it. `pid` / `start` / `role` exist for the
    suites, which have to describe workers that are not themselves.
    """
    path_dir = state_dir(directory)
    if path_dir is None:
        return None
    reading = reading or {}
    available = bool(reading.get("available"))
    pid = os.getpid() if pid is None else pid
    if role is None:
        role = HTTP_ROLE if os.environ.get(WORKER_COUNT_ENV) else OTHER_ROLE
    loaded = loaded_at or reading.get("loaded_at")
    payload = {
        "pid": int(pid),
        "start": _proc_start(pid) if start is None else start,
        "role": role,
        "repo": (str(import_safety.resolved(pathlib.Path(repo))) if repo else None),
        "loaded_at": str(loaded) if loaded else None,
        "available": available,
        "reason_key": reading.get("reason_key"),
        # Only when it was read: a sha beside `available: false` is the shape that
        # made `/health` say a blind worker was serving a commit.
        "commit": reading.get("commit") if available else None,
    }
    try:
        path_dir.mkdir(parents=True, exist_ok=True)
        _write_atomic(path_dir / ("%d.json" % int(pid)), payload)
    except (OSError, ValueError, TypeError):
        return None
    return path_dir / ("%d.json" % int(pid))


# ── the box, as its workers report it ────────────────────────────────────────

def _expected_workers() -> int | None:
    """`GUNICORN_WORKERS` as a number, or None when the box does not say."""
    raw = (os.environ.get(WORKER_COUNT_ENV) or "").strip()
    return int(raw) if raw.isdigit() else None


def _read_marker(path: pathlib.Path) -> dict | None:
    """One marker, or None when it cannot be read as one."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def state(*, repo=None, directory=None, expected=None, alive=None) -> dict:
    """The box's workers, counted. One directory listing, no request to anybody.

    Every field is present in every outcome: an unmeasured box reports `seen: None`
    rather than `0`, because "no worker was counted" and "no worker is blind" are
    opposite answers and only one of them is good news.
    """
    path_dir = state_dir(directory)
    out: dict = {
        "key": UNMEASURED,
        "seen": None,
        "blind": None,
        "commits": {},
        "reasons": {},
        "skipped": 0,
        "expected": _expected_workers() if expected is None else expected,
        "unregistered": None,
        "directory": str(path_dir) if path_dir is not None else "",
        "detail": None,
    }
    if path_dir is None:
        out["detail"] = _trim(TEMP_DIR_ERROR) or "no usable temp directory"
        return out
    try:
        entries = sorted(path_dir.iterdir())
    except FileNotFoundError:
        out["detail"] = ("no worker has recorded a reading in %s" % path_dir)
        return out
    except OSError as exc:
        out["detail"] = "%s: %s" % (type(exc).__name__, _trim(exc) or "unreadable")
        return out

    check = alive or _process_alive
    wanted = str(import_safety.resolved(pathlib.Path(repo))) if repo else None
    seen = blind = skipped = 0
    commits: dict = {}
    reasons: dict = {}
    for entry in entries:
        if not entry.name.endswith(".json") or entry.name.startswith("."):
            continue
        marker = _read_marker(entry)
        if marker is None:
            # Something is there and it is not a marker. Count it as a worker this
            # reading could not account for, and say so — silently ignoring it is
            # how a half-written file becomes a clean bill of health.
            skipped += 1
            continue
        verdict = check(marker.get("pid"), marker.get("start"))
        if verdict is False:
            # Pruned before the role and checkout filters, and deliberately: a marker
            # whose process is gone is not a reading of anything, and the ones that
            # *are* filtered out below (Celery's, another checkout's, a test run's)
            # would otherwise sit here for the life of the box, one per process
            # start, each of them costing a `stat` on every `/health`.
            try:
                entry.unlink()
            except OSError:
                pass
            continue
        if marker.get("role") != HTTP_ROLE:
            # Celery, `manage.py`, a one-off script: real processes holding this
            # checkout, with their own gates and their own reload schedule.
            continue
        if wanted is not None and marker.get("repo") != wanted:
            # Another checkout on the same box. Its workers are not this app's.
            continue
        if verdict is None:
            skipped += 1
            continue
        seen += 1
        if marker.get("available") and marker.get("commit"):
            commits[marker["commit"]] = commits.get(marker["commit"], 0) + 1
        else:
            blind += 1
            reason = _trim(marker.get("reason_key")) or "no reason given"
            reasons[reason] = reasons.get(reason, 0) + 1

    if not seen and not skipped:
        out["detail"] = "no live worker has recorded a reading in %s" % path_dir
        return out

    out["seen"] = seen
    out["blind"] = blind
    out["skipped"] = skipped
    out["commits"] = commits
    out["reasons"] = reasons
    if out["expected"] is not None:
        out["unregistered"] = max(0, int(out["expected"]) - seen)
    out["key"] = (MIXED if (blind or skipped or len(commits) > 1) else UNIFORM)
    if skipped:
        out["detail"] = ("%d marker(s) could not be read as a worker reading" % skipped)
    return out
