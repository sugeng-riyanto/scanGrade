"""`active` in the database while the room is gone: the mismatch, and its repair.

A ScanMeet session row says "there is a room". LiveKit says otherwise when it has
restarted, emptied the room past its timeout, or accepted the deletion while the
response was lost. Every route trusts the row, so that divergence is invisible
until it is expensive:

* the teacher's Stop finds nothing to delete and reports success, so the session
  ends without ever having deleted media;
* a pupil who reloads is handed a join token for a room name nobody serves —
  and, because LiveKit creates a room on first join, their own rejoin would
  *recreate* the room the teacher thought was gone.

Three rules make the repair safe rather than a second way to be wrong:

1. **Only a room the sweep has seen may be declared missing.** `media_state`
   starts `unknown`; a session nobody has joined yet has no LiveKit room and that
   is normal, not stale. Only `live` → absent is evidence of a disappearance.
2. **A failed probe changes nothing.** If the room list cannot be read, that is a
   property of the box; parking every live session because LiveKit was briefly
   unreachable would end lessons. `reconcile` returns `measured: False` and has
   written no row.
3. **`stale` is not `ended`, and it is written conditionally.** `ended` is a
   person's decision and must never be overwritten by a sweep; both statuses
   refuse new tokens, and both free the one-active-session index, so a teacher can
   start a fresh room — a *new* room name — without the old row being revived.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)

TABLE = "scan_live_sessions"
STALE_REASON = "media room missing"
COLUMNS = "id,school_id,exam_id,room_name,status,media_state,created_at"


def _now(now=None) -> datetime:
    """The clock, or the moment a caller supplies (a datetime or a stamp)."""
    moment = datetime.now(timezone.utc) if now is None else now
    if isinstance(moment, str):
        try:
            moment = datetime.fromisoformat(moment.strip().replace("Z", "+00:00"))
        except ValueError:
            moment = datetime.now(timezone.utc)
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()


def _active_sessions(db) -> list[dict]:
    return (db.table(TABLE).select(COLUMNS).eq("status", "active")
            .execute().data or [])


def _record(db, session, moment, *, media_state) -> bool:
    """Note what this pass saw, without moving the session's status."""
    # No `updated_at` here: `scan_live_sessions` has none (migration 070), and a
    # sweep that invents a column fails on the first row it touches.
    payload = {"reconciled_at": _stamp(moment)}
    if media_state:
        payload["media_state"] = media_state
    rows = (db.table(TABLE).update(payload)
            .eq("id", session["id"]).eq("school_id", session["school_id"])
            .eq("status", "active").execute().data or [])
    return bool(rows)


def _park(db, session, moment) -> bool:
    """Park a session whose room we had seen and can no longer find."""
    rows = (db.table(TABLE).update({
        "status": "stale", "media_state": "missing",
        "stale_reason": STALE_REASON, "reconciled_at": _stamp(moment),
    }).eq("id", session["id"]).eq("school_id", session["school_id"])
        .eq("status", "active").eq("media_state", "live")
        .execute().data or [])
    if rows:
        log.warning("ScanMeet session %s is stale: its room %s no longer exists",
                    session["id"], session.get("room_name"))
    return bool(rows)


def reconcile(config, db, *, room_names, enqueue=None, now=None) -> dict:
    """One sweep. Never raises on a probe failure, and never guesses.

    `room_names(config)` is the LiveKit boundary, injected so this can be tested
    without a socket; `enqueue` is `live_cleanup.enqueue`, called for each newly
    parked session so the deletion debt is recorded (a room that is already gone
    closes that job on its first attempt).
    """
    moment = _now(now)
    try:
        present = set(room_names(config))
    except Exception as exc:  # noqa: BLE001 - a probe failure must not end lessons
        log.warning("ScanMeet reconcile could not read the room list: %s", exc)
        return {"measured": False, "reason": str(exc),
                "live": 0, "stale": 0, "waiting": 0}

    live = stale = waiting = 0
    for session in _active_sessions(db):
        if session.get("room_name") in present:
            _record(db, session, moment, media_state="live")
            live += 1
            continue
        if session.get("media_state") != "live":
            # Never seen, so this is a session nobody has joined yet — or one
            # whose room has not been created. Absence proves nothing.
            _record(db, session, moment, media_state=None)
            waiting += 1
            continue
        if _park(db, session, moment):
            stale += 1
            if enqueue is not None:
                try:
                    enqueue(db, session, reason="room_missing", now=moment)
                except Exception:  # noqa: BLE001 - the park already happened
                    log.warning("ScanMeet reconcile could not record cleanup for %s",
                                session["id"], exc_info=True)
    return {"measured": True, "live": live, "stale": stale, "waiting": waiting}


__all__ = ["STALE_REASON", "reconcile"]
