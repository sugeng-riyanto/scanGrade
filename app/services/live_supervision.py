"""Private, default-OFF exam proctor assignments and durable media handover.

The database serializes management operations. A transfer rotates rooms only
once the cleanup worker has deleted the retired room. It never buys a new
session or grants permission to edit an exam.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

FLAG = "SCANGRADE_LIVE_SUPERVISION_ENABLED"


def enabled(config):
    return str(config.get(FLAG, os.environ.get(FLAG, "false"))).lower() in ("1", "true", "yes", "on")


def assigned(db, exam, user):
    """Fail closed when the additive migration is not installed."""
    try:
        rows = (db.table("scanmeet_exam_supervisors").select("valid_until,revoked_at")
                .eq("school_id", exam["school_id"]).eq("exam_id", exam["id"])
                .eq("supervisor_id", user).limit(1).execute().data or [])
        if not rows or rows[0].get("revoked_at"):
            return False
        until = datetime.fromisoformat(str(rows[0]["valid_until"]).replace("Z", "+00:00"))
        return until.tzinfo is not None and until > datetime.now(timezone.utc)
    except Exception:
        return False


def rpc(db, name, params):
    data = db.rpc(name, params).execute().data
    if isinstance(data, list) and len(data) == 1:
        data = data[0]
    if not isinstance(data, dict) or not data:
        raise RuntimeError("ScanMeet supervision operation returned no outcome")
    return data


def finish(db, job):
    """A failed callback leaves deletion debt pending, never a false success."""
    data = rpc(db, "scanmeet_finish_handover", {
        "p_handover": job["handover_id"], "p_claim": job["claim_token"],
    })
    if data.get("state") not in ("completed", "cancelled"):
        raise RuntimeError("ScanMeet handover not finalized")
    return data


def attempt_handover(config, db, handover_id, school_id, *, end_room):
    from app.services import live_cleanup

    rows = (db.table(live_cleanup.TABLE).select("id").eq("handover_id", handover_id)
            .eq("school_id", school_id).eq("status", "pending").limit(1).execute().data or [])
    if not rows:
        return "pending"
    job = live_cleanup.claim_next(db, owner="handover-request", job_id=rows[0]["id"])
    if not job:
        return "pending"
    result = live_cleanup.attempt(config, db, job, end_room=end_room)
    return "done" if result["state"] == "done" else "pending"
