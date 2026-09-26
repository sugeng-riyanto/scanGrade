"""Whether the lock's shared store is actually sharing anything.

The draft lock (`app/routes/api.py`) and the rate limiter ride one pooled Redis
connection, and both fall back to this worker when it cannot be reached. Falling
back is the right trade — refusing to store a paper is the one outcome a cache
outage must never cause here — but the fallback's only trace used to be a
`logger.warning`: the site serves normally, `/api/student/sync-draft` still
answers `saved: true`, and the fact that three gevent workers are no longer
serialising against each other lives in `journalctl` on a box nobody is logged
into. Worse, one of the two paths out of the lock (`_lock_conn()` returning None)
did not even log.

So the fallback is recorded where a *page* can see it, and that shapes the design:

* **A process-local record** for the worker that answered the request — how many
  locks the store decided (taken or already held, which are the same fact about the
  store), how many had to be held locally instead, and the reason it is failing now.
  This is what a single-worker box needs, and the only part that can be kept without
  touching the filesystem.
* **A marker file every worker can read**, because this box runs three of them and
  a per-process counter answers only when the page happens to land on the worker
  that fell back — the exact per-worker invisibility the sync throttle had. One
  write per worker per outage (never per lock; `sync-draft` is hot), first writer
  wins so the marker keeps the *earliest* record, and the first shared lock
  deletes it.

The probe is the other half and it is not redundant: the marker says an outage
happened, `rate_limiter.get_redis_status()` says whether the store answers *now*.
Neither alone answers the operator's question ("is this box sharing state right
now, and if not, since when?").

Fail-open in every direction. No state, an unreadable marker, a full disk, a
read-only temp directory: each means "nothing recorded", never an exception from
inside a student's save. And the state file must never live inside the checkout —
the deploy runner refuses a dirty tree, so a marker written there would leave an
untracked file on the box that deploys this code.
"""

from __future__ import annotations

import json
import os
import pathlib
import tempfile
import threading
import time
from datetime import datetime, timezone

# ── the keys the page headlines ──────────────────────────────────────────────

#: The store answers and nothing is on record: locks really are shared.
SHARED = "shared"
#: No shared store is configured at all. On a dev box that is normal; on the box
#: the installer provisions it for, it is a misconfiguration with the same
#: consequence as an outage.
UNCONFIGURED = "unconfigured"
#: Configured, and it does not answer: every lock and every rate limit is this
#: worker's own right now.
UNREACHABLE = "unreachable"
#: It answers *now*, and the lock fell back since this worker started (or a marker
#: says another worker did). The amber case — the warning that arrives while the
#: site still looks completely healthy.
RECORDED = "recorded"
#: The store could not be asked at all. Never rendered as healthy.
UNKNOWN = "unknown"

#: Where the marker lives when `SCANGRADE_LOCK_STATE_FILE` does not say otherwise.
#: The system temp directory, not the checkout: see the module docstring.
DEFAULT_STATE_FILE = os.path.join(tempfile.gettempdir(), "scangrade-lock-fallback.json")

#: How long a healthy worker waits between checks that a marker still needs
#: deleting. It is what heals a marker written by a worker that has since been
#: recycled, without putting a `stat` on the sync path.
CLEAR_INTERVAL_S = 60.0

#: An exception string can be a page of HTML; the card is not.
REASON_MAX = 200

_lock = threading.Lock()
_fallbacks = 0
_shared = 0
_first_fallback_at: float | None = None
_last_fallback_at: float | None = None
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
    return pathlib.Path(os.environ.get("SCANGRADE_LOCK_STATE_FILE") or DEFAULT_STATE_FILE)


def reset() -> None:
    """Forget everything this process counted. Tests only — the marker stays."""
    global _fallbacks, _shared, _first_fallback_at, _last_fallback_at, _last_reason
    global _cleared, _cleared_at, _marker_written, _last_clear_check, _started_at
    with _lock:
        _fallbacks = 0
        _shared = 0
        _first_fallback_at = None
        _last_fallback_at = None
        _last_reason = None
        _cleared = 0
        _cleared_at = None
        _marker_written = False
        _last_clear_check = 0.0
        _started_at = time.time()


def record_shared() -> None:
    """The shared store served a lock — the only outcome that must leave no trace.

    Runs once per `sync-draft`, so the filesystem is touched only when a marker may
    exist: one check a minute, which is also what clears a marker written by a
    worker that has since been recycled.
    """
    global _shared, _cleared, _cleared_at, _marker_written, _last_clear_check
    with _lock:
        _shared += 1
        now = time.monotonic()
        if not (_marker_written or now - _last_clear_check >= CLEAR_INTERVAL_S):
            return
        _last_clear_check = now
        if _remove_marker(state_file()):
            _cleared += 1
            _cleared_at = time.time()
        _marker_written = False


def record_fallback(reason) -> None:
    """The store did not serve a lock, so this worker is holding it locally.

    Counting is unconditional and cheap; the marker is written once per outage per
    worker, and never over one another worker already left — the earliest record of
    an outage is the evidence, and a later writer overwriting it would bury it.
    """
    global _fallbacks, _first_fallback_at, _last_fallback_at, _last_reason, _marker_written
    with _lock:
        now = time.time()
        _fallbacks += 1
        if _first_fallback_at is None:
            _first_fallback_at = now
        _last_fallback_at = now
        _last_reason = _trim(reason)
        if _marker_written:
            return
        _marker_written = True
        _write_marker(state_file(), now, reason)


def _write_marker(path: pathlib.Path, now: float, reason) -> None:
    """Leave the outage on record, or fail silently trying.

    Exclusive create, so a marker another worker wrote first survives; the flag is
    set either way, because retrying on every lock of an outage is exactly the I/O
    this avoids.
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
        # Absent is the one case that gets to mean "nothing on record".
        return out
    except NotADirectoryError as e:
        # `FileNotFoundError`'s sibling, and *not* absent: something is at this
        # path and it is not the marker.
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


def _default_probe() -> dict:
    from app.utils.rate_limiter import get_redis_status
    return get_redis_status()


def _probe(probe) -> tuple[bool | None, str | None, str | None]:
    """(reachable, version, error). `None` reachable = the probe could not answer."""
    try:
        answer = (probe or _default_probe)() or {}
    except Exception as e:
        return None, None, _trim(e)
    if not isinstance(answer, dict):
        return None, None, "the probe answered something that is not a reading"
    connected = answer.get("connected")
    reachable = connected if isinstance(connected, bool) else None
    version = answer.get("version")
    error = answer.get("error")
    return reachable, (str(version) if version else None), (_trim(error) or None)


def state(probe=None, now: float | None = None, path=None) -> dict:
    """Everything the status page needs to say about the shared store.

    Every reading is a stable key plus its data: the sentence a reader sees is
    written in the template, in both languages, where the i18n sweep can check it.
    """
    if now is None:
        now = time.time()
    path = state_file(path)
    reachable, version, error = _probe(probe)
    marker = _read_marker(path, now)

    with _lock:
        reading = {
            "worker_ok": _shared,
            "worker_fallbacks": _fallbacks,
            "worker_first_at": _iso(_first_fallback_at),
            "worker_last_at": _iso(_last_fallback_at),
            "worker_reason": _last_reason,
            "worker_cleared": _cleared,
            "worker_cleared_at": _iso(_cleared_at),
            "worker_started_at": _iso(_started_at),
        }

    recorded = bool(reading["worker_fallbacks"]) or marker["present"]
    if reachable is True:
        key = RECORDED if recorded else SHARED
    elif reachable is False:
        key = UNCONFIGURED if error == "not_configured" else UNREACHABLE
    else:
        key = UNKNOWN

    return {
        "key": key,
        "measured_at": _iso(now),
        "reachable": reachable,
        "version": version,
        "probe_error": error,
        "recorded": recorded,
        "worker": str(os.getpid()),
        "state_file": str(path),
        "marker": marker,
        **reading,
    }
