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
4. **One empty room list is a candidate, not a verdict.** A room list is a
   snapshot: LiveKit can answer successfully with a short list during a restart or
   a shard move, and parking a lesson over that would end a teacher's session for a
   property of the probe. A first absence records `missing_since` and changes
   nothing else; only an absence that has persisted for `MISSING_GRACE`, observed
   again on a later pass, parks the session. A room that reappears clears the
   candidate, so a transient answer costs nothing.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

TABLE = "scan_live_sessions"
STALE_REASON = "media room missing"
COLUMNS = "id,school_id,exam_id,room_name,status,media_state,missing_since,created_at"

#: How long a room must stay absent before its session is parked. Long enough to
#: outlast a LiveKit restart or a room list taken mid-move, short enough that a
#: teacher who stops and reloads is not shown a room that no longer exists.
MISSING_GRACE = timedelta(seconds=60)


def _now(now=None) -> datetime:
    """The clock, or the moment a caller supplies (a datetime or a stamp)."""
    moment = datetime.now(timezone.utc) if now is None else now
    if isinstance(moment, str):
        moment = _parse(moment) or datetime.now(timezone.utc)
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _parse(value) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        moment = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
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
    #
    # Seeing the room again clears the candidate: the absence was transient, so
    # there is nothing left to count.
    payload = {"reconciled_at": _stamp(moment), "missing_since": None}
    if media_state:
        payload["media_state"] = media_state
    rows = (db.table(TABLE).update(payload)
            .eq("id", session["id"]).eq("school_id", session["school_id"])
            .eq("status", "active").execute().data or [])
    return bool(rows)


def _mark_missing(db, session, moment) -> bool:
    """First absence: record *when*, and change nothing else.

    Conditional on `media_state = 'live'` and on `missing_since` still being NULL,
    so a second pass cannot restart the clock and a session that was never seen
    cannot enter the state at all.
    """
    rows = (db.table(TABLE).update({
        "missing_since": _stamp(moment), "reconciled_at": _stamp(moment),
    }).eq("id", session["id"]).eq("school_id", session["school_id"])
        .eq("status", "active").eq("media_state", "live")
        .is_("missing_since", "null").execute().data or [])
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


def require_grace(moment, since) -> bool:
    """Has an absence lasted long enough to be treated as real?"""
    first = _parse(since)
    return first is not None and moment - first >= MISSING_GRACE


def reconcile(config, db, *, room_names, enqueue=None, now=None) -> dict:
    """One sweep. Never raises on a probe failure, and never guesses.

    `room_names(config)` is the LiveKit boundary, injected so this can be tested
    without a socket; `enqueue` is `live_cleanup.enqueue`, called for each newly
    parked session so the deletion debt is recorded (a room that is already gone
    closes that job on its first attempt).

    Counters: `live` (seen now), `missing` (absent, candidate recorded, not yet
    parked), `stale` (parked this pass), `waiting` (never seen — nobody has joined
    yet, so absence means nothing).
    """
    moment = _now(now)
    try:
        present = set(room_names(config))
    except Exception as exc:  # noqa: BLE001 - a probe failure must not end lessons
        log.warning("ScanMeet reconcile could not read the room list: %s", exc)
        return {"measured": False, "reason": str(exc),
                "live": 0, "missing": 0, "stale": 0, "waiting": 0}

    live = missing = stale = waiting = 0
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
        since = session.get("missing_since")
        if since is None:
            # First absence. Start the clock; park nothing.
            if _mark_missing(db, session, moment):
                missing += 1
            else:
                waiting += 1
            continue
        if not require_grace(moment, since):
            missing += 1
            continue
        if _park(db, session, moment):
            stale += 1
            if enqueue is not None:
                try:
                    enqueue(db, session, reason="room_missing", now=moment)
                except Exception:  # noqa: BLE001 - the park already happened
                    log.warning("ScanMeet reconcile could not record cleanup for %s",
                                session["id"], exc_info=True)
    return {"measured": True, "live": live, "missing": missing,
            "stale": stale, "waiting": waiting}


__all__ = ["STALE_REASON", "MISSING_GRACE", "require_grace", "reconcile"]
