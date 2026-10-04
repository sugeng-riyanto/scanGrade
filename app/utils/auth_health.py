"""Whether GoTrue is hiccupping on account creates — where a page can see it.

`create_user_with_retry` (`app/utils/auth_retry.py`) exists because GoTrue answers
`Database error creating new user` for a *transient* insert failure and repeating
the create works. That retry is invisible: the operator sees success, and the only
trace of a box whose auth server is struggling is a school eventually reporting
"it keeps failing". The retry count is the leading indicator, and nothing showed
it.

This is the same shape `lock_health` uses, for the same reasons:

* **A process-local counter** for the worker that answered the request — how many
  creates needed a retry, how many exhausted them, and the last reason. That is
  what a single-worker box needs, and the part that costs nothing.
* **A marker file every worker can read**, because this box runs three gevent
  workers and a per-process counter answers only when the page happens to land on
  the worker that retried. One write per worker per outage (never per create),
  first writer wins so the marker keeps the *earliest* record, and the first
  first-try success deletes it.

Fail-open in every direction. No record, an unreadable marker, a full disk, a
read-only temp directory: each means "nothing recorded", never an exception from
inside somebody creating a teacher. And the marker must never live inside the
checkout — the deploy runner refuses a dirty tree, so a file written there would
leave the box permanently dirty.
"""

from __future__ import annotations

import json
import os
import pathlib
import tempfile
import threading
import time
from datetime import datetime, timezone

# ── the two states a page headlines ──────────────────────────────────────────

#: Nothing on record: account creates are landing on the first try here.
CLEAN = "clean"
#: A create needed a retry since this worker started, or a marker says another
#: worker did. The amber case — the warning that arrives while nothing looks wrong.
RECORDED = "recorded"

#: Where the marker lives when `SCANGRADE_AUTH_STATE_FILE` does not say otherwise.
#: The system temp directory, not the checkout: see the module docstring.
DEFAULT_STATE_FILE = os.path.join(tempfile.gettempdir(), "scangrade-auth-retries.json")

#: How long a clean worker waits between checks that a marker still needs deleting.
CLEAR_INTERVAL_S = 60.0

#: An exception string can be a page of HTML; the card is not.
REASON_MAX = 200

#: A test hook so the suites can point the record at a temp path without an env
#: var. `None` in production, where the env var or the default wins.
_STATE_FILE_OVERRIDE: pathlib.Path | None = None

_lock = threading.Lock()
_retries = 0
_exhausted = 0
_first_at: float | None = None
_last_at: float | None = None
_last_reason: str | None = None
_cleared = 0
_cleared_at: float | None = None
_started_at = time.time()
#: This worker has already left its marker for the current outage.
_marker_written = False
_last_clear_check = 0.0


def _trim(text) -> str:
    """A reason short enough for a card, never a blank one."""
    out = " ".join(str(text or "").split())
    return out[:REASON_MAX]


def _iso(stamp: float | None) -> str | None:
    if stamp is None:
        return None
    return datetime.fromtimestamp(stamp, tz=timezone.utc).isoformat(timespec="seconds")


def state_file(path=None) -> pathlib.Path:
    """The marker's path: the argument, else the env var, else the temp directory."""
    if path is not None:
        return pathlib.Path(path)
    if _STATE_FILE_OVERRIDE is not None:
        return pathlib.Path(_STATE_FILE_OVERRIDE)
    return pathlib.Path(os.environ.get("SCANGRADE_AUTH_STATE_FILE") or DEFAULT_STATE_FILE)


def reset() -> None:
    """Forget everything this process counted. Tests only — the marker stays."""
    global _retries, _exhausted, _first_at, _last_at, _last_reason
    global _cleared, _cleared_at, _marker_written, _last_clear_check, _started_at
    with _lock:
        _retries = 0
        _exhausted = 0
        _first_at = None
        _last_at = None
        _last_reason = None
        _cleared = 0
        _cleared_at = None
        _marker_written = False
        _last_clear_check = 0.0
        _started_at = time.time()


def _note(reason, path) -> None:
    """Stamp the reason and leave the marker, once per worker per outage.

    Shared by a retry and an exhausted create so the *terminal* failure is not
    counted as one more retry — the two counters are counts of two different
    things, and a page that showed "two retries" for one attempt that failed twice
    would overstate what happened.
    """
    global _first_at, _last_at, _last_reason, _marker_written
    now = time.time()
    if _first_at is None:
        _first_at = now
    _last_at = now
    _last_reason = _trim(reason)
    if _marker_written:
        return
    _marker_written = True
    _write_marker(state_file(path), now, reason)


def record_retry(reason, path=None) -> None:
    """A create hit GoTrue's transient database answer and another attempt follows.

    Counting is unconditional and cheap; the marker is written once per worker per
    outage, and never over one another worker already left — the earliest record of
    an outage is the evidence, and a later writer overwriting it would bury it.
    """
    global _retries
    with _lock:
        _retries += 1
        _note(reason, path)


def record_exhausted(reason, path=None) -> None:
    """Every attempt was transient and the create never landed.

    The reason is recorded too, because an exhausted create is the one an operator
    is asked about, and it must not be the case that the *last* reason on the card
    came from a retry that later succeeded — while a retry that later succeeds is
    exactly what the retry counter is for.
    """
    global _exhausted
    with _lock:
        _exhausted += 1
        _note(reason, path)


def record_clean(path=None, now: float | None = None) -> None:
    """A create landed on the first try — the one outcome that heals the marker.

    Runs on a create, not a hot path, but still throttled: healing does not need a
    `stat` on every create, and the throttle is what clears a marker written by a
    worker that has since been recycled.
    """
    global _cleared, _cleared_at, _marker_written, _last_clear_check
    with _lock:
        now = time.monotonic() if now is None else now
        if not (_marker_written or now - _last_clear_check >= CLEAR_INTERVAL_S):
            return
        _last_clear_check = now
        if _remove_marker(state_file(path)):
            _cleared += 1
            _cleared_at = time.time()
        _marker_written = False


def _write_marker(path: pathlib.Path, now: float, reason) -> None:
    """Leave the outage on record, or fail silently trying.

    Exclusive create, so a marker another worker wrote first survives; the flag is
    set either way, because retrying on every create of an outage is exactly the
    I/O this avoids.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "x", encoding="utf-8") as handle:
            json.dump({"at": _iso(now), "reason": _trim(reason),
                       "worker": str(os.getpid())}, handle)
            handle.write("\n")
    except OSError:
        pass


def _remove_marker(path: pathlib.Path) -> bool:
    """Delete the marker. True when one was actually there."""
    try:
        path.unlink()
        return True
    except OSError:
        return False


def _read_marker(path: pathlib.Path, now: float) -> dict:
    """The marker as a reading, with `absent` never standing in for unreadable."""
    out = {"present": False, "key": "absent", "at": None, "reason": None,
           "worker": None, "age_seconds": None}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return out
    except NotADirectoryError as e:
        return dict(out, present=True, key="unreadable", reason=_trim(e))
    except OSError as e:
        return dict(out, present=True, key="unreadable", reason=_trim(e))

    out["present"] = True
    try:
        data = json.loads(text)
    except (ValueError, TypeError) as e:
        return dict(out, key="malformed", reason=_trim(e))
    if not isinstance(data, dict):
        return dict(out, key="malformed", reason="the marker is not an object")

    at = data.get("at") if isinstance(data.get("at"), str) else None
    out.update(key="ok", at=at, reason=_trim(data.get("reason")) or None,
               worker=str(data.get("worker")) if data.get("worker") else None)
    if at:
        try:
            written = datetime.fromisoformat(at)
            if written.tzinfo is None:
                written = written.replace(tzinfo=timezone.utc)
            out["age_seconds"] = int(now - written.timestamp())
        except (ValueError, TypeError):
            out["age_seconds"] = None
    return out


def state(now: float | None = None, path=None) -> dict:
    """Everything the status page needs to say about account-create retries.

    Every reading is a stable key plus its data: the sentence a reader sees is
    written in the template, in both languages, where the i18n sweep can check it.
    """
    if now is None:
        now = time.time()
    path = state_file(path)
    marker = _read_marker(path, now)

    with _lock:
        reading = {
            "worker_retries": _retries,
            "worker_exhausted": _exhausted,
            "worker_first_at": _iso(_first_at),
            "worker_last_at": _iso(_last_at),
            "worker_reason": _last_reason,
            "worker_cleared": _cleared,
            "worker_cleared_at": _iso(_cleared_at),
            "worker_started_at": _iso(_started_at),
        }

    recorded = bool(_retries) or marker["present"]
    return {
        "key": RECORDED if recorded else CLEAN,
        "measured_at": _iso(now),
        "recorded": recorded,
        "worker": str(os.getpid()),
        "state_file": str(path),
        "marker": marker,
        **reading,
    }
