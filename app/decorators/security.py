import functools
from flask import g, request, jsonify, abort

from app.utils import denials


# Roles that may see or alter assessment data for other people's students.
STAFF_ROLES = ("guru", "admin_sekolah", "super_admin")


def _wants_json():
    accept = request.headers.get("Accept", "")
    return "application/json" in accept or request.path.startswith("/api/")


def require_role(*roles):
    """Restrict a view to the given roles.

    ``require_school_access`` answers "is this row from your school?" — it does
    NOT answer "are you allowed to do this at all". Endpoints guarded by school
    access alone were reachable by every signed-in member of that school,
    students included: ``/api/exams/<id>/report`` handed a whole class's names,
    NISN and scores to any student who asked. Use both — this one for the role,
    that one for the row.

    Place it UNDER ``@login_required`` so the session is applied to ``g`` first.
    """
    def decorator(f):
        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            if g.get("user_role") not in roles:
                if _wants_json():
                    return jsonify({"error": denials.OUT_OF_SCOPE}), 403
                abort(403)
            return f(*args, **kwargs)
        return wrapper
    return decorator


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
                return (jsonify({"error": "ID diperlukan"}), 400) if _wants_json() else abort(400)

            user_school_id = g.get("user_school_id")
            if not user_school_id:
                return (jsonify({"error": denials.NO_SCHOOL}), 403) if _wants_json() else abort(403)

            db = get_supabase()

            try:
                if school_join:
                    fk_column, parent_table = school_join
                    # One round-trip instead of two (~110 ms saved). Ask
                    # PostgREST to embed the parent row; if the relationship
                    # can't be embedded on this schema, fall back to the chained
                    # lookup so correctness never depends on the optimisation.
                    try:
                        row = (
                            db.table(table)
                            .select(f"{fk_column}, {parent_table}(school_id)")
                            .eq("id", resource_id)
                            .single()
                            .execute()
                            .data
                        ) or {}
                        parent = row.get(parent_table) or {}
                        resource_school_id = parent.get("school_id")
                    except Exception:
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
                return (jsonify({"error": "Data tidak ditemukan"}), 404) if _wants_json() else abort(404)

            if str(user_school_id) != str(resource_school_id):
                return (jsonify({"error": denials.NOT_YOUR_SCHOOL}), 403) if _wants_json() else abort(403)

            return f(*args, **kwargs)
        return wrapper
    return decorator
