"""The teacher's per-examination ScanMeet switch, persisted on the paper.

Migration 070 made a Live room possible but recorded no decision about the
paper, so before this module "ScanMeet is on" meant "somebody happens to have an
active session". That is the wrong authority in both directions: a room
recreated after a LiveKit hiccup is not a teacher turning the feature on, and a
pupil whose teacher switched the feature off has to be refused **now**, not when
the room eventually disappears. `exams.scan_live_enabled` (migration 071) is the
decision; this module reads it and is the only writer.

Two rules shape it:

* **OFF is a revocation, not a formality.** Turning the switch off ends an
  active session in the database first — that is what every join token checks —
  and only then asks LiveKit to delete the room. A media failure is reported as
  ``media_cleanup: pending`` and never turns into a failed toggle: the exam
  continues, and the row already refuses new tokens.
* **ON never creates a session.** Starting the room stays the explicit
  ``POST /sessions`` action, so a toggle request cannot become a room, two
  simultaneous ON requests cannot produce two rooms, and a teacher who turns the
  feature on early is not publishing video to an empty room for an hour.
"""
from __future__ import annotations

#: The column migration 071 adds. Read through `enabled()` so the "absent column"
#: case (a box that has not applied the migration yet) is OFF rather than a crash.
ENABLED_COLUMN = "scan_live_enabled"


class Refused(Exception):
    """The switch could not be written, and nothing was changed."""


def enabled(exam) -> bool:
    """The paper's switch. A missing column, or anything but `true`, is OFF."""
    return bool(exam) and exam.get(ENABLED_COLUMN) is True


def _active(db, exam):
    rows = (db.table("scan_live_sessions")
            .select("id,room_name,created_by,status")
            .eq("school_id", exam["school_id"]).eq("exam_id", exam["id"])
            .eq("status", "active").limit(1).execute().data or [])
    return rows[0] if rows else None


def set_enabled(db, exam, *, value: bool, end_room) -> dict:
    """Persist the switch and reconcile any active room with it.

    `end_room` is the LiveKit boundary, injected so this can be exercised
    without a socket. Returns the state the caller may report:
    ``{"enabled", "ended_session", "media_cleanup"}``.
    """
    value = bool(value)
    try:
        updated = (db.table("exams").update({ENABLED_COLUMN: value})
                   .eq("id", exam["id"]).eq("school_id", exam["school_id"])
                   .execute().data or [])
    except Exception as exc:  # migration 071 not applied, or the row is gone
        raise Refused(str(exc)) from exc
    if not updated:
        raise Refused("exam is not in this school")

    result = {"enabled": value, "ended_session": None, "media_cleanup": None}
    active = _active(db, exam)
    if active is None or value:
        # ON leaves an existing room alone (the room's own creator supervises
        # it); OFF with no room is already the desired state.
        return result

    ended = (db.table("scan_live_sessions").update({"status": "ended"})
             .eq("id", active["id"]).eq("school_id", exam["school_id"])
             .eq("status", "active").execute().data or [])
    if not ended:
        # Another writer closed or recreated the room between the read and the
        # write. The switch is already stored; report the state we can see.
        raise Refused("session changed while ending")
    result["ended_session"] = ended[0].get("id")
    try:
        end_room(active.get("room_name"))
    except Exception:
        # The database row is the gate for new tokens, so this is clean-up debt,
        # not a failed revocation. Never fail the teacher's toggle over it.
        result["media_cleanup"] = "pending"
    return result
