import json
import io
import os
import logging
import dataclasses
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from flask import Blueprint, jsonify, render_template, request, redirect, url_for, flash, g, send_file, current_app
from app.utils.auth import teacher_or_admin_required, get_supabase, login_required, subscription_write_required
from app.utils.cache import cache_get, cache_set, cache_delete
from app.utils.helpers import read_with_retry, row_or_none
from app.decorators.security import require_school_access
from app.decorators.subscription import require_subscription
from app.utils.exam_access import can_manage_exam, exam_class_ids
from app.services.export_service import export_to_xlsx, export_to_pdf
from app.services.answer_sheet_generator import generate_answer_sheet
from app.services.question_types import (
    KIND_CHOICE, KIND_DRAG, KIND_ESSAY, KIND_MATCH, KIND_TRUE_FALSE, MCQ,
    canonical_type, default_weights, describe_answer, earned_points, essay_marker,
    grade_answer, has_answer, is_essay, is_objective, normalise_key,
    objective_result, question_kind, scheme_in,
)
from app.services import mark_scheme
from app.services.pdf_service import upload_pdf
from app.services.audit_service import log_activity
from app.utils.req_cache import (invalidate_teacher_assignments, school_classes,
                                 school_subjects, teacher_assignments_for)
from app.utils import exam_window
from app.services import analysis_report, item_analysis

logger = logging.getLogger(__name__)

teacher_bp = Blueprint("teacher", __name__)


# ── Who may act on an exam ───────────────────────────────────────────────────
#
# ``require_school_access`` proves a row belongs to the caller's school. That is
# NOT the same as being allowed to recalculate, unpublish or export it, and it
# says nothing about a submission id at all. Routes that take an id therefore go
# through the two helpers below, which apply one rule everywhere:
# the exam's owner, or the admin of its school.


def _wants_json():
    """True when the caller expects a JSON answer rather than a redirect.

    A browser asks for ``text/html`` and a real form submit arrives as
    ``application/x-www-form-urlencoded``; both are better served by a redirect
    with a flash message. A fetch/XHR sends ``*/*`` with no form body, and an
    API caller asks for ``application/json`` explicitly — those must get a 403
    they can read, because a 302 followed to an HTML page looks like success
    and hides the refusal.
    """
    accept = request.headers.get("Accept") or ""
    if request.is_json or "application/json" in accept:
        return True
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and not request.form:
        return True
    return False


def _deny(message, as_json, redirect_to="/teacher/exams"):
    if as_json:
        return jsonify({"error": message}), 403
    flash(message, "error")
    return redirect(redirect_to)


def _guard_exam(supabase, exam_id, columns="id,teacher_id,school_id", as_json=True,
                redirect_to="/teacher/grading"):
    """Return ``(exam, None)`` when the caller may act on it, else ``(None, response)``.

    ``columns`` is passed through, so a route can fetch everything it needs and let
    the guard pay for the lookup — no second query just to check permission.

    A failed lookup DENIES. A permission check that raises answers 500, which
    keeps the data safe but hides the cause and looks like a broken feature; the
    failure is logged and the caller is told access could not be confirmed.
    """
    try:
        exam = row_or_none(
            supabase.table("exams").select(columns).eq("id", exam_id).maybe_single().execute()
        )
    except Exception:
        logger.exception("Access check failed for exam %s", exam_id)
        return None, _deny("Tidak dapat memverifikasi akses ke ujian ini", as_json, redirect_to)
    if not exam:
        if as_json:
            return None, (jsonify({"error": "Ujian tidak ditemukan"}), 404)
        flash("Ujian tidak ditemukan", "error")
        return None, redirect(redirect_to)
    if not can_manage_exam(g.user_id, g.get("user_role"), g.get("user_school_id"), exam):
        logger.warning("Denied exam access: exam=%s user=%s role=%s",
                       exam_id, g.user_id, g.get("user_role"))
        return None, _deny("Tidak punya akses ke ujian ini", as_json, redirect_to)
    return exam, None


def _guard_submission(supabase, submission_id, as_json=True, redirect_to="/teacher/results"):
    """Same, for a submission id — resolves the owning exam first."""
    try:
        sub = row_or_none(
            supabase.table("submissions")
            .select("id,exam_id,student_id,exams(id,teacher_id,school_id)")
            .eq("id", submission_id).maybe_single().execute()
        )
    except Exception:
        logger.exception("Access check failed for submission %s", submission_id)
        return None, _deny("Tidak dapat memverifikasi akses ke submission ini", as_json, redirect_to)
    if not sub:
        if as_json:
            return None, (jsonify({"error": "Submission tidak ditemukan"}), 404)
        flash("Submission tidak ditemukan", "error")
        return None, redirect(redirect_to)
    exam = sub.get("exams") or {}
    if not can_manage_exam(g.user_id, g.get("user_role"), g.get("user_school_id"), exam):
        logger.warning("Denied submission access: submission=%s user=%s", submission_id, g.user_id)
        return None, _deny("Tidak punya akses ke submission ini", as_json, redirect_to)
    sub["_exam"] = exam
    return sub, None


def _extract_mcq_answer(student_ans):
    """The letter inside the offline `{"answer": …}` wrapper, or the bare value."""
    if isinstance(student_ans, dict):
        return student_ans.get("answer", "")
    return student_ans or ""


def _is_mcq_correct(student_ans, key_val):
    """Kept as a name for callers that only ever mean a choice question.

    The rule itself is `question_types.grade_answer`, because the same comparison
    written out here was written out in six other places — and every one of them
    classified a true/false question as an essay.
    """
    return grade_answer(MCQ, key_val, student_ans)


def _apply_mark_scheme(question_types, total_questions, question_weights):
    """The scheme's own points, computed on the server.

    The builder keeps a JavaScript copy of the mark-scheme arithmetic so the running
    total moves while a teacher types. That copy is only a *preview*: if it could
    write marks, a stale cached page — or a form edited in devtools — would decide
    what a paper is worth, and the browser and the stored scores would drift apart
    with nothing to show for it. The authority is `app/services/mark_scheme.py`, and
    this is the one place the two exam-saving routes consult it.

    A paper *without* a scheme is returned untouched. That is not politeness: a
    result here is recomputed from the stored answers whenever marks are published
    or recalculated, so applying a rule to an old exam would move marks already in
    students' hands.
    """
    scheme = scheme_in(question_weights)
    if not scheme:
        return question_weights
    return mark_scheme.weights_for(
        question_types,
        total_questions,
        by_type=scheme.get("by_type"),
        partial=bool(scheme.get("partial")),
        existing=question_weights,
    )


JSON_COLUMNS = ("answer_key", "question_types", "question_weights", "question_pages")


def _json_fields(row):
    """A row's JSON columns as objects. PostgREST returns `jsonb` parsed and a
    text column as a string, and which one arrives depends on the column's type in
    a given project — so every reader of an exam row has to do this, and doing it
    in four places is how one of them ends up with a `str` in its hands."""
    for field in JSON_COLUMNS:
        value = row.get(field)
        if isinstance(value, str):
            try:
                row[field] = json.loads(value)
            except (json.JSONDecodeError, TypeError):
                row[field] = {}
    return row


def _recalculate_scores(exam_id):
    supabase = get_supabase()
    exam = supabase.table("exams").select("*").eq("id", exam_id).single().execute().data
    if not exam:
        return
    _json_fields(exam)
    answer_key = exam.get("answer_key") or {}
    question_types = exam.get("question_types") or {}
    question_weights = exam.get("question_weights") or {}
    total_q = exam.get("total_questions", 0)
    # The 70/30 split and the per-question shares are one function now. Writing
    # them out here is what made this route decide that a true/false question was
    # an essay and place it in the essay pool — where it earned nothing from the
    # auto-grader and then had 30% of the paper's marks divided among the wrong
    # questions.
    if not question_weights and total_q > 0:
        question_weights = default_weights(question_types, total_q)
    subs = supabase.table("submissions").select("id, answers, penalty, teacher_feedback").eq("exam_id", exam_id).in_("status", ["submitted", "graded", "published"]).execute().data or []
    if not subs:
        return
    # Build update list — all scoring in Python, then parallel DB writes
    updates = []
    for sub in subs:
        for _sf in ("answers", "teacher_feedback"):
            _sv = sub.get(_sf)
            if isinstance(_sv, str):
                try: sub[_sf] = json.loads(_sv)
                except (json.JSONDecodeError, TypeError): sub[_sf] = {}
        answers = sub.get("answers") or {}
        earned, _graded = earned_points(question_types, answer_key, answers,
                                       question_weights, total_q)
        fb = sub.get("teacher_feedback") or {}
        fb_scores = fb.get("scores", {}) or {}
        for qi, sv in fb_scores.items():
            if sv is not None and sv != "":
                ew = float(question_weights.get(str(qi), 0))
                if ew > 0:
                    earned += float(sv) / 100.0 * ew
        final = round(min(earned, 100), 2)
        penalty = float(sub.get("penalty") or 0)
        final = max(0, round(final - penalty, 2))
        # The stored objective score, by the same one rule every other writer uses
        # (`question_types.objective_result`): a percentage of the paper's
        # objective questions, with an unkeyed question scored wrong. The number
        # here is the one this function has always written
        # (`correct / objective questions`); what changed is that the routes which
        # divided by the *keyed* count now agree with it instead of paying a pupil
        # 100 for a two-tenths-marked paper.
        objective = objective_result(question_types, answer_key, answers, total_q)
        updates.append((sub["id"], objective.score, final))
    # Parallel DB updates — 300 subs / 20 threads ≈ 3s instead of 60s serial
    def _update_one(item):
        sub_id, sc, fs = item
        try:
            supabase.table("submissions").update({"score": sc, "final_score": fs}).eq("id", sub_id).execute()
        except Exception as exc:
            logger.warning("_recalculate_scores: failed sub %s: %s", sub_id, exc)
    max_workers = min(len(updates), 20)
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        list(pool.map(_update_one, updates))


#: How a question's type reads in a spreadsheet export. A kind, not a type, so
#: the three new objective types do not each need a row here.
_QUESTION_KIND_LABELS = {
    KIND_CHOICE: "MCQ",
    KIND_TRUE_FALSE: "True/False",
    KIND_MATCH: "Matching",
    KIND_DRAG: "Drag & drop",
    KIND_ESSAY: "Essay",
}


def _as_dict(value) -> dict:
    """A jsonb column can come back as a JSON *string*.

    `exams.answer_key` is written with `json.dumps(...)`, so the column holds a
    JSON string inside jsonb and PostgREST returns it as one. Every reader in this
    file guards for that separately; this names it once.
    """
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return {}
    return value if isinstance(value, dict) else {}


def _as_list(value) -> list:
    """The list-valued counterpart of :func:`_as_dict` (`pdf_page_urls`)."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return []
    return value if isinstance(value, list) else []


def _normalise_exam_json(exam_data: dict) -> dict:
    """Parse the jsonb columns the exam builder reads before it sees them.

    `question_pages` is written with `json.dumps`, so it arrives as a JSON
    *string*; the builder does `question_pages[i]` for each question, and
    indexing a string by `'0'`..`'9'` returns a **character**. The exam-edit form
    therefore showed `{`, `"`, `0`, `"` … in the page fields — five pages of
    nonsense that the teacher could not even recognise as wrong, and that saving
    wrote back over the real mapping. A page range that has turned into `"{"`
    parses to no pages at all, which silently stops the student's paper from
    turning.
    """
    for field in ("question_types", "answer_key", "question_weights", "question_pages",
                  "question_audio", "question_canvas", "question_texts"):
        if isinstance(exam_data.get(field), str):
            exam_data[field] = _as_dict(exam_data.get(field))
    if isinstance(exam_data.get("pdf_page_urls"), str):
        exam_data["pdf_page_urls"] = _as_list(exam_data.get("pdf_page_urls"))
    return exam_data


def _answer_key_gap(exam: dict) -> dict | None:
    """How short this exam's key falls, or None when there is nothing to say.

    Returns the objective question count, how many of them the key answers, how
    many it does not, and the highest score a student can still reach. The numbers
    come from `question_types.objective_result` — the same function that marks the
    paper — so a card built from this cannot disagree with the mark a pupil is
    given, which is the whole point of measuring it here instead of re-deriving it.

    Both the empty key and the *partly filled* one are reported, and they used to be
    treated differently on purpose: the card's sentence was "scores will be 0", and a
    partial key did not do that — it did something worse. It paid a student 100 out
    of a paper that was two-tenths marked, because the scoring routes divided by the
    number of answers the key happened to contain. That denominator is gone (see
    `objective_result`), so the two situations are now the same defect with different
    numbers, and both get a card that states its own number.
    """
    qtypes = _as_dict(exam.get("question_types"))
    key = _as_dict(exam.get("answer_key"))
    # Index the scorer over the questions this exam actually has: its own count, or
    # one past the highest index its type map names — a type map longer than
    # `total_questions` would otherwise be silently uncounted, and a key that covers
    # those questions would look short.
    named = [int(i) for i in qtypes if str(i).lstrip("-").isdigit() and int(i) >= 0]
    span = max([int(exam.get("total_questions") or 0)] + [i + 1 for i in named])
    result = objective_result(qtypes, key, {}, span)
    if not result.out_of or not result.unkeyed:
        return None
    return {"objective": result.out_of, "keyed": result.keyed,
            "unkeyed": len(result.unkeyed), "ceiling": result.ceiling}


def _needs_answer_key(exam: dict) -> bool:
    """Is the "scores will be 0" card actually true of this exam?

    It is true when the exam has objective questions and the key answers **none**
    of them: an unkeyed objective question is scored wrong by `objective_result`, so
    every student scores 0 on that half however well they answered — which is
    exactly what the card claims.

    It used to be `if not exam.get("answer_key")`, read off a row that never
    carried the column. Commit fb9aef2 trimmed this route's `select("*")` to an
    explicit column list and left `answer_key` out, so every exam with an MCQ
    question was reported as missing its key — permanently, and nothing a teacher
    did could clear it.

    A *partly* filled key is not this card's case: it does not produce 0, it
    produces a lower ceiling, and it has its own card (`_partial_answer_key`) that
    puts that ceiling in a number. Two situations, two sentences, neither of them a
    lie — which is what the previous split got wrong when the partial case was
    silently fine.
    """
    gap = _answer_key_gap(exam)
    return bool(gap) and gap["keyed"] == 0


def _partial_answer_key(exam: dict) -> bool:
    """Does this exam have objective questions the key does not answer — but some it does?"""
    gap = _answer_key_gap(exam)
    return bool(gap) and gap["keyed"] > 0


def _needs_class_assignment(exam: dict) -> bool:
    """Is this exam invisible to every pupil because no class was picked?

    An exam is offered to the classes the teacher assigned it to and to nobody
    else (`exam_access.class_assignment_allows`), so an active, visible exam with
    no classes reaches no one — the teacher sees it in their own list and the
    pupils see nothing, with nothing anywhere saying why.

    The test is deliberately narrow: only an exam the teacher has *published and
    activated* is a surprise, because that is the state in which they expect a
    class to be answering it. A draft nobody can reach is not news.

    Same lesson as `_needs_answer_key` above: `class_ids` has to be in this
    route's column list, or this card is permanent and nothing the teacher does
    can clear it.
    """
    return bool(exam.get("is_published")) and exam.get("status") == "active" \
        and not exam_class_ids(exam)


def _invalidate_teacher_dashboard() -> None:
    """Drop this teacher's cached dashboard row.

    The dashboard caches for 20 seconds, so without this a teacher saves an
    answer key, comes back, and sees the page exactly as it was — which is what
    "I filled it in and nothing changed" looked like from the outside.
    """
    try:
        cache_delete(f"t_dash:{g.user_id}")
    except Exception:
        pass


@teacher_bp.route("/dashboard")
@teacher_or_admin_required
def dashboard():
    supabase = get_supabase()
    # Try cache first (20s TTL)
    cache_key = f"t_dash:{g.user_id}"
    cached = cache_get(cache_key)
    if cached:
        return render_template("teacher/dashboard.html", **cached)

    # `answer_key` is here because the "no answer key" card below reads it:
    # leaving a column out of the list while the code below still reads it made
    # that warning permanent. See _needs_answer_key().
    # `class_ids` is here for the same reason `answer_key` is: the "no class"
    # card below reads it, and a column missing from this list would make that
    # warning permanent. See _needs_class_assignment().
    # Retried, unlike the rest of this page: Supabase drops keep-alive
    # connections and this read threw `RemoteProtocolError: Server disconnected`
    # straight out of the view — measured on the running server, the dashboard
    # answered its error page on 2 of 5 loads while nothing was wrong. A retry is
    # what `read_with_retry` exists for, and this is the page a teacher opens
    # first.
    res = read_with_retry(lambda: supabase.table("exams").select("id,title,subject,question_types,answer_key,total_questions,status,is_published,created_at,start_at,question_canvas,question_audio,class_ids").eq("teacher_id", g.user_id).order("created_at", desc=True).execute())
    exams = res.data or []

    exam_ids = [e["id"] for e in exams]
    total_students = 0
    all_scores = []
    pending_grading = 0
    upcoming_exams = []
    grading_progress = {}
    exams_no_key = []
    exams_partial_key = []
    #: The lowest ceiling among the partly keyed exams: the honest single number for
    #: a card that has to fit in one sentence. Reporting the best of them would
    #: understate what the situation costs the weakest paper.
    partial_key_ceiling = 0
    # Both warnings are computed inside `if exam_ids`, so they need a default for
    # the teacher who has no exams yet — the template reads them unconditionally.
    exams_unassigned = []
    # Assigned inside `if exam_ids` below, but the class-analytics section always
    # reads it — a teacher with zero exams got UnboundLocalError (500) otherwise.
    subs = []

    if exam_ids:
        subs = read_with_retry(lambda: supabase.table("submissions").select("student_id,score,final_score,status,exam_id").in_("exam_id", exam_ids).execute()).data or []
        unique_students = set(s["student_id"] for s in subs)
        total_students = len(unique_students)
        all_scores = [float(s.get("final_score") or s.get("score") or 0) for s in subs if s.get("final_score") or s.get("score")]

        # Count submissions that need manual grading (submitted/draft but exam has essay questions)
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        for e in exams:
            eid = e["id"]
            exam_subs = [s for s in subs if s.get("exam_id") == eid]
            graded = sum(1 for s in exam_subs if s.get("status") in ("graded", "published"))
            total = len(exam_subs)
            if total > 0:
                grading_progress[eid] = {"graded": graded, "total": total}

            # Check if exam has essay questions that need grading
            qt = e.get("question_types")
            if isinstance(qt, str):
                try: qt = json.loads(qt)
                except: qt = {}
            # Pending grading is about *teacher-marked* questions, so ask which
            # questions a teacher has to mark rather than which ones are not MCQ.
            has_essay = any(is_essay(v) for v in (qt.values() if isinstance(qt, dict) else [])) if qt else False
            if has_essay:
                ungraded = [s for s in exam_subs if s.get("status") in ("submitted", "draft")]
                pending_grading += len(ungraded)

            # Upcoming exams (start in future but within 7 days)
            start_at = e.get("start_at")
            if start_at:
                try:
                    if isinstance(start_at, str):
                        start_dt = datetime.fromisoformat(start_at.replace("Z", "+00:00"))
                    else:
                        start_dt = start_at
                    days_until = (start_dt - now).days
                    if 0 <= days_until <= 7 and e.get("status") == "active":
                        upcoming_exams.append(e)
                except:
                    pass

        # Exams the warning on the card is true about: MCQ questions, no answer
        # set to score them with, so the students' MCQ score is 0.
        exams_no_key = [e for e in exams if _needs_answer_key(e)]
        # Exams whose key answers *some* objective questions but not all. They do not
        # score 0, so they are not the card above; they score a ceiling, and the card
        # for them says what it is.
        exams_partial_key = [e for e in exams if _partial_answer_key(e)]
        ceiling = min((_answer_key_gap(e)["ceiling"] for e in exams_partial_key),
                      default=0)
        # A whole number when it is one, so a full paper's 100 does not print as
        # "100.0" beside a partial one's "42.5".
        partial_key_ceiling = int(ceiling) if float(ceiling).is_integer() else ceiling
        # Live exams that reach nobody: no class ticked, so no pupil can see them.
        exams_unassigned = [e for e in exams if _needs_class_assignment(e)]

    # ── Class Analytics ──
    # Per-exam performance breakdown (sorted by avg — hardest first)
    exam_stats = []
    for e in exams:
        eid = e["id"]
        exam_subs = [s for s in subs if s.get("exam_id") == eid]
        scores = [float(s.get("final_score") or s.get("score") or 0) for s in exam_subs if s.get("final_score") or s.get("score")]
        if scores:
            exam_stats.append({
                "title": e.get("title", "-")[:25],
                "avg": round(sum(scores) / len(scores), 1),
                "min": min(scores),
                "max": max(scores),
                "count": len(scores),
            })
    exam_stats.sort(key=lambda x: x["avg"])

    # Student improvement tracking (first vs latest score)
    student_improvement = []
    student_scores_map = {}
    for s in sorted(subs, key=lambda x: x.get("submitted_at", "")):
        sid = s.get("student_id")
        sc = s.get("final_score") if s.get("final_score") is not None else s.get("score")
        if sid and sc:
            if sid not in student_scores_map:
                student_scores_map[sid] = {"first": float(sc), "latest": float(sc)}
            else:
                student_scores_map[sid]["latest"] = float(sc)
    for sid, sc in student_scores_map.items():
        diff = sc["latest"] - sc["first"]
        student_improvement.append({"student_id": sid, "first": sc["first"], "latest": sc["latest"], "diff": round(diff, 1)})
    student_improvement.sort(key=lambda x: x["diff"], reverse=True)

    # The session carries the school, so no profile round-trip here.
    school_id = g.get("user_school_id")

    # Assignments, classes and subjects are the same on every dashboard load and
    # the last two are identical for every teacher in the school, so they come
    # from the shared cache. A teacher who assigns a class invalidates their own
    # list at the write, so this is not a delay they can observe.
    classes = school_classes(school_id)
    subjects = school_subjects(school_id)
    assignments = teacher_assignments_for(g.user_id, school_id)

    user_name = g.user_name or g.user_email or ""
    avg_score = round(sum(all_scores) / len(all_scores), 1) if all_scores else "-"

    template_data = {
        "exams": exams, "total_students": total_students,
        "avg_score": avg_score, "all_scores": all_scores, "user_name": user_name,
        "assignments": assignments, "classes": classes, "subjects": subjects,
        "exams_no_key": exams_no_key, "exams_partial_key": exams_partial_key,
        "partial_key_ceiling": partial_key_ceiling,
        "exams_unassigned": exams_unassigned,
        "pending_grading": pending_grading, "upcoming_exams": upcoming_exams,
        "grading_progress": grading_progress,
        "exam_stats": exam_stats[:6],
        "student_improvement": student_improvement[:5],
    }
    try:
        cache_set(cache_key, template_data, ttl=20)
    except Exception:
        pass
    return render_template("teacher/dashboard.html", **template_data)


@teacher_bp.route("/templates")
@teacher_or_admin_required
def exam_templates():
    """Exam template marketplace — browse and copy templates."""
    supabase = get_supabase()
    school_id = g.get("user_school_id")
    templates = []
    if school_id:
        templates = supabase.table("exams") \
            .select("id,title,subject,total_questions,question_types,description,teacher_id,created_at") \
            .eq("is_template", True) \
            .order("created_at", desc=True) \
            .execute().data or []
    return render_template("teacher/templates.html", templates=templates, school_id=school_id)


@teacher_bp.route("/exams/parse-pdf", methods=["POST"])
@teacher_or_admin_required
def exam_parse_pdf():
    """Upload PDF → markdown → AI classification (MCQ vs Essay) → preview."""
    try:
        if "pdf" not in request.files:
            return jsonify({"error": "Tidak ada file PDF"}), 400
        pdf_file = request.files["pdf"]
        raw = pdf_file.read()
        if len(raw) > 50 * 1024 * 1024:
            return jsonify({"error": "PDF terlalu besar. Maksimal 50MB"}), 413

        ai_mode = request.form.get("ai_mode", "false") == "true"
        use_vision = request.form.get("use_vision", "false") == "true"

        # Step 1: PDF → Clean Markdown
        try:
            from app.services.pdf_parser import pdf_to_markdown, classify_with_ai, classify_heuristic, generate_preview_html, generate_answer_key, is_scanned_pdf, question_pages
        except ImportError:
            return jsonify({"error": "Library tidak tersedia. Jalankan: pip install pymupdf"}), 500

        # Get API key for Gemini Vision if needed
        vision_api_key = ""
        if use_vision:
            try:
                from app.services.ai_service import _get_active_key
                vision_key = _get_active_key(g.user_id)
                if vision_key and vision_key.get("provider") == "gemini":
                    vision_api_key = vision_key.get("api_key", "")
                elif vision_key:
                    # Try to find a gemini key
                    supabase = get_supabase()
                    gemini_keys = supabase.table("teacher_ai_keys").select("*").eq("teacher_id", g.user_id).eq("provider", "gemini").limit(1).execute().data
                    if gemini_keys:
                        vision_api_key = gemini_keys[0].get("api_key", "")
            except:
                pass

        parsed = pdf_to_markdown(raw, use_vision=use_vision, vision_api_key=vision_api_key, lang=request.form.get("lang", "en"))
        if parsed.get("error"):
            return jsonify({"error": parsed["error"]}), 422

        questions = None
        ai_used = False
        answer_key_generated = False
        answer_key = {}
        answer_key_error = ""
        key = None

        # Step 2 & 3: AI processing (only in AI mode)
        if ai_mode:
            try:
                from app.services.ai_service import _get_active_key
                key = _get_active_key(g.user_id)
            except Exception as e:
                current_app.logger.warning("Failed to get AI key: %s", e)

            # Step 2: Classify questions
            try:
                if key and key.get("api_key"):
                    questions = classify_with_ai(
                        parsed["markdown"],
                        api_key=key["api_key"],
                        provider=key.get("provider", "groq"),
                    )
                    if questions and len(questions) > 0:
                        ai_used = True
            except Exception as e:
                current_app.logger.warning("AI classification failed: %s", e)

            if not questions:
                questions = classify_heuristic(parsed["markdown"])

            # Step 2b: the page each question is printed on. The document was
            # already split into pages before classification — the teacher asking
            # for it by hand was asking for an answer the parser had thrown away.
            # Every question this cannot locate simply keeps no page, and the
            # builder's field stays empty for the teacher to fill as before.
            question_pages(questions, parsed.get("pages") or [])

            parsed["questions"] = questions
            parsed["mcq_count"] = sum(1 for q in questions if is_objective(q.get("type")))
            parsed["essay_count"] = sum(1 for q in questions if is_essay(q.get("type")))

            # Step 3: Generate answer key
            try:
                if key and key.get("api_key"):
                    current_app.logger.info("Generating answer key with provider: %s", key.get("provider"))
                    ak = generate_answer_key(
                        parsed["markdown"], questions,
                        api_key=key["api_key"],
                        provider=key.get("provider", "groq"),
                        lang=request.form.get("lang", "en"))
                    if ak and len(ak) > 0:
                        if "_error" in ak:
                            answer_key_error = ak["_error"]
                        else:
                            answer_key = ak
                            answer_key_generated = True
                            current_app.logger.info("Answer key generated: %d answers", len(ak))
                    else:
                        current_app.logger.warning("Answer key returned empty")
                else:
                    answer_key_error = "Belum ada API key aktif. Atur di Pengaturan AI."
            except Exception as e:
                answer_key_error = f"Gagal: {str(e)[:100]}"
                current_app.logger.error("Answer key gen error: %s", e, exc_info=True)
        else:
            # Manual mode: no AI, just empty questions
            questions = parsed.get("questions", [])
            parsed["questions"] = questions
            parsed["mcq_count"] = 0
            parsed["essay_count"] = 0

        # Step 4: Save PDF for exam canvas
        pdf_url = ""
        try:
            import uuid
            upload_dir = os.path.join(current_app.root_path, "static", "uploads", "exams")
            os.makedirs(upload_dir, exist_ok=True)
            pdf_filename = f"temp_{str(uuid.uuid4())[:12]}.pdf"
            pdf_path = os.path.join(upload_dir, pdf_filename)
            with open(pdf_path, "wb") as f:
                f.write(raw)
            pdf_url = f"/static/uploads/exams/{pdf_filename}"
        except Exception as e:
            current_app.logger.warning("PDF save skipped: %s", e)

        # Step 4: Generate rubrics for essay questions
        lang = request.form.get("lang", "en")
        for q in questions or []:
            if q.get("type") == "essay":
                try:
                    from app.services.rubric_generator import generate_rubric
                    q["rubric"] = generate_rubric(q.get("text", ""), lang=lang)
                except Exception as e:
                    current_app.logger.warning("Rubric skipped: %s", e)

        preview = generate_preview_html(parsed)
        return jsonify({
            "success": True,
            "ai_classified": ai_used,
            "answer_key_generated": answer_key_generated,
            "answer_key": answer_key,
            "answer_key_error": answer_key_error,
            "markdown": parsed["markdown"][:500000],
            "page_count": parsed["page_count"],
            "mcq_count": parsed["mcq_count"],
            "essay_count": parsed["essay_count"],
            "questions": questions,
            "preview_html": preview,
            "pdf_url": pdf_url,
            "pdf_id": pdf_url.split("/")[-1].replace(".pdf", "").replace("temp_", "") if pdf_url else "",
        })
    except Exception as e:
        current_app.logger.error("parse-pdf error: %s", e, exc_info=True)
        return jsonify({"error": f"Gagal memproses PDF: {str(e)[:200]}"}), 500


@teacher_bp.route("/exams/generate-key", methods=["POST"])
@teacher_or_admin_required
def exam_generate_key():
    """Generate answer key for already-uploaded PDF (on-demand). Also classifies if needed."""
    from app.services.pdf_parser import generate_answer_key, classify_heuristic
    data = request.get_json() or {}
    markdown = data.get("markdown", "")
    questions = data.get("questions", [])
    lang = data.get("lang", "en")
    if not markdown:
        return jsonify({"error": "No markdown provided"}), 400

    # Classify questions if not provided
    if not questions:
        questions = classify_heuristic(markdown)
        if not questions:
            return jsonify({"error": "Tidak dapat mendeteksi soal dari PDF"}), 422

    from app.services.ai_service import _get_active_key
    key = _get_active_key(g.user_id)
    if not key or not key.get("api_key"):
        return jsonify({"error": "Belum ada API key aktif. Atur di Pengaturan AI."}), 400

    try:
        ak = generate_answer_key(markdown, questions, api_key=key["api_key"], provider=key.get("provider", "groq"), lang=lang)
        if ak and "_error" in ak:
            return jsonify({"error": ak["_error"]}), 429
        if ak and len(ak) > 0:
            return jsonify({"success": True, "answer_key": ak, "answer_key_generated": True, "questions": questions})
        current_app.logger.error("generate_answer_key returned empty for user %s. questions=%d, markdown=%d chars, provider=%s",
                                 g.user_id, len(questions), len(markdown), key.get("provider", "groq"))
    except Exception as e:
        current_app.logger.error("generate_answer_key exception: %s", str(e)[:500])
        return jsonify({"error": str(e)[:200]}), 500
    return jsonify({"error": "Gagal generate key"}), 500


@teacher_bp.route("/exams/parse-pdf/markdown", methods=["POST"])
@teacher_or_admin_required
def exam_pdf_markdown():
    """Return PDF as clean markdown text for download."""
    if "pdf" not in request.files:
        return jsonify({"error": "No PDF"}), 400
    raw = request.files["pdf"].read()
    from app.services.pdf_parser import pdf_to_markdown
    result = pdf_to_markdown(raw)
    if result.get("error"):
        return jsonify({"error": result["error"]}), 422
    return result["markdown"], 200, {"Content-Type": "text/markdown; charset=utf-8"}


@teacher_bp.route("/exams/export-scan", methods=["POST"])
@teacher_or_admin_required
def export_scan_results():
    """Export AI scan results as XLSX."""
    data = request.get_json() or {}
    questions = data.get("questions", [])
    fmt = data.get("format", "xlsx")

    if not questions:
        return jsonify({"error": "Tidak ada data"}), 400

    if fmt == "xlsx":
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Scan AI"

        header_font = Font(bold=True, color="FFFFFF", size=11)
        header_fill = PatternFill(start_color="4338CA", end_color="4338CA", fill_type="solid")
        thin = Border(left=Side(style="thin"), right=Side(style="thin"), top=Side(style="thin"), bottom=Side(style="thin"))

        headers = ["No", "Tipe", "Teks Soal", "Rubrik"]
        for col, h in enumerate(headers, 1):
            c = ws.cell(row=1, column=col, value=h)
            c.font = header_font; c.fill = header_fill; c.alignment = Alignment(horizontal="center"); c.border = thin

        for i, q in enumerate(questions, 1):
            rubric_text = ""
            if q.get("rubric"):
                rubric_text = "; ".join(f"{r.get('kriteria','')} ({r.get('bobot',0)}%)" for r in q["rubric"])
            kind = question_kind(q.get("type"))
            row = [i, _QUESTION_KIND_LABELS.get(kind, "Essay"), q.get("text", ""), rubric_text]
            for col, val in enumerate(row, 1):
                c = ws.cell(row=i + 1, column=col, value=val)
                c.border = thin
                c.alignment = Alignment(wrap_text=True, vertical="top")

        ws.column_dimensions["A"].width = 5
        ws.column_dimensions["B"].width = 10
        ws.column_dimensions["C"].width = 60
        ws.column_dimensions["D"].width = 50

        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        return send_file(buf, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        as_attachment=True, download_name="scan_ai.xlsx")

    return jsonify({"error": "Format tidak didukung"}), 400


@teacher_bp.route("/exams/new", methods=["GET", "POST"])
@subscription_write_required
@teacher_or_admin_required
@require_subscription("create_exam")
def exam_form():
    supabase = get_supabase()
    if request.method == "GET":
        supabase = get_supabase()
        sid = g.get("user_school_id")
        subjects = []
        classes = []
        if sid:
            # Get teacher's assigned subjects and classes
            try:
                teacher_assignments = supabase.table("teacher_assignments") \
                    .select("*, subjects(id, name, code), classes(id, name, grade_level)") \
                    .eq("teacher_id", g.user_id) \
                    .execute().data or []
                for a in teacher_assignments:
                    if a.get("subjects"):
                        if a["subjects"] not in subjects:
                            subjects.append(a["subjects"])
                    if a.get("classes"):
                        if a["classes"] not in classes:
                            classes.append(a["classes"])
            except Exception:
                current_app.logger.warning("Failed to fetch teacher assignments, falling back to all")
            # Fallback: for admin/super_admin, show all subjects; for guru, empty list
            if not subjects:
                subjects = supabase.table("subjects").select("*").eq("school_id", sid).order("name").execute().data or []
            if not classes:
                classes = supabase.table("classes").select("*").eq("school_id", sid).order("name").execute().data or []
        return render_template("teacher/exam_form.html", exam=None, subjects=subjects, classes=classes)

    title = request.form.get("title")
    subject = request.form.get("subject")
    subject_id = request.form.get("subject_id") or None
    class_ids = request.form.getlist("class_ids")
    is_template = request.form.get("is_template", "false") == "true"
    source_exam_id = request.form.get("source_exam_id") or None
    max_attempts = int(request.form.get("max_attempts", 1))
    publish_mode = request.form.get("publish_mode", "manual")
    total_questions = int(request.form.get("total_questions", 10))
    if total_questions < 1:
        flash("Minimal 1 soal", "error")
        return redirect(request.referrer or "/teacher/exams")
    duration_minutes = int(request.form.get("duration_minutes", 60))
    passing_score = int(request.form.get("passing_score", 70))
    description = request.form.get("description", "")
    action = request.form.get("action", "save_draft")
    # Both ends of the window go through one converter, so the start and the end
    # cannot end up on different clocks — the one mistake here that would move a
    # deadline by seven hours without anything looking wrong.
    tz_off = g.get("tz_offset", 7)
    if action == "publish":
        start_at = None
    else:
        start_at = exam_window.to_utc_iso(request.form.get("start_at", ""), tz_off)
    # The assignment window end: the last instant a student may *begin*. The
    # duration is counted from each student's own start and may run past it (the
    # form says so on the field); `auto_submit_on_window_end` is the switch that
    # makes the window end the deadline instead.
    end_at = exam_window.to_utc_iso(request.form.get("end_at", ""), tz_off)
    auto_submit_on_window_end = request.form.get("auto_submit_on_window_end") == "true"
    _start_dt, _end_dt = exam_window.parse_dt(start_at), exam_window.parse_dt(end_at)
    if _start_dt and _end_dt and _end_dt <= _start_dt:
        flash("Batas akhir ujian harus setelah waktu mulai", "error")
        return redirect(request.referrer or "/teacher/exams")

    question_types = json.loads(request.form.get("question_types", "{}"))
    answer_key = json.loads(request.form.get("answer_key", "{}"))
    question_weights = json.loads(request.form.get("question_weights", "{}"))
    # The marks a paper is scored by are decided by _apply_mark_scheme, never by
    # whatever the page posted (it posts a preview of the same numbers).
    question_weights = _apply_mark_scheme(question_types, total_questions, question_weights)
    question_audio = {}
    question_canvas = {}
    anti_cheat_enabled = True
    penalty_per_violation = int(request.form.get("penalty_per_violation", 5))
    max_violations = int(request.form.get("max_violations", 5))
    auto_submit_on_max = request.form.get("auto_submit_on_max") == "true"
    fullscreen_required = request.form.get("fullscreen_required") == "true"
    randomize_questions = request.form.get("randomize_questions", "false") == "true"
    randomize_options = request.form.get("randomize_options", "false") == "true"
    watermark_name = request.form.get("watermark_name") == "true"
    block_copy_paste = request.form.get("block_copy_paste") == "true"
    block_right_click = request.form.get("block_right_click") == "true"
    block_screenshot = request.form.get("block_screenshot", "false") == "true"
    allow_calculator = request.form.get("allow_calculator", "false") == "true"
    for i in range(total_questions):
        qtype = question_types.get(str(i))
        # A canvas overlay is for a question the student answers by writing on the
        # paper. That is the essay family, and only the essay family — the three
        # new objective types are answered with a tap, and painting a canvas over
        # their part of the paper would cover the question.
        if is_essay(qtype):
            question_canvas[str(i)] = True
        audio_url = request.form.get(f"audio_{i}", "").strip()
        youtube_url = request.form.get(f"youtube_{i}", "").strip()
        media = {}
        if audio_url:
            media["audio"] = audio_url
        if youtube_url:
            media["youtube"] = youtube_url
        if media:
            question_audio[str(i)] = media

    data = {
        "teacher_id": g.user_id,
        "school_id": g.get("user_school_id"),
        "title": title,
        "subject": subject,
        "subject_id": subject_id,
        "class_ids": class_ids,
        "start_at": start_at,
        "end_at": end_at,
        "auto_submit_on_window_end": auto_submit_on_window_end,
        "is_template": is_template,
        "source_exam_id": source_exam_id,
        "max_attempts": max_attempts,
        "publish_mode": publish_mode,
        "question_pages": request.form.get("question_pages", "{}"),
        "total_questions": total_questions,
        "duration_minutes": duration_minutes,
        "passing_score": passing_score,
        "description": description,
        "status": "active" if action in ("save_active", "publish") else "draft",
        "is_published": action == "publish",
        "publish_mode": "auto" if action == "publish" else publish_mode,
        "answer_key": answer_key,
        "question_types": question_types,
        "question_weights": question_weights,
        "question_audio": question_audio,
        "question_canvas": question_canvas,
        "anti_cheat_enabled": anti_cheat_enabled,
        "penalty_per_violation": penalty_per_violation,
        "max_violations": max_violations,
        "auto_submit_on_max": auto_submit_on_max,
        "fullscreen_required": fullscreen_required,
        "randomize_questions": randomize_questions,
        "randomize_options": randomize_options,
        "watermark_name": watermark_name,
        "block_copy_paste": block_copy_paste,
        "block_right_click": block_right_click,
        "block_screenshot": block_screenshot,
        "allow_calculator": allow_calculator,
    }
    try:
        res = supabase.table("exams").insert(data).execute()
    except Exception:
        for key in ["question_weights", "question_texts", "anti_cheat_enabled", "penalty_per_violation", "max_violations", "auto_submit_on_max", "fullscreen_required", "randomize_questions", "randomize_options", "watermark_name", "block_copy_paste", "block_right_click", "block_screenshot", "allow_calculator", "subject_id", "class_ids", "start_at", "end_at", "auto_submit_on_window_end", "is_template", "source_exam_id", "max_attempts", "publish_mode", "question_pages"]:
            data.pop(key, None)
        res = supabase.table("exams").insert(data).execute()
    exam_id = res.data[0]["id"]
    log_activity("create", "exam", exam_id, new_data={"title": title, "subject": subject, "total_questions": total_questions}, user_id=g.user_id)
    # Handle PDF upload inline
    pdf_file = request.files.get("pdf")
    if pdf_file and pdf_file.filename:
        try:
            from app.services.pdf_service import upload_pdf
            result = upload_pdf(pdf_file, exam_id)
            supabase.table("exams").update({
                "pdf_url": result["pdf_path"],
                "pdf_page_urls": result["page_urls"],
            }).eq("id", exam_id).execute()
        except Exception as e:
            current_app.logger.error(f"PDF upload failed: {e}")
    # Handle AJAX-uploaded PDF via pdf_preview_url
    pdf_preview = request.form.get("pdf_preview_url", "")
    if pdf_preview and not pdf_preview.startswith("http") and not (pdf_file and pdf_file.filename):
        try:
            local_path = os.path.join(current_app.root_path, "static", "uploads", "exams", os.path.basename(pdf_preview))
            pdf_bytes = None
            if os.path.exists(local_path):
                with open(local_path, "rb") as f:
                    pdf_bytes = f.read()
            else:
                # Try alternative: maybe root_path is project root, not app/
                alt_path = os.path.join(os.path.dirname(current_app.root_path), "static", "uploads", "exams", os.path.basename(pdf_preview))
                if os.path.exists(alt_path):
                    with open(alt_path, "rb") as f:
                        pdf_bytes = f.read()
            if pdf_bytes:
                from app.services.pdf_service import upload_pdf
                class _MF:
                    def __init__(self, d, n): self._d = d; self.filename = n
                    def read(self): return self._d
                result = upload_pdf(_MF(pdf_bytes, "exam.pdf"), exam_id)
                supabase.table("exams").update({
                    "pdf_url": result["pdf_path"],
                    "pdf_page_urls": result["page_urls"],
                }).eq("id", exam_id).execute()
                current_app.logger.info("PDF processed for exam %s: %d pages", exam_id, result["total_pages"])
            else:
                current_app.logger.warning("PDF temp file not found for exam %s: %s", exam_id, pdf_preview)
        except Exception as e:
            current_app.logger.warning(f"PDF preview processing failed: {e}")
            flash("PDF gagal diproses untuk canvas siswa. Upload ulang PDF setelah menyimpan.", "warning")
    # If action is publish, also publish scores automatically
    if action == "publish":
        try:
            _recalculate_scores(exam_id)
            supabase.table("submissions").update({"is_published": True, "status": "published"}).eq("exam_id", exam_id).execute()
        except Exception:
            pass
        flash("✅ Ujian berhasil dipublikasikan! Siswa sekarang bisa mengerjakan.", "success")
    else:
        flash("✅ Ujian berhasil disimpan.", "success")
    # A new exam is a new row on the dashboard, including its own "no answer key"
    # card if it has MCQ questions.
    _invalidate_teacher_dashboard()
    return redirect("/teacher/exams" if action == "publish" else f"/teacher/exams/{exam_id}")


@teacher_bp.route("/exams/<exam_id>", methods=["GET", "POST", "DELETE"])
@subscription_write_required
@teacher_or_admin_required
def exam_detail(exam_id):
    supabase = get_supabase()
    # This route edits AND deletes, and DELETE here bypassed the check added to
    # /exams/<id>/delete — require_school_access alone let any teacher at the
    # school remove a colleague's exam. Owner or school admin only.
    exam_row, err = _guard_exam(supabase, exam_id, columns="*", as_json=_wants_json())
    if err:
        return err
    if request.method == "DELETE":
        supabase.table("exams").delete().eq("id", exam_id).execute()
        _invalidate_teacher_dashboard()
        return jsonify({"success": True})
    if request.method == "GET":
        exam_data = _normalise_exam_json(exam_row)
        exam_data.setdefault("question_weights", {})
        sid = g.get("user_school_id")
        subjects = []
        classes = []
        if sid:
            subjects = supabase.table("subjects").select("*").eq("school_id", sid).order("name").execute().data or []
            classes = supabase.table("classes").select("*").eq("school_id", sid).order("name").execute().data or []
        return render_template("teacher/exam_form.html", exam=exam_data, subjects=subjects, classes=classes)

    title = request.form.get("title")
    subject = request.form.get("subject")
    subject_id = request.form.get("subject_id") or None
    class_ids = request.form.getlist("class_ids")
    is_template = request.form.get("is_template", "false") == "true"
    source_exam_id = request.form.get("source_exam_id") or None
    max_attempts = int(request.form.get("max_attempts", 1))
    publish_mode = request.form.get("publish_mode", "manual")
    total_questions = int(request.form.get("total_questions", 10))
    if total_questions < 1:
        flash("Minimal 1 soal", "error")
        return redirect(request.referrer or "/teacher/exams")
    duration_minutes = int(request.form.get("duration_minutes", 60))
    passing_score = int(request.form.get("passing_score", 70))
    description = request.form.get("description", "")
    action = request.form.get("action", "save_draft")
    # Both ends of the window go through one converter, so the start and the end
    # cannot end up on different clocks — the one mistake here that would move a
    # deadline by seven hours without anything looking wrong.
    tz_off = g.get("tz_offset", 7)
    if action == "publish":
        start_at = None
    else:
        start_at = exam_window.to_utc_iso(request.form.get("start_at", ""), tz_off)
    # The assignment window end: the last instant a student may *begin*. The
    # duration is counted from each student's own start and may run past it (the
    # form says so on the field); `auto_submit_on_window_end` is the switch that
    # makes the window end the deadline instead.
    end_at = exam_window.to_utc_iso(request.form.get("end_at", ""), tz_off)
    auto_submit_on_window_end = request.form.get("auto_submit_on_window_end") == "true"
    _start_dt, _end_dt = exam_window.parse_dt(start_at), exam_window.parse_dt(end_at)
    if _start_dt and _end_dt and _end_dt <= _start_dt:
        flash("Batas akhir ujian harus setelah waktu mulai", "error")
        return redirect(request.referrer or "/teacher/exams")

    question_types = json.loads(request.form.get("question_types", "{}"))
    answer_key = json.loads(request.form.get("answer_key", "{}"))
    question_weights = json.loads(request.form.get("question_weights", "{}"))
    # The marks a paper is scored by are decided by _apply_mark_scheme, never by
    # whatever the page posted (it posts a preview of the same numbers).
    question_weights = _apply_mark_scheme(question_types, total_questions, question_weights)
    question_audio = {}
    question_canvas = {}
    anti_cheat_enabled = True
    penalty_per_violation = int(request.form.get("penalty_per_violation", 5))
    max_violations = int(request.form.get("max_violations", 5))
    auto_submit_on_max = request.form.get("auto_submit_on_max") == "true"
    fullscreen_required = request.form.get("fullscreen_required") == "true"
    randomize_questions = request.form.get("randomize_questions", "false") == "true"
    randomize_options = request.form.get("randomize_options", "false") == "true"
    watermark_name = request.form.get("watermark_name") == "true"
    block_copy_paste = request.form.get("block_copy_paste") == "true"
    block_right_click = request.form.get("block_right_click") == "true"
    block_screenshot = request.form.get("block_screenshot", "false") == "true"
    allow_calculator = request.form.get("allow_calculator", "false") == "true"
    for i in range(total_questions):
        qtype = question_types.get(str(i))
        # A canvas overlay is for a question the student answers by writing on the
        # paper. That is the essay family, and only the essay family — the three
        # new objective types are answered with a tap, and painting a canvas over
        # their part of the paper would cover the question.
        if is_essay(qtype):
            question_canvas[str(i)] = True
        audio_url = request.form.get(f"audio_{i}", "").strip()
        youtube_url = request.form.get(f"youtube_{i}", "").strip()
        media = {}
        if audio_url:
            media["audio"] = audio_url
        if youtube_url:
            media["youtube"] = youtube_url
        if media:
            question_audio[str(i)] = media

    data = {
        "teacher_id": g.user_id,
        "school_id": g.get("user_school_id"),
        "title": title,
        "subject": subject,
        "subject_id": subject_id,
        "class_ids": class_ids,
        "start_at": start_at,
        "end_at": end_at,
        "auto_submit_on_window_end": auto_submit_on_window_end,
        "is_template": is_template,
        "source_exam_id": source_exam_id,
        "max_attempts": max_attempts,
        "publish_mode": publish_mode,
        "question_pages": request.form.get("question_pages", "{}"),
        "total_questions": total_questions,
        "duration_minutes": duration_minutes,
        "passing_score": passing_score,
        "description": description,
        "status": "active" if action in ("save_active", "publish") else "draft",
        "is_published": action == "publish",
        "publish_mode": "auto" if action == "publish" else publish_mode,
        "answer_key": answer_key,
        "question_types": question_types,
        "question_weights": question_weights,
        "question_audio": question_audio,
        "question_canvas": question_canvas,
        "anti_cheat_enabled": anti_cheat_enabled,
        "penalty_per_violation": penalty_per_violation,
        "max_violations": max_violations,
        "auto_submit_on_max": auto_submit_on_max,
        "fullscreen_required": fullscreen_required,
        "randomize_questions": randomize_questions,
        "randomize_options": randomize_options,
        "watermark_name": watermark_name,
        "block_copy_paste": block_copy_paste,
        "block_right_click": block_right_click,
        "block_screenshot": block_screenshot,
        "allow_calculator": allow_calculator,
    }
    try:
        supabase.table("exams").update(data).eq("id", exam_id).execute()
    except Exception:
        for key in ["question_weights", "question_texts", "anti_cheat_enabled", "penalty_per_violation", "max_violations", "auto_submit_on_max", "fullscreen_required", "randomize_questions", "randomize_options", "watermark_name", "block_copy_paste", "block_right_click", "block_screenshot", "allow_calculator", "subject_id", "class_ids", "start_at", "end_at", "auto_submit_on_window_end", "is_template", "source_exam_id", "max_attempts", "publish_mode", "question_pages"]:
            data.pop(key, None)
        supabase.table("exams").update(data).eq("id", exam_id).execute()

    # Process PDF: upload to Supabase, generate page images for student canvas
    pdf_preview = request.form.get("pdf_preview_url", "")
    if pdf_preview and not pdf_preview.startswith("http"):
        try:
            local_path = os.path.join(current_app.root_path, "static", "uploads", "exams", os.path.basename(pdf_preview))
            pdf_bytes = None
            if os.path.exists(local_path):
                with open(local_path, "rb") as f:
                    pdf_bytes = f.read()
            else:
                alt_path = os.path.join(os.path.dirname(current_app.root_path), "static", "uploads", "exams", os.path.basename(pdf_preview))
                if os.path.exists(alt_path):
                    with open(alt_path, "rb") as f:
                        pdf_bytes = f.read()
            if pdf_bytes:
                from app.services.pdf_service import upload_pdf
                class MockFile:
                    def __init__(self, data, name):
                        self._data = data
                        self.filename = name
                    def read(self): return self._data
                result = upload_pdf(MockFile(pdf_bytes, "exam.pdf"), exam_id)
                supabase.table("exams").update({
                    "pdf_url": result["pdf_path"],
                    "pdf_page_urls": result["page_urls"],
                }).eq("id", exam_id).execute()
                current_app.logger.info("PDF processed for exam %s: %d pages", exam_id, result["total_pages"])
            else:
                current_app.logger.warning("PDF temp file not found for exam %s: %s", exam_id, pdf_preview)
        except Exception as e:
            current_app.logger.warning("PDF processing failed for exam %s: %s", exam_id, e)

    _recalculate_scores(exam_id)
    log_activity("update", "exam", exam_id, new_data={"title": title, "status": data.get("status")}, user_id=g.user_id)
    # Everything the dashboard shows about this exam just changed — its key, its
    # question types, its status — and that page is cached for 20 seconds.
    _invalidate_teacher_dashboard()
    return redirect("/teacher/exams" if action in ("publish", "save_active") else f"/teacher/exams/{exam_id}")


@teacher_bp.route("/exams/<exam_id>/preprocess-essays", methods=["POST"])
@teacher_or_admin_required
@require_school_access("exams", "exam_id")
def preprocess_exam_essays(exam_id):
    """Generate embeddings + rubric for all essay questions in an exam."""
    supabase = get_supabase()
    exam = supabase.table("exams").select("question_types,question_texts,question_rubrics,total_questions").eq("id", exam_id).single().execute().data
    if not exam:
        return jsonify({"error": "Exam not found"}), 404

    qtypes = exam.get("question_types") or {}
    total_q = exam.get("total_questions", 0)
    from app.services.ai_embedding import preprocess_exam_questions
    from app.services.rubric_generator import generate_rubric

    # Build questions list
    questions = []
    question_texts = exam.get("question_texts") or {}
    for i in range(total_q):
        qi = str(i)
        if is_essay(qtypes.get(qi)):
            text = question_texts.get(qi, "")
            rubric = generate_rubric(text)
            questions.append({"number": i + 1, "type": essay_marker(qtypes.get(qi)), "text": text, "rubric": rubric})

    result = preprocess_exam_questions(exam_id, questions, supabase)
    return jsonify({"success": True, **result})


@teacher_bp.route("/preview/<exam_id>")
@teacher_or_admin_required
@require_school_access("exams", "exam_id")
def preview_exam(exam_id):
    supabase = get_supabase()
    res = row_or_none(supabase.table("exams").select("*").eq("id", exam_id).maybe_single().execute())
    if not res:
        return redirect("/teacher/exams")
    return render_template("teacher/preview_exam.html", exam=res)


@teacher_bp.route("/exams/<exam_id>/publish-exam", methods=["POST"])
@subscription_write_required
@teacher_or_admin_required
@require_school_access("exams", "exam_id")
def publish_exam(exam_id):
    supabase = get_supabase()
    supabase.table("exams").update({
        "is_published": True,
        "status": "active",
    }).eq("id", exam_id).execute()
    log_activity("publish", "exam", exam_id, user_id=g.user_id)
    return redirect(f"/teacher/preview/{exam_id}")


@teacher_bp.route("/exams/<exam_id>/upload-pdf", methods=["GET", "POST"])
@subscription_write_required
@teacher_or_admin_required
@require_school_access("exams", "exam_id")
def upload_exam_pdf(exam_id):
    supabase = get_supabase()
    if request.method == "GET":
        exam = supabase.table("exams").select("*").eq("id", exam_id).single().execute().data
        return render_template("teacher/upload_pdf.html", exam=exam)

    if "pdf" not in request.files:
        exam = supabase.table("exams").select("*").eq("id", exam_id).single().execute().data
        return render_template("teacher/upload_pdf.html", exam=exam, error="Pilih file PDF")

    file = request.files["pdf"]
    try:
        result = upload_pdf(file, exam_id)
    except ValueError as e:
        exam = supabase.table("exams").select("*").eq("id", exam_id).single().execute().data
        return render_template("teacher/upload_pdf.html", exam=exam, error=str(e))
    supabase.table("exams").update({
        "pdf_url": result["pdf_path"],
        "pdf_page_urls": result["page_urls"],
    }).eq("id", exam_id).execute()
    log_activity("upload", "exam", exam_id, new_data={"pages": len(result.get("page_urls", []))}, user_id=g.user_id)
    return redirect(f"/teacher/preview/{exam_id}")


@teacher_bp.route("/exams")
@teacher_or_admin_required
def my_exams():
    supabase = get_supabase()
    # A teacher sees their own exams; an admin sees the school's. Previously this
    # filtered by teacher_id for everyone, so an admin_sekolah — who is allowed on
    # this page — got a permanently empty list, with no clue why.
    query = supabase.table("exams").select("*").eq("teacher_id", g.user_id)
    if g.get("user_role") == "admin_sekolah" and g.get("user_school_id"):
        query = supabase.table("exams").select("*").eq("school_id", g.get("user_school_id"))
    res = query.order("created_at", desc=True).execute()
    exams = res.data or []
    # Which cards get the "no class" badge. Computed here rather than in the
    # template because `class_ids` arrives as jsonb — a JSON *string* is truthy in
    # Jinja, so `{% if not exam.class_ids %}` would miss exactly the empty case it
    # is there to catch.
    unassigned_ids = {e["id"] for e in exams if _needs_class_assignment(e)}
    return render_template("teacher/exams.html", exams=exams, unassigned_ids=unassigned_ids)


@teacher_bp.route("/exams/<exam_id>/toggle-status", methods=["POST"])
@subscription_write_required
@teacher_or_admin_required
def toggle_exam_status(exam_id):
    supabase = get_supabase()
    # require_school_access let a colleague in the same school deactivate an exam —
    # mid-session, with students still working. Owner or school admin only now, and
    # the status comes back with the permission check, so it is still one query.
    exam, err = _guard_exam(supabase, exam_id, columns="id,teacher_id,school_id,status",
                            as_json=_wants_json())
    if err:
        return err
    new_status = "draft" if exam["status"] == "active" else "active"
    supabase.table("exams").update({"status": new_status}).eq("id", exam_id).execute()
    _invalidate_teacher_dashboard()
    if request.headers.get("Accept", "") == "application/json" or request.is_json:
        return jsonify({"success": True, "status": new_status})
    return redirect(request.referrer or "/teacher/exams")


@teacher_bp.route("/exams/<exam_id>/toggle-visibility", methods=["POST"])
@subscription_write_required
@teacher_or_admin_required
def toggle_exam_visibility(exam_id):
    supabase = get_supabase()
    # Withdrawing an exam from students is equally consequential, so it follows the
    # same rule as status — and reuses the row the check already loaded.
    exam, err = _guard_exam(supabase, exam_id,
                            columns="id,teacher_id,school_id,is_published",
                            as_json=_wants_json())
    if err:
        return err
    new_val = not exam["is_published"]
    supabase.table("exams").update({"is_published": new_val}).eq("id", exam_id).execute()
    _invalidate_teacher_dashboard()
    if request.headers.get("Accept", "") == "application/json" or request.is_json:
        return jsonify({"success": True, "is_published": new_val})
    return redirect(request.referrer or "/teacher/exams")


@teacher_bp.route("/exams/<exam_id>/delete", methods=["POST"])
@subscription_write_required
@teacher_or_admin_required
def delete_exam(exam_id):
    supabase = get_supabase()
    # This cascades: violations, access codes, analytics cache, every submission,
    # then the exam. require_school_access meant any teacher at the school could
    # erase a colleague's whole assessment. Owner or school admin only.
    _, err = _guard_exam(supabase, exam_id, as_json=_wants_json())
    if err:
        return err
    supabase.table("violation_logs").delete().eq("exam_id", exam_id).execute()
    supabase.table("exam_access_codes").delete().eq("exam_id", exam_id).execute()
    supabase.table("analytics_cache").delete().eq("exam_id", exam_id).execute()
    supabase.table("submissions").delete().eq("exam_id", exam_id).execute()
    supabase.table("exams").delete().eq("id", exam_id).execute()
    _invalidate_teacher_dashboard()
    log_activity("delete", "exam", exam_id, user_id=g.user_id)
    if request.headers.get("Accept", "") == "application/json" or request.is_json:
        return jsonify({"success": True})
    return redirect("/teacher/exams")


@teacher_bp.route("/exams/<exam_id>/duplicate", methods=["POST"])
@subscription_write_required
@teacher_or_admin_required
def duplicate_exam(exam_id):
    supabase = get_supabase()
    # Copies the exam including its answer key, so it follows the same rule and
    # reuses the row the permission check loaded.
    src_exam, err = _guard_exam(supabase, exam_id, columns="*", as_json=_wants_json(),
                                redirect_to="/teacher/exams")
    if err:
        return err
    try:
        exam = src_exam
        import copy, uuid
        new_data = {k: v for k, v in exam.items() if k not in ("id", "created_at", "updated_at")}
        new_data["title"] = exam["title"] + " (salinan)"
        new_data["status"] = "draft"
        new_data["is_published"] = False
        new_data["is_template"] = False
        new_data["source_exam_id"] = exam_id
        new_exam = supabase.table("exams").insert(new_data).execute()
        new_id = new_exam.data[0]["id"]
        log_activity("duplicate", "exam", new_id, new_data={"source": exam_id, "title": new_data["title"]}, user_id=g.user_id)
        flash("Ujian berhasil digandakan. Silakan edit sesuai kebutuhan.", "success")
        return redirect(f"/teacher/exams/{new_id}")
    except Exception as e:
        flash(f"Gagal menggandakan: {str(e)[:60]}", "error")
        return redirect("/teacher/exams")


@teacher_bp.route("/exams/<exam_id>/answer-keys", methods=["GET", "POST"])
@teacher_or_admin_required
def answer_keys(exam_id):
    supabase = get_supabase()
    # This page IS the answer key, so it follows the same rule as the grading
    # queue, and reuses the row the check loaded.
    exam, err = _guard_exam(supabase, exam_id, columns="*", as_json=_wants_json())
    if err:
        return err
    if not exam:
        flash("Ujian tidak ditemukan", "error")
        return redirect("/teacher/exams")

    # Parse JSON fields
    for fld in ("answer_key", "question_types"):
        v = exam.get(fld)
        if isinstance(v, str):
            try: exam[fld] = json.loads(v)
            except: exam[fld] = {}

    if request.method == "POST":
        answer_key = request.form.get("answer_key", "{}")
        try:
            answer_key = json.loads(answer_key)
        except json.JSONDecodeError:
            answer_key = {}
        qtypes = exam.get("question_types", {}) or {}
        stored = exam.get("answer_key", {}) or {}
        if isinstance(stored, str):
            try:
                stored = json.loads(stored)
            except (json.JSONDecodeError, TypeError):
                stored = {}
        # This page edits choice keys. It used to write `essay` over every other
        # question it did not understand — which is now a matching question's pairs
        # and the sequence a drag & drop or ordering question asks for — so saving a
        # letter here destroyed a key the builder had set. Merge instead, and keep
        # what the form did not have a control for.
        merged = dict(stored)
        for k, v in answer_key.items():
            qtype = qtypes.get(str(k))
            if is_objective(qtype) and question_kind(qtype) != KIND_CHOICE:
                continue
            merged[str(k)] = normalise_key(qtype, v)
        answer_key = merged
        supabase.table("exams").update({"answer_key": json.dumps(answer_key)}).eq("id", exam_id).execute()
        # Recalculate scores
        try:
            from app.routes.teacher import _recalculate_scores
            _recalculate_scores(exam_id)
        except Exception:
            pass
        # The dashboard's warning is about this key, and that page is cached.
        _invalidate_teacher_dashboard()
        flash("Kunci jawaban berhasil disimpan & nilai diperbarui!", "success")
        return redirect(f"/teacher/exams/{exam_id}/answer-keys")

    # GET: build question list from question_types
    qtypes = exam.get("question_types", {})
    akey = exam.get("answer_key", {})
    questions = []
    for i in sorted(qtypes.keys(), key=int):
        idx = str(i)
        qtype = qtypes[idx]
        k = akey.get(idx)
        questions.append({
            "index": int(idx),
            "type": qtype,
            # The family and the readable key are computed here so the page never
            # has to compare a type name to decide what it is looking at.
            "kind": question_kind(qtype),
            "answer_text": describe_answer(qtype, k),
            "key": k,
            "is_bonus": k == "bonus",
            "is_multi": isinstance(k, list),
        })

    return render_template("teacher/answer_keys.html", exam=exam, questions=questions)


@teacher_bp.route("/scan")
@teacher_or_admin_required
def scan_page():
    supabase = get_supabase()
    res = supabase.table("exams").select("*").eq("teacher_id", g.user_id).execute()
    students = supabase.table("profiles").select("id,full_name,phone").eq("role", "murid").execute()
    return render_template("teacher/scan.html", exams=res.data, students=students.data)


@teacher_bp.route("/retractions")
@teacher_or_admin_required
def retraction_requests():
    supabase = get_supabase()
    exam_ids = [e["id"] for e in supabase.table("exams").select("id").eq("teacher_id", g.user_id).execute().data or []]
    requests = []
    if exam_ids:
        subs = supabase.table("submissions").select("id,student_id,exam_id,answers,submitted_at,exams(title),profiles(full_name)").in_("exam_id", exam_ids).execute().data or []
        for s in subs:
            answers = s.get("answers")
            if isinstance(answers, str):
                try:
                    answers = json.loads(answers)
                except (json.JSONDecodeError, TypeError):
                    answers = {}
            if isinstance(answers, dict) and answers.get("_retract_request", {}).get("status") == "pending":
                s["exam_title"] = (s.get("exams") or {}).get("title", "-")
                s["student_name"] = (s.get("profiles") or {}).get("full_name", "-")
                s["requested_at"] = answers["_retract_request"].get("requested_at", "")
                requests.append(s)
    return render_template("teacher/retractions.html", requests=requests)


@teacher_bp.route("/retractions/<submission_id>/approve", methods=["POST"])
@teacher_or_admin_required
@require_school_access("submissions", "submission_id", ("exam_id", "exams"))
def approve_retraction(submission_id):
    supabase = get_supabase()
    sub = supabase.table("submissions").select("answers").eq("id", submission_id).single().execute().data
    if not sub:
        flash("Submission tidak ditemukan", "error")
        return redirect(url_for("teacher.retraction_requests"))
    answers = sub.get("answers")
    if isinstance(answers, str):
        try:
            answers = json.loads(answers)
        except (json.JSONDecodeError, TypeError):
            answers = {}
    if not isinstance(answers, dict) or "_retract_request" not in answers or not isinstance(answers["_retract_request"], dict):
        flash("Tidak ada permintaan retraction", "error")
        return redirect(url_for("teacher.retraction_requests"))
    answers["_retract_request"]["status"] = "approved"
    try:
        supabase.table("submissions").update({"answers": json.dumps(answers), "status": "retracted"}).eq("id", submission_id).execute()
    except Exception:
        supabase.table("submissions").update({"answers": json.dumps(answers)}).eq("id", submission_id).execute()
    log_activity("retract_approve", "submission", submission_id, user_id=g.user_id)
    flash("Retraction berhasil disetujui", "success")
    return redirect(url_for("teacher.retraction_requests"))


@teacher_bp.route("/retractions/<submission_id>/reject", methods=["POST"])
@teacher_or_admin_required
@require_school_access("submissions", "submission_id", ("exam_id", "exams"))
def reject_retraction(submission_id):
    supabase = get_supabase()
    sub = supabase.table("submissions").select("answers").eq("id", submission_id).single().execute().data
    if not sub:
        flash("Submission tidak ditemukan", "error")
        return redirect(url_for("teacher.retraction_requests"))
    answers = sub.get("answers")
    if isinstance(answers, str):
        try:
            answers = json.loads(answers)
        except (json.JSONDecodeError, TypeError):
            answers = {}
    if not isinstance(answers, dict) or "_retract_request" not in answers or not isinstance(answers["_retract_request"], dict):
        flash("Tidak ada permintaan retraction", "error")
        return redirect(url_for("teacher.retraction_requests"))
    answers["_retract_request"]["status"] = "rejected"
    supabase.table("submissions").update({"answers": json.dumps(answers)}).eq("id", submission_id).execute()
    log_activity("retract_reject", "submission", submission_id, user_id=g.user_id)
    flash("Retraction ditolak", "success")
    return redirect(url_for("teacher.retraction_requests"))


# The mark the roster colours green and calls a pass. One constant, because the
# printed sheet has to agree with the screen it was printed from.
PASS_MARK = 70


def _teacher_exams(supabase, user_role, school_id):
    """The exams this caller may pick from the results page."""
    query = supabase.table("exams").select("id,title,subject").eq("teacher_id", g.user_id)
    if user_role == "admin_sekolah" and school_id:
        query = supabase.table("exams").select("id,title,subject").eq("school_id", school_id)
    return query.execute().data or []


def _exam_results(supabase, exam_id):
    """Every row the results page and the printed sheet both show.

    Returns ``(subs, scan_subs, online_subs, stats)``. Two renderings of one
    exam's results must not be able to disagree, so the deduplication, the
    source split and the statistics are computed here once instead of in each
    caller.

    ``stats['late']`` counts the papers ``submitted_late`` flags. The rows are
    selected with ``*``, so the flag arrives with them and the count is free —
    and it is the *stored* flag the submit route wrote, not a recomputation, so
    a teacher who edits the exam's window after the sitting cannot change what a
    paper's result says about when it arrived.
    """
    subs = supabase.table("submissions").select("*, profiles(full_name)") \
        .eq("exam_id", exam_id).execute().data or []

    for s in subs:
        if s.get("profiles"):
            s["student_name"] = s.pop("profiles").get("full_name", "")

    # Deduplicate: keep latest per (student_id, source)
    seen = {}
    for s in sorted(subs, key=lambda x: x.get("submitted_at", "") or "", reverse=True):
        sid = s.get("student_id", "")
        ans = s.get("answers") or {}
        if isinstance(ans, str):
            try:
                ans = json.loads(ans)
            except Exception:
                ans = {}
        # Written back, so a row is the same shape wherever it is read: the
        # printed sheet looks up the NISN on a scan submission, and a row that
        # still held its JSON as text would print a dash instead of a number.
        s["answers"] = ans
        source = "scan" if isinstance(ans, dict) and ans.get("_nisn") else "online"
        key = (sid, source)
        if key not in seen:
            s["_source"] = source
            seen[key] = s
    subs = list(seen.values())
    scan_subs = [s for s in subs if s.get("_source") == "scan"]
    online_subs = [s for s in subs if s.get("_source") != "scan"]

    scores = [_final_score(s) for s in subs]
    passed = [v for v in scores if v >= PASS_MARK]
    stats = {
        "avg": round(sum(scores) / len(scores), 1) if scores else 0,
        "max": max(scores) if scores else 0,
        "min": min(scores) if scores else 0,
        "count": len(scores),
        "passed": len(passed),
        "pass_rate": round(100 * len(passed) / len(scores)) if scores else 0,
        "threshold": PASS_MARK,
        # Counted here, in the same pass as the other statistics, so the header
        # chip and the per-row badge cannot disagree about how many there were.
        "late": sum(1 for row in subs if row.get("submitted_late")),
    }
    return subs, scan_subs, online_subs, stats


def _final_score(submission):
    """The number the app scores a submission by, wherever it is ranked."""
    value = submission.get("final_score")
    if value is None:
        value = submission.get("score")
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


@teacher_bp.route("/results")
@teacher_or_admin_required
def results():
    exam_id = request.args.get("exam_id")
    supabase = get_supabase()
    user_role = g.get("user_role")
    school_id = g.get("user_school_id")

    if not exam_id:
        query = supabase.table("exams").select("id,title").eq("teacher_id", g.user_id)
        if user_role == "admin_sekolah" and school_id:
            query = supabase.table("exams").select("id,title").eq("school_id", school_id)
        exams = query.execute().data or []
        return render_template("teacher/results.html", submissions=[], stats={}, exam_id="", exams=exams, exam={}, scan_subs=[], online_subs=[])

    # exam_id comes from the query string and the submissions used to be fetched
    # before any check, so another school's names and scores were rendered on demand.
    _, err = _guard_exam(supabase, exam_id, as_json=_wants_json(),
                         redirect_to="/teacher/results")
    if err:
        return err

    exams = _teacher_exams(supabase, user_role, school_id)
    exam = next((e for e in exams if e["id"] == exam_id), {})
    subs, scan_subs, online_subs, stats = _exam_results(supabase, exam_id)

    return render_template("teacher/results.html", submissions=subs, stats=stats, exam_id=exam_id, exams=exams, exam=exam, scan_subs=scan_subs, online_subs=online_subs)


@teacher_bp.route("/results/print")
@teacher_or_admin_required
def results_print():
    """One exam's results as a document to print and file.

    The results page prints as the app: a toolbar, a row of buttons and cards.
    What a school actually files is a sheet — exam identity, class statistics,
    the ranked roster and somewhere to sign — so this renders that instead.
    """
    from app.services.report_card_service import print_stamp, profile_name, school_for

    exam_id = request.args.get("exam_id")
    if not exam_id:
        return redirect("/teacher/results")

    supabase = get_supabase()
    # Same guard as the screen page: the sheet carries every student's name and
    # mark, so it cannot be the route that is easier to reach.
    exam, err = _guard_exam(supabase, exam_id, columns="*", as_json=False,
                            redirect_to="/teacher/results")
    if err:
        return err

    subs, scan_subs, online_subs, stats = _exam_results(supabase, exam_id)
    roster = sorted(subs, key=_final_score, reverse=True)

    return render_template(
        "teacher/print_exam_report.html",
        exam=exam,
        roster=roster,
        stats=stats,
        class_names=_class_names(supabase, exam.get("class_ids")),
        school=school_for(supabase, exam.get("school_id")),
        teacher_name=profile_name(supabase, exam.get("teacher_id")),
        printed_on=print_stamp(),
        pass_mark=PASS_MARK,
    )


def _analysis_of(supabase, exam_id, as_json=True, redirect_to="/teacher/results"):
    """``(exam, analysis, error)`` — the paper, and what its questions actually did.

    The weights are resolved the way the *grader* resolves them: an exam saved
    before schemes existed carries none, and is marked against the 70/30 default.
    Analysing it with an empty weight map would report every question as worth
    nothing and measure a paper nobody sat.
    """
    exam, err = _guard_exam(supabase, exam_id, columns="*", as_json=as_json,
                            redirect_to=redirect_to)
    if err:
        return None, None, err
    exam = _json_fields(exam)
    total = int(exam.get("total_questions") or 0)
    if not (exam.get("question_weights") or {}) and total > 0:
        exam["question_weights"] = default_weights(exam.get("question_types") or {},
                                                   total)
    submissions, _scan, _online, _stats = _exam_results(supabase, exam_id)
    return exam, item_analysis.analyse(exam, submissions), None


def _chart_payload(analysis):
    """What the three charts draw, as plain data.

    Built here rather than in the template because the alternative — Jinja loops
    assembling arrays inside an `x-data` attribute — is untestable, and because a
    chart that quietly drops a question (a `None` difficulty, say) should be a
    decision with a name. A question with no calibration has no place on the logit
    chart and is left out of *that* chart only; it still appears in the item map,
    which is computed from the classical columns that never go missing.
    """
    return {
        "summary": dataclasses.asdict(analysis.summary),
        "items": [{
            "no": item.index + 1,
            "kind": item.kind,
            "pct": item.pct,
            "disc": item.discrimination,
            "flag": item.flag,
            "marks": item.marks,
        } for item in analysis.items],
        "logits": [{
            "no": item.index + 1,
            "kind": item.kind,
            "b": item.measure,
            "se": item.se,
            "flag": item.flag,
        } for item in analysis.items if item.measure is not None],
        "bins": [{"centre": centre, "count": count}
                 for centre, count in analysis.person_bins],
        "people": [{"name": person.name, "raw": person.raw,
                    "possible": person.possible, "theta": person.measure,
                    "extreme": person.extreme}
                   for person in analysis.people if person.measure is not None],
    }


@teacher_bp.route("/analysis/<exam_id>")
@teacher_or_admin_required
def exam_analysis(exam_id):
    """What a class's answers say about the paper, not about the students.

    The results page answers "who scored what". A question that everyone got
    right measured nothing, a question the strong half did worse on is worth a
    second look, and a distractor nobody chose was never an option — none of that
    is visible in a roster, and all of it is already sitting in the answers the
    answer sheet collected.
    """
    supabase = get_supabase()
    exam, analysis, err = _analysis_of(supabase, exam_id, redirect_to="/teacher/results")
    if err:
        return err
    return render_template("teacher/analysis.html", exam=exam, analysis=analysis,
                           chart=_chart_payload(analysis))


@teacher_bp.route("/analysis/<exam_id>/download.csv")
@teacher_or_admin_required
def exam_analysis_csv(exam_id):
    """The analysis as a spreadsheet — sortable, because the numbers are read by
    comparing questions with each other."""
    supabase = get_supabase()
    lang = request.args.get("lang") or "id"
    exam, analysis, err = _analysis_of(supabase, exam_id, as_json=True,
                                       redirect_to="/teacher/results")
    if err:
        return err
    payload = analysis_report.analysis_csv(analysis, exam, lang)
    # A BOM, so Excel opens the file as UTF-8 and a student's accented name is not
    # a row of mojibake on the teacher's machine.
    buf = io.BytesIO(payload.encode("utf-8-sig"))
    return send_file(buf, mimetype="text/csv; charset=utf-8", as_attachment=True,
                     download_name=analysis_report.filename(analysis, exam, "csv"))


@teacher_bp.route("/analysis/<exam_id>/download.pdf")
@teacher_or_admin_required
def exam_analysis_pdf(exam_id):
    """The analysis as the document that goes in the exam file."""
    from app.services.report_card_service import profile_name, school_for

    supabase = get_supabase()
    lang = request.args.get("lang") or "id"
    exam, analysis, err = _analysis_of(supabase, exam_id, as_json=True,
                                       redirect_to="/teacher/results")
    if err:
        return err
    pdf = analysis_report.analysis_pdf(
        analysis, exam,
        school=(school_for(supabase, exam.get("school_id")) or {}).get("name", ""),
        teacher=profile_name(supabase, exam.get("teacher_id")), lang=lang)
    return send_file(io.BytesIO(pdf), mimetype="application/pdf", as_attachment=True,
                     download_name=analysis_report.filename(analysis, exam, "pdf"))


@teacher_bp.route("/submissions/<submission_id>/print")
@teacher_or_admin_required
def print_submission_card(submission_id):
    """The report card a teacher hands back, as a document.

    This is what the marking view's print button used to attempt: printing a
    submission from that page printed the marking interface, canvas tools and
    all. The sheet is the same one the student prints, so the copy in the file
    and the copy at home cannot disagree.

    It is not gated on release — the marking is the teacher's own record, and
    printing before the result is announced is a normal use. The sheet says so
    on its face when that is the case.
    """
    from app.services.report_card_service import load_report_card, print_stamp

    supabase = get_supabase()
    _, err = _guard_submission(supabase, submission_id, as_json=False,
                               redirect_to="/teacher/results")
    if err:
        return err

    card = load_report_card(supabase, submission_id)
    if not card:
        flash("Submission tidak ditemukan", "error")
        return redirect("/teacher/results")
    return render_template("print/report_card.html", printed_on=print_stamp(),
                           show_key=True, **card)


def _class_names(supabase, class_ids):
    """Names for the classes an exam was assigned to, best effort.

    A sheet that cannot resolve a class name still prints; it just omits the
    line, which is better than refusing to print a list of marks.
    """
    ids = [c for c in (class_ids or []) if c]
    if isinstance(class_ids, str):
        try:
            ids = [c for c in json.loads(class_ids) if c]
        except (json.JSONDecodeError, TypeError):
            ids = []
    if not ids:
        return []
    try:
        rows = supabase.table("classes").select("id,name").in_("id", ids).execute().data or []
    except Exception:
        logger.exception("Could not resolve class names for %s", ids)
        return []
    named = {row["id"]: row.get("name", "") for row in rows}
    return [named[i] for i in ids if named.get(i)]


@teacher_bp.route("/grade-question/<exam_id>/<int:question_index>")
@teacher_or_admin_required
def grade_question(exam_id, question_index):
    """Grade a single question across all students."""
    supabase = get_supabase()
    # This page renders the answer key. Its sibling API filtered by owner; the page
    # did not, so any teacher could read any exam's key just by typing the URL.
    exam, err = _guard_exam(
        supabase, exam_id, as_json=_wants_json(),
        columns="id,teacher_id,school_id,title,total_questions,question_types,answer_key")
    if err:
        return err
    for f in ("question_types", "answer_key"):
        v = exam.get(f)
        if isinstance(v, str):
            try: exam[f] = json.loads(v)
            except: exam[f] = {}
    return render_template("teacher/grade_question.html", exam=exam, exam_id=exam_id)


@teacher_bp.route("/api/grade-question/<exam_id>/<int:question_index>")
@teacher_or_admin_required
def grade_question_api(exam_id, question_index):
    """API: return all students' answers for a specific question."""
    supabase = get_supabase()
    _, err = _guard_exam(supabase, exam_id)
    if err:
        return err
    subs = supabase.table("submissions").select("id,student_id,answers,score,final_score,status,submitted_at,profiles(full_name)").eq("exam_id", exam_id).execute().data or []

    students = []
    for s in subs:
        answers = s.get("answers") or {}
        if isinstance(answers, str):
            try: answers = json.loads(answers)
            except: answers = {}

        student_name = (s.get("profiles") or {}).get("full_name", s.get("student_id", "")[:12])
        qi = str(question_index)
        ans_data = answers.get(qi)

        # Extract answer text/option
        answer_val = ""
        essay_text = ""
        has_canvas = False
        feedback = {"score": None, "feedback": None}

        if isinstance(ans_data, dict):
            answer_val = ans_data.get("answer", "")
            if ans_data.get("pages"):
                has_canvas = True
            if ans_data.get("text"):
                essay_text = ans_data["text"]
            feedback["score"] = ans_data.get("ai_score") or ans_data.get("score")
            feedback["feedback"] = ans_data.get("feedback") or ans_data.get("ai_feedback")
        elif isinstance(ans_data, str):
            answer_val = ans_data

        students.append({
            "submission_id": s["id"],
            "name": student_name,
            "answer": answer_val,
            "essayText": essay_text or answer_val if essay_text else "",
            "hasCanvas": has_canvas,
            "status": s.get("status", ""),
            "score": s.get("score"),
            "final_score": s.get("final_score"),
            "submitted_at": str(s.get("submitted_at", ""))[:19],
            "feedback": feedback,
        })

    return jsonify({"students": students})


@teacher_bp.route("/api/grade-question/<exam_id>/<int:question_index>/save", methods=["POST"])
@teacher_or_admin_required
def grade_question_save(exam_id, question_index):
    """Save a grade update for a specific question on a submission."""
    data = request.get_json()
    submission_id = data.get("submission_id")
    if not submission_id:
        return jsonify({"error": "No submission_id"}), 400

    supabase = get_supabase()
    # Two holes closed here. There was no access check at all, so any teacher could
    # overwrite any submission in any school; and the submission was never tied to
    # the exam in the URL, so access to one exam was enough to rewrite another's.
    _, err = _guard_exam(supabase, exam_id)
    if err:
        return err
    sub = row_or_none(
        supabase.table("submissions").select("id,exam_id,answers")
        .eq("id", submission_id).maybe_single().execute()
    )
    if not sub:
        return jsonify({"error": "Not found"}), 404
    if str(sub.get("exam_id") or "") != str(exam_id):
        return jsonify({"error": "Submission bukan milik ujian ini"}), 400

    answers = sub.get("answers") or {}
    if isinstance(answers, str):
        try: answers = json.loads(answers)
        except: answers = {}

    qi = str(question_index)
    current = answers.get(qi, {})
    if not isinstance(current, dict):
        current = {"answer": current}

    if "answer" in data:
        current["answer"] = data["answer"]
    if "essay_score" in data:
        current["ai_score"] = float(data["essay_score"]) if data.get("essay_score") else None
    if "essay_feedback" in data:
        current["feedback"] = data["essay_feedback"]

    answers[qi] = current
    supabase.table("submissions").update({"answers": answers}).eq("id", submission_id).execute()
    return jsonify({"success": True})


@teacher_bp.route("/grade/<submission_id>")
@teacher_or_admin_required
@require_school_access("submissions", "submission_id", ("exam_id", "exams"))
def grade_detail(submission_id):
    supabase = get_supabase()
    try:
        sub = row_or_none(
            supabase.table("submissions").select("*").eq("id", submission_id).maybe_single().execute()
        )
        if not sub:
            flash("Submission tidak ditemukan", "error")
            return redirect("/teacher/grading")
        exam = supabase.table("exams").select("*").eq("id", sub["exam_id"]).single().execute().data
        exam.setdefault("question_weights", {})
        if not exam.get("question_weights") and exam.get("total_questions", 0) > 0:
            exam["question_weights"] = default_weights(
                _as_dict(exam.get("question_types")), exam["total_questions"])
        student = supabase.table("profiles").select("id,full_name,phone").eq("id", sub["student_id"]).single().execute().data or {}
        # Parse JSON string fields
        for field in ("teacher_feedback", "answers"):
            val = sub.get(field)
            if isinstance(val, str):
                try:
                    sub[field] = json.loads(val)
                except (json.JSONDecodeError, TypeError):
                    sub[field] = {} if field != "answers" else {}
            if not isinstance(sub.get(field), dict):
                sub[field] = {} if field != "answers" else {}
        # Parse exam JSON fields
        for _field in ("question_types", "answer_key", "question_weights", "question_pages", "pdf_page_urls"):
            _val = exam.get(_field)
            if isinstance(_val, str):
                try:
                    exam[_field] = json.loads(_val)
                except (json.JSONDecodeError, TypeError):
                    exam[_field] = {}
        return render_template("teacher/grade_detail.html", submission=sub, exam=exam, exam_id=sub["exam_id"], student=student)
    except Exception as e:
        current_app.logger.error("grade_detail error: %s", str(e), exc_info=True)
        flash(f"Terjadi kesalahan: {str(e)[:100]}", "error")
        return redirect("/teacher/grading")


@teacher_bp.route("/grade/<submission_id>/override", methods=["POST"])
@subscription_write_required
@teacher_or_admin_required
@require_school_access("submissions", "submission_id", ("exam_id", "exams"))
def override_score(submission_id):
    if request.is_json:
        data = request.get_json()
        new_score = data.get("final_score")
        feedback = data.get("teacher_feedback", {})
        penalty_override = data.get("penalty")  # optional penalty override
    else:
        new_score = request.form.get("final_score")
        feedback_raw = request.form.get("teacher_feedback", "{}")
        penalty_override = request.form.get("penalty")
        try:
            feedback = json.loads(feedback_raw)
        except json.JSONDecodeError:
            feedback = {}
    supabase = get_supabase()
    final_score_val = float(new_score) if new_score is not None and new_score != '' else None
    update_data = {
        "final_score": final_score_val,
        "status": "graded",
        "teacher_feedback": feedback,
    }
    # If teacher provides penalty override, update it
    if penalty_override is not None and penalty_override != '':
        update_data["penalty"] = round(float(penalty_override), 2)
    supabase.table("submissions").update(update_data).eq("id", submission_id).execute()
    # Recalculate after grading
    try:
        sub_updated = supabase.table("submissions").select("exam_id").eq("id", submission_id).single().execute().data
        if sub_updated:
            _recalculate_scores(sub_updated["exam_id"])
    except Exception:
        pass
    if request.is_json:
        return jsonify({"success": True, "final_score": final_score_val})
    return redirect(request.referrer or "/teacher/results")


# --- Penalty Appeals ---
@teacher_bp.route("/penalty-appeals")
@teacher_or_admin_required
def penalty_appeals():
    """List all pending penalty appeals."""
    supabase = get_supabase()
    user_role = g.get("user_role")
    school_id = g.get("user_school_id")
    exam_ids = [e["id"] for e in supabase.table("exams").select("id").eq("teacher_id", g.user_id).execute().data or []]
    if not exam_ids and user_role == "admin_sekolah" and school_id:
        exam_ids = [e["id"] for e in supabase.table("exams").select("id").eq("school_id", school_id).execute().data or []]
    appeals = []
    if exam_ids:
        subs = supabase.table("submissions").select("id,student_id,exam_id,penalty,final_score,answers,submitted_at,exams(title),profiles(full_name)").in_("exam_id", exam_ids).execute().data or []
        for s in subs:
            answers = s.get("answers")
            if isinstance(answers, str):
                try: answers = json.loads(answers)
                except: answers = {}
            if isinstance(answers, dict):
                appeal = answers.get("_penalty_appeal")
                if isinstance(appeal, dict) and appeal.get("status") == "pending":
                    s["exam_title"] = (s.get("exams") or {}).get("title", "-")
                    s["student_name"] = (s.get("profiles") or {}).get("full_name", "-")
                    s["appeal"] = appeal
                    appeals.append(s)
    return render_template("teacher/penalty_appeals.html", appeals=appeals)


@teacher_bp.route("/api/penalty-appeal/<submission_id>", methods=["POST"])
@teacher_or_admin_required
@require_school_access("submissions", "submission_id", ("exam_id", "exams"))
def api_penalty_appeal_handle(submission_id):
    """Teacher approves/rejects a penalty appeal."""
    data = request.get_json(silent=True) or {}
    action = data.get("action")
    reduction_type = data.get("reduction_type")
    reduction_value = data.get("reduction_value")
    response_msg = str(data.get("response", "")).strip()

    if action not in ("approve", "reject"):
        return jsonify({"error": "action harus 'approve' atau 'reject'"}), 400

    supabase = get_supabase()
    sub = supabase.table("submissions").select("id,student_id,exam_id,penalty,answers,final_score").eq("id", submission_id).single().execute().data
    if not sub:
        return jsonify({"error": "Not found"}), 404

    answers = sub.get("answers") or {}
    if isinstance(answers, str):
        try: answers = json.loads(answers)
        except: answers = {}
    if not isinstance(answers, dict) or "_penalty_appeal" not in answers:
        return jsonify({"error": "Tidak ada banding"}), 404

    appeal = answers["_penalty_appeal"]
    current_penalty = float(sub.get("penalty") or 0)
    penalty_reduction = 0

    if action == "approve":
        if reduction_type == "all":
            penalty_reduction = current_penalty
        elif reduction_type == "fixed":
            penalty_reduction = min(float(reduction_value or 0), current_penalty)
        elif reduction_type == "percent":
            pct = min(float(reduction_value or 0), 100)
            penalty_reduction = round(current_penalty * pct / 100, 2)
        else:
            return jsonify({"error": "reduction_type harus fixed/percent/all"}), 400
        penalty_reduction = max(0, min(penalty_reduction, current_penalty))
        new_penalty = round(current_penalty - penalty_reduction, 2)
        current_final = float(sub.get("final_score") or 0)
        new_final = round(current_final + penalty_reduction, 2)
        supabase.table("submissions").update({
            "penalty": new_penalty,
            "final_score": new_final,
        }).eq("id", submission_id).execute()

    appeal["status"] = "approved" if action == "approve" else "rejected"
    appeal["responded_at"] = datetime.now(timezone.utc).isoformat()
    appeal["response"] = response_msg
    appeal["penalty_reduction"] = penalty_reduction if action == "approve" else 0
    answers["_penalty_appeal"] = appeal
    supabase.table("submissions").update({"answers": json.dumps(answers)}).eq("id", submission_id).execute()

    return jsonify({"success": True, "message": "Banding " + ("disetujui" if action == "approve" else "ditolak")})


@teacher_bp.route("/publish/<exam_id>", methods=["GET", "POST"])
@subscription_write_required
@require_school_access("exams", "exam_id")
@teacher_or_admin_required
def publish_scores(exam_id):
    supabase = get_supabase()
    if request.method == "GET":
        exam = supabase.table("exams").select("id,title,passing_score").eq("id", exam_id).single().execute().data
        subs = supabase.table("submissions").select("id,student_id,final_score,status,profiles(full_name)").eq("exam_id", exam_id).execute().data or []
        subs.sort(key=lambda s: (s.get("profiles") or {}).get("full_name", ""))
        return render_template("teacher/publish_preview.html", exam=exam, submissions=subs)
    _recalculate_scores(exam_id)
    # Get student IDs to invalidate their dashboard caches
    try:
        student_subs = supabase.table("submissions").select("student_id").eq("exam_id", exam_id).execute().data or []
        for s in student_subs:
            sid = s.get("student_id")
            if sid:
                cache_delete(f"dash:{sid}")
    except Exception:
        pass
    supabase.table("submissions") \
        .update({"is_published": True, "status": "published"}) \
        .eq("exam_id", exam_id) \
        .execute()
    # Invalidate teacher dashboard cache
    _invalidate_teacher_dashboard()
    return redirect("/teacher/results?exam_id=" + exam_id)


@teacher_bp.route("/publish/<exam_id>/unpublish", methods=["POST"])
@subscription_write_required
@teacher_or_admin_required
def unpublish_scores(exam_id):
    supabase = get_supabase()
    # This withdraws released marks and had NO check at all, so a teacher in one
    # school could unpublish another school's results by posting an exam id.
    _, err = _guard_exam(supabase, exam_id, as_json=_wants_json(),
                         redirect_to="/teacher/results?exam_id=" + exam_id)
    if err:
        return err
    supabase.table("submissions") \
        .update({"is_published": False, "status": "graded"}) \
        .eq("exam_id", exam_id) \
        .execute()
    return redirect("/teacher/results?exam_id=" + exam_id)


@teacher_bp.route("/exams/<exam_id>/recalculate", methods=["POST"])
@teacher_or_admin_required
def recalculate_exam_scores(exam_id):
    """Recalculate all scores for an exam (MCQ auto-grade + essay + penalty)."""
    _, err = _guard_exam(get_supabase(), exam_id, as_json=_wants_json(),
                         redirect_to="/teacher/results?exam_id=" + exam_id)
    if err:
        return err
    _recalculate_scores(exam_id)
    flash("Nilai berhasil dihitung ulang", "success")
    return redirect("/teacher/results?exam_id=" + exam_id)


@teacher_bp.route("/publish/submission/<submission_id>", methods=["POST"])
@subscription_write_required
@teacher_or_admin_required
def publish_single(submission_id):
    # Replaced a hand-rolled check that skipped itself entirely whenever the
    # submission lookup came back empty, then re-queried the same row. One shared
    # rule now, resolved in a single query.
    supabase = get_supabase()
    sub, err = _guard_submission(supabase, submission_id, as_json=_wants_json())
    if err:
        return err
    supabase.table("submissions") \
        .update({"is_published": True, "status": "published"}) \
        .eq("id", submission_id) \
        .execute()
    if sub.get("student_id"):
        cache_delete(f"dash:{sub['student_id']}")
    return redirect("/teacher/results?exam_id=" + sub["exam_id"])


# --- Export ---
@teacher_bp.route("/export/xlsx")
@teacher_or_admin_required
def export_xlsx():
    exam_id = request.args.get("exam_id")
    supabase = get_supabase()
    # exam_id came straight from the query string with no check, and select("*")
    # includes the answer key — any teacher could download any school's results.
    exam, err = _guard_exam(supabase, exam_id, columns="*")
    if err:
        return err
    subs = supabase.table("submissions").select("*, profiles(full_name)").eq("exam_id", exam_id).execute().data or []
    for s in subs:
        s["student_name"] = (s.pop("profiles", None) or {}).get("full_name", s.get("student_id", "")[:12])
    buf = export_to_xlsx(subs, exam)
    return send_file(buf, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                     as_attachment=True, download_name=f"nilai_{exam_id[:8]}.xlsx")


@teacher_bp.route("/export/pdf")
@teacher_or_admin_required
def export_pdf():
    exam_id = request.args.get("exam_id")
    supabase = get_supabase()
    exam, err = _guard_exam(supabase, exam_id, columns="*")
    if err:
        return err
    subs = supabase.table("submissions").select("*, profiles(full_name)").eq("exam_id", exam_id).execute().data or []
    for s in subs:
        s["student_name"] = (s.pop("profiles", None) or {}).get("full_name", s.get("student_id", "")[:12])
    buf = export_to_pdf(subs, exam.get("title", "Hasil"), exam)
    return send_file(buf, mimetype="application/pdf", as_attachment=True,
                     download_name=f"nilai_{exam_id[:8]}.pdf")


@teacher_bp.route("/export/bubble-sheet/<exam_id>")
@teacher_or_admin_required
def bubble_sheet(exam_id):
    supabase = get_supabase()
    # Restricted to the columns actually used, so the answer key is never fetched
    # — and the exam must belong to the caller.
    exam, err = _guard_exam(supabase, exam_id,
                            columns="total_questions,question_types,subject")
    if err:
        return err
    qtypes = exam.get("question_types") or {}
    # The printed LJK is bubble paper: it can carry a choice question and nothing
    # else. A matching or drag-and-drop answer has no bubbles to fill, and a
    # true/false question would need a two-bubble column the sheet does not print,
    # so the sheet is sized by the choice questions specifically.
    bubble_count = sum(1 for i in range(exam["total_questions"])
                       if question_kind(qtypes.get(str(i))) == KIND_CHOICE)
    if bubble_count == 0:
        bubble_count = exam["total_questions"]

    buf = generate_answer_sheet(
        total_questions=bubble_count,
        subject=exam.get("subject", ""),
        school_name="",
    )
    return send_file(buf, mimetype="application/pdf", as_attachment=True,
                     download_name=f"LJK_{exam_id[:8]}.pdf")


@teacher_bp.route("/grading")
@teacher_or_admin_required
def grading_center():
    supabase = get_supabase()
    user_role = g.get("user_role")
    school_id = g.get("user_school_id")
    exam_ids = [e["id"] for e in supabase.table("exams").select("id").eq("teacher_id", g.user_id).execute().data or []]
    # For admin_sekolah, also get exams from their school (regardless of teacher_id)
    if user_role == "admin_sekolah" and school_id:
        exam_ids = [e["id"] for e in supabase.table("exams").select("id").eq("school_id", school_id).execute().data or []]
    pending_subs = []
    graded_subs = []
    if exam_ids:
        subs = supabase.table("submissions").select("id,student_id,exam_id,score,final_score,status,submitted_at,exams(title),profiles(full_name)").in_("exam_id", exam_ids).order("submitted_at", desc=True).execute().data or []
        for s in subs:
            s["student_name"] = (s.get("profiles") or {}).get("full_name", "-") if s.get("profiles") else "-"
            s["exam_title"] = (s.get("exams") or {}).get("title", "-") if s.get("exams") else "-"
            if s["status"] == "submitted":
                pending_subs.append(s)
            elif s["status"] in ("graded", "published"):
                graded_subs.append(s)
    # Group by exam
    exam_groups = {}
    for s in pending_subs + graded_subs:
        eid = s["exam_id"]
        if eid not in exam_groups:
            exam_groups[eid] = {"id": eid, "title": s["exam_title"], "pending": [], "graded": []}
        if s["status"] == "submitted":
            exam_groups[eid]["pending"].append(s)
        else:
            exam_groups[eid]["graded"].append(s)
    return render_template("teacher/grading.html", exam_groups=list(exam_groups.values()))


@teacher_bp.route("/grading/<exam_id>")
@teacher_or_admin_required
def grading_queue(exam_id):
    """Per-exam split-pane grading queue."""
    supabase = get_supabase()
    # The JSON API behind this page already filtered by owner. The page itself did
    # not, so its answer key was readable by URL from any school.
    exam, err = _guard_exam(
        supabase, exam_id, as_json=_wants_json(), redirect_to="/teacher/grading",
        columns="id,teacher_id,school_id,title,subject,question_types,"
                "question_weights,answer_key,total_questions")
    if err:
        return err
    return render_template("teacher/grading_queue.html", exam=exam)


@teacher_bp.route("/api/grading-queue/<exam_id>")
@teacher_or_admin_required
def api_grading_queue(exam_id):
    """JSON data for grading queue — list of submissions with essay preview."""
    supabase = get_supabase()
    user_role = g.get("user_role")
    school_id = g.get("user_school_id")

    # Verify access
    exam = supabase.table("exams").select("id,teacher_id,school_id,question_types,question_weights,answer_key,total_questions").eq("id", exam_id).single().execute().data
    if not exam:
        return jsonify({"error": "Not found"}), 404
    if user_role != "super_admin" and exam["teacher_id"] != g.user_id:
        if user_role == "admin_sekolah" and str(exam.get("school_id", "")) == str(school_id or ""):
            pass
        else:
            return jsonify({"error": "Forbidden"}), 403

    question_types = exam.get("question_types") or {}
    essay_indices = [str(i) for i in range(int(exam.get("total_questions", 0)))
                     if is_essay(question_types.get(str(i)))]

    subs = supabase.table("submissions").select("id,student_id,answers,teacher_feedback,score,final_score,status,violations,penalty,submitted_at,profiles!inner(full_name)").eq("exam_id", exam_id).in_("status", ["submitted", "graded", "published"]).order("submitted_at", desc=False).execute().data or []

    # Build essay question info
    essay_qs = []
    qw = exam.get("question_weights", {}) or {}
    if not qw and essay_indices:
        equal_weight = round(100 / len(essay_indices), 2)
        qw = {ei: equal_weight for ei in essay_indices}
    for idx_str in essay_indices:
        idx = int(idx_str)
        essay_qs.append({"index": idx, "label": f"Soal {idx + 1}", "weight": qw.get(idx_str, 0)})

    result = []
    for s in subs:
        answers = s.get("answers") or {}
        if isinstance(answers, str):
            try: answers = json.loads(answers)
            except: answers = {}
        fb = s.get("teacher_feedback") or {}
        if isinstance(fb, str):
            try: fb = json.loads(fb)
            except: fb = {}
        fb_scores = fb.get("scores") or {}
        fb_comments = fb.get("comments") or {}
        graded_essays = sum(1 for ei in essay_indices if ei in fb_scores)
        preview = ""
        per_question = []
        for ei in essay_indices:
            ans = answers.get(ei)
            text = ""
            if isinstance(ans, dict):
                text = (ans.get("text") or ans.get("answer", "") or "").strip()
            elif isinstance(ans, str):
                text = ans.strip()
            if not preview and text:
                preview = text[:150]
            per_question.append({
                "index": ei,
                "score": fb_scores.get(ei),
                "comment": fb_comments.get(ei, ""),
                "preview": text[:100] if text else "",
            })
        profile = s.get("profiles") or {}
        result.append({
            "id": s["id"],
            "student_name": profile.get("full_name") or "-",
            "status": s["status"],
            "submitted_at": s.get("submitted_at"),
            "essay_count": len(essay_indices),
            "graded_essays": graded_essays,
            "score": s.get("final_score") or s.get("score"),
            "score_penalty": s.get("penalty") or 0,
            "has_drawing": any(isinstance(answers.get(ei), dict) and answers[ei].get("pages") for ei in essay_indices),
            "preview": preview,
            "questions": per_question,
            "teacher_feedback": fb,
        })

    return jsonify({
        "submissions": result,
        "total": len(result),
        "pending": sum(1 for r in result if r["status"] == "submitted"),
        "graded": sum(1 for r in result if r["status"] in ("graded", "published")),
        "essay_indices": essay_indices,
    })


@teacher_bp.route("/analytics")
@teacher_or_admin_required
def analytics():
    import statistics
    supabase = get_supabase()
    exams = supabase.table("exams").select("id,title,passing_score").eq("teacher_id", g.user_id).execute().data or []
    exam_ids = [e["id"] for e in exams]
    all_scores = []
    exam_breakdown = []
    dist_bins = [0, 0, 0, 0, 0]
    exam_labels = []
    exam_avgs = []
    exam_medians = []
    total_submissions = 0
    pass_count = 0
    for e in exams:
        subs = supabase.table("submissions").select("score,final_score").eq("exam_id", e["id"]).execute().data or []
        scores = [float(s.get("final_score") or s.get("score") or 0) for s in subs if s.get("final_score") or s.get("score")]
        all_scores.extend(scores)
        total_submissions += len(subs)
        passing = e.get("passing_score") or 70
        pc = sum(1 for sc in scores if sc >= passing)
        pass_count += pc
        if scores:
            sorted_s = sorted(scores)
            n = len(sorted_s)
            median = sorted_s[n // 2] if n % 2 == 1 else (sorted_s[n // 2 - 1] + sorted_s[n // 2]) / 2
            exam_breakdown.append({
                "title": e["title"],
                "count": len(scores),
                "avg": round(sum(scores) / len(scores), 1),
                "median": round(median, 1),
                "max": round(max(scores), 1),
                "min": round(min(scores), 1),
                "pass_pct": round(pc / len(scores) * 100),
            })
            exam_labels.append(e["title"][:20])
            exam_avgs.append(round(sum(scores) / len(scores), 1))
            exam_medians.append(round(median, 1))
    for sc in all_scores:
        if sc < 20: dist_bins[0] += 1
        elif sc < 40: dist_bins[1] += 1
        elif sc < 60: dist_bins[2] += 1
        elif sc < 80: dist_bins[3] += 1
        else: dist_bins[4] += 1
    avg_score = round(sum(all_scores) / len(all_scores), 1) if all_scores else 0
    pass_rate = round(pass_count / len(all_scores) * 100) if all_scores else 0
    std_dev = round(statistics.stdev(all_scores), 1) if len(all_scores) > 1 else 0
    stats = {
        "total_exams": len(exams),
        "total_submissions": total_submissions,
        "avg_score": avg_score,
        "pass_rate": pass_rate,
        "std_dev": std_dev,
    }
    return render_template("teacher/analytics.html", stats=stats, exam_breakdown=exam_breakdown, dist_bins=dist_bins, exam_labels=exam_labels, exam_avgs=exam_avgs, exam_medians=exam_medians)


@teacher_bp.route("/reset-password", methods=["POST"])
@login_required
def teacher_reset_password():
    if g.get("user_role") not in ("guru",):
        return jsonify({"error": "Forbidden"}), 403
    supabase = get_supabase()
    pw = request.form.get("password", "").strip()
    if len(pw) < 6:
        return jsonify({"error": "Password minimal 6 karakter"}), 400
    try:
        supabase.auth.admin.update_user_by_id(g.user_id, {"password": pw})
        log_activity("reset_password", "user", g.user_id, user_id=g.user_id)
        return jsonify({"success": True, "message": "Password berhasil diubah"})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@teacher_bp.route("/profile/update", methods=["POST"])
@login_required
def teacher_update_profile():
    if g.get("user_role") not in ("guru",):
        return jsonify({"error": "Forbidden"}), 403
    supabase = get_supabase()
    data = {}
    for key in ("phone",):
        val = request.form.get(key)
        if val is not None:
            data[key] = val.strip()
    if not data:
        return jsonify({"error": "Tidak ada data yang diubah"}), 400
    try:
        supabase.table("profiles").update(data).eq("id", g.user_id).execute()
        log_activity("update", "profile", g.user_id, new_data=data, user_id=g.user_id)
        return jsonify({"success": True, "message": "Profil berhasil diperbarui"})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@teacher_bp.route("/assignments", methods=["GET", "POST"])
@teacher_or_admin_required
def assignments():
    supabase = get_supabase()
    school_id = g.get("user_school_id")
    if not school_id:
        if request.is_json or request.headers.get("HX-Request"):
            return jsonify({"error": "School not found"}), 400
        return redirect("/teacher/dashboard")

    if request.method == "POST":
        if g.get("user_role") not in ("admin_sekolah", "super_admin"):
            return jsonify({"error": "Hanya admin sekolah yang bisa menambah kelas"}), 403
        class_id = request.form.get("class_id")
        subject_id = request.form.get("subject_id")
        if not class_id or not subject_id:
            if request.is_json or request.headers.get("HX-Request"):
                return jsonify({"error": "Class and subject required"}), 400
            return redirect("/teacher/dashboard")
        try:
            # on_conflict names the constraint that a repeat should collide with.
            # Without it supabase-py targets the primary key, and this payload has
            # no `id` — so there is nothing to conflict with, and assigning a pair
            # that already exists fails on UNIQUE(teacher_id, class_id, subject_id)
            # with 23505 instead of updating the row it was meant to update.
            res = supabase.table("teacher_assignments").upsert({
                "teacher_id": g.user_id,
                "class_id": class_id,
                "subject_id": subject_id,
                "school_id": school_id,
            }, on_conflict="teacher_id,class_id,subject_id").execute()
            aid = res.data[0]["id"] if res.data else None
            # The teacher's own list and the school's subject count are cached;
            # an assignment the teacher just made must appear immediately.
            invalidate_teacher_assignments(g.user_id, school_id)
            log_activity("create", "teacher_assignment", aid, new_data={"class_id": class_id, "subject_id": subject_id}, user_id=g.user_id)
            if request.is_json or request.headers.get("HX-Request"):
                return jsonify({"success": True})
            return redirect("/teacher/dashboard")
        except Exception as e:
            if request.is_json or request.headers.get("HX-Request"):
                return jsonify({"error": str(e)}), 400
            return redirect("/teacher/dashboard")

    return jsonify(teacher_assignments_for(g.user_id, school_id))


@teacher_bp.route("/assignments/<assignment_id>", methods=["DELETE"])
@teacher_or_admin_required
def delete_assignment(assignment_id):
    if g.get("user_role") not in ("admin_sekolah", "super_admin"):
        return jsonify({"error": "Hanya admin sekolah yang bisa menghapus kelas"}), 403
    supabase = get_supabase()
    try:
        supabase.table("teacher_assignments").delete().eq("id", assignment_id).eq("teacher_id", g.user_id).execute()
        invalidate_teacher_assignments(g.user_id, g.get("user_school_id"))
        log_activity("delete", "teacher_assignment", assignment_id, user_id=g.user_id)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@teacher_bp.route("/api/ai/wizard-status")
@teacher_or_admin_required
def ai_wizard_status():
    from app.services.ai_service import get_teacher_ai_status
    return jsonify(get_teacher_ai_status(g.user_id))


@teacher_bp.route("/api/ai/test-demo", methods=["POST"])
@teacher_or_admin_required
def ai_test_demo():
    from app.services.ai_service import _get_demo_key, _call_ai
    key = _get_demo_key()
    if not key:
        return jsonify({"error": "Demo key tidak tersedia. Hubungi admin."}), 400
    try:
        raw = _call_ai(key, 'Jawab dalam satu kata: Berapa 2+2? Format JSON: {"answer": <number>}')
        import json
        data = json.loads(raw.strip().replace("```json", "").replace("```", "").strip())
        if data.get("answer") == 4:
            return jsonify({"success": True, "message": "✅ Demo AI aktif! Koneksi berhasil."})
        return jsonify({"success": True, "message": f"✅ Demo AI aktif. Response: {raw[:80]}"})
    except Exception as e:
        return jsonify({"error": f"❌ Gagal: {str(e)[:120]}"}), 400


@teacher_bp.route("/ai-settings")
@teacher_or_admin_required
def ai_settings():
    supabase = get_supabase()
    keys = supabase.table("teacher_ai_keys").select("*").eq("teacher_id", g.user_id).order("created_at", desc=True).execute().data or []
    settings_res = supabase.table("teacher_ai_settings").select("*").eq("teacher_id", g.user_id).limit(1).execute()
    if not settings_res.data:
        default_prompts = [
            {"id": "default", "label": "Default (Semua Mapel)", "template": "Kamu adalah asisten koreksi ujian. Koreksi jawaban esai berikut berdasarkan soal dan bobot maksimal.\n\nSoal: {question}\nPedoman Penskoran: {rubric}\nBobot Maksimal: {max_score} poin\nJawaban Siswa: \"{answer}\"\n\nBerikan skor (0-{max_score}) dan feedback singkat dalam bahasa Indonesia.\nFormat JSON: {\"score\": <number>, \"feedback\": \"<string>\"}"},
            {"id": "ipa", "label": "IPA / Sains", "template": "Kamu adalah asisten koreksi mata pelajaran IPA (Fisika, Kimia, Biologi, Earth Science).\n\nSoal: {question}\nPedoman Penskoran: {rubric}\nBobot Maksimal: {max_score} poin\nJawaban Siswa: \"{answer}\"\n\nBerikan skor (0-{max_score}). Nilai berdasarkan: keakuratan konsep sains, penggunaan istilah ilmiah yang tepat, logika ilmiah, dan kelengkapan jawaban. Feedback dalam bahasa Indonesia.\nFormat JSON: {\"score\": <number>, \"feedback\": \"<string>\"}"},
            {"id": "matematika", "label": "Matematika", "template": "Kamu adalah asisten koreksi mata pelajaran Matematika.\n\nSoal: {question}\nPedoman Penskoran: {rubric}\nBobot Maksimal: {max_score} poin\nJawaban Siswa: \"{answer}\"\n\nBerikan skor (0-{max_score}). Nilai berdasarkan: kebenaran rumus, ketepatan langkah-langkah penyelesaian, keakuratan perhitungan, dan kesimpulan akhir. Feedback dalam bahasa Indonesia.\nFormat JSON: {\"score\": <number>, \"feedback\": \"<string>\"}"},
            {"id": "bahasa", "label": "Bahasa (Inggris/Indonesia/Arab/Mandarin)", "template": "Kamu adalah asisten koreksi mata pelajaran Bahasa (Indonesia, Inggris, Arab, Mandarin).\n\nSoal: {question}\nPedoman Penskoran: {rubric}\nBobot Maksimal: {max_score} poin\nJawaban Siswa: \"{answer}\"\n\nBerikan skor (0-{max_score}). Nilai berdasarkan: tata bahasa (grammar/tata bahasa), kosa kata (vocabulary/kosakata), struktur tulisan, kesesuaian konteks, dan kreativitas. Feedback dalam bahasa Indonesia.\nFormat JSON: {\"score\": <number>, \"feedback\": \"<string>\"}"},
            {"id": "ips", "label": "IPS / Sosial", "template": "Kamu adalah asisten koreksi mata pelajaran IPS (Geografi, Sosiologi, Ekonomi).\n\nSoal: {question}\nPedoman Penskoran: {rubric}\nBobot Maksimal: {max_score} poin\nJawaban Siswa: \"{answer}\"\n\nBerikan skor (0-{max_score}). Nilai berdasarkan: kedalaman analisis, penggunaan data/contoh konkret, argumen logis, dan keterkaitan antar konsep sosial. Feedback dalam bahasa Indonesia.\nFormat JSON: {\"score\": <number>, \"feedback\": \"<string>\"}"},
            {"id": "ict", "label": "ICT / Coding", "template": "Kamu adalah asisten koreksi mata pelajaran ICT, Coding, dan Computer Science.\n\nSoal: {question}\nPedoman Penskoran: {rubric}\nBobot Maksimal: {max_score} poin\nJawaban Siswa: \"{answer}\"\n\nBerikan skor (0-{max_score}). Nilai berdasarkan: kebenaran logika algoritma, sintaks kode, efisiensi solusi, dan dokumentasi/penjelasan. Feedback dalam bahasa Indonesia.\nFormat JSON: {\"score\": <number>, \"feedback\": \"<string>\"}"},
            {"id": "agama", "label": "Agama", "template": "Kamu adalah asisten koreksi mata pelajaran Pendidikan Agama.\n\nSoal: {question}\nPedoman Penskoran: {rubric}\nBobot Maksimal: {max_score} poin\nJawaban Siswa: \"{answer}\"\n\nBerikan skor (0-{max_score}). Nilai berdasarkan: pemahaman konsep keagamaan, ketepatan dalil/sumber, implementasi dalam kehidupan, dan sikap toleransi. Feedback dalam bahasa Indonesia.\nFormat JSON: {\"score\": <number>, \"feedback\": \"<string>\"}"},
            {"id": "penjas", "label": "PJOK / Olahraga", "template": "Kamu adalah asisten koreksi mata pelajaran Pendidikan Jasmani, Olahraga, dan Kesehatan.\n\nSoal: {question}\nPedoman Penskoran: {rubric}\nBobot Maksimal: {max_score} poin\nJawaban Siswa: \"{answer}\"\n\nBerikan skor (0-{max_score}). Nilai berdasarkan: pemahaman teori olahraga, teknik gerakan, keselamatan, dan kebugaran jasmani. Feedback dalam bahasa Indonesia.\nFormat JSON: {\"score\": <number>, \"feedback\": \"<string>\"}"},
            {"id": "iot", "label": "IoT / Teknologi", "template": "Kamu adalah asisten koreksi mata pelajaran Internet of Things dan Teknologi Embedded.\n\nSoal: {question}\nPedoman Penskoran: {rubric}\nBobot Maksimal: {max_score} poin\nJawaban Siswa: \"{answer}\"\n\nBerikan skor (0-{max_score}). Nilai berdasarkan: pemahaman sistem IoT, integrasi sensor, jaringan komunikasi, dan pemecahan masalah teknis. Feedback dalam bahasa Indonesia.\nFormat JSON: {\"score\": <number>, \"feedback\": \"<string>\"}"},
            {"id": "ketat", "label": "Ketat (Semua Mapel)", "template": "Kamu adalah pemeriksa ujian yang sangat ketat. Koreksi jawaban esai berikut.\n\nSoal: {question}\nBobot Maksimal: {max_score} poin\nJawaban: \"{answer}\"\n\nBerikan skor (0-{max_score}). Jangan mudah memberi nilai tinggi. Feedback harus menyebutkan kekurangan secara spesifik.\nFormat JSON: {\"score\": <number>, \"feedback\": \"<string>\"}"},
            {"id": "ringan", "label": "Ringan (Semua Mapel)", "template": "Kamu adalah guru yang baik hati dan memotivasi. Koreksi jawaban esai berikut.\n\nSoal: {question}\nBobot Maksimal: {max_score} poin\nJawaban: \"{answer}\"\n\nBerikan skor (0-{max_score}). Beri nilai maksimal jika jawaban mendekati benar. Feedback yang membangun dan memotivasi.\nFormat JSON: {\"score\": <number>, \"feedback\": \"<string>\"}"},
        ]
        supabase.table("teacher_ai_settings").insert({
            "teacher_id": g.user_id, "prompts": default_prompts, "active_prompt_id": "default"
        }).execute()
        settings = {"teacher_id": g.user_id, "prompts": default_prompts, "active_prompt_id": "default"}
    else:
        settings = settings_res.data[0]
        pr = settings.get("prompts")
        if isinstance(pr, str):
            try:
                settings["prompts"] = json.loads(pr)
            except (json.JSONDecodeError, TypeError):
                settings["prompts"] = []
        elif pr is None:
            settings["prompts"] = []
        # Auto-upgrade if less than 3 prompts (old version)
        try:
            if len(settings.get("prompts", [])) < 3:
                default_prompts = [
                    {"id": "default", "label": "Default (Semua Mapel)", "template": "Kamu adalah asisten koreksi ujian. Koreksi jawaban esai berikut berdasarkan soal dan bobot maksimal.\n\nSoal: {question}\nPedoman Penskoran: {rubric}\nBobot Maksimal: {max_score} poin\nJawaban Siswa: \"{answer}\"\n\nBerikan skor (0-{max_score}) dan feedback singkat dalam bahasa Indonesia.\nFormat JSON: {\"score\": <number>, \"feedback\": \"<string>\"}"},
                    {"id": "ipa", "label": "IPA / Sains", "template": "Kamu adalah asisten koreksi mata pelajaran IPA (Fisika, Kimia, Biologi, Earth Science).\n\nSoal: {question}\nPedoman Penskoran: {rubric}\nBobot Maksimal: {max_score} poin\nJawaban Siswa: \"{answer}\"\n\nBerikan skor (0-{max_score}). Nilai berdasarkan: keakuratan konsep sains, penggunaan istilah ilmiah yang tepat, logika ilmiah, dan kelengkapan jawaban. Feedback dalam bahasa Indonesia.\nFormat JSON: {\"score\": <number>, \"feedback\": \"<string>\"}"},
                    {"id": "matematika", "label": "Matematika", "template": "Kamu adalah asisten koreksi mata pelajaran Matematika.\n\nSoal: {question}\nPedoman Penskoran: {rubric}\nBobot Maksimal: {max_score} poin\nJawaban Siswa: \"{answer}\"\n\nBerikan skor (0-{max_score}). Nilai berdasarkan: kebenaran rumus, ketepatan langkah-langkah penyelesaian, keakuratan perhitungan, dan kesimpulan akhir. Feedback dalam bahasa Indonesia.\nFormat JSON: {\"score\": <number>, \"feedback\": \"<string>\"}"},
                    {"id": "bahasa", "label": "Bahasa (Inggris/Indonesia/Arab/Mandarin)", "template": "Kamu adalah asisten koreksi mata pelajaran Bahasa (Indonesia, Inggris, Arab, Mandarin).\n\nSoal: {question}\nPedoman Penskoran: {rubric}\nBobot Maksimal: {max_score} poin\nJawaban Siswa: \"{answer}\"\n\nBerikan skor (0-{max_score}). Nilai berdasarkan: tata bahasa (grammar/tata bahasa), kosa kata (vocabulary/kosakata), struktur tulisan, kesesuaian konteks, dan kreativitas. Feedback dalam bahasa Indonesia.\nFormat JSON: {\"score\": <number>, \"feedback\": \"<string>\"}"},
                    {"id": "ips", "label": "IPS / Sosial", "template": "Kamu adalah asisten koreksi mata pelajaran IPS (Geografi, Sosiologi, Ekonomi).\n\nSoal: {question}\nPedoman Penskoran: {rubric}\nBobot Maksimal: {max_score} poin\nJawaban Siswa: \"{answer}\"\n\nBerikan skor (0-{max_score}). Nilai berdasarkan: kedalaman analisis, penggunaan data/contoh konkret, argumen logis, dan keterkaitan antar konsep sosial. Feedback dalam bahasa Indonesia.\nFormat JSON: {\"score\": <number>, \"feedback\": \"<string>\"}"},
                    {"id": "ict", "label": "ICT / Coding", "template": "Kamu adalah asisten koreksi mata pelajaran ICT, Coding, dan Computer Science.\n\nSoal: {question}\nPedoman Penskoran: {rubric}\nBobot Maksimal: {max_score} poin\nJawaban Siswa: \"{answer}\"\n\nBerikan skor (0-{max_score}). Nilai berdasarkan: kebenaran logika algoritma, sintaks kode, efisiensi solusi, dan dokumentasi/penjelasan. Feedback dalam bahasa Indonesia.\nFormat JSON: {\"score\": <number>, \"feedback\": \"<string>\"}"},
                    {"id": "agama", "label": "Agama", "template": "Kamu adalah asisten koreksi mata pelajaran Pendidikan Agama.\n\nSoal: {question}\nPedoman Penskoran: {rubric}\nBobot Maksimal: {max_score} poin\nJawaban Siswa: \"{answer}\"\n\nBerikan skor (0-{max_score}). Nilai berdasarkan: pemahaman konsep keagamaan, ketepatan dalil/sumber, implementasi dalam kehidupan, dan sikap toleransi. Feedback dalam bahasa Indonesia.\nFormat JSON: {\"score\": <number>, \"feedback\": \"<string>\"}"},
                    {"id": "penjas", "label": "PJOK / Olahraga", "template": "Kamu adalah asisten koreksi mata pelajaran Pendidikan Jasmani, Olahraga, dan Kesehatan.\n\nSoal: {question}\nPedoman Penskoran: {rubric}\nBobot Maksimal: {max_score} poin\nJawaban Siswa: \"{answer}\"\n\nBerikan skor (0-{max_score}). Nilai berdasarkan: pemahaman teori olahraga, teknik gerakan, keselamatan, dan kebugaran jasmani. Feedback dalam bahasa Indonesia.\nFormat JSON: {\"score\": <number>, \"feedback\": \"<string>\"}"},
                    {"id": "iot", "label": "IoT / Teknologi", "template": "Kamu adalah asisten koreksi mata pelajaran Internet of Things dan Teknologi Embedded.\n\nSoal: {question}\nPedoman Penskoran: {rubric}\nBobot Maksimal: {max_score} poin\nJawaban Siswa: \"{answer}\"\n\nBerikan skor (0-{max_score}). Nilai berdasarkan: pemahaman sistem IoT, integrasi sensor, jaringan komunikasi, dan pemecahan masalah teknis. Feedback dalam bahasa Indonesia.\nFormat JSON: {\"score\": <number>, \"feedback\": \"<string>\"}"},
                    {"id": "ketat", "label": "Ketat (Semua Mapel)", "template": "Kamu adalah pemeriksa ujian yang sangat ketat. Koreksi jawaban esai berikut.\n\nSoal: {question}\nBobot Maksimal: {max_score} poin\nJawaban: \"{answer}\"\n\nBerikan skor (0-{max_score}). Jangan mudah memberi nilai tinggi. Feedback harus menyebutkan kekurangan secara spesifik.\nFormat JSON: {\"score\": <number>, \"feedback\": \"<string>\"}"},
                    {"id": "ringan", "label": "Ringan (Semua Mapel)", "template": "Kamu adalah guru yang baik hati dan memotivasi. Koreksi jawaban esai berikut.\n\nSoal: {question}\nBobot Maksimal: {max_score} poin\nJawaban: \"{answer}\"\n\nBerikan skor (0-{max_score}). Beri nilai maksimal jika jawaban mendekati benar. Feedback yang membangun dan memotivasi.\nFormat JSON: {\"score\": <number>, \"feedback\": \"<string>\"}"},
                ]
                supabase.table("teacher_ai_settings").update({
                    "prompts": default_prompts, "active_prompt_id": "default"
                }).eq("teacher_id", g.user_id).execute()
                settings["prompts"] = default_prompts
                settings["active_prompt_id"] = "default"
        except Exception as e:
            current_app.logger.error(f"Auto-upgrade prompts error: {e}")
    return render_template("teacher/ai_settings.html", keys=keys, settings=settings)


@teacher_bp.route("/ai-settings/add-key", methods=["POST"])
@subscription_write_required
@teacher_or_admin_required
def ai_add_key():
    supabase = get_supabase()
    provider = request.form.get("provider", "gemini")
    api_key = request.form.get("api_key", "").strip()
    label = request.form.get("label", "").strip()
    base_url = request.form.get("base_url", "").strip()
    model_name = request.form.get("model_name", "").strip()
    if not api_key:
        flash("API Key wajib diisi", "error")
        return redirect("/teacher/ai-settings")
    supabase.table("teacher_ai_keys").update({"is_active": False}).eq("teacher_id", g.user_id).execute()
    data = {
        "teacher_id": g.user_id, "provider": provider,
        "api_key": api_key, "label": label or provider,
        "is_active": True,
    }
    if provider == "custom":
        data["base_url"] = base_url
        data["model_name"] = model_name or "gpt-4o-mini"
    supabase.table("teacher_ai_keys").insert(data).execute()
    flash("API Key berhasil ditambahkan dan diaktifkan", "success")
    return redirect("/teacher/ai-settings")


@teacher_bp.route("/ai-settings/<key_id>/toggle", methods=["POST"])
@subscription_write_required
@teacher_or_admin_required
def ai_toggle_key(key_id):
    supabase = get_supabase()
    key = supabase.table("teacher_ai_keys").select("is_active").eq("id", key_id).eq("teacher_id", g.user_id).single().execute()
    if key.data:
        if key.data.get("is_active"):
            supabase.table("teacher_ai_keys").update({"is_active": False}).eq("id", key_id).execute()
        else:
            supabase.table("teacher_ai_keys").update({"is_active": False}).eq("teacher_id", g.user_id).execute()
            supabase.table("teacher_ai_keys").update({"is_active": True}).eq("id", key_id).execute()
    return redirect("/teacher/ai-settings")


@teacher_bp.route("/ai-settings/<key_id>/delete", methods=["POST"])
@subscription_write_required
@teacher_or_admin_required
def ai_delete_key(key_id):
    supabase = get_supabase()
    supabase.table("teacher_ai_keys").delete().eq("id", key_id).eq("teacher_id", g.user_id).execute()
    flash("API Key berhasil dihapus", "success")
    return redirect("/teacher/ai-settings")


@teacher_bp.route("/ai-settings/save-prompt", methods=["POST"])
@teacher_or_admin_required
def ai_save_prompt():
    supabase = get_supabase()
    prompts_raw = request.form.get("prompts", "[]").strip()
    active_id = request.form.get("active_prompt_id", "default").strip()
    try:
        prompts = json.loads(prompts_raw)
    except json.JSONDecodeError:
        flash("Data prompts tidak valid", "error")
        return redirect("/teacher/ai-settings")
    supabase.table("teacher_ai_settings").upsert({
        "teacher_id": g.user_id, "prompts": prompts, "active_prompt_id": active_id
    }).execute()
    flash("Prompt berhasil disimpan", "success")
    return redirect("/teacher/ai-settings")


@teacher_bp.route("/ai-settings/reset-prompt", methods=["POST"])
@teacher_or_admin_required
def ai_reset_prompt():
    default_prompts = [
        {"id": "default", "label": "Default (Semua Mapel)", "template": "Kamu adalah asisten koreksi ujian. Koreksi jawaban esai berikut berdasarkan soal dan bobot maksimal.\n\nSoal: {question}\nPedoman Penskoran: {rubric}\nBobot Maksimal: {max_score} poin\nJawaban Siswa: \"{answer}\"\n\nBerikan skor (0-{max_score}) dan feedback singkat dalam bahasa Indonesia.\nFormat JSON: {\"score\": <number>, \"feedback\": \"<string>\"}"},
        {"id": "ipa", "label": "IPA / Sains", "template": "Kamu adalah asisten koreksi mata pelajaran IPA (Fisika, Kimia, Biologi, Earth Science).\n\nSoal: {question}\nPedoman Penskoran: {rubric}\nBobot Maksimal: {max_score} poin\nJawaban Siswa: \"{answer}\"\n\nBerikan skor (0-{max_score}). Nilai berdasarkan: keakuratan konsep sains, penggunaan istilah ilmiah yang tepat, logika ilmiah, dan kelengkapan jawaban. Feedback dalam bahasa Indonesia.\nFormat JSON: {\"score\": <number>, \"feedback\": \"<string>\"}"},
        {"id": "matematika", "label": "Matematika", "template": "Kamu adalah asisten koreksi mata pelajaran Matematika.\n\nSoal: {question}\nPedoman Penskoran: {rubric}\nBobot Maksimal: {max_score} poin\nJawaban Siswa: \"{answer}\"\n\nBerikan skor (0-{max_score}). Nilai berdasarkan: kebenaran rumus, ketepatan langkah-langkah penyelesaian, keakuratan perhitungan, dan kesimpulan akhir. Feedback dalam bahasa Indonesia.\nFormat JSON: {\"score\": <number>, \"feedback\": \"<string>\"}"},
        {"id": "bahasa", "label": "Bahasa (Inggris/Indonesia/Arab/Mandarin)", "template": "Kamu adalah asisten koreksi mata pelajaran Bahasa (Indonesia, Inggris, Arab, Mandarin).\n\nSoal: {question}\nPedoman Penskoran: {rubric}\nBobot Maksimal: {max_score} poin\nJawaban Siswa: \"{answer}\"\n\nBerikan skor (0-{max_score}). Nilai berdasarkan: tata bahasa (grammar/tata bahasa), kosa kata (vocabulary/kosakata), struktur tulisan, kesesuaian konteks, dan kreativitas. Feedback dalam bahasa Indonesia.\nFormat JSON: {\"score\": <number>, \"feedback\": \"<string>\"}"},
        {"id": "ips", "label": "IPS / Sosial", "template": "Kamu adalah asisten koreksi mata pelajaran IPS (Geografi, Sosiologi, Ekonomi).\n\nSoal: {question}\nPedoman Penskoran: {rubric}\nBobot Maksimal: {max_score} poin\nJawaban Siswa: \"{answer}\"\n\nBerikan skor (0-{max_score}). Nilai berdasarkan: kedalaman analisis, penggunaan data/contoh konkret, argumen logis, dan keterkaitan antar konsep sosial. Feedback dalam bahasa Indonesia.\nFormat JSON: {\"score\": <number>, \"feedback\": \"<string>\"}"},
        {"id": "ict", "label": "ICT / Coding", "template": "Kamu adalah asisten koreksi mata pelajaran ICT, Coding, dan Computer Science.\n\nSoal: {question}\nPedoman Penskoran: {rubric}\nBobot Maksimal: {max_score} poin\nJawaban Siswa: \"{answer}\"\n\nBerikan skor (0-{max_score}). Nilai berdasarkan: kebenaran logika algoritma, sintaks kode, efisiensi solusi, dan dokumentasi/penjelasan. Feedback dalam bahasa Indonesia.\nFormat JSON: {\"score\": <number>, \"feedback\": \"<string>\"}"},
        {"id": "agama", "label": "Agama", "template": "Kamu adalah asisten koreksi mata pelajaran Pendidikan Agama.\n\nSoal: {question}\nPedoman Penskoran: {rubric}\nBobot Maksimal: {max_score} poin\nJawaban Siswa: \"{answer}\"\n\nBerikan skor (0-{max_score}). Nilai berdasarkan: pemahaman konsep keagamaan, ketepatan dalil/sumber, implementasi dalam kehidupan, dan sikap toleransi. Feedback dalam bahasa Indonesia.\nFormat JSON: {\"score\": <number>, \"feedback\": \"<string>\"}"},
        {"id": "penjas", "label": "PJOK / Olahraga", "template": "Kamu adalah asisten koreksi mata pelajaran Pendidikan Jasmani, Olahraga, dan Kesehatan.\n\nSoal: {question}\nPedoman Penskoran: {rubric}\nBobot Maksimal: {max_score} poin\nJawaban Siswa: \"{answer}\"\n\nBerikan skor (0-{max_score}). Nilai berdasarkan: pemahaman teori olahraga, teknik gerakan, keselamatan, dan kebugaran jasmani. Feedback dalam bahasa Indonesia.\nFormat JSON: {\"score\": <number>, \"feedback\": \"<string>\"}"},
        {"id": "iot", "label": "IoT / Teknologi", "template": "Kamu adalah asisten koreksi mata pelajaran Internet of Things dan Teknologi Embedded.\n\nSoal: {question}\nPedoman Penskoran: {rubric}\nBobot Maksimal: {max_score} poin\nJawaban Siswa: \"{answer}\"\n\nBerikan skor (0-{max_score}). Nilai berdasarkan: pemahaman sistem IoT, integrasi sensor, jaringan komunikasi, dan pemecahan masalah teknis. Feedback dalam bahasa Indonesia.\nFormat JSON: {\"score\": <number>, \"feedback\": \"<string>\"}"},
        {"id": "ketat", "label": "Ketat (Semua Mapel)", "template": "Kamu adalah pemeriksa ujian yang sangat ketat. Koreksi jawaban esai berikut.\n\nSoal: {question}\nBobot Maksimal: {max_score} poin\nJawaban: \"{answer}\"\n\nBerikan skor (0-{max_score}). Jangan mudah memberi nilai tinggi. Feedback harus menyebutkan kekurangan secara spesifik.\nFormat JSON: {\"score\": <number>, \"feedback\": \"<string>\"}"},
        {"id": "ringan", "label": "Ringan (Semua Mapel)", "template": "Kamu adalah guru yang baik hati dan memotivasi. Koreksi jawaban esai berikut.\n\nSoal: {question}\nBobot Maksimal: {max_score} poin\nJawaban: \"{answer}\"\n\nBerikan skor (0-{max_score}). Beri nilai maksimal jika jawaban mendekati benar. Feedback yang membangun dan memotivasi.\nFormat JSON: {\"score\": <number>, \"feedback\": \"<string>\"}"},
    ]
    supabase = get_supabase()
    supabase.table("teacher_ai_settings").upsert({
        "teacher_id": g.user_id, "prompts": default_prompts, "active_prompt_id": "default"
    }).execute()
    flash("Prompt dikembalikan ke default", "success")
    return redirect("/teacher/ai-settings")


@teacher_bp.route("/students")
@teacher_or_admin_required
def students():
    supabase = get_supabase()
    school_id = g.get("user_school_id")
    query = supabase.table("profiles").select("id,full_name,phone,role").eq("role", "murid")
    if school_id:
        query = query.eq("school_id", school_id)
    students = query.execute().data or []
    exam_ids = [e["id"] for e in supabase.table("exams").select("id").eq("teacher_id", g.user_id).execute().data or []]
    if exam_ids:
        subs = supabase.table("submissions").select("student_id,score,final_score").in_("exam_id", exam_ids).execute().data or []
        sub_map = {}
        for s in subs:
            sid = s["student_id"]
            if sid not in sub_map:
                sub_map[sid] = []
            sc = float(s.get("final_score") or s.get("score") or 0)
            sub_map[sid].append(sc)
        for st in students:
            st_scores = sub_map.get(st["id"], [])
            st["sub_count"] = len(st_scores)
            st["avg_score"] = round(sum(st_scores) / len(st_scores), 1) if st_scores else None
    return render_template("teacher/students.html", students=students)


@teacher_bp.route("/classes")
@teacher_or_admin_required
def teacher_classes():
    supabase = get_supabase()
    sid = g.get("user_school_id")
    assignments = []
    school_info = {}
    active_year = None
    if sid:
        try:
            assignments = supabase.table("teacher_assignments") \
                .select("*, classes!inner(id, name), subjects!inner(id, name)") \
                .eq("teacher_id", g.user_id) \
                .eq("school_id", sid) \
                .execute().data or []
        except Exception:
            assignments = []
        try:
            sch = supabase.table("schools").select("name, npsn").eq("id", sid).single().execute()
            if sch.data: school_info = sch.data
        except: pass
        try:
            years = supabase.table("school_years").select("*").eq("school_id", sid).eq("is_active", True).limit(1).execute()
            if years.data: active_year = years.data[0]
        except: pass
    return render_template("teacher/classes.html", assignments=assignments,
                           school_info=school_info, active_year=active_year)


@teacher_bp.route("/tools/exam/<exam_id>/check-pdf", methods=["GET"])
@teacher_or_admin_required
@require_school_access("exams", "exam_id")
def exam_check_pdf(exam_id):
    """Diagnostic endpoint: returns exam's PDF fields and file status."""
    supabase = get_supabase()
    exam = supabase.table("exams").select("id,pdf_url,pdf_page_urls,title,status,answer_key").eq("id", exam_id).single().execute().data
    if not exam:
        return jsonify({"error": "not found"}), 404
    page_urls = exam.get("pdf_page_urls") or []
    pdf_url = exam.get("pdf_url") or ""
    exam_dir = os.path.join(current_app.root_path, "static", "uploads", "exams", exam_id)
    files_on_disk = []
    if os.path.isdir(exam_dir):
        files_on_disk = os.listdir(exam_dir)
    return jsonify({
        "exam_id": exam_id,
        "title": exam.get("title"),
        "status": exam.get("status"),
        "pdf_url": pdf_url,
        "pdf_page_urls_count": len(page_urls),
        "pdf_page_urls": page_urls[:3],
        "has_valid_pages": len(page_urls) > 0,
        "files_in_exam_dir": files_on_disk,
        "exam_dir": exam_dir,
        "root_path": current_app.root_path,
    })


@teacher_bp.route("/tools/exam/<exam_id>/reprocess-pdf", methods=["POST"])
@teacher_or_admin_required
@require_school_access("exams", "exam_id")
def exam_reprocess_pdf(exam_id):
    """Reprocess PDF for existing exam: regenerate local page images."""
    from app.services.pdf_service import upload_pdf

    supabase = get_supabase()
    exam = supabase.table("exams").select("pdf_url,pdf_page_urls,title").eq("id", exam_id).single().execute().data
    if not exam:
        return jsonify({"error": "not found"}), 404

    # Strategy 1: local exam.pdf already exists → regenerate from that
    local_pdf = os.path.join(current_app.root_path, "static", "uploads", "exams", exam_id, "exam.pdf")
    if os.path.exists(local_pdf):
        try:
            with open(local_pdf, "rb") as f:
                raw = f.read()
            if raw[:4] == b'%PDF':
                class _MF:
                    def __init__(self, d, n): self._d = d; self.filename = n
                    def read(self): return self._d
                result = upload_pdf(_MF(raw, "exam.pdf"), exam_id)
                supabase.table("exams").update({
                    "pdf_url": result["pdf_path"],
                    "pdf_page_urls": result["page_urls"],
                }).eq("id", exam_id).execute()
                return jsonify({"success": True, "source": "local_pdf", "pages": result["total_pages"]})
        except Exception as e:
            current_app.logger.warning("Reprocess from local PDF failed: %s", e)

    # Strategy 2: look for temp files in uploads/exams
    upload_dir = os.path.join(current_app.root_path, "static", "uploads", "exams")
    if os.path.isdir(upload_dir):
        temp_files = sorted(
            [os.path.join(upload_dir, f) for f in os.listdir(upload_dir)
             if f.startswith("temp_") and f.endswith(".pdf")],
            key=os.path.getmtime, reverse=True
        )
        for fp in temp_files:
            try:
                with open(fp, "rb") as f:
                    raw = f.read()
                if raw[:4] != b'%PDF':
                    continue
                class _MF:
                    def __init__(self, d, n): self._d = d; self.filename = n
                    def read(self): return self._d
                result = upload_pdf(_MF(raw, "exam.pdf"), exam_id)
                supabase.table("exams").update({
                    "pdf_url": result["pdf_path"],
                    "pdf_page_urls": result["page_urls"],
                }).eq("id", exam_id).execute()
                return jsonify({"success": True, "source": "temp_file", "file": os.path.basename(fp), "pages": result["total_pages"]})
            except Exception as e:
                current_app.logger.warning("Reprocess from temp file failed: %s", e)
                continue

    return jsonify({"error": "No PDF source found (no local PDF, no temp files)"}), 404


@teacher_bp.route("/exams/<exam_id>/proctoring")
@teacher_or_admin_required
def exam_proctoring(exam_id):
    """Proctoring dashboard — live view of student exam progress."""
    supabase = get_supabase()
    exam, err = _guard_exam(
        supabase, exam_id, as_json=_wants_json(), redirect_to="/teacher/exams",
        columns="id,teacher_id,school_id,title,subject,total_questions,"
                "duration_minutes,start_at,status")
    if err:
        return err
    return render_template("teacher/proctoring.html", exam=exam, exam_id=exam_id)


@teacher_bp.route("/api/exams/<exam_id>/proctoring-data")
@teacher_or_admin_required
def exam_proctoring_data(exam_id):
    """API: return live proctoring data (submissions + violations) for an exam."""
    supabase = get_supabase()

    # Live answers and violation detail for every student in the room — scoped to
    # the exam's owner (or the school's admin), like the grading queue.
    exam, err = _guard_exam(supabase, exam_id,
                            columns="id,teacher_id,school_id,class_ids,total_questions")
    if err:
        return err
    class_ids = exam.get("class_ids") or []
    total_q = exam.get("total_questions", 0)

    students = []
    if class_ids:
        # The class a pupil sits in is `profiles.class_id`; there is no
        # `student_classes` join table in this database, and the request for one
        # did not return an empty list — PostgREST answers `PGRST205` and the
        # route raised, so the proctoring panel was a 500 rather than a room.
        students = supabase.table("profiles") \
            .select("id,full_name") \
            .in_("class_id", class_ids) \
            .eq("role", "murid") \
            .execute().data or []

    # Get submissions for this exam
    subs = supabase.table("submissions") \
        .select("student_id,status,answers,submitted_at,updated_at") \
        .eq("exam_id", exam_id) \
        .execute().data or []

    sub_map = {s["student_id"]: s for s in subs}

    # Get violation counts
    try:
        viols = supabase.table("violation_logs") \
            .select("user_id") \
            .eq("exam_id", exam_id) \
            .execute().data or []
    except Exception:
        viols = []

    viol_count = {}
    for v in viols:
        uid = v.get("user_id", "")
        viol_count[uid] = viol_count.get(uid, 0) + 1

    now = datetime.now(timezone.utc)

    result = []
    for s in students:
        sid = s["id"]
        sub = sub_map.get(sid)
        answers = sub.get("answers") or {} if sub else {}
        if isinstance(answers, str):
            try:
                answers = json.loads(answers)
            except Exception:
                answers = {}

        # Count how many questions have answers
        ans_count = 0
        if isinstance(answers, dict):
            for v in answers.values():
                if isinstance(v, dict) and v.get("answer"):
                    ans_count += 1
                elif isinstance(v, str) and v:
                    ans_count += 1
                elif isinstance(v, dict):
                    ans_count += 1

        result.append({
            "id": sid,
            "name": s.get("full_name", sid[:12]),
            "status": sub.get("status", "not_started") if sub else "not_started",
            "answers_count": ans_count,
            "total_questions": total_q,
            "violations": viol_count.get(sid, 0),
            "updated_at": (sub.get("updated_at") or sub.get("submitted_at") or "").split(".")[0].replace("T", " ") if sub else "",
        })

    return jsonify({
        "students": result,
        "timestamp": now.isoformat(),
        "total_students": len(result),
        "started": sum(1 for r in result if r["status"] != "not_started"),
        "submitted": sum(1 for r in result if r["status"] in ("submitted", "graded", "published")),
    })


@teacher_bp.route("/exams/<exam_id>/generate-remedial", methods=["POST"])
@teacher_or_admin_required
@require_school_access("exams", "exam_id")
def generate_remedial(exam_id):
    """Analyze exam results and generate remedial questions via AI."""
    supabase = get_supabase()
    exam = supabase.table("exams").select("*").eq("id", exam_id).single().execute().data
    if not exam:
        return jsonify({"error": "Exam not found"}), 404

    # Get submissions with answers
    subs = supabase.table("submissions").select("id,student_id,answers,final_score").eq("exam_id", exam_id).execute().data or []

    # Analyze MCQ scores per question
    qtypes = exam.get("question_types") or {}
    if isinstance(qtypes, str):
        try: qtypes = json.loads(qtypes)
        except: qtypes = {}
    answer_key = exam.get("answer_key") or {}
    if isinstance(answer_key, str):
        try: answer_key = json.loads(answer_key)
        except: answer_key = {}
    question_texts = exam.get("question_texts") or {}
    if isinstance(question_texts, str):
        try: question_texts = json.loads(question_texts)
        except: question_texts = {}
    total_q = exam.get("total_questions", 0)

    # Count correct/wrong per MCQ question
    q_correct = {}
    q_total = {}
    for sub in subs:
        answers = sub.get("answers") or {}
        if isinstance(answers, str):
            try: answers = json.loads(answers)
            except: answers = {}
        for qi in range(total_q):
            qi_str = str(qi)
            if is_objective(qtypes.get(qi_str)):
                q_total[qi_str] = q_total.get(qi_str, 0) + 1
                if qi_str in answer_key and qi_str in answers and \
                        grade_answer(qtypes.get(qi_str), answer_key[qi_str], answers[qi_str]):
                    q_correct[qi_str] = q_correct.get(qi_str, 0) + 1

    # Find top 3 most-failed objective questions
    fail_rate = []
    for qi in range(total_q):
        qi_str = str(qi)
        if q_total.get(qi_str, 0) > 0:
            correct = q_correct.get(qi_str, 0)
            total = q_total[qi_str]
            rate = (total - correct) / total
            fail_rate.append((qi, rate, q_total[qi_str]))

    fail_rate.sort(key=lambda x: x[1], reverse=True)
    worst_q = fail_rate[:3]

    # Build prompt for AI
    prompt_parts = ["Buat 5 soal remedial tipe MCQ berdasarkan analisis berikut:\n"]
    prompt_parts.append(f"Ujian: {exam.get('title', '')}\n")
    prompt_parts.append(f"Mata Pelajaran: {exam.get('subject', '')}\n\n")

    if worst_q:
        prompt_parts.append("Soal dengan tingkat kesalahan tertinggi:\n")
        for qi, rate, total in worst_q:
            q_text = question_texts.get(str(qi), f"Soal {qi+1}")
            q_key = answer_key.get(str(qi), "-")
            prompt_parts.append(f"Soal {qi+1} (salah {total - q_correct.get(str(qi), 0)}/{total} siswa): {q_text} (kunci: {q_key})")

    prompt_parts.append("""
    \nBuat 5 soal pilihan ganda dengan 5 opsi (A-E) yang mirip dengan soal-soal di atas.
    Setiap soal harus memiliki: nomor, pertanyaan, 5 opsi (A-E), dan kunci jawaban.

    Format output JSON:
    {"questions": [{"number": 1, "question": "teks soal", "options": {"A": "...", "B": "...", "C": "...", "D": "...", "E": "..."}, "answer": "A"}]}
    """)

    prompt = "\n".join(prompt_parts)

    # Call AI
    from app.services.ai_service import _get_active_key, _call_ai
    key = _get_active_key(g.user_id)
    if not key:
        return jsonify({"error": "Belum ada API key AI. Atur di Pengaturan AI."}), 400

    try:
        raw = _call_ai(key, prompt)
        import re
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*|```$", "", cleaned, flags=re.DOTALL).strip()
        data = json.loads(cleaned)
        questions = data.get("questions", [])
    except Exception as e:
        return jsonify({"error": f"AI gagal generate: {str(e)[:150]}"}), 500

    if not questions:
        return jsonify({"error": "AI tidak menghasilkan soal"}), 500

    # Create a new exam draft with remedial questions
    title = f"Remedial - {exam.get('title', 'Ujian')}"
    new_qtypes = {}
    new_answer_key = {}
    new_qtexts = {}
    for q in questions:
        qi = q["number"] - 1
        new_qtypes[str(qi)] = "mcq"
        new_answer_key[str(qi)] = q.get("answer", "A")
        opts = q.get("options", {})
        txt = q["question"]
        for k, v in opts.items():
            txt += f"\n{k}. {v}"
        new_qtexts[str(qi)] = txt

    total_new = len(questions)
    new_exam = {
        "teacher_id": g.user_id,
        "school_id": g.get("user_school_id"),
        "title": title,
        "subject": exam.get("subject", ""),
        "class_ids": exam.get("class_ids", []),
        "total_questions": total_new,
        "duration_minutes": min(exam.get("duration_minutes", 60) // 2, 30),
        "description": f"Soal remedial otomatis dari ujian {exam.get('title', '')}",
        "status": "draft",
        "is_published": False,
        "publish_mode": "manual",
        "question_types": new_qtypes,
        "answer_key": new_answer_key,
        "question_texts": new_qtexts,
    }

    try:
        res = supabase.table("exams").insert(new_exam).execute()
        new_id = res.data[0]["id"]
        return jsonify({"success": True, "redirect": f"/teacher/exams/{new_id}"})
    except Exception:
        # Fallback: try without newer fields
        for key in ["question_texts", "publish_mode"]:
            new_exam.pop(key, None)
        res = supabase.table("exams").insert(new_exam).execute()
        new_id = res.data[0]["id"]
        return jsonify({"success": True, "redirect": f"/teacher/exams/{new_id}"})


@teacher_bp.route("/exams/<exam_id>/cheat-analysis")
@teacher_or_admin_required
def cheat_analysis(exam_id):
    """Cheat pattern detection dashboard."""
    _, err = _guard_exam(get_supabase(), exam_id, as_json=_wants_json(),
                         redirect_to="/teacher/exams")
    if err:
        return err
    return render_template("teacher/cheat_analysis.html", exam_id=exam_id)


@teacher_bp.route("/api/exams/<exam_id>/cheat-data")
@teacher_or_admin_required
def cheat_analysis_data(exam_id):
    """API: analyze submissions for cheating patterns."""
    supabase = get_supabase()
    # Returns every student's answers and the answer key, so it follows the same
    # rule — and the row the check loads is the row this handler needs.
    exam, err = _guard_exam(
        supabase, exam_id,
        columns="id,teacher_id,school_id,title,question_types,answer_key,total_questions")
    if err:
        return err

    answer_key = exam.get("answer_key") or {}
    if isinstance(answer_key, str):
        try: answer_key = json.loads(answer_key)
        except: answer_key = {}
    qtypes = exam.get("question_types") or {}
    if isinstance(qtypes, str):
        try: qtypes = json.loads(qtypes)
        except: qtypes = {}

    subs = supabase.table("submissions") \
        .select("id,student_id,answers,submitted_at,created_at,profiles(full_name)") \
        .eq("exam_id", exam_id) \
        .in_("status", ["submitted", "graded", "published"]) \
        .execute().data or []

    parsed = []
    for s in subs:
        answers = s.get("answers") or {}
        if isinstance(answers, str):
            try: answers = json.loads(answers)
            except: answers = {}
        profile = s.get("profiles") or {}
        parsed.append({
            "id": s["id"],
            "student_id": s["student_id"],
            "name": profile.get("full_name", s["student_id"][:12]),
            "answers": answers,
            "submitted_at": s.get("submitted_at") or s.get("created_at") or "",
        })

    # 1. Identical Wrong Answer Detection
    total_q = exam.get("total_questions", 0)
    wrong_answers = {}
    for p in parsed:
        ans = p["answers"]
        wrong_pattern = []
        for qi in range(total_q):
            qi_str = str(qi)
            stu_ans = ans.get(qi_str, "")
            if isinstance(stu_ans, dict):
                stu_ans = stu_ans.get("answer", "")
            key = answer_key.get(qi_str, "")
            if key and stu_ans and stu_ans != key:
                wrong_pattern.append(f"{qi}:{stu_ans}")
        if wrong_pattern:
            pattern = "|".join(wrong_pattern)
            if pattern not in wrong_answers:
                wrong_answers[pattern] = []
            wrong_answers[pattern].append(p["name"])

    identical_groups = [{"students": v, "count": len(v), "pattern": k[:100]}
                        for k, v in wrong_answers.items() if len(v) >= 2]
    identical_groups.sort(key=lambda x: x["count"], reverse=True)

    # 2. Submission Timing Cluster
    from collections import defaultdict
    time_clusters = []
    timestamps = [(p["name"], p["submitted_at"]) for p in parsed if p.get("submitted_at")]
    import datetime
    from datetime import timezone
    for i, (n1, t1) in enumerate(timestamps):
        cluster = [n1]
        for j, (n2, t2) in enumerate(timestamps):
            if i != j and t1 and t2:
                try:
                    dt1 = datetime.datetime.fromisoformat(t1.replace("Z", "+00:00").split(".")[0])
                    dt2 = datetime.datetime.fromisoformat(t2.replace("Z", "+00:00").split(".")[0])
                    diff = abs((dt1 - dt2).total_seconds())
                    if diff < 3:
                        cluster.append(n2)
                except: pass
        if len(cluster) >= 3:
            cluster.sort()
            key = ",".join(cluster)
            if not any(key == c.get("key") for c in time_clusters):
                time_clusters.append({"key": key, "students": list(set(cluster)), "count": len(set(cluster))})

    time_clusters.sort(key=lambda x: x["count"], reverse=True)

    return jsonify({
        "exam_title": exam.get("title", ""),
        "identical_groups": identical_groups[:10],
        "time_clusters": time_clusters[:10],
        "total_students": len(parsed),
    })


@teacher_bp.route("/exams/<exam_id>/accreditation-report")
@teacher_or_admin_required
@require_school_access("exams", "exam_id")
def accreditation_report(exam_id):
    """Generate school accreditation report as PDF."""
    supabase = get_supabase()
    exam = supabase.table("exams").select("title,subject,teacher_id,total_questions,passing_score").eq("id", exam_id).single().execute().data or {}
    subs = supabase.table("submissions").select("score,final_score,status,student_id,profiles(full_name)").eq("exam_id", exam_id).in_("status", ["graded", "published"]).execute().data or []

    from app.services.export_service import _wrap_text
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    import io

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=20, bottomMargin=20)
    styles = getSampleStyleSheet()
    story = []

    # Title
    story.append(Paragraph(f"Laporan Hasil Ujian", styles["Title"]))
    story.append(Paragraph(f"{exam.get('title', '')} - {exam.get('subject', '')}", styles["Heading2"]))
    story.append(Spacer(1, 12))

    # Stats
    scores = [float(s.get("final_score") or s.get("score") or 0) for s in subs]
    avg = sum(scores) / len(scores) if scores else 0
    passed = sum(1 for s in scores if s >= (exam.get("passing_score", 70)))
    story.append(Paragraph(f"Jumlah Siswa: {len(subs)}", styles["Normal"]))
    story.append(Paragraph(f"Rata-rata: {avg:.1f}", styles["Normal"]))
    story.append(Paragraph(f"KKM: {exam.get('passing_score', 70)}", styles["Normal"]))
    story.append(Paragraph(f"Lulus: {passed}/{len(subs)} ({passed*100//len(subs) if subs else 0}%)", styles["Normal"]))
    story.append(Spacer(1, 20))

    # Score distribution table
    bins = {"0-39": 0, "40-59": 0, "60-79": 0, "80-100": 0}
    for s in scores:
        if s < 40: bins["0-39"] += 1
        elif s < 60: bins["40-59"] += 1
        elif s < 80: bins["60-79"] += 1
        else: bins["80-100"] += 1

    dist_data = [["Rentang Nilai", "Jumlah Siswa"]]
    for k, v in bins.items():
        dist_data.append([k, str(v)])
    dist_data.append(["Total", str(len(subs))])

    t = Table(dist_data, colWidths=[150, 100])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#3b82f6")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
    ]))
    story.append(t)
    story.append(Spacer(1, 20))

    # Student list
    story.append(Paragraph("Daftar Nilai Siswa:", styles["Heading3"]))
    student_data = [["No", "Nama", "Nilai", "Status"]]
    for i, s in enumerate(subs, 1):
        profile = s.get("profiles") or {}
        name = profile.get("full_name", s["student_id"][:12])
        score = float(s.get("final_score") or s.get("score") or 0)
        passed_txt = "Lulus" if score >= (exam.get("passing_score", 70)) else "Remedial"
        student_data.append([str(i), name, f"{score:.0f}", passed_txt])

    t2 = Table(student_data, colWidths=[30, 200, 60, 80])
    t2.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#3b82f6")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("ALIGN", (2, 0), (3, -1), "CENTER"),
    ]))
    story.append(t2)
    story.append(Spacer(1, 12))

    story.append(Paragraph(f"Dicetak: {datetime.now(timezone.utc).strftime('%d %B %Y')}", styles["Normal"]))

    doc.build(story)
    buf.seek(0)
    return send_file(buf, mimetype="application/pdf", as_attachment=True,
                     download_name=f"Laporan_{exam.get('title', 'Ujian')[:30]}.pdf")

@teacher_bp.route("/comms")
@login_required
def teacher_comms():
    return render_template("shared/comms.html")


@teacher_bp.route("/settings")
@login_required
def teacher_settings():
    """Teacher settings page (password, data export, deletion request)."""
    return render_template("teacher/settings.html")
