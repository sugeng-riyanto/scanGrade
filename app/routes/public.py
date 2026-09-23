"""Public-facing routes — landing, pricing, demo request, capacity evidence."""

import io
from datetime import datetime, timezone
from flask import (Blueprint, abort, jsonify, render_template, request,
                   send_file)
from app.utils.auth import get_supabase
from app.utils.helpers import row_or_none
from app.utils.logger import get_logger

public_bp = Blueprint("public", __name__)
logger = get_logger("public")


def _rate_limit(limit):
    """The app's limiter when it has one — a public page that runs a full
    calibration on every open should not be free to hammer."""
    from app.utils.rate_limiter import limiter
    return limiter.limit(limit) if limiter else (lambda f: f)


@public_bp.route("/loaderio-51ecf273210e88abe9f24d4eb2dba2a8.html")
def loaderio_verify():
    return "loaderio-51ecf273210e88abe9f24d4eb2dba2a8", 200, {"Content-Type": "text/plain"}


@public_bp.route("/pricing")
def pricing():
    try:
        from app.utils.auth import get_supabase
        supabase = get_supabase()
        plans = supabase.table("subscription_plans").select("*").eq("is_active", True).order("sort_order").execute().data or []
    except Exception:
        plans = []
    return render_template("pricing.html", plans=plans)


@public_bp.route("/api/demo-request", methods=["POST"])
def demo_request():
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "message": "Data tidak boleh kosong"}), 400

    school_name = (data.get("school_name") or "").strip()
    email = (data.get("email") or "").strip()
    phone = (data.get("phone") or "").strip()
    message = (data.get("message") or "").strip()

    if not school_name:
        return jsonify({"success": False, "message": "Nama sekolah wajib diisi"}), 400
    if not email or "@" not in email:
        return jsonify({"success": False, "message": "Email tidak valid"}), 400

    supabase = get_supabase()
    try:
        supabase.table("audit_logs").insert({
            "action": "demo_request",
            "entity_type": "lead",
            "new_data": {"school_name": school_name, "email": email, "phone": phone, "message": message},
            "created_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
        logger.info("Demo request from %s (%s)", school_name, email)
        return jsonify({"success": True, "message": "Terima kasih! Tim kami akan menghubungi Anda."})
    except Exception as e:
        logger.warning("Failed to save demo request: %s", e)
        return jsonify({"success": True, "message": "Terima kasih! Kami akan menghubungi Anda."})


@public_bp.route("/capacity")
def capacity():
    """The measured capacity of this deployment, read from the committed evidence.

    Deliberately public and deliberately not a marketing page: the figures, their
    dates and their configuration are rendered from `docs/measurements/` by
    `app/services/capacity_service.py`, the same reader
    `tests/unit/test_landing_claims.py` holds the landing page to. A school can
    check the number against the file it came from, and the file is downloadable
    from this page.
    """
    from app.services.capacity_service import report

    return render_template("public/capacity.html", cap=report())


@public_bp.route("/capacity/evidence/<name>")
def capacity_evidence(name):
    """One committed artifact, as text.

    The whitelist is the report's own file list, so the only files reachable here
    are the ones the page displays. Served as ``text/plain`` on purpose: an
    artifact is evidence to read, not a document to execute in a visitor's
    browser.
    """
    from app.services.capacity_service import artifact_path

    path = artifact_path(name)
    if path is None:
        abort(404)
    return send_file(path, mimetype="text/plain", as_attachment=False,
                     download_name=path.name)


# ── a shared item analysis ───────────────────────────────────────────────────
#
# A teacher's report, reachable with a link and no account. Three rules decide
# what a stranger gets, and they are the reason this lives in its own module
# rather than behind the teacher's route with a flag:
#
#   1. **The token is the permission.** `analysis_share.resolve` answers the same
#      404 for an unknown, expired and revoked token, because telling a stranger
#      which one it was tells them a token existed.
#   2. **The answer key and the student names never render.** The item statistics
#      are the point of sharing; which bubble is correct and who sat the paper are
#      not. That is enforced here — at the payload and at the document — not in
#      the template, so a new panel added later cannot forget it.
#   3. **The teacher's name is not published.** The school is named (a shared
#      report about "SMA Negeri 1" is checkable) and the person is not.


def _shared_report(supabase, exam_id):
    """``(exam, analysis)`` for a visitor holding a link, with no permission check.

    Deliberately not `_guard_exam`, which asks who is looking: nobody is. It reuses
    the same loaders and the same analysis the teacher's page uses, so the shared
    report and the teacher's report cannot disagree about a number.
    """
    from app.routes.teacher import _chart_payload, _exam_results, _json_fields
    from app.services import item_analysis
    from app.services.question_types import default_weights

    exam = row_or_none(supabase.table("exams").select("*")
                       .eq("id", exam_id).maybe_single().execute())
    if not exam:
        return None, None
    exam = _json_fields(exam)
    total = int(exam.get("total_questions") or 0)
    if not (exam.get("question_weights") or {}) and total > 0:
        exam["question_weights"] = default_weights(
            exam.get("question_types") or {}, total)
    submissions, _scan, _online, _stats = _exam_results(supabase, exam_id)
    return exam, item_analysis.analyse(exam, submissions)


def _link_or_404(supabase, token):
    from app.services import analysis_share

    link = analysis_share.resolve(supabase, token)
    if not link:
        abort(404)
    return link


def _shared_learner(supabase, link):
    """One learner's page from a token that names them, and nothing else.

    Two differences from the teacher's copy, both decided here rather than in the
    template:

    * **the key never travels** — `with_key=False` keeps it out of the payload, so
      no edit to the markup can start publishing it. A learner's page is the most
      tempting place in the app to show the correct letter, and an exam can still
      be open for the rest of the class;
    * **only this learner is named.** The page is built from one person's row, so
      there is no other name in the object at all — the ranking, the statements and
      the per-student payload of the class report are all absent by construction.
    """
    from app.routes.teacher import _report_cover
    from app.services import analysis_share, exam_report

    from app.services import analysis_scope

    exam, analysis = _shared_report(supabase, link["exam_id"])
    if not exam:
        abort(404)
    cover = _report_cover(supabase, exam)
    # Rule 3, kept where the redaction lives: the school is named and the person is
    # not. The page does not print a teacher today, so this is the line that keeps
    # that true if somebody adds one.
    cover["teacher_name"] = ""
    who = exam_report.learner(analysis, cover, link.get("student_id"), with_key=False)
    if not who:
        abort(404)
    # Counted after the page is known to exist, so a link to a learner who has been
    # removed is not reported to its owner as a reader.
    analysis_share.register_view(supabase, link)
    return render_template(
        "teacher/analysis_student.html", exam=cover, analysis=analysis,
        learner=who, public_view=True, share=None,
        back_url="/", download_base=f"/r/{link['token']}",
        lang=analysis_scope.language(request.args.get("lang")))


@public_bp.route("/r/<token>")
@_rate_limit("90 per minute")
def shared_analysis(token):
    """One exam's item analysis, shared by a teacher, redacted for a stranger.

    The token's own row decides which of two documents this is: the exam's report,
    or — when the link names a learner — that one learner's page. One route and one
    token shape, because a teacher has one thing to hand out and one thing to
    revoke, and a second URL prefix would be a second thing to remember.
    """
    from app.routes.teacher import _chart_payload
    from app.services import analysis_share

    supabase = get_supabase()
    link = _link_or_404(supabase, token)
    if link.get("student_id"):
        return _shared_learner(supabase, link)
    exam, analysis = _shared_report(supabase, link["exam_id"])
    if not exam:
        abort(404)
    # Counted after the report is known to exist, so a link to a deleted exam is
    # not reported to its owner as a reader.
    analysis_share.register_view(supabase, link)
    # The framework travels in the link's own query string, so a shared report
    # opens in the framework it was shared in: the reader chooses *what question
    # they are being shown*, and two links to the same exam can mean different
    # things without either of them being wrong.
    from app.services import analysis_frameworks

    framework = analysis_frameworks.resolve(request.args.get("framework"))
    # `public_view` is what tells the template this is the redacted copy. The
    # session cannot: a teacher who is signed in and opens somebody's share link
    # used to render the *teacher's* copy of a shared report — key marker and
    # all — because they had a cookie.
    return render_template(
        "teacher/analysis.html", exam=exam, analysis=analysis,
        chart=_chart_payload(analysis, exam, public=True, framework=framework),
        framework=framework, public_view=True,
        download_base=f"/r/{token}")


def _shared_learner_file(supabase, link, ext):
    """One learner's file, from a token that names them, redacted the same way.

    The learner page's own payload goes to `learner_report` with `public=True`, so
    the key is absent from the document at the *builder* — not hidden by the
    markup — and no markup change can start publishing it. The page is built from
    one person's row, so no other learner's name is in this file at all.

    Deliberately not `analysis_share.register_view`: neither this route nor its
    exam-level twin counts a download as an open, so a family that opens the page
    and then saves the file is one visit, not two. The counter lives on the page
    both routes are linked from.
    """
    from app.routes.teacher import _learner_file, _report_cover
    from app.services import analysis_share  # noqa: F401 - the redaction contract
    from app.services import exam_report

    from app.services import analysis_scope  # noqa: F401

    exam, analysis = _shared_report(supabase, link["exam_id"])
    if not analysis:
        abort(404)
    cover = _report_cover(supabase, exam)
    # Rule 3, kept where the redaction lives: the school is named and the person
    # is not — the shared document carries no teacher's name either.
    cover["teacher_name"] = ""
    who = exam_report.learner(analysis, cover, link.get("student_id"), with_key=False)
    if not who:
        abort(404)
    from app.services import learner_report

    lang = request.args.get("lang") or "id"
    # The *public* name: same person, same exam, and it says so by being built for
    # the copy that carries no key rather than by a flag the caller passed.
    return _learner_file(who, cover, ext, lang,
                         learner_report.filename(who, cover, ext), public=True)


@public_bp.route("/r/<token>/download.<ext>")
@_rate_limit("30 per minute")
def shared_analysis_file(token, ext):
    """The same three documents the teacher can download, redacted the same way.

    The extension is a whitelist rather than a format string: a path built from
    a caller's text is how a download route becomes a file-read route.
    """
    from app.routes.teacher import _exam_results, _json_fields  # noqa: F401
    from app.services import analysis_report, analysis_share
    from app.services.report_card_service import school_for

    if ext not in ("csv", "xlsx", "pdf"):
        abort(404)
    supabase = get_supabase()
    link = _link_or_404(supabase, token)
    # A token's own row decides which document this is, exactly as it does on the
    # page: the exam's report, or — when the link names a learner — that learner's
    # own file. One token, one thing to hand out and one thing to revoke.
    if link.get("student_id"):
        return _shared_learner_file(supabase, link, ext)
    exam, analysis = _shared_report(supabase, link["exam_id"])
    if not analysis:
        abort(404)
    from app.services import analysis_frameworks

    lang = request.args.get("lang") or "id"
    framework = analysis_frameworks.resolve(request.args.get("framework"))
    name = analysis_report.filename(analysis, exam, ext)

    if ext == "csv":
        payload = analysis_report.analysis_csv(analysis, exam, lang, public=True,
                                               framework=framework)
        buf = io.BytesIO(payload.encode("utf-8-sig"))
        return send_file(buf, mimetype="text/csv", as_attachment=True,
                         download_name=name)
    if ext == "xlsx":
        book = analysis_report.analysis_xlsx(analysis, exam, lang=lang, public=True,
                                             framework=framework)
        return send_file(
            io.BytesIO(book), as_attachment=True,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            download_name=name)
    pdf = analysis_report.analysis_pdf(
        analysis, exam,
        school=(school_for(supabase, exam.get("school_id")) or {}).get("name", ""),
        teacher="", lang=lang, public=True, framework=framework)
    return send_file(io.BytesIO(pdf), mimetype="application/pdf", as_attachment=True,
                     download_name=name)


@public_bp.route("/privacy")
def privacy():
    """Kebijakan Privasi — UU PDP compliance."""
    supabase = get_supabase()
    dpo_contact = None
    try:
        dpo = supabase.table("system_settings").select("value").eq("key", "dpo_contact").single().execute().data
        if dpo:
            dpo_contact = dpo.get("value")
    except Exception:
        pass
    return render_template("compliance/privacy.html", dpo_contact=dpo_contact)


@public_bp.route("/terms")
def terms():
    """Syarat dan Ketentuan — PSE Kominfo compliance."""
    supabase = get_supabase()
    dpo_contact = None
    try:
        dpo = supabase.table("system_settings").select("value").eq("key", "dpo_contact").single().execute().data
        if dpo:
            dpo_contact = dpo.get("value")
    except Exception:
        pass
    return render_template("compliance/terms.html", dpo_contact=dpo_contact)
