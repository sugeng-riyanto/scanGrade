"""One AI suggestion for one essay — built, fenced, validated and stored.

The shape of the promise
------------------------
A suggestion is **advice**, and this module is careful to keep it that way:

* it reads the question, the maximum points and the teacher's rubric from the
  database, never from the request — a browser cannot widen a question's marks;
* it builds the prompt through `ai_grading_prompt.build_grading_prompt`, so the
  student's answer is always fenced data and the teacher's text is always a
  rubric rather than the system instruction;
* it parses the model's JSON and clamps `skor_saran` into `0 .. poin_maks`,
  recording the clamp in `anomaly` instead of storing raw model output;
* it writes `ai_grading_suggestion` only. No submission, no `teacher_feedback`,
  no score. The teacher applies it, and the existing marking path saves it.

The row is also the job record: `queued → processing → done | failed` is what
lets a teacher watch progress, and what makes a re-request update the same row
rather than stack duplicates.

Privacy
-------
The payload sent to the provider is the question, the rubric and the answer
text. A pupil's name, NISN and class are deliberately absent: the correction is
linked to the pupil here, by `attempt_id`, and not at the third-party API. This
is the fact `docs/COMPLIANCE.md` records.
"""
from __future__ import annotations

import json
import logging
import re

from app.services.ai_grading_prompt import (
    build_grading_prompt, clean_answer_text, points_to_percent,
)
from app.services.essay_text import essay_answer_text

logger = logging.getLogger("app")

TABLE = "ai_grading_suggestion"
PROMPT_TABLE = "ai_grading_prompt"
DEFAULT_SUBJECT = "default"

#: How long a reason may be. Long enough for a sentence, short enough that the
#: panel stays readable and the row stays cheap.
MAX_REASON = 1200

#: Statuses that mean "do not enqueue another one".
LIVE = ("queued", "processing")
DONE = "done"

_STATUS_MESSAGES = {
    "no_key": "Belum ada API key AI yang aktif. Atur di Pengaturan AI.",
    "not_essay": "Soal ini bukan soal esai, jadi tidak dikoreksi AI.",
    "no_answer": "Tidak ada jawaban teks untuk soal ini.",
    "timeout": "Layanan AI tidak menjawab tepat waktu. Coba lagi sebentar lagi.",
    "rate": "Kuota API AI sedang penuh. Tunggu beberapa saat atau gunakan key lain.",
    "auth": "Kunci API AI ditolak. Periksa kembali key di Pengaturan AI.",
    "provider": "Layanan AI gagal memproses jawaban ini. Coba lagi.",
}


# ── the subject key ──────────────────────────────────────────────────────────

def subject_key(subject) -> str:
    """The normalized key a prompt is stored under.

    `exams.subject` is free text ("Matematika", "matematika ", "IPA Terpadu"),
    and the prompt must follow the exam through that spelling rather than fail
    to match it. Case and runs of whitespace are the only differences folded:
    two genuinely different subjects stay different.
    """
    text = re.sub(r"\s+", " ", str(subject or "")).strip().lower()
    return text or DEFAULT_SUBJECT


# ── reading the marking context ──────────────────────────────────────────────

def _rows(result):
    return getattr(result, "data", None) or []


def _mapping(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def answer_text_of(entry) -> str:
    """The answer's text, from the shapes the exam page actually stores.

    Delegates to `essay_text.essay_answer_text`, the same reader the teacher's
    marking page uses: an AI asked to grade one text while the marker is shown
    another is an AI grading a different paper. A dict answer carries the
    toolbar's `text`, the paragraphs behind it, or text typed into boxes on the
    paper; a plain string is an older paper. Drawing pages are *not* text: an OCR
    pass has to produce the text first, and pretending a canvas has an answer
    would send an empty essay to the model and store a zero.
    """
    return essay_answer_text(entry)


def question_points(exam, index) -> float:
    """This question's share of the paper, by the same rule the marker uses.

    `exam_scoring.exam_parts` is the one place weights are defaulted and
    completed; asking it here is what stops the AI's ceiling from being 100
    while the question is worth 20.
    """
    from app.services.exam_scoring import exam_parts

    parts = exam_parts(exam or {})
    try:
        value = float(parts.weights.get(str(index), 0) or 0)
    except (TypeError, ValueError):
        value = 0.0
    return value


def is_essay_question(exam, index) -> bool:
    from app.services.question_types import is_essay

    types = _mapping((exam or {}).get("question_types"))
    return is_essay(types.get(str(index)))


def question_text_of(exam, index) -> str:
    texts = _mapping((exam or {}).get("question_texts"))
    return str(texts.get(str(index)) or "")


def rubric_of(exam, index) -> str:
    rubrics = _mapping((exam or {}).get("question_rubrics"))
    value = rubrics.get(str(index))
    return value if isinstance(value, str) else ""


def resolve_prompt(supabase, teacher_id, subject) -> dict:
    """The rubric that applies: this subject's, else the default, else none.

    A per-subject prompt is the promise `/teacher/ai-settings` makes. The
    default row is the fallback a teacher writes once; when neither exists the
    builder's own default criterion applies, and `version` is 0 so a suggestion
    can say it was made without a saved rubric.
    """
    key = subject_key(subject)
    try:
        rows = _rows(supabase.table(PROMPT_TABLE).select("*")
                     .eq("teacher_id", teacher_id).execute())
    except Exception:
        logger.exception("could not read the grading prompts for %s", teacher_id)
        rows = []
    by_key = {str(r.get("subject_key")): r for r in rows if r.get("subject_key")}
    row = by_key.get(key)
    if row is None and key != DEFAULT_SUBJECT:
        row = by_key.get(DEFAULT_SUBJECT)
    if not row:
        return {"prompt_text": "", "version": 0, "subject_key": key,
                "subject_label": None, "id": None}
    return {
        "prompt_text": row.get("prompt_text") or "",
        "version": int(row.get("version") or 1),
        "subject_key": row.get("subject_key") or key,
        "subject_label": row.get("subject_label"),
        "id": row.get("id"),
    }


# ── parsing and validating the model's answer ────────────────────────────────

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)
_SCORE_RE = re.compile(r'"?skor_saran"?\s*[:=]\s*(-?\d+(?:[.,]\d+)?)')
_REASON_RE = re.compile(r'"?alasan"?\s*[:=]\s*"([^"]*)"', re.DOTALL)
_CONFIDENCE_RE = re.compile(r'"?tingkat_keyakinan"?\s*[:=]\s*(-?\d+(?:[.,]\d+)?)')


def _extract_json(raw: str):
    """The first JSON object in a reply, with code fences tolerated.

    Models wrap JSON in ```json often enough that refusing it would be a bug
    report rather than a validation rule; a bare object is preferred, and the
    tolerant field regexes below are the last resort.
    """
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        match = _JSON_OBJECT_RE.search(text)
        if match:
            try:
                return json.loads(match.group(0))
            except (json.JSONDecodeError, TypeError):
                return None
    return None


def _number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.strip().replace(",", ".")
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def parse_suggestion(raw, max_points) -> tuple[dict, str]:
    """`(suggestion, anomaly)` from a model reply, or `ValueError`.

    Out-of-range is **clamped, not accepted**: an answer of 120 on a 20-point
    question becomes 20 and the anomaly names what arrived, so a teacher can see
    that the model misbehaved. Missing or non-numeric score is an error — there
    is nothing to clamp, and inventing a zero would be a suggestion the model
    never made.
    """
    try:
        maximum = float(max_points)
    except (TypeError, ValueError):
        raise ValueError("poin maksimal soal tidak valid")
    if maximum <= 0:
        raise ValueError("poin maksimal soal harus lebih dari 0")

    data = _extract_json(raw)
    anomalies = []
    if isinstance(data, dict):
        score = _number(data.get("skor_saran", data.get("score")))
        reason = data.get("alasan", data.get("reasoning", data.get("feedback", "")))
        confidence = _number(data.get("tingkat_keyakinan", data.get("confidence")))
    else:
        score = None
        reason = ""
        confidence = None
        text = raw or ""
        match = _SCORE_RE.search(text)
        if match:
            score = _number(match.group(1))
        match = _REASON_RE.search(text)
        if match:
            reason = match.group(1)
        match = _CONFIDENCE_RE.search(text)
        if match:
            confidence = _number(match.group(1))
        anomalies.append("balasan bukan JSON yang valid")

    if score is None:
        raise ValueError("balasan AI tidak memuat skor_saran berupa angka")

    clamped = round(max(0.0, min(score, maximum)), 2)
    if clamped != round(score, 2):
        anomalies.append(
            f"skor di luar rentang: {round(score, 2)} pada soal {maximum:g} poin "
            f"dipotong menjadi {clamped:g}")

    if confidence is None:
        confidence = 0.5
        anomalies.append("tingkat keyakinan tidak dibaca; dipakai 0.5")
    confidence = round(max(0.0, min(float(confidence), 1.0)), 2)

    reason_text = clean_answer_text(reason) if isinstance(reason, str) else str(reason or "")
    reason_text = reason_text.strip()[:MAX_REASON]

    return {
        "skor_saran": clamped,
        "poin_maks": round(maximum, 2),
        "alasan": reason_text,
        "tingkat_keyakinan": confidence,
    }, "; ".join(anomalies)


# ── the stored row ───────────────────────────────────────────────────────────

def _existing(supabase, attempt_id, question_index):
    rows = _rows(supabase.table(TABLE).select("*")
                 .eq("attempt_id", attempt_id)
                 .eq("question_index", int(question_index)).execute())
    return rows[0] if rows else None


def queue_suggestion(supabase, *, attempt_id, question_index, poin_maks,
                     requested_by, prompt_version=0, prompt_subject=None,
                     batch_id=None, force=False) -> tuple[dict, bool]:
    """The row to work on, and whether a job should be enqueued.

    A row already `queued`/`processing` is returned untouched, so a double click
    cannot bill the same essay twice. `done` is kept unless `force`, which is
    how a teacher asks again after changing their rubric.
    """
    existing = _existing(supabase, attempt_id, question_index)
    payload = {
        "attempt_id": attempt_id,
        "question_index": int(question_index),
        "poin_maks": float(poin_maks),
        "prompt_version": prompt_version or None,
        "prompt_subject": prompt_subject,
        "requested_by": requested_by,
        "batch_id": batch_id,
        "status": "queued",
        "skor_saran": None,
        "alasan": None,
        "tingkat_keyakinan": None,
        "anomaly": None,
        "error_message": None,
        "applied_at": None,
        "applied_by": None,
        "applied_score": None,
    }
    if existing:
        if existing.get("status") in LIVE and not force:
            return existing, False
        if existing.get("status") == DONE and not force:
            return existing, False
        updated = _rows(supabase.table(TABLE).update(payload)
                        .eq("id", existing["id"]).execute())
        row = updated[0] if updated else {**existing, **payload}
        return row, True
    try:
        inserted = _rows(supabase.table(TABLE).insert(payload).execute())
    except Exception:
        # Two clicks (or two tabs) can pass the `_existing` read together; the
        # unique index then refuses the second insert. The row the other request
        # created is the one to work on, not an error for the teacher.
        raced = _existing(supabase, attempt_id, question_index)
        if raced:
            return raced, False
        raise
    return (inserted[0] if inserted else payload), True


def friendly_error(exc) -> tuple[str, str]:
    """`(code, sentence)` for a failure a teacher can act on.

    The provider's own text is not shown: it can carry a URL with a key, a stack
    trace, or an English sentence about an HTTP status. The class of failure is
    enough to decide what to do next, and the detail goes to the log.
    """
    text = str(exc or "").lower()
    if "timeout" in text or "timed out" in text or "waktu" in text:
        return "timeout", _STATUS_MESSAGES["timeout"]
    if "quota" in text or "rate" in text or "429" in text:
        return "rate", _STATUS_MESSAGES["rate"]
    if "api key" in text or "api_key" in text or "401" in text or "403" in text \
            or "unauthorized" in text or "permission" in text:
        return "auth", _STATUS_MESSAGES["auth"]
    return "provider", _STATUS_MESSAGES["provider"]


def _scrub(text, key) -> str:
    secret = str((key or {}).get("api_key") or "")
    if secret and len(secret) > 6:
        return str(text).replace(secret, "[kunci disembunyikan]")
    return str(text)


def run_suggestion(supabase, suggestion_id) -> dict:
    """Do the work for one queued row. Called by the Celery task, not a request.

    Every exit path writes the row's terminal status, because a row left
    `processing` is a teacher's spinner that never stops.
    """
    rows = _rows(supabase.table(TABLE).select("*").eq("id", suggestion_id).execute())
    if not rows:
        return {"status": "failed", "error": "Saran AI tidak ditemukan."}
    row = rows[0]
    attempt_id = row["attempt_id"]
    question_index = int(row["question_index"])

    supabase.table(TABLE).update({"status": "processing"}).eq("id", suggestion_id).execute()

    try:
        sub = _rows(supabase.table("submissions")
                    .select("id,exam_id,answers").eq("id", attempt_id).execute())
        if not sub:
            return _fail(supabase, suggestion_id, "Kertas jawaban tidak ditemukan.")
        submission = sub[0]
        exam_rows = _rows(supabase.table("exams").select(
            "id,teacher_id,school_id,subject,question_types,question_weights,"
            "question_texts,question_rubrics,total_questions"
        ).eq("id", submission["exam_id"]).execute())
        if not exam_rows:
            return _fail(supabase, suggestion_id, "Ujian tidak ditemukan.")
        exam = exam_rows[0]

        if not is_essay_question(exam, question_index):
            return _fail(supabase, suggestion_id, _STATUS_MESSAGES["not_essay"], code="not_essay")

        maximum = question_points(exam, question_index)
        if maximum <= 0:
            return _fail(supabase, suggestion_id,
                         "Soal ini tidak punya bobot poin, jadi tidak bisa dinilai AI.")
        # The stored maximum is what the request was accepted with; if the exam's
        # weights changed in between, the AI's ceiling follows the truth, not the
        # stale row.
        supabase.table(TABLE).update({"poin_maks": maximum}).eq("id", suggestion_id).execute()

        answers = _mapping(submission.get("answers"))
        text = answer_text_of(answers.get(str(question_index)))
        if not text.strip():
            return _fail(supabase, suggestion_id, _STATUS_MESSAGES["no_answer"],
                         code="no_answer")

        teacher_id = row.get("requested_by")
        subject = exam.get("subject")
        prompt = resolve_prompt(supabase, teacher_id, subject)

        from app.services.ai_service import _call_ai, _get_active_key, _parse_ai_response, _save_log

        key = _get_active_key(teacher_id)
        if not key:
            return _fail(supabase, suggestion_id, _STATUS_MESSAGES["no_key"], code="no_key")

        final_prompt = build_grading_prompt(
            teacher_prompt=prompt["prompt_text"],
            question_text=question_text_of(exam, question_index),
            student_answer=text,
            max_points=maximum,
            rubric=rubric_of(exam, question_index),
        )

        raw = _call_ai(key, final_prompt)
        parsed, anomaly = parse_suggestion(raw, maximum)

        try:
            _save_log(teacher_id, attempt_id, question_index,
                      key.get("provider", "ai"), parsed["skor_saran"],
                      parsed["alasan"], final_prompt, raw, 0)
        except Exception:
            logger.exception("could not write the AI grading log for %s", suggestion_id)

        payload = {
            "status": "done",
            **parsed,
            "prompt_version": prompt["version"] or None,
            "prompt_subject": prompt["subject_key"],
            "provider": key.get("provider", "ai"),
            "model": key.get("model_name") or key.get("model") or "",
            "anomaly": anomaly or None,
            "error_message": None,
        }
        _rows(supabase.table(TABLE).update(payload).eq("id", suggestion_id).execute())
        if anomaly:
            logger.warning("AI suggestion %s anomaly: %s", suggestion_id, anomaly)
        return {"status": "done", **parsed, "anomaly": anomaly or None}

    except ValueError as exc:
        logger.error("AI suggestion %s rejected its own input: %s", suggestion_id, exc)
        return _fail(supabase, suggestion_id, str(exc)[:200])
    except Exception as exc:
        code, message = friendly_error(exc)
        logger.error("AI suggestion %s failed (%s): %s", suggestion_id, code,
                     _scrub(exc, key if "key" in locals() else None), exc_info=True)
        return _fail(supabase, suggestion_id, message)


def _fail(supabase, suggestion_id, message, code="failed"):
    try:
        supabase.table(TABLE).update({"status": "failed",
                                      "error_message": str(message)[:500]}) \
            .eq("id", suggestion_id).execute()
    except Exception:
        logger.exception("could not mark AI suggestion %s failed", suggestion_id)
    return {"status": "failed", "code": code, "error": message}


#: Public name for the failure writer: a route whose broker is down has to record
#: the same terminal state a task would have written.
fail = _fail


# ── reading suggestions back ─────────────────────────────────────────────────

def for_submission(supabase, submission_id) -> dict:
    """`{question_index: row}` for one paper, for the marking page and polls."""
    try:
        rows = _rows(supabase.table(TABLE).select("*")
                     .eq("attempt_id", submission_id).execute())
    except Exception:
        logger.exception("could not read AI suggestions for %s", submission_id)
        return {}
    return {int(r["question_index"]): r for r in rows if r.get("question_index") is not None}


def status_for(supabase, submission_ids) -> dict:
    """`{submission_id: {done, pending, failed, total}}` in one query.

    Used by the queue and the overview: a per-submission lookup would be a
    round-trip per row, and the indicator exists to save opening each paper.
    """
    ids = [s for s in (submission_ids or []) if s]
    if not ids:
        return {}
    try:
        rows = _rows(supabase.table(TABLE)
                     .select("attempt_id,status").in_("attempt_id", ids).execute())
    except Exception:
        logger.exception("could not read AI suggestion status")
        return {}
    out = {}
    for row in rows:
        key = str(row.get("attempt_id"))
        bucket = out.setdefault(key, {"done": 0, "pending": 0, "failed": 0, "total": 0})
        bucket["total"] += 1
        status = row.get("status")
        if status == DONE:
            bucket["done"] += 1
        elif status == "failed":
            bucket["failed"] += 1
        elif status in LIVE:
            bucket["pending"] += 1
    return out


def percent_of(row) -> float | None:
    """The teacher-field value for a stored suggestion, or None."""
    if not row or row.get("skor_saran") is None or not row.get("poin_maks"):
        return None
    return points_to_percent(row["skor_saran"], row["poin_maks"])
