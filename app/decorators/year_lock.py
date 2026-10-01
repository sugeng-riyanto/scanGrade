"""Refuse a write whose target belongs to a closed school year.

Closing a year is a promise — its papers and marks become history — and a promise
kept only by the year-close wizard is not kept at all: the grading screen, the
answer-key editor, the recalculate button and the override all write the same rows
from other doors.

Enforcement lives here, next to the route, rather than in the services underneath,
for one reason: a service cannot tell a *write* from a *read*, and locking reads
would hide a pupil's own results from them. The decorator knows the request method,
so it refuses only what changes something.

The year is resolved from the resource, not from the session: a teacher may be
signed into a school whose running year is open and still open a form that belongs
to last year's paper. `academic_year.year_of_exam` / `year_of_submission` do that
resolution, and `write_refusal` says whether the year is closed.
"""
from __future__ import annotations

import functools
import logging

from flask import flash, jsonify, redirect, request

logger = logging.getLogger(__name__)

#: A refusal, as an ``(id, en)`` message pair is not used here: the flash text is
#: already rendered on a page whose language the server does not know, so the
#: Indonesian half is the one shown, exactly as the other admin refusals do it.
CLOSED_YEAR_MESSAGE = ("Tahun ajaran ini sudah ditutup dan bersifat arsip. "
                       "Ujian dan nilai di dalamnya tidak bisa diubah lagi.")
CLOSED_YEAR_API = "Tahun ajaran sudah ditutup (arsip)"


def _wants_json() -> bool:
    return (request.is_json or request.path.startswith("/api/")
            or "application/json" in (request.headers.get("Accept") or ""))


def open_year_required(*id_keys):
    """Refuse this view when the year its target belongs to is closed.

    `id_keys` is the URL parameter(s) the resource id arrives in, in the order to
    try — e.g. ``@open_year_required("exam_id")`` or
    ``@open_year_required("submission_id")``. The key is named rather than guessed
    so that the guard which sweeps new routes can check it against the route
    pattern instead of trusting a convention.
    """
    def decorator(f):
        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            from app.services import academic_year
            from app.utils.auth import get_supabase

            resource_id, key = None, None
            for candidate in id_keys:
                value = kwargs.get(candidate)
                if value is None and request.view_args:
                    value = request.view_args.get(candidate)
                if value:
                    resource_id, key = value, candidate
                    break

            if not resource_id:
                # No id to resolve against. Refusing would break the route for a
                # reason the caller cannot fix; the guard test is what makes sure
                # a route that needs this decorator names a key that exists.
                return f(*args, **kwargs)

            supabase = get_supabase()
            if key and key.endswith("submission_id"):
                year_id = academic_year.year_of_submission(supabase, resource_id)
            else:
                year_id = academic_year.year_of_exam(supabase, resource_id)

            reason = academic_year.write_refusal(supabase, year_id)
            if reason:
                logger.info("refused a write into a closed year: %s %s",
                            request.method, request.path)
                if _wants_json():
                    return jsonify({"error": CLOSED_YEAR_API}), 403
                flash(CLOSED_YEAR_MESSAGE, "error")
                return redirect(request.referrer or "/teacher/exams")
            return f(*args, **kwargs)
        return wrapper
    return decorator
