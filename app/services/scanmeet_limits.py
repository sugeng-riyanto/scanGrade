"""Expire media sessions without touching exam submissions, answers or timers."""
from datetime import datetime, timezone
import logging

from app.services import live_cleanup, scanmeet_packages

log = logging.getLogger(__name__)


def expire_sessions(db, *, now=None):
    now = now or datetime.now(timezone.utc)
    result = {"measured": True, "ended": 0, "pending": 0}
    try:
        sessions = db.table("scan_live_sessions").select("*").eq("status", "active").execute().data or []
        ids = list({s["school_id"] for s in sessions})
        licenses = (db.table("scanmeet_school_entitlements").select("school_id,enabled,status,valid_until,archived_at").in_("school_id", ids).execute().data or []) if ids else []
        by_id = {row["school_id"]: row for row in licenses}
    except Exception:
        log.warning("ScanMeet expiry could not be measured", exc_info=True)
        return {"measured": False, "ended": 0, "pending": 0}
    for session in sessions:
        license_row = scanmeet_packages.describe(by_id.get(session["school_id"]), now=now)
        if scanmeet_packages.session_open(session, now=now) and license_row["effective_status"] in ("active", "trial"):
            continue
        try:
            # Durable deletion debt first. If storing it fails, leave the expired
            # row active so the next sweep retries; its deadline already denies
            # every token. Never strand an ended room without a deletion job.
            live_cleanup.enqueue(db, session, reason="scanmeet_time_limit", error="session or license expired")
            ended = db.table("scan_live_sessions").update({"status": "ended"}).eq("id", session["id"]).eq("school_id", session["school_id"]).eq("status", "active").execute().data or []
            if not ended:
                continue
            result["ended"] += 1
        except Exception:
            result["pending"] += 1
            log.exception("ScanMeet expiry requires cleanup retry")
    return result
