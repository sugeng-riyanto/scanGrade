"""Auth middleware — school access verification decorator.

Extracts the user's school_id from Flask g (populated by @login_required)
and verifies it matches the resource's school_id in Supabase.

Usage:
    from app.middlewares.auth import require_school_access

    @teacher_bp.route("/exams/<exam_id>")
    @teacher_or_admin_required
    @require_school_access("exams", "exam_id")
    def exam_detail(exam_id):
        ...
"""

from app.decorators.security import require_school_access
