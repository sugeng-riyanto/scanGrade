"""A room deletion that did not happen is a debt, not a log line.

`end_room()` was called exactly once. When LiveKit was down or slow, the route
logged an exception and answered ``"media_cleanup": "pending"`` — which was true
and useless: the session row stops *new* tokens, but every participant already
inside the room keeps publishing, and nothing on the box would ever try the
deletion again. A single failed API call therefore left a camera's worth of
video running with no owner.

This module is the ledger and the retry. What it guarantees, and why each part
is shaped the way it is:

* **The debt outlives the request and the process.** The job lives in
  `scan_live_cleanup_jobs` (migration 073), so a worker restart, a gunicorn
  reload, or a box reboot resumes it. Nothing is held in memory.
* **One open job per room.** Enqueuing twice annotates the existing row instead
  of queueing a second deletion — the partial unique index is the authority, and
  a duplicate insert is answered by reading the row that won.
* **Two workers cannot both delete one room.** A claim is a compare-and-swap on
  `claim_token`: a worker writes its own token only if the token it read is still
  there. The loser's update matches no rows and it moves on.
* **A crash does not strand a job and does not burn its budget.** A claim's lease
  is `CLAIM_LEASE`; a job claimed but never finished becomes claimable again
  after it, while `attempts` (which counts *failures*) is untouched — a worker
  that dies mid-call is a box problem, not a failed deletion.
* **A room that is already gone is a success.** LiveKit answers a deletion of a
  missing room with an error, and treating that as a failure would retry a room
  nobody can join for eight rounds and then call it abandoned. `_already_gone`
  is the one place that error is read.
* **Giving up is loud and recorded.** After `MAX_ATTEMPTS` the row becomes
  `abandoned` with the last error and an `ERROR` log; it is never silently
  dropped, and an operator can see it.
* **The debt is not a child of the row it outlives.** Migration 073 declared
  `session_id`/`school_id` `ON DELETE CASCADE`, so deleting a session, a school,
  or a row under a retention purge erased the only record that a room still
  needed deleting. Migration 074 releases the reference (`ON DELETE SET NULL`)
  and stamps `orphaned_at`; this worker reads `room_name`, never the foreign
  keys, so an orphaned job is still deletable.
* **Maintenance is not creation.** `run_once` never consults the two feature
  flags. They are the *creation* switch — the routes refuse a session, a token
  or a room without them — but a room that already exists keeps carrying pupil
  video after a teacher revokes ScanMeet, so disposal must not be gated on the
  switch that stops new rooms. The pass needs credentials and schema instead, and
  names which one is missing: a maintenance path that cannot run is `BLOCKED`,
  never `PASS`.
"""
from __future__ import annotations

import logging
import secrets
import threading
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

TABLE = "scan_live_cleanup_jobs"

#: Failures before a job needs a person. 30s→1m→2m→…→1h caps at eight tries,
#: which spans about two hours: long enough for a LiveKit outage to be over,
#: short enough that a permanently broken room is escalated the same morning.
MAX_ATTEMPTS = 8
BASE_SECONDS = 30
CAP_SECONDS = 3600

#: How long one worker owns a claim before another may take it. Longer than any
#: LiveKit call should take; shorter than a lesson.
CLAIM_LEASE = timedelta(minutes=5)

DEFAULT_INTERVAL_SECONDS = 60
RECONCILE_INTERVAL_SECONDS = 300

_thread = None
_stop = threading.Event()
_last_reconcile = None


# ── time, in one place, because rows come back as strings ────────────────────
def _now(now=None) -> datetime:
    """The clock, or the moment a caller supplies (a datetime or a stamp)."""
    moment = datetime.now(timezone.utc) if now is None else now
    if isinstance(moment, str):
        moment = _parse(moment) or datetime.now(timezone.utc)
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()


def _parse(value) -> datetime | None:
    """PostgREST hands timestamps back as strings; a malformed one is 'unknown'."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        moment = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def backoff_seconds(attempts: int) -> int:
    """The wait after failure number `attempts`: 30s, 1m, 2m, … capped at an hour."""
    step = max(1, int(attempts))
    return min(BASE_SECONDS * (2 ** (step - 1)), CAP_SECONDS)


# ── the queue ────────────────────────────────────────────────────────────────
def _open_job(db, session) -> dict | None:
    room = session.get("room_name")
    if not room or not session.get("id"):
        return None
    rows = (db.table(TABLE).select("*")
            .eq("session_id", session["id"]).eq("room_name", room)
            .eq("status", "pending").limit(1).execute().data or [])
    return rows[0] if rows else None


def enqueue(db, session, *, reason="session_ended", error=None, now=None) -> dict | None:
    """Record that `session`'s room still needs deleting. Idempotent per room.

    Returns the open job (the existing one when this was a second failure), or
    ``None`` when the session names no room — a caller cannot invent a deletion
    out of a row that has nothing to delete.
    """
    room = session.get("room_name")
    if not room or not session.get("id") or not session.get("school_id"):
        log.warning("ScanMeet cleanup not recorded: session without a room")
        return None
    moment = _now(now)
    existing = _open_job(db, session)
    if existing is not None:
        # A second failure for the same room. The schedule is deliberately left
        # alone: an alert loop that calls Stop repeatedly must not push the next
        # attempt out forever, and the row already carries the room.
        annotate = {"updated_at": _stamp(moment),
                    "last_error": (str(error) if error else existing.get("last_error"))}
        rows = (db.table(TABLE).update(annotate).eq("id", existing["id"])
                .eq("status", "pending").execute().data or [])
        return rows[0] if rows else existing
    row = {
        "school_id": session["school_id"], "session_id": session["id"],
        "room_name": room, "reason": reason, "status": "pending", "attempts": 0,
        "last_error": str(error) if error else None,
        "next_attempt_at": _stamp(moment), "claim_token": "",
        "created_at": _stamp(moment), "updated_at": _stamp(moment),
    }
    try:
        return db.table(TABLE).insert(row).execute().data[0]
    except Exception:
        # The partial unique index refused a second open job: another request
        # enqueued this room between the read above and this insert.
        again = _open_job(db, session)
        if again is None:
            raise
        return again


def claim_next(db, *, owner: str, now=None) -> dict | None:
    """Take ownership of one due job, or return ``None`` when there is none.

    Ordered by due time and bounded by a read limit, so a large ledger does not
    come back in one response. The lease test and the CAS are both here on
    purpose: the lease decides whether a claim is still alive, the CAS decides
    which of several workers gets the row.
    """
    moment = _now(now)
    rows = (db.table(TABLE).select("*").eq("status", "pending")
            .order("next_attempt_at").limit(25).execute().data or [])
    for job in rows:
        due = _parse(job.get("next_attempt_at"))
        if due is not None and due > moment:
            continue  # backed off; another row later in the list may still be due
        held = _parse(job.get("claimed_at"))
        if held is not None and moment - held < CLAIM_LEASE:
            continue  # a live claim: its worker is, as far as we know, deleting
        token = secrets.token_hex(8)
        won = (db.table(TABLE)
               .update({"claim_token": token, "claimed_by": owner,
                        "claimed_at": _stamp(moment), "updated_at": _stamp(moment)})
               .eq("id", job["id"]).eq("status", "pending")
               .eq("claim_token", job.get("claim_token") or "")
               .execute().data or [])
        if won:
            return won[0]
    return None


def _renew(db, job, moment) -> bool:
    """Extend our lease before a slow call, conditional on still owning the row.

    The compare-and-swap on `claim_token` is what makes a claim exclusive: a
    single `UPDATE ... WHERE id = ? AND status = 'pending' AND claim_token = ?`
    is atomic, so of two workers reading the same row exactly one write matches.
    `claimed_at` is *not* part of that condition and does not need to be — a lease
    is a courtesy to a crashed worker, while the token is the ownership claim.

    What the token alone cannot do is keep a claim alive through a LiveKit call
    longer than the lease. So the lease is re-asserted here, with the same
    condition; if it no longer matches, another worker owns the room and this one
    must not call the API or record an outcome for it.
    """
    rows = (db.table(TABLE)
            .update({"claimed_at": _stamp(moment), "updated_at": _stamp(moment)})
            .eq("id", job["id"]).eq("status", "pending")
            .eq("claim_token", job.get("claim_token") or "")
            .execute().data or [])
    return bool(rows)


def _already_gone(exc) -> bool:
    """LiveKit reports a deletion of a missing room as an error, not a success."""
    text = str(exc).lower()
    return any(word in text for word in (
        "not found", "not_found", "notfound", "no such room",
        "unknown room", "does not exist", "room_missing"))


def _close(db, job, moment, *, note) -> dict:
    rows = (db.table(TABLE)
            .update({"status": "done", "last_error": note,
                     "claim_token": "", "updated_at": _stamp(moment)})
            .eq("id", job["id"]).eq("status", "pending")
            .eq("claim_token", job.get("claim_token") or "")
            .execute().data or [])
    if not rows:
        # Someone else finished or abandoned it while this attempt was in flight.
        # The deletion succeeded either way; the row is not ours to report on.
        return {"state": "lost", "job": job}
    return {"state": "done", "job": rows[0]}


def _defer(db, job, moment, exc) -> dict:
    attempts = int(job.get("attempts") or 0) + 1
    abandoned = attempts >= MAX_ATTEMPTS
    payload = {
        "attempts": attempts,
        "last_error": str(exc)[:500],
        "status": "abandoned" if abandoned else "pending",
        "next_attempt_at": _stamp(moment + timedelta(seconds=backoff_seconds(attempts))),
        # The attempt is over, so ownership is released: the claim lease exists to
        # recover a worker that *died mid-call*, and holding it after a recorded
        # failure would delay every retry to five minutes whatever the backoff says.
        "claim_token": "", "claimed_by": None, "claimed_at": None,
        "updated_at": _stamp(moment),
    }
    if abandoned:
        # Not a retry any more: the room may still be live and a person has to
        # look. This is the line an operator greps for.
        log.error("ScanMeet room %s could not be deleted after %d attempts; "
                  "cleanup abandoned: %s", job.get("room_name"), attempts, exc)
    else:
        log.warning("ScanMeet room %s not deleted (attempt %d/%d), retrying in %ss: %s",
                    job.get("room_name"), attempts, MAX_ATTEMPTS,
                    backoff_seconds(attempts), exc)
    rows = (db.table(TABLE).update(payload)
            .eq("id", job["id"]).eq("status", "pending")
            .eq("claim_token", job.get("claim_token") or "")
            .execute().data or [])
    if not rows:
        return {"state": "lost", "job": job}
    return {"state": "abandoned" if abandoned else "retried", "job": rows[0]}


def attempt(config, db, job, *, end_room, now=None) -> dict:
    """One deletion for one claimed job, recorded either way."""
    moment = _now(now)
    if not _renew(db, job, moment):
        # The lease expired and somebody else claimed the room while this worker
        # was between the claim and the call. Not ours to delete or to report.
        return {"state": "lost", "job": job}
    try:
        end_room(job.get("room_name"))
    except Exception as exc:  # noqa: BLE001 - the debt must survive any failure
        if _already_gone(exc):
            # The room is gone; the goal is met, so this is not a retry.
            return _close(db, job, moment, note="room was already absent: %s" % exc)
        return _defer(db, job, moment, exc)
    return _close(db, job, moment, note=None)


def run_pending(config, db, *, end_room, owner=None, now=None, limit=10) -> dict:
    """Drain what is due, once. Bounded so one tick cannot become a loop.

    `now` is read once and reused: a job deferred by this pass is therefore not
    due again inside it, which is what stops a failing LiveKit from turning one
    tick into eight back-to-back attempts.
    """
    moment = _now(now)
    owner = owner or ("worker-" + secrets.token_hex(4))
    summary = {"attempted": 0, "done": 0, "retried": 0, "abandoned": 0, "lost": 0}
    for _ in range(max(0, int(limit))):
        job = claim_next(db, owner=owner, now=moment)
        if job is None:
            break
        summary["attempted"] += 1
        outcome = attempt(config, db, job, end_room=end_room, now=moment)
        key = {"done": "done", "retried": "retried",
               "abandoned": "abandoned"}.get(outcome["state"], "lost")
        summary[key] += 1
    return summary


# ── the pass, and the loop that runs it ──────────────────────────────────────
def schema_ready(db) -> bool:
    """Is migration 073/074 applied to this database?

    A worker on a box whose database is behind its code must say so, not retry a
    missing table every minute and not claim it drained a queue it never read.
    """
    try:
        db.table(TABLE).select("id").limit(1).execute()
    except Exception as exc:                                   # noqa: BLE001
        log.warning("ScanMeet maintenance cannot read %s: %s", TABLE, exc)
        return False
    return True


FEATURE_INDEPENDENCE = (
    "deletion and reconciliation run regardless of SCANGRADE_LIVE_ENABLED and "
    "SCANGRADE_LIVE_EXAM_ENABLED; those two flags gate creation, session and "
    "token issuance, which the routes refuse on their own")


def run_once(app=None, *, now=None) -> dict:
    """One maintenance pass: drain deletions, and reconcile rooms now and then.

    **The feature flags are deliberately not consulted.** Revoking ScanMeet stops
    new sessions, new tokens and new rooms — the routes hold that — but a room that
    already exists keeps publishing to everyone inside it, so disposing of it must
    not be gated on the switch that stops creating rooms. Gating it was the
    release-blocking deadlock the review found: turn the flags off after a failed
    deletion and the debt could never be paid. What the pass *does* require is the
    means to act: LiveKit credentials and the tables migration 073/074 creates. A
    missing one of those is reported as an explicit reason (a `BLOCKED`
    maintenance state), never as a pass.

    Reconciliation here is read-only with respect to LiveKit: it lists rooms,
    never creates or joins one, and it cannot move a session back to `active`.
    """
    from flask import current_app

    from app.services import live_policy, live_reconcile, livekit_gateway
    from app.utils.auth import get_supabase

    app = app or current_app
    config = app.config
    if not live_policy.client_ready(config):
        return {"ran": False, "blocked": "LiveKit credentials are not configured",
                "rules": FEATURE_INDEPENDENCE}

    db = get_supabase()
    if not schema_ready(db):
        return {"ran": False,
                "blocked": "migration 073/074 is not applied (scan_live_cleanup_jobs "
                           "is missing), so there is no ledger to drain",
                "rules": FEATURE_INDEPENDENCE}

    result = {"ran": True, "rules": FEATURE_INDEPENDENCE,
              "cleanup": run_pending(
                  config, db,
                  end_room=lambda room: livekit_gateway.end_room(config, room),
                  now=now)}

    # Expiry is a revocation event, not merely a new-token admission check.
    # Report missing migration 075 or unavailable DB as a blocked sweep rather
    # than allowing the maintenance pass to claim successful media cleanup.
    from app.services import live_expiry_cleanup
    result["expiry"] = live_expiry_cleanup.sweep_expired(
        db,
        end_room=lambda room: livekit_gateway.end_room(config, room),
        enqueue=enqueue,
        now=now,
    )

    # The queue can be healthy while expiry revocation is unmeasured or has
    # unresolved failures. Surface that as a top-level degraded state so an
    # operator cannot mistake ran=True for successful camera revocation.
    expiry = result["expiry"]
    if (not expiry.get("measured") or expiry.get("errors")
            or expiry.get("cleanup_pending") or expiry.get("cleanup_unrecorded")):
        result["degraded"] = True
        result["expiry_blocked"] = (
            "expiry entitlement scan was not measured"
            if not expiry.get("measured") else
            "expiry revocation has unresolved errors or room cleanup debt"
        )

    global _last_reconcile
    moment = _now(now)
    due = _last_reconcile is None or (
        moment - _last_reconcile).total_seconds() >= RECONCILE_INTERVAL_SECONDS
    if due:
        result["reconcile"] = live_reconcile.reconcile(
            config, db, room_names=livekit_gateway.room_names,
            enqueue=enqueue, now=moment)
        _last_reconcile = moment
    return result


def _loop(app, interval: int) -> None:
    # The first pass waits a full interval, like every other loop here:
    # constructing an app must not write, and nothing in this queue is more urgent
    # than a minute old.
    while not _stop.wait(interval):
        try:
            with app.app_context():
                run_once(app)
        except Exception as exc:                                  # noqa: BLE001
            log.error("ScanMeet cleanup loop error: %s", exc)


def start_cleanup_scheduler(interval: int | None = None, app=None) -> None:
    """Start the retry loop (safe to call more than once)."""
    global _thread
    if _thread and _thread.is_alive():
        return
    if app is None:
        from flask import current_app

        app = current_app._get_current_object()
    _stop.clear()
    _thread = threading.Thread(target=_loop,
                               args=(app, interval or DEFAULT_INTERVAL_SECONDS),
                               name="scanmeet-cleanup", daemon=True)
    _thread.start()
    log.info("ScanMeet cleanup scheduler started (interval=%ss)",
             interval or DEFAULT_INTERVAL_SECONDS)


def stop_cleanup_scheduler() -> None:
    global _thread
    _stop.set()
    _thread = None


__all__ = [
    "MAX_ATTEMPTS", "BASE_SECONDS", "CAP_SECONDS", "CLAIM_LEASE", "TABLE",
    "FEATURE_INDEPENDENCE", "backoff_seconds", "enqueue", "claim_next",
    "attempt", "run_pending", "schema_ready", "run_once",
    "start_cleanup_scheduler", "stop_cleanup_scheduler",
]
