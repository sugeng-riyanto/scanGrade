from datetime import datetime, timezone


def set_exam_context(exam_id, extra=None):
    try:
        import sentry_sdk
        ctx = {"exam_id": exam_id, "timestamp": datetime.now(timezone.utc).isoformat()}
        if extra:
            ctx.update(extra)
        sentry_sdk.set_context("exam", ctx)
    except ImportError:
        pass


def set_student_context(student_id, exam_id):
    try:
        import sentry_sdk
        sentry_sdk.set_context("student_exam", {
            "student_id": student_id,
            "exam_id": exam_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
    except ImportError:
        pass


def set_school_context(school_id):
    try:
        import sentry_sdk
        sentry_sdk.set_context("school", {
            "school_id": school_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
    except ImportError:
        pass
