"""AI essay grading — a suggestion a teacher turns into a mark.

This module used to do three things at once, and the third one was the problem: it
built the prompt by pasting the pupil's answer after a rubric, it accepted whatever
number the model returned, and on the bulk path it wrote that number straight into
`submissions.answers`. A model could therefore decide a child's mark with no teacher
in the loop, and nothing in the schema said otherwise.

What it does now, and nothing else:

* **build** the prompt in one place (`ai_prompt.build_grading_prompt`), with the
  answer fenced as data and a fixed system turn that a rubric cannot rewrite;
* **run** it in a Celery worker, so a teacher's request returns immediately on a
  1-vCPU box instead of holding a gunicorn worker open for an external API's
  latency;
* **validate** the reply against the question's own points before storing it, and
  record what had to be corrected as an anomaly rather than quietly fixing it;
* **store** it as `ai_grading_suggestion`, a row whose status a *person* moves.

The one thing it no longer does is write a mark. `apply_suggestion` records that a
teacher accepted a suggestion and hands the number back for the form; the mark
itself is written by the grading flow the teacher already presses save in. That is
the whole difference between "AI helps grade" and "AI grades", and it is a
separation of tables rather than a comment.
"""
import json
import logging

from app.celery_app import celery_app
from app.services import ai_prompt
from app.services.question_types import is_essay

logger = logging.getLogger("app")

TABLE = "ai_grading_suggestion"

#: A suggestion in one of these states is still the answer to the question the
#: teacher asked; asking again is a duplicate, not a second opinion. `applied` and
#: `rejected` are decisions, and a decision does not block a new run — a teacher who
#: rejected a suggestion may reasonably ask again after editing the rubric.
OPEN_STATES = ("queued", "running", "ready")

#: How the prompt is built. Stored on every row so a mark can be explained by the
#: prompt that produced it even after this file changes.
PROMPT_VERSION = "v2-fenced"

#: A bulk request is a person clicking once on a class. Beyond this it is a script,
#: and a script asking for 400 API calls from a button is a bill nobody approved.
MAX_BULK = 100

#: Points for a question the exam carries no weight for. The same default the
#: marking page already uses (`question_weights[qIdx] || 100`), so the number the
#: teacher sees beside the field is the number the model was asked for.
DEFAULT_POINTS = 100.0


def _rows(result):
    return getattr(result, "data", None) or []


# ── reading the paper ────────────────────────────────────────────────────────

def question_points(exam: dict, question_index) -> float:
    """The marks question `question_index` is worth, from `question_weights`.

    `_scheme` is skipped explicitly rather than by accident: it rides in the same
    map (see `question_types.SCHEME_KEY`) and reading it as a question's points
    would give one question the whole mark scheme as its maximum.
    """
    weights = (exam or {}).get("question_weights") or {}
    if isinstance(weights, str):
        try:
            weights = json.loads(weights)
        except ValueError:
            weights = {}
    if not isinstance(weights, dict):
        weights = {}
    raw = weights.get(str(question_index))
    try:
        points = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_POINTS
    return points if points > 0 else DEFAULT_POINTS


def answer_text(submission: dict, question_index) -> str:
    """The pupil's words for one question, as stored.

    The answer lives in `submissions.answers`, keyed by question index, and its
    shape has drifted over time: a bare string, `{"answer": ...}`, `{"text": ...}`.
    `ai_prompt.plain_text` folds all three and strips the editor's markup, so this
    only has to find the right slot.
    """
    answers = (submission or {}).get("answers") or {}
    if isinstance(answers, str):
        try:
            answers = json.loads(answers)
        except ValueError:
            return ""
    if not isinstance(answers, dict):
        return ""
    value = answers.get(str(question_index))
    if isinstance(value, dict):
        for key in ("answer", "text", "value", "content"):
            if value.get(key):
                return str(value[key])
        return ""
    return str(value) if value else ""


def _criteria_for(teacher_id, supabase, subject=None) -> str:
    """The teacher's own grading prompt, read as *criteria* for this exam's subject.

    Placeholders are dropped for the same reason `ai_service.suggest_grade` drops
    them: `{answer}` inside the criteria would put the pupil's text back inside an
    instruction, which is the hole this module exists to close.

    `subject` is the exam's own, so a prompt a teacher wrote for Matematika is the
    one used when marking Matematika — otherwise "a different prompt for each
    subject" would be a page that describes behaviour the grader does not have.
    """
    from app.services.ai_service import _PLACEHOLDER, _get_active_prompt, _get_ai_settings

    try:
        settings = _get_ai_settings(teacher_id)
    except Exception as exc:                      # a settings row is not worth a 500
        logger.warning("AI settings unreadable for %s: %s", teacher_id, exc)
        return ""
    return _PLACEHOLDER.sub("", _get_active_prompt(settings, subject=subject) or "").strip()


def _exam_row(supabase, exam_id) -> dict:
    if not exam_id:
        return {}
    # Every window column this module reads is named: PostgREST reads a column left
    # out of a select as *absent*, never as an error, so a forgotten `question_weights`
    # would silently fall back to 100 points for the whole paper.
    res = supabase.table("exams").select(
        "id,subject,question_texts,question_weights,question_rubrics,diagram_contexts"
    ).eq("id", exam_id).limit(1).execute()
    return (_rows(res) or [{}])[0]


# ── creating a suggestion ────────────────────────────────────────────────────

def create_suggestion(supabase, *, teacher_id, submission_id, question_index,
                      exam_id=None, school_id=None, answer_override=None):
    """Queue one suggestion, or hand back the one already in flight.

    The duplicate check is what stops a double-clicked button from costing two
    provider calls; the partial unique index in migration 068 is the same rule at
    the database, because a check in Python alone loses the race between two
    workers.
    """
    existing = _rows(
        supabase.table(TABLE).select("*")
        .eq("submission_id", submission_id)
        .eq("question_index", int(question_index))
        .eq("teacher_id", teacher_id)
        .in_("status", list(OPEN_STATES))
        .limit(1).execute()
    )
    if existing:
        return existing[0], True

    payload = {
        "submission_id": submission_id,
        "question_index": int(question_index),
        "teacher_id": teacher_id,
        "exam_id": exam_id,
        "school_id": school_id,
        "status": "queued",
        "prompt_version": PROMPT_VERSION,
    }
    if answer_override:
        # Only the teacher's OCR reading of a canvas answer reaches this. It is
        # stored rather than posted to the worker because a task that takes its
        # text from a request body has no barrier at all — the fence in `ai_prompt`
        # would then be protecting a prompt whose contents arrived from outside.
        payload["answer_override"] = str(answer_override)[:ai_prompt.MAX_ANSWER_CHARS * 2]
    try:
        written = _rows(supabase.table(TABLE).insert(payload).execute())
    except Exception as exc:
        # The partial unique index refused it: a worker inserted the same row
        # between the read above and this write. Read it back rather than fail.
        logger.info("suggestion insert raced for %s Q%s: %s",
                    submission_id, question_index, exc)
        again = _rows(
            supabase.table(TABLE).select("*")
            .eq("submission_id", submission_id)
            .eq("question_index", int(question_index))
            .eq("teacher_id", teacher_id)
            .in_("status", list(OPEN_STATES))
            .limit(1).execute()
        )
        if again:
            return again[0], True
        raise
    row = written[0] if written else dict(payload)
    return row, False


def queue_suggestion(teacher_id, *, submission_id, question_index, exam_id=None,
                     school_id=None, ocr_text=""):
    """Create the row and hand it to Celery. Returns `(row, duplicate)`."""
    from app.utils.auth import get_supabase

    supabase = get_supabase()
    row, duplicate = create_suggestion(
        supabase,
        teacher_id=teacher_id,
        submission_id=submission_id,
        question_index=question_index,
        exam_id=exam_id,
        school_id=school_id,
        answer_override=ocr_text or None,
    )
    if not duplicate:
        grade_suggestion_task.delay(row["id"])
    return row, duplicate


def queue_bulk(teacher_id, exam_id, submission_ids=None, school_id=None):
    """Queue one suggestion per unmarked essay, up to `MAX_BULK`.

    Per-essay tasks rather than one long task: the worker's own concurrency is the
    provider's rate limit, a failed essay does not take the batch down with it, and
    progress is a count of rows rather than state held in a single worker that a
    second click would have to find.
    """
    from app.utils.auth import get_supabase

    supabase = get_supabase()
    exam = _exam_row(supabase, exam_id)
    qtypes = exam.get("question_types") or {}
    if isinstance(qtypes, str):
        qtypes = json.loads(qtypes)

    query = supabase.table("submissions").select("id,answers").eq("exam_id", exam_id)
    if submission_ids:
        query = query.in_("id", list(submission_ids)[:MAX_BULK])
    submissions = _rows(query.execute())[:MAX_BULK]

    indexes = [i for i in range(len(qtypes) or 0) if is_essay(qtypes.get(str(i)))]

    queued, duplicates, skipped = 0, 0, 0
    for sub in submissions:
        for index in indexes:
            if not answer_text(sub, index):
                skipped += 1
                continue
            row, duplicate = create_suggestion(
                supabase,
                teacher_id=teacher_id,
                submission_id=sub["id"],
                question_index=index,
                exam_id=exam_id,
                school_id=school_id,
            )
            if duplicate:
                duplicates += 1
                continue
            grade_suggestion_task.delay(row["id"])
            queued += 1

    return {
        "queued": queued,
        "already_running": duplicates,
        "skipped_empty": skipped,
        "submissions": len(submissions),
        "questions": len(indexes),
    }


# ── running one suggestion ───────────────────────────────────────────────────

def _fail(supabase, suggestion_id, message):
    logger.warning("AI suggestion %s failed: %s", suggestion_id, message)
    try:
        supabase.table(TABLE).update({
            "status": "failed",
            "error": str(message)[:400],
        }).eq("id", suggestion_id).execute()
    except Exception as exc:                      # a failed update must not retry the job
        logger.error("could not record AI failure for %s: %s", suggestion_id, exc)
    return {"status": "failed", "error": str(message)[:400]}


def run_suggestion(suggestion_id) -> dict:
    """Do the work for one queued row. Safe to call twice.

    Re-reads the paper rather than trusting inputs frozen at enqueue time: the
    answer and the question's points are both editable, and a suggestion computed
    against yesterday's answer is a suggestion about a different essay.
    """
    from app.utils.auth import get_supabase
    from app.services.ai_service import _call_ai, _get_active_key

    supabase = get_supabase()
    rows = _rows(supabase.table(TABLE).select("*").eq("id", suggestion_id).limit(1).execute())
    if not rows:
        return {"status": "missing"}
    row = rows[0]

    if row.get("status") not in ("queued", "running"):
        # Applied, rejected, or already ready. Re-running would either overwrite a
        # decision or spend a second call on a question already answered.
        return {"status": row.get("status"), "skipped": True}

    supabase.table(TABLE).update({"status": "running"}).eq("id", suggestion_id).execute()

    key = _get_active_key(row["teacher_id"])
    if not key:
        return _fail(supabase, suggestion_id,
                     "Belum ada API key AI aktif. Atur di Pengaturan AI.")

    try:
        submission = (_rows(
            supabase.table("submissions").select("id,answers")
            .eq("id", row["submission_id"]).limit(1).execute()
        ) or [{}])[0]
        exam = _exam_row(supabase, row.get("exam_id"))
    except Exception as exc:
        return _fail(supabase, suggestion_id, f"Gagal membaca pekerjaan: {exc}")

    index = int(row["question_index"])
    points = question_points(exam, index)
    qtexts = exam.get("question_texts") or {}
    rubrics = exam.get("question_rubrics") or {}
    if isinstance(qtexts, str):
        qtexts = json.loads(qtexts)
    if isinstance(rubrics, str):
        rubrics = json.loads(rubrics)

    answer = row.get("answer_override") or answer_text(submission, index)
    if not answer:
        return _fail(supabase, suggestion_id, "Tidak ada jawaban murid untuk dinilai.")

    built = ai_prompt.build_grading_prompt(
        teacher_prompt=_criteria_for(row["teacher_id"], supabase, exam.get("subject")),
        question_text=qtexts.get(str(index), ""),
        rubric=rubrics.get(str(index), ""),
        max_score=points,
        student_answer=answer,
    )

    try:
        raw = _call_ai(key, built["user"], built["system"])
    except Exception as exc:
        # Provider failures are the most likely failure here — a bad key, a rate
        # limit, a timeout — and each one has a sentence a teacher can act on.
        return _fail(supabase, suggestion_id, _provider_message(exc))

    try:
        suggestion = ai_prompt.parse_suggestion(raw, built["max_score"])
    except ai_prompt.PromptError as exc:
        return _fail(supabase, suggestion_id, str(exc))

    supabase.table(TABLE).update({
        "status": "ready",
        "score_suggested": suggestion.score,
        "max_score": suggestion.max_score,
        "feedback": suggestion.feedback,
        "reasoning": suggestion.reasoning,
        "confidence": suggestion.confidence,
        "anomalies": list(suggestion.anomalies) + list(built["notes"]),
        "provider": key.get("provider"),
        "model_used": key.get("model_name"),
        "answer_chars": len(ai_prompt.plain_text(answer)),
        "error": None,
    }).eq("id", suggestion_id).execute()

    return {"status": "ready", **suggestion.as_dict()}


def _provider_message(exc) -> str:
    """A provider error a teacher can act on, not a stack trace."""
    text = str(exc)
    lowered = text.lower()
    if "quota" in lowered or "rate limit" in lowered or "429" in lowered:
        return "Kuota API AI sedang habis atau dibatasi. Coba lagi beberapa saat lagi."
    if "api key" in lowered or "invalid" in lowered or "401" in lowered or "403" in lowered:
        return "API key AI ditolak provider. Periksa key di Pengaturan AI."
    if "timeout" in lowered or "timed out" in lowered:
        return "Provider AI tidak merespons tepat waktu. Coba lagi."
    return f"Gagal menghubungi provider AI: {text[:200]}"


@celery_app.task(bind=True, max_retries=0, name="ai.grade_suggestion")
def grade_suggestion_task(self, suggestion_id):
    """Celery entry point. Never retries on its own.

    A retry here spends the school's money twice on the same question, and the row
    already carries the failure for the teacher to retry deliberately.
    """
    try:
        return run_suggestion(suggestion_id)
    except Exception as exc:
        logger.error("AI suggestion task crashed: %s", exc, exc_info=True)
        try:
            from app.utils.auth import get_supabase
            _fail(get_supabase(), suggestion_id, str(exc))
        except Exception:
            pass
        return {"status": "failed", "error": str(exc)[:200]}


# ── a person decides ─────────────────────────────────────────────────────────

def _decide(supabase, suggestion_id, teacher_id, status):
    rows = _rows(
        supabase.table(TABLE).select("*")
        .eq("id", suggestion_id).eq("teacher_id", teacher_id).limit(1).execute()
    )
    if not rows:
        raise LookupError("saran tidak ditemukan")
    row = rows[0]
    if row.get("status") != "ready":
        raise ValueError(f"saran belum siap (status: {row.get('status')})")
    from datetime import datetime, timezone
    supabase.table(TABLE).update({
        "status": status,
        "resolved_at": datetime.now(timezone.utc).isoformat(),
        "resolved_by": teacher_id,
    }).eq("id", suggestion_id).eq("teacher_id", teacher_id).execute()
    return {**row, "status": status}


def apply_suggestion(supabase, suggestion_id, teacher_id) -> dict:
    """Record that a teacher accepted a suggestion, and hand the number back.

    Deliberately writes nothing to `submissions`. The teacher's form holds the
    value and the existing save route writes it, so a suggestion a teacher accepted
    and then changed is the teacher's mark — not a value the AI's row overrides on
    the next page load.
    """
    return _decide(supabase, suggestion_id, teacher_id, "applied")


def reject_suggestion(supabase, suggestion_id, teacher_id) -> dict:
    return _decide(supabase, suggestion_id, teacher_id, "rejected")


def suggestions_for(supabase, submission_id, teacher_id=None) -> dict:
    """Every suggestion for one paper, newest per question, keyed by question."""
    query = supabase.table(TABLE).select("*").eq("submission_id", submission_id)
    if teacher_id:
        query = query.eq("teacher_id", teacher_id)
    rows = _rows(query.execute())
    latest = {}
    for row in rows:
        index = int(row.get("question_index") or 0)
        if index not in latest or str(row.get("created_at") or "") > str(latest[index].get("created_at") or ""):
            latest[index] = row
    return latest


def status_counts(supabase, exam_id) -> dict:
    """How many suggestions of each status this exam has, for the overview page."""
    rows = _rows(
        supabase.table(TABLE).select("status")
        .eq("exam_id", exam_id).in_("status", ["queued", "running", "ready"]).execute()
    )
    counts = {"queued": 0, "running": 0, "ready": 0}
    for row in rows:
        state = row.get("status")
        if state in counts:
            counts[state] += 1
    return counts
