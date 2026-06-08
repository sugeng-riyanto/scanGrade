import functools
from flask import g, request, jsonify, abort


def _wants_json():
    accept = request.headers.get("Accept", "")
    return "application/json" in accept or request.path.startswith("/api/")


def require_school_access(table, resource_id_param="id", school_join=None):
    """Verify the current user's school_id matches the resource's school_id.

    Args:
        table: Supabase table name (e.g. "exams").
        resource_id_param: URL parameter name holding the resource ID.
        school_join: Optional (fk_column, parent_table) for chained lookups.
                     Example: ("exam_id", "exams") for submissions that
                     reference exams which have school_id.
    """
    def decorator(f):
        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            from app.utils.auth import get_supabase

            resource_id = kwargs.get(resource_id_param) or request.args.get(resource_id_param)
            if not resource_id:
                return (jsonify({"error": "Resource ID required"}), 400) if _wants_json() else abort(400)

            user_school_id = g.get("user_school_id")
            if not user_school_id:
                return (jsonify({"error": "Access denied: no school"}), 403) if _wants_json() else abort(403)

            db = get_supabase()

            try:
                if school_join:
                    fk_column, parent_table = school_join
                    child = db.table(table).select(fk_column).eq("id", resource_id).single().execute().data
                    if not child:
                        resource_school_id = None
                    else:
                        parent = db.table(parent_table).select("school_id").eq("id", child[fk_column]).single().execute().data
                        resource_school_id = parent.get("school_id") if parent else None
                else:
                    row = db.table(table).select("school_id").eq("id", resource_id).single().execute().data
                    resource_school_id = row.get("school_id") if row else None
            except Exception:
                resource_school_id = None

            if not resource_school_id:
                return (jsonify({"error": "Resource not found"}), 404) if _wants_json() else abort(404)

            if str(user_school_id) != str(resource_school_id):
                return (jsonify({"error": "Access denied: different school"}), 403) if _wants_json() else abort(403)

            return f(*args, **kwargs)
        return wrapper
    return decorator
