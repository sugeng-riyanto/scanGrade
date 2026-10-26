"""Bounded ScanMeet entitlement expiry sweep.

New admission checks cannot revoke already-connected LiveKit publishers.
This worker independently terminates previously active rooms for expired schools.
It is deliberately separate from the feature-creation switches.
"""
from __future__ import annotations

from datetime import datetime, timezone


def _confirmed_expired(db, school_id, now):
    """Only a successful, explicit expired entitlement read permits deletion.

    Admission is fail-closed on DB outages; destructive cleanup must not treat
    an unknown license state as proof of expiry.
    """
    rows = (db.table("scanmeet_school_entitlements")
            .select("enabled,status,valid_until")
            .eq("school_id", school_id).limit(1).execute().data or [])
    if len(rows) != 1:
        raise ValueError("entitlement unavailable or ambiguous")
    row = rows[0]
    if row.get("enabled") is not True or row.get("status") not in ("trial", "paid"):
        return False
    expiry = row.get("valid_until")
    if not isinstance(expiry, str):
        raise ValueError("entitlement expiry unavailable")
    deadline = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
    if deadline.tzinfo is None:
        raise ValueError("entitlement expiry timezone unavailable")
    return deadline <= now


def sweep_expired(db, *, end_room, enqueue, now=None, limit=25, max_schools=25):
    """Attempt expiry revocation; return explicit failures, never a false PASS.

    Expiry enforcement is eventually consistent, not instantaneous. The real
    media-disconnection bound still requires staging verification.
    """
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError("limit must be a positive integer")
    if not isinstance(max_schools, int) or isinstance(max_schools, bool) or max_schools < 1:
        raise ValueError("max_schools must be a positive integer")
    moment = now or datetime.now(timezone.utc)
    if isinstance(moment, str):
        moment = datetime.fromisoformat(moment.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        raise ValueError("Expiry sweep requires an aware clock")
    stamp = moment.astimezone(timezone.utc).isoformat()
    report = {"measured": False, "schools": 0, "ended": 0,
              "cleanup_pending": [], "cleanup_unrecorded": [], "errors": [],
              "has_more": False, "atomic_revocation_verified": False}
    # Iterate stable pages: a fixed first-page limit would starve later schools
    # forever, because expired licenses remain expired after their rooms end.
    try:
        expired = []
        offset = 0
        while len(expired) < max_schools + 1:
            page_size = min(limit, max_schools + 1 - len(expired))
            page = (db.table("scanmeet_school_entitlements")
                    .select("school_id,enabled,status,valid_until")
                    .eq("enabled", True)
                    .in_("status", ["trial", "paid"])
                    .lte("valid_until", stamp)
                    .order("school_id").range(offset, offset + page_size - 1)
                    .execute().data or [])
            expired.extend(page)
            if len(page) < page_size:
                break
            offset += len(page)
    except Exception as exc:
        report["errors"].append("entitlement query failed: " + type(exc).__name__)
        return report
    report["measured"] = True
    report["has_more"] = len(expired) > max_schools
    for school in expired[:max_schools]:
        school_id = school["school_id"]
        report["schools"] += 1
        try:
            # Recheck because a license can be renewed after the sweep query.
            latest = (db.table("scanmeet_school_entitlements")
                      .select("enabled,status,valid_until")
                      .eq("school_id", school_id).limit(1).execute().data or [])
            if not latest:
                report["errors"].append("entitlement disappeared: " + str(school_id))
                continue
            # Do not delete on an unknown DB state: only confirmed expiry.
            if not _confirmed_expired(db, school_id, moment):
                continue
            # Drain in batches without a fixed first-page ceiling. Ended rows
            # leave the active query; break if no progress to avoid a busy loop.
            renewed = False
            while True:
                sessions = (db.table("scan_live_sessions")
                            .select("id,room_name,school_id,status")
                            .eq("school_id", school_id).eq("status", "active")
                            .limit(100).execute().data or [])
                if not sessions:
                    break
                progressed = 0
                for session in sessions:
                    # Recheck renewal close to the deletion boundary. This is
                    # not an atomic guard; race E2E remains a release gate.
                    if not _confirmed_expired(db, school_id, moment):
                        renewed = True
                        break
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
                            debt = enqueue(db, ended[0], reason="school_license_expired", error=exc)
                            if debt is None:
                                report["cleanup_unrecorded"].append(session["id"])
                        except Exception:
                            report["cleanup_unrecorded"].append(session["id"])
                if renewed:
                    break
                if progressed == 0:
                    report["errors"].append("no progress ending sessions for " + str(school_id))
                    break
        except Exception as exc:
            report["errors"].append("school " + str(school_id) + ": " + type(exc).__name__)
    return report
