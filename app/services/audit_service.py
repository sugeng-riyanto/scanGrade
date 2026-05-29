from datetime import datetime, timezone
from flask import g, request, current_app

# Predefined action constants
ACTION_LOGIN = "login"
ACTION_LOGOUT = "logout"
ACTION_REGISTER = "register"
ACTION_ACTIVATE = "activate"
ACTION_CREATE = "create"
ACTION_UPDATE = "update"
ACTION_DELETE = "delete"
ACTION_APPROVE = "approve"
ACTION_REJECT = "reject"
ACTION_UPLOAD = "upload"
ACTION_EXPORT = "export"
ACTION_IMPORT = "import"
ACTION_PUBLISH = "publish"
ACTION_GRADE = "grade"
ACTION_SUBMIT = "submit"
ACTION_RETRACT = "retract"
ACTION_RESET_PASSWORD = "reset_password"
ACTION_PROMOTE = "promote"

# Entity type constants
ENTITY_USER = "user"
ENTITY_PROFILE = "profile"
ENTITY_EXAM = "exam"
ENTITY_SUBMISSION = "submission"
ENTITY_CLASS = "class"
ENTITY_SCHOOL = "school"
ENTITY_SCHOOL_YEAR = "school_year"
ENTITY_TEACHER = "teacher"
ENTITY_STUDENT = "student"
ENTITY_SUBJECT = "subject"
ENTITY_REGISTRATION_REQUEST = "registration_request"
ENTITY_TEACHER_ASSIGNMENT = "teacher_assignment"
ENTITY_VIOLATION = "violation"


def get_supabase():
    return current_app.extensions["supabase"]


def log_activity(
    action: str,
    entity_type: str,
    entity_id: str = None,
    old_data: dict = None,
    new_data: dict = None,
    user_id: str = None,
    ip_address: str = None,
    user_agent: str = None,
):
    supabase = get_supabase()
    try:
        uid = user_id or getattr(g, "user_id", None)
        ip = ip_address or request.remote_addr or ""
        ua = user_agent or request.headers.get("User-Agent", "")
        entry = {
            "user_id": uid,
            "action": action,
            "entity_type": entity_type,
            "entity_id": str(entity_id) if entity_id else None,
            "old_data": old_data,
            "new_data": new_data,
            "ip_address": ip,
            "user_agent": ua[:500],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        supabase.table("audit_logs").insert(entry).execute()
    except Exception as e:
        current_app.logger.error(f"Audit log failed: {e}")


def log_create(entity_type: str, entity_id: str, data: dict = None, **kwargs):
    log_activity(ACTION_CREATE, entity_type, entity_id, new_data=data, **kwargs)


def log_update(entity_type: str, entity_id: str, old_data: dict = None, new_data: dict = None, **kwargs):
    log_activity(ACTION_UPDATE, entity_type, entity_id, old_data=old_data, new_data=new_data, **kwargs)


def log_delete(entity_type: str, entity_id: str, old_data: dict = None, **kwargs):
    log_activity(ACTION_DELETE, entity_type, entity_id, old_data=old_data, **kwargs)


def fetch_audit_logs(
    limit: int = 100,
    offset: int = 0,
    action: str = None,
    entity_type: str = None,
    user_id: str = None,
    days: int = None,
):
    supabase = get_supabase()
    try:
        q = supabase.table("audit_logs").select("*, profiles!left(full_name, email)").order("created_at", desc=True)
        if action:
            q = q.eq("action", action)
        if entity_type:
            q = q.eq("entity_type", entity_type)
        if user_id:
            q = q.eq("user_id", user_id)
        if days:
            from datetime import datetime, timedelta, timezone
            since = datetime.now(timezone.utc) - timedelta(days=days)
            q = q.gte("created_at", since.isoformat())
        q = q.range(offset, offset + limit - 1)
        res = q.execute()
        return res.data or []
    except Exception:
        return []


def count_audit_logs(action: str = None, entity_type: str = None, days: int = None):
    supabase = get_supabase()
    try:
        q = supabase.table("audit_logs").select("id", count="exact")
        if action:
            q = q.eq("action", action)
        if entity_type:
            q = q.eq("entity_type", entity_type)
        if days:
            from datetime import datetime, timedelta, timezone
            since = datetime.now(timezone.utc) - timedelta(days=days)
            q = q.gte("created_at", since.isoformat())
        res = q.execute()
        return res.count or 0
    except Exception:
        return 0


def get_activity_summary(days: int = 30):
    supabase = get_supabase()
    from datetime import datetime, timedelta, timezone
    since = datetime.now(timezone.utc) - timedelta(days=days)
    since_str = since.isoformat()

    total = 0
    logins = 0
    creates = 0
    deletes = 0
    by_action = {}

    try:
        total = supabase.table("audit_logs").select("id", count="exact").gte("created_at", since_str).execute().count or 0
    except Exception:
        pass
    try:
        logins = supabase.table("audit_logs").select("id", count="exact").eq("action", "login").gte("created_at", since_str).execute().count or 0
    except Exception:
        pass
    try:
        creates = supabase.table("audit_logs").select("id", count="exact").eq("action", "create").gte("created_at", since_str).execute().count or 0
    except Exception:
        pass
    try:
        deletes = supabase.table("audit_logs").select("id", count="exact").eq("action", "delete").gte("created_at", since_str).execute().count or 0
    except Exception:
        pass
    try:
        action_counts = supabase.table("audit_logs").select("action, count:action", count="exact") \
            .gte("created_at", since_str) \
            .execute()
        if action_counts.data:
            for row in action_counts.data:
                a = row.get("action", "unknown")
                by_action[a] = by_action.get(a, 0) + 1
    except Exception:
        pass

    return {
        "total": total,
        "logins": logins,
        "creates": creates,
        "deletes": deletes,
        "by_action": by_action,
    }
