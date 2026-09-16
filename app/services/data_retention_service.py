import logging
from datetime import datetime, timedelta, timezone

from app.utils.auth import get_supabase

logger = logging.getLogger("app")

# Retention periods in days
RETENTION = {
    "submissions": 365 * 5,       # 5 tahun — academic records
    "notifications": 365 * 2,     # 2 tahun — communications
    "violations": 365 * 2,        # 2 tahun — audit trail
    "drafts": 30,                 # 30 hari — unfinished exams
    "temp_files": 3600,           # 1 jam — temp uploads (seconds)
    "inactive_accounts": 365 * 3, # 3 tahun — then anonymize
    "grace_period": 90,           # 90 hari — before hard-delete soft-deleted
    "deleted_accounts": 365,      # 1 tahun — keep anonymized record
}

# ── Soft-delete API ──

def soft_delete_submissions(before: datetime = None):
    """Soft-delete old submissions beyond retention period."""
    supabase = get_supabase()
    if before is None:
        before = datetime.now(timezone.utc) - timedelta(days=RETENTION["submissions"])
    cutoff = before.isoformat()
    try:
        res = supabase.table("submissions").update({
            "deleted_at": datetime.now(timezone.utc).isoformat(),
            "deletion_reason": "retention_expired"
        }).lt("created_at", cutoff).is_("deleted_at", "null").execute()
        count = len(res.data or [])
        if count:
            logger.info("Retention: soft-deleted %d old submissions", count)
        return count
    except Exception as e:
        logger.error("Retention error (submissions): %s", e)
        return 0


def soft_delete_notifications(before: datetime = None):
    """Soft-delete old notifications beyond retention period."""
    supabase = get_supabase()
    if before is None:
        before = datetime.now(timezone.utc) - timedelta(days=RETENTION["notifications"])
    cutoff = before.isoformat()
    try:
        res = supabase.table("notifications").update({
            "deleted_at": datetime.now(timezone.utc).isoformat()
        }).lt("created_at", cutoff).is_("deleted_at", "null").execute()
        count = len(res.data or [])
        if count:
            logger.info("Retention: soft-deleted %d old notifications", count)
        return count
    except Exception as e:
        logger.error("Retention error (notifications): %s", e)
        return 0


def soft_delete_violations(before: datetime = None):
    """Soft-delete old violation logs beyond retention period."""
    supabase = get_supabase()
    if before is None:
        before = datetime.now(timezone.utc) - timedelta(days=RETENTION["violations"])
    cutoff = before.isoformat()
    try:
        res = supabase.table("violation_logs").update({
            "deleted_at": datetime.now(timezone.utc).isoformat()
        }).lt("created_at", cutoff).is_("deleted_at", "null").execute()
        count = len(res.data or [])
        if count:
            logger.info("Retention: soft-deleted %d old violations", count)
        return count
    except Exception as e:
        logger.error("Retention error (violations): %s", e)
        return 0


def delete_draft_submissions(before: datetime = None):
    """Hard-delete draft submissions older than retention period."""
    supabase = get_supabase()
    if before is None:
        before = datetime.now(timezone.utc) - timedelta(days=RETENTION["drafts"])
    cutoff = before.isoformat()
    try:
        res = supabase.table("submissions").delete().eq("status", "draft").lt("created_at", cutoff).execute()
        count = len(res.data or [])
        if count:
            logger.info("Retention: hard-deleted %d old draft submissions", count)
        return count
    except Exception as e:
        logger.error("Retention error (drafts): %s", e)
        return 0


def _last_activity_at(profile, supabase):
    """Best-effort timestamp of the most recent activity for a profile.

    Falls back to the profile's own timestamps so an account that simply has no
    submissions (every teacher, admin, or brand-new student) is NOT treated as
    inactive — that mistake wiped real names once already.
    """
    candidates = [profile.get("updated_at"), profile.get("created_at")]
    try:
        sub = (
            supabase.table("submissions")
            .select("created_at")
            .eq("student_id", profile["id"])
            .order("created_at", desc=True)
            .limit(1)
            .execute()
            .data
        )
        if sub:
            candidates.append(sub[0].get("created_at"))
    except Exception:
        pass
    stamps = [str(c) for c in candidates if c]
    return max(stamps) if stamps else None


def anonymize_inactive_profiles(before: datetime = None):
    """Anonymize student accounts inactive beyond the retention period.

    Only ``murid`` rows are eligible, and only when their most recent activity
    is older than the cutoff. Staff accounts and freshly created accounts are
    never touched.
    """
    supabase = get_supabase()
    if before is None:
        before = datetime.now(timezone.utc) - timedelta(days=RETENTION["inactive_accounts"])
    cutoff = before.isoformat()
    try:
        profiles = (
            supabase.table("profiles")
            .select("id,role,created_at,updated_at")
            .eq("role", "murid")
            .is_("deleted_at", "null")
            .is_("anonymized_at", "null")
            .execute()
            .data
        ) or []
        anonymized = 0
        for p in profiles:
            try:
                last_active = _last_activity_at(p, supabase)
                if last_active and last_active > cutoff:
                    continue
                if not last_active:
                    # No usable signal — skip rather than guess.
                    continue
                supabase.table("profiles").update({
                    "full_name": "Akun Dinonaktifkan",
                    "phone": None,
                    "anonymized_at": datetime.now(timezone.utc).isoformat()
                }).eq("id", p["id"]).execute()
                anonymized += 1
            except Exception as e:
                logger.warning("Retention: anonymize skipped for %s: %s", p.get("id"), e)
        if anonymized:
            logger.info("Retention: anonymized %d inactive student profiles", anonymized)
        return anonymized
    except Exception as e:
        logger.error("Retention error (anonymize): %s", e)
        return 0


def hard_delete_expired(before: datetime = None):
    """Hard-delete soft-deleted records past grace period."""
    supabase = get_supabase()
    if before is None:
        before = datetime.now(timezone.utc) - timedelta(days=RETENTION["grace_period"])
    cutoff = before.isoformat()
    total = 0
    for table in ("submissions", "notifications", "violation_logs"):
        try:
            res = supabase.table(table).delete().lt("deleted_at", cutoff).execute()
            total += len(res.data or [])
        except Exception as e:
            logger.error("Retention error (hard-delete %s): %s", table, e)
    try:
        res = supabase.table("profiles").delete().lt("deleted_at", cutoff).execute()
        total += len(res.data or [])
    except Exception as e:
        logger.error("Retention error (hard-delete profiles): %s", e)
    if total:
        logger.info("Retention: hard-deleted %d expired records", total)
    return total


def purge_all():
    """Run all retention purge tasks."""
    return {
        "submissions": soft_delete_submissions(),
        "notifications": soft_delete_notifications(),
        "violations": soft_delete_violations(),
        "drafts": delete_draft_submissions(),
        "anonymized": anonymize_inactive_profiles(),
        "hard_deleted": hard_delete_expired(),
    }


# ── Deletion Request ──

def request_deletion(user_id: str, reason: str = ""):
    """Submit a data deletion request."""
    supabase = get_supabase()
    try:
        # Check if there's already a pending request
        existing = supabase.table("deletion_requests").select("id").eq("user_id", user_id).eq("status", "pending").execute().data
        if existing:
            return {"error": "Sudah ada permintaan penghapusan yang menunggu"}, 409
        res = supabase.table("deletion_requests").insert({
            "user_id": user_id, "reason": reason, "status": "pending"
        }).execute()
        return {"success": True, "id": res.data[0]["id"]}, 200
    except Exception as e:
        logger.error("Deletion request error: %s", e)
        return {"error": f"Gagal: {str(e)[:100]}"}, 500


def cancel_deletion_request(user_id: str):
    """Cancel a pending deletion request."""
    supabase = get_supabase()
    try:
        supabase.table("deletion_requests").update({"status": "cancelled"}).eq("user_id", user_id).eq("status", "pending").execute()
        return {"success": True}, 200
    except Exception as e:
        return {"error": str(e)[:100]}, 500


def process_deletion_request(request_id: int, admin_id: str, action: str, notes: str = ""):
    """Approve or reject a deletion request (admin only)."""
    supabase = get_supabase()
    try:
        req = supabase.table("deletion_requests").select("user_id, status").eq("id", request_id).single().execute().data
        if not req or req["status"] != "pending":
            return {"error": "Request not found or already processed"}, 404
        now = datetime.now(timezone.utc).isoformat()
        if action == "approve":
            # Soft-delete the profile
            supabase.table("profiles").update({
                "deleted_at": now,
                "status": "suspended",
                "anonymized_at": now,
                "full_name": "Akun Dihapus",
                "phone": None,
            }).eq("id", req["user_id"]).execute()
            # Soft-delete user's submissions
            supabase.table("submissions").update({"deleted_at": now, "deletion_reason": "user_request"}).eq("student_id", req["user_id"]).is_("deleted_at", "null").execute()
        supabase.table("deletion_requests").update({
            "status": "approved" if action == "approve" else "rejected",
            "processed_at": now,
            "processed_by": admin_id,
            "notes": notes,
        }).eq("id", request_id).execute()
        return {"success": True}, 200
    except Exception as e:
        return {"error": str(e)[:100]}, 500


# ── Data Export ──

def export_user_data(user_id: str):
    """Collect all user data for export (profile, submissions, messages, violations)."""
    supabase = get_supabase()
    result = {"exported_at": datetime.now(timezone.utc).isoformat()}
    try:
        profile = supabase.table("profiles").select("*").eq("id", user_id).single().execute().data
        result["profile"] = profile or {}
    except Exception:
        result["profile"] = {}
    try:
        submissions = supabase.table("submissions").select("id, exam_id, status, score, final_score, penalty, violations, created_at").eq("student_id", user_id).order("created_at", desc=True).execute().data or []
        result["submissions"] = submissions
    except Exception:
        result["submissions"] = []
    try:
        # `notification_recipients` has no `created_at` — it is a link row with
        # `read_at` — so ordering *it* by that name is a 42703 and this whole
        # export silently came back empty. The time a message was sent is on the
        # notification; the list is sorted by it here rather than asking
        # PostgREST to order an embedded table.
        messages = supabase.table("notification_recipients") \
            .select("read_at, notifications!inner(id, title, message, sender_role, created_at)") \
            .eq("recipient_id", user_id).execute().data or []
        rows = [r.get("notifications") or {} for r in messages]
        for row, link in zip(rows, messages):
            row["read_at"] = link.get("read_at")
        result["messages_received"] = sorted(
            rows, key=lambda r: str(r.get("created_at") or ""), reverse=True)
    except Exception:
        result["messages_received"] = []
    try:
        sent = supabase.table("notifications").select("id, title, message, sender_role, created_at").eq("sender_id", user_id).order("created_at", desc=True).execute().data or []
        result["messages_sent"] = sent
    except Exception:
        result["messages_sent"] = []
    try:
        # A violation log holds `user_id`, not `student_id`, and no `penalty`:
        # the marks a violation cost are computed at submit time and recorded on
        # the submission, so there is nothing per row to export. Both wrong names
        # failed the request, which is why an export contained no violations at
        # all. `metadata` carries the detail that was recorded with the event.
        violations = supabase.table("violation_logs") \
            .select("id, exam_id, violation_type, metadata, created_at") \
            .eq("user_id", user_id).order("created_at", desc=True).execute().data or []
        result["violations"] = violations
    except Exception:
        result["violations"] = []
    return result


# ── Scheduler ──

import threading
import time

_retention_thread = None
_retention_interval = 86400  # 24 jam
_running = False


def _run_retention_loop(app=None):
    """Purge expired data on a timer.

    purge_all() reads the Supabase client from current_app, so each pass has to
    run inside an application context — without one the retention job silently
    errored and UU PDP retention never actually happened.
    """
    global _running
    _running = True
    while _running:
        try:
            if app is not None:
                with app.app_context():
                    result = purge_all()
            else:
                result = purge_all()
            total = sum(result.values())
            if total > 0:
                logger.info("Data retention purge: %s", result)
        except Exception as e:
            logger.error("Data retention error: %s", e)
        time.sleep(_retention_interval)


def start_retention_scheduler(interval=86400, app=None):
    global _retention_thread, _retention_interval
    if _retention_thread and _retention_thread.is_alive():
        return
    _retention_interval = interval
    _retention_thread = threading.Thread(target=_run_retention_loop, args=(app,), daemon=True)
    _retention_thread.start()
    logger.info("Data retention scheduler started (interval=%ds)", _retention_interval)


def stop_retention_scheduler():
    global _running
    _running = False
