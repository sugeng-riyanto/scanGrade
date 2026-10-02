"""Grading assist — the three things a long marking session needs.

Marking one essay is a form. Marking **200** essays for the same question is a
different job, and three facts make it tolerable that the schema did not hold:

* a comment the teacher has already written once, applied again with one click,
  carrying an optional deduction so marking and subtracting are one action;
* a "come back to this one" flag, held as one row per (paper, question);
* a record of what a mark used to be, written only when it actually changed.

Everything here is scoped to the caller. A comment belongs to the teacher who
wrote it — `apply_comment` refuses one that is not theirs rather than silently
applying a stranger's words to a child's paper. A flag belongs to a paper; the
route that sets one proves the paper is the caller's to grade before calling.

The audit is deliberately quiet: re-saving a paper nobody touched writes nothing,
because a history of edits that did not happen is worse than no history at all.
"""
import logging

logger = logging.getLogger(__name__)

BANK_TABLE = "grading_comment_bank"
FLAG_TABLE = "grading_flag"
AUDIT_TABLE = "grading_audit_log"

MAX_BODY = 2000
MAX_REASON = 280


# ── validation ───────────────────────────────────────────────────────────────

def _clean_body(body) -> str:
    text = (body or "").strip()
    if not text:
        raise ValueError("komentar tidak boleh kosong")
    if len(text) > MAX_BODY:
        raise ValueError("komentar terlalu panjang")
    return text


def _clean_reason(reason) -> str:
    return (reason or "").strip()[:MAX_REASON]


def _clean_delta(delta):
    """The deduction, or None. Out-of-range is refused, not clamped."""
    if delta is None or delta == "":
        return None
    try:
        value = int(round(float(delta)))
    except (TypeError, ValueError):
        raise ValueError("pengurangan bukan angka")
    if not -100 <= value <= 100:
        raise ValueError("pengurangan harus antara -100 dan 100")
    return value


def _rows(result):
    return getattr(result, "data", None) or []


def _num(value):
    """A mark, or None. `''` and `None` both mean 'not marked yet'."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ── the comment bank ─────────────────────────────────────────────────────────

def list_bank(supabase, teacher_id, subject=None, question_index=None, limit=20):
    """The teacher's own comments, most-used first.

    Filtering happens in Python rather than in the query because the two
    optional dimensions (subject, question) mean four different shapes, and the
    bank is small by nature — a teacher curates dozens of phrases, not millions.
    """
    rows = _rows(supabase.table(BANK_TABLE).select("*")
                 .eq("teacher_id", teacher_id).execute())
    if subject:
        rows = [r for r in rows if not r.get("subject") or str(r.get("subject")) == str(subject)]
    if question_index is not None:
        rows = [r for r in rows
                if r.get("question_index") in (None, "", int(question_index))]
    rows.sort(key=lambda r: (-int(r.get("uses") or 0), str(r.get("created_at") or "")))
    return rows[:limit]


def add_comment(supabase, teacher_id, body, subject=None, question_index=None,
                score_delta=None):
    """Write a new bank comment. Raises ValueError before writing anything."""
    text = _clean_body(body)
    delta = _clean_delta(score_delta)
    payload = {"teacher_id": teacher_id, "body": text, "score_delta": delta, "uses": 0}
    if subject:
        payload["subject"] = subject
    if question_index is not None:
        payload["question_index"] = int(question_index)
    written = _rows(supabase.table(BANK_TABLE).insert(payload).execute())
    return written[0] if written else dict(payload)


def apply_comment(supabase, teacher_id, comment_id):
    """Count one use of the teacher's own comment, and hand back its body.

    Refuses a comment that is not the caller's: applying a stranger's phrase to a
    child's paper is not a thing this app should do quietly.
    """
    rows = _rows(supabase.table(BANK_TABLE).select("*")
                 .eq("id", comment_id).eq("teacher_id", teacher_id).execute())
    if not rows:
        raise LookupError("komentar bukan milik Anda")
    row = rows[0]
    uses = int(row.get("uses") or 0) + 1
    supabase.table(BANK_TABLE).update({"uses": uses}) \
        .eq("id", comment_id).eq("teacher_id", teacher_id).execute()
    return {"body": row.get("body") or "", "score_delta": row.get("score_delta"),
            "uses": uses}


# ── the review flag ──────────────────────────────────────────────────────────

def set_flag(supabase, attempt_id, question_index, teacher_id, reason=""):
    """Flag one question of one paper, replacing any flag already there."""
    payload = {"attempt_id": attempt_id, "question_index": int(question_index),
               "teacher_id": teacher_id, "reason": _clean_reason(reason) or None}
    existing = _rows(supabase.table(FLAG_TABLE).select("id")
                     .eq("attempt_id", attempt_id)
                     .eq("question_index", int(question_index)).execute())
    if existing:
        supabase.table(FLAG_TABLE).update(payload) \
            .eq("attempt_id", attempt_id).eq("question_index", int(question_index)) \
            .execute()
    else:
        supabase.table(FLAG_TABLE).insert(payload).execute()
    return payload


def clear_flag(supabase, attempt_id, question_index, teacher_id):
    """Remove the flag from one question, leaving the paper's others alone."""
    supabase.table(FLAG_TABLE).delete() \
        .eq("attempt_id", attempt_id).eq("question_index", int(question_index)) \
        .execute()


def flags_for(supabase, attempt_id):
    """The set of question indices flagged on one paper."""
    rows = _rows(supabase.table(FLAG_TABLE).select("question_index")
                 .eq("attempt_id", attempt_id).execute())
    return {int(r["question_index"]) for r in rows if r.get("question_index") is not None}


# ── the audit ────────────────────────────────────────────────────────────────

def record_score_change(supabase, attempt_id, question_index, teacher_id,
                        old_score, new_score):
    """Write one audit row if the mark moved. Returns the row, or None.

    A paper re-saved without touching a question must not manufacture an edit:
    `_num` folds `None`/`''` into 'not marked yet' so the comparison is about the
    mark, not about how an empty field was spelled.
    """
    before, after = _num(old_score), _num(new_score)
    if before == after:
        return None
    payload = {"attempt_id": attempt_id, "question_index": int(question_index),
               "teacher_id": teacher_id, "old_score": before, "new_score": after}
    supabase.table(AUDIT_TABLE).insert(payload).execute()
    return payload
