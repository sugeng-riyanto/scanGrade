"""Bounded ScanMeet entitlement revocation sweep.

New admission checks cannot revoke already-connected LiveKit publishers.
This worker independently terminates previously active rooms for schools that may
no longer use media. It is deliberately separate from the feature-creation
switches.

**A licence stops being permission in two ways, and until this change only one of
them was swept.** Expiry was handled. A licence *switched off* in the super-admin
form was not: that form stores `enabled = false` and forces
`status = 'disabled'`, `valid_until = NULL`, while the candidate query asked only
for `enabled = true` rows past their deadline and the recheck answered "not
expired" for everything else. So a disabled school's room was never selected and,
had it been, would have been skipped — the teacher kept supervising a room that
kept publishing pupil video after the add-on was switched off. That is the first
half of the P0 the release gate asks about ("switch the licence off"), and the
half an expiring-at-midnight licence would never have caught.

**What still does not delete a room.** A read that *fails*, two licence rows for
one school, and an expiry that cannot be parsed are *errors*, never a revocation.
Admission is fail-closed; destructive cleanup must not treat an unknown licence
state as proof that it lapsed. A school with **no row at all** is the different
case — the read succeeded and found nothing, which is the answer admission has
always given — so it is a revocation, and the candidate set is built from active
sessions as well as licence rows precisely so that school can be reached.

Only a successful read may end a room, and it is repeated next to every deletion
so a renewal (or a licence switched back on) wins the race.
"""
from __future__ import annotations

from datetime import datetime, timezone

EXPIRED = "expired"
REVOKED = "revoked"

#: The reason recorded in the cleanup ledger, so a retried deletion says which
#: decision asked for it long after the licence row has changed again.
DEBT_REASON = {EXPIRED: "school_license_expired", REVOKED: "school_license_disabled"}


def _licence_verdict(db, school_id, now):
    """`expired` / `revoked` when a *successful* read says so, else `None`.

    Raises `ValueError` when the licence cannot be read well enough to authorise
    a deletion; the caller records that and deletes nothing. `None` covers both
    "still licensed" and "renewed a moment ago", because both mean keep the room.

    A row that exists and says `enabled = false` (or carries a status that is not
    `trial`/`paid`) is *revoked*: the row is present and it says no, which is a
    decision, unlike a query that failed.
    """
    # Two rows, not one: `school_id` is the primary key today, and asking for a
    # second row is what makes a duplicate visible instead of silently picking
    # whichever row the database returned first.
    rows = (db.table("scanmeet_school_entitlements")
            .select("enabled,status,valid_until")
            .eq("school_id", school_id).limit(2).execute().data or [])
    if not rows:
        # The read succeeded and found nothing. Admission has always treated a
        # missing licence as a no, and a room still running under a licence that
        # was deleted is exactly the case a row-only sweep can never reach.
        return REVOKED
    if len(rows) > 1:
        # Two rows for one school is a schema problem, not a decision.
        raise ValueError("entitlement ambiguous")
    row = rows[0]
    if row.get("enabled") is not True or row.get("status") not in ("trial", "paid"):
        return REVOKED
    expiry = row.get("valid_until")
    if not isinstance(expiry, str):
        raise ValueError("entitlement expiry unavailable")
    deadline = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
    if deadline.tzinfo is None:
        raise ValueError("entitlement expiry timezone unavailable")
    return EXPIRED if deadline <= now else None


def _candidates(db, limit):
    """Every entitlement row, paged. The sweep decides, not the filter.

    Asking the query to identify the lapsed rows is what hid a switched-off
    licence: one filter can say "enabled and past its deadline" but not "disabled,
    or past its deadline, or unpaid". Paging the rows and judging each with
    `_licence_verdict()` also guarantees discovery and the recheck before a
    deletion use the same rule, so they cannot disagree.

    Iterated in stable pages rather than one `limit`: a licence stays lapsed after
    its rooms end, so a fixed first page would starve every later school forever.

    **The licence table is not the only key set.** A school whose licence row was
    deleted outright has no row to enumerate it by, so every active session's
    school is added too — a room can always be reached by the school that owns it,
    and `_licence_verdict()` decides from there.
    """
    rows, offset = [], 0
    while True:
        page = (db.table("scanmeet_school_entitlements")
                .select("school_id,enabled,status,valid_until")
                .order("school_id").range(offset, offset + limit - 1)
                .execute().data or [])
        rows.extend(page)
        if len(page) < limit:
            break
        offset += len(page)
    listed = {row.get("school_id") for row in rows}
    running = (db.table("scan_live_sessions").select("school_id")
               .eq("status", "active").execute().data or [])
    for row in running:
        school = row.get("school_id")
        if school and school not in listed:
            listed.add(school)
            rows.append({"school_id": school})
    return rows


def _end_school_rooms(db, school_id, moment, *, reason, end_room, enqueue, report):
    """End every active room of one school whose licence is still gone."""
    while True:
        sessions = (db.table("scan_live_sessions")
                    .select("id,room_name,school_id,status")
                    .eq("school_id", school_id).eq("status", "active")
                    .limit(100).execute().data or [])
        if not sessions:
            return
        progressed = 0
        for session in sessions:
            # Recheck renewal close to the deletion boundary, before each room:
            # not an atomic guard, which is why the race is a staging E2E gate.
            if _licence_verdict(db, school_id, moment) is None:
                return
            ended = (db.table("scan_live_sessions")
                     .update({"status": "ended"})
                     .eq("id", session["id"]).eq("school_id", school_id)
                     .eq("status", "active").execute().data or [])
            if not ended:
                continue
            progressed += 1
            report["ended"] += 1
            try:
                end_room(session["room_name"])
            except Exception as exc:
                report["cleanup_pending"].append(session["id"])
                try:
                    debt = enqueue(db, ended[0], reason=reason, error=exc)
                    if debt is None:
                        report["cleanup_unrecorded"].append(session["id"])
                except Exception:
                    report["cleanup_unrecorded"].append(session["id"])
        if progressed == 0:
            report["errors"].append("no progress ending sessions for " + str(school_id))
            return


def sweep_expired(db, *, end_room, enqueue, now=None, limit=25):
    """Attempt revocation for every lapsed licence; report failures explicitly.

    Enforcement is eventually consistent, not instantaneous. The real
    media-disconnection bound still requires staging verification (two browsers,
    a real LiveKit project), which is why this reports `measured` and errors
    rather than claiming a PASS.

    `report` carries `expired_schools` and `revoked_schools` separately: an
    operator reading one number should not have to guess which decision ended a
    camera.
    """
    moment = now or datetime.now(timezone.utc)
    if isinstance(moment, str):
        moment = datetime.fromisoformat(moment.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        raise ValueError("Expiry sweep requires an aware clock")
    report = {"measured": False, "schools": 0, "ended": 0,
              "expired_schools": 0, "revoked_schools": 0,
              "cleanup_pending": [], "cleanup_unrecorded": [], "errors": []}

    try:
        candidates = _candidates(db, limit)
    except Exception as exc:
        report["errors"].append("entitlement query failed: " + type(exc).__name__)
        return report
    report["measured"] = True

    for school in candidates:
        school_id = school.get("school_id")
        try:
            # Recheck because a licence can be renewed (or switched back on)
            # after the sweep listed it.
            verdict = _licence_verdict(db, school_id, moment)
        except Exception as exc:
            report["errors"].append("school " + str(school_id) + ": " + str(exc)
                                    if isinstance(exc, ValueError)
                                    else "school " + str(school_id) + ": "
                                    + type(exc).__name__)
            continue
        if verdict is None:
            continue
        report["schools"] += 1
        report[("expired_schools" if verdict == EXPIRED else "revoked_schools")] += 1
        try:
            _end_school_rooms(db, school_id, moment, reason=DEBT_REASON[verdict],
                              end_room=end_room, enqueue=enqueue, report=report)
        except Exception as exc:
            report["errors"].append("school " + str(school_id) + ": "
                                    + type(exc).__name__)
    return report


__all__ = ["EXPIRED", "REVOKED", "DEBT_REASON", "sweep_expired"]
