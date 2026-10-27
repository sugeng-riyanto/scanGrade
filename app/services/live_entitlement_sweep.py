"""An add-on that lapses must stop the camera that is already running.

Every other gate in this feature answers a *request*: `POST /sessions` refuses a
school without the add-on, a switch turned off refuses the next token, and
`live_school_entitlement.active()` is asked on the way in. None of them runs
again. So a licence switched off — or allowed to expire at midnight — while a
teacher is supervising a paper changed the answer for the next person who asked
and for nobody already inside the room: the pupil's camera kept publishing, the
teacher kept receiving it, and only a LiveKit token's own TTL was ever going to
interrupt it. That is the exact gap this module closes, and it was found by
trying to *measure* revocation on staging rather than by reading the routes.

Four rules, each of which is a way the obvious implementation would be wrong:

* **Only a definitive `INACTIVE` revokes.** `live_school_entitlement.state()`
  separates "this school may not" from "this read failed". A licence is a
  decision; a transport error on the box is not, and a sweep that treated the two
  alike would end every running examination during a Supabase hiccup. `UNKNOWN`
  parks nothing and is reported by school id so a person can see the difference.
* **A revocation is a compare-and-swap, not a write.** Parking is conditional on
  `status = 'active'`, so a second sweep, a teacher's Stop that lands between the
  read and the write, and a session already `stale` from the reconcile sweep
  cannot be re-ended — and a room is deleted exactly once.
* **A failure to delete is debt, not a log line.** The row is parked first (that
  is what refuses new tokens), then LiveKit is asked to delete. If that call
  fails, the room is still carrying pupil video, so the deletion goes into
  `live_cleanup`'s ledger with its own reason and the retry worker finishes it.
  Nothing here retries in-request.
* **The creation flags are not consulted.** `SCANGRADE_LIVE_ENABLED` and
  `SCANGRADE_LIVE_EXAM_ENABLED` gate *creation*; a room that exists outlives the
  switch, so disposal must not be conditioned on the switch — the same rule the
  cleanup ledger already documents as `FEATURE_INDEPENDENCE`.

`stale` rather than `ended`: migration 073 draws that line explicitly — `ended`
is a person's decision (a teacher pressed Stop), `stale` is the system saying the
media it promised may not continue. Both refuse new join tokens and both free the
one-active-session index, so a teacher whose school lapsed can see the room is
over and start a fresh one if the add-on comes back. No migration is needed for
this: `stale_reason` already exists to say why the system parked a session.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.services import live_school_entitlement as entitlement

log = logging.getLogger(__name__)

TABLE = "scan_live_sessions"

#: Why the session was parked. Read by an operator answering "why did this
#: lesson stop at 09:14" long after the licence row has changed again.
REASON = "school add-on unavailable"

#: The debt's own reason, distinct from a Stop or a switch-off, so the retry
#: ledger can be read without guessing which of the three asked for the deletion.
CLEANUP_REASON = "entitlement_revoked"

COLUMNS = "id,school_id,exam_id,room_name,status,media_state"

FEATURE_INDEPENDENCE = (
    "revocation runs regardless of SCANGRADE_LIVE_ENABLED and "
    "SCANGRADE_LIVE_EXAM_ENABLED; those flags gate creation, while a room that "
    "already exists keeps publishing")


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


def _park(db, session, moment, *, media_state=None) -> bool:
    """Move the session out of `active`, if it is still there.

    Every condition is in the WHERE, not in a prior read: `status = 'active'` is
    the compare half, and `school_id` keeps the write inside the row's own school
    even if an id were ever reused across tenants.
    """
    payload = {"status": "stale", "stale_reason": REASON,
               "reconciled_at": _stamp(moment)}
    if media_state:
        payload["media_state"] = media_state
    rows = (db.table(TABLE).update(payload)
            .eq("id", session["id"]).eq("school_id", session["school_id"])
            .eq("status", "active").execute().data or [])
    return bool(rows)


def revoke_lapsed(db, *, end_room, entitlement_state=None, enqueue=None,
                  now=None) -> dict:
    """One pass: end every active room whose school is definitively not entitled.

    `entitlement_state(db, school_id, now=...)` is the licence boundary and
    `end_room(room_name)` the LiveKit one, both injected so this runs without a
    database or a socket. `enqueue(db, session, reason=..., error=..., now=...)`
    is `live_cleanup.enqueue`: a deletion that failed becomes durable debt.

    Counters: `revoked` (sessions parked), `deleted` (rooms LiveKit confirmed
    gone), `deferred` (parked, deletion recorded for retry), `kept` (session of
    an entitled school), `unread` (sessions left alone because the licence could
    not be read), and `unknown_schools` (the ids, so a person can look).
    """
    read = entitlement_state or entitlement.state
    moment = _now(now)
    sessions = _active_sessions(db)
    verdicts: dict[str, str] = {}
    unknown: list[str] = []
    summary = {"measured": True, "schools": 0, "sessions": len(sessions),
               "revoked": 0, "deleted": 0, "deferred": 0, "kept": 0,
               "unread": 0, "unknown_schools": unknown,
               "rules": FEATURE_INDEPENDENCE}
    if not sessions:
        return summary

    for session in sessions:
        school = str(session.get("school_id") or "")
        # One read per school per pass, not one per session: a school with
        # thirty running rooms is thirty rows and one licence.
        if school not in verdicts:
            try:
                verdicts[school] = read(db, school, now=moment)
            except Exception:  # noqa: BLE001 - a boundary that raised said nothing
                verdicts[school] = entitlement.UNKNOWN
            summary["schools"] = len(verdicts)
        verdict = verdicts[school]

        if verdict == entitlement.ACTIVE:
            summary["kept"] += 1
            continue
        if verdict != entitlement.INACTIVE:
            # Unreadable is not a decision. Leaving the lesson running is the
            # cost of not guessing; the caller's report names the school.
            summary["unread"] += 1
            if school not in unknown:
                unknown.append(school)
            continue

        if not session.get("room_name") or not session.get("id"):
            # A session with no room has no media to revoke; leave it to the
            # ledger's schema checks instead of inventing a target for LiveKit.
            summary["unread"] += 1
            continue

        if not _park(db, session, moment):
            # Somebody else closed or re-parked it between the read and the
            # write: not ours, and deleting a room another writer just handled
            # would be the double-deletion the CAS exists to prevent.
            continue
        summary["revoked"] += 1
        try:
            end_room(session["room_name"])
        except Exception as exc:  # noqa: BLE001 - the debt must outlive this pass
            summary["deferred"] += 1
            log.warning("ScanMeet room %s kept running after the school's add-on "
                        "lapsed; recording cleanup debt: %s",
                        session["room_name"], exc)
            if enqueue is not None:
                try:
                    enqueue(db, session, reason=CLEANUP_REASON, error=exc,
                            now=moment)
                except Exception:  # noqa: BLE001 - the revocation already happened
                    log.error("ScanMeet could not record cleanup for %s after a "
                              "lapsed add-on", session["id"], exc_info=True)
            continue
        summary["deleted"] += 1
        # `media_state = 'missing'` is a fact now, not a guess: LiveKit confirmed
        # the deletion. It also keeps the reconcile sweep from ever treating this
        # room as one it has seen.
        try:
            (db.table(TABLE).update({"media_state": "missing"})
             .eq("id", session["id"]).eq("school_id", session["school_id"])
             .execute())
        except Exception:  # noqa: BLE001 - the room is gone; the note is cosmetic
            log.warning("ScanMeet could not note the deleted room for %s",
                        session["id"], exc_info=True)

    if unknown:
        log.warning("ScanMeet could not read the add-on state for %d school(s); "
                    "nothing was revoked for them: %s",
                    len(unknown), ", ".join(unknown[:10]))
    return summary


__all__ = ["TABLE", "REASON", "CLEANUP_REASON", "FEATURE_INDEPENDENCE",
           "revoke_lapsed"]
