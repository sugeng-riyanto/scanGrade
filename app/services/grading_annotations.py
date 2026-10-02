"""Annotations — a mark on the pupil's words, never an edit of them.

The comment bank is the marker's *own* phrase reused across papers. This is the
other half: this sentence is the argument, that claim contradicts itself, this
one earns the mark. A highlight, a strike-through, a comment pin — each is a row
that names a **range of the answer's text**.

That is the whole point of the design. The answer itself is never written to, so
removing a mark restores exactly what the child typed, and no report can show a
word they did not write. Nothing in this module touches `submissions`.

The vocabulary is closed and the range is checked before anything is written: an
unknown kind, an unknown colour, or a range that is empty, inverted, negative or
past the end of the answer is refused with a `ValueError` rather than stored and
rendered as a mark over nothing.

A mark belongs to the teacher who made it. Editing or removing another teacher's
mark is refused with a `LookupError`, exactly as `apply_comment` refuses a
stranger's comment — words put on one child's paper are not another's to change.
"""
import logging

logger = logging.getLogger(__name__)

TABLE = "grading_annotation"

KINDS = ("highlight", "strike", "comment")
COLORS = ("amber", "rose", "sky", "emerald", "violet")

MAX_NOTE = 2000


def _rows(result):
    return getattr(result, "data", None) or []


def _clean_kind(kind) -> str:
    value = (kind or "").strip().lower()
    if value not in KINDS:
        raise ValueError("jenis anotasi tidak dikenal")
    return value


def _clean_color(color):
    if color is None or color == "":
        return None
    value = str(color).strip().lower()
    if value not in COLORS:
        raise ValueError("warna anotasi tidak dikenal")
    return value


def _clean_note(note):
    if note is None:
        return None
    text = str(note).strip()
    if not text:
        return None
    if len(text) > MAX_NOTE:
        raise ValueError("catatan terlalu panjang")
    return text


def _clean_range(start, end, text_length=None):
    """The half-open range, or a ValueError. Checked before anything is written.

    `text_length` is passed in when the caller holds the answer: a range past the
    end of it would render a mark over nothing, which is the same silent
    breakage a bad embed link is.
    """
    try:
        start = int(start)
        end = int(end)
    except (TypeError, ValueError):
        raise ValueError("rentang anotasi harus berupa angka")
    if start < 0:
        raise ValueError("awal rentang tidak boleh negatif")
    if end <= start:
        raise ValueError("rentang anotasi harus menunjuk setidaknya satu karakter")
    if text_length is not None and end > int(text_length):
        raise ValueError("rentang melewati akhir jawaban")
    return start, end


# ── read ─────────────────────────────────────────────────────────────────────

def list_annotations(supabase, attempt_id, question_index):
    """One paper's marks on one question, in reading order."""
    rows = _rows(supabase.table(TABLE).select("*")
                 .eq("attempt_id", attempt_id)
                 .eq("question_index", int(question_index)).execute())
    rows.sort(key=lambda r: (int(r.get("start_offset") or 0),
                             int(r.get("end_offset") or 0)))
    return rows


# ── write ────────────────────────────────────────────────────────────────────

def add_annotation(supabase, attempt_id, question_index, teacher_id, kind,
                   start, end, text_length=None, note=None, color=None):
    """Mark a range of the answer. Raises ValueError before writing anything."""
    payload = {
        "attempt_id": attempt_id,
        "question_index": int(question_index),
        "kind": _clean_kind(kind),
        "created_by": teacher_id,
        "note": _clean_note(note),
        "color": _clean_color(color),
    }
    payload["start_offset"], payload["end_offset"] = _clean_range(
        start, end, text_length)
    written = _rows(supabase.table(TABLE).insert(payload).execute())
    return written[0] if written else dict(payload)


def _owned(supabase, annotation_id, teacher_id):
    rows = _rows(supabase.table(TABLE).select("*")
                 .eq("id", annotation_id).eq("created_by", teacher_id).execute())
    if not rows:
        raise LookupError("anotasi bukan milik Anda")
    return rows[0]


def update_annotation(supabase, annotation_id, teacher_id, note=None, color=None):
    """Change a mark's note or colour. The range is not editable in place.

    Moving a mark is remove-and-again rather than an edit, so the two offsets
    stay a pair the reader can trust.
    """
    _owned(supabase, annotation_id, teacher_id)
    payload = {}
    if note is not None:
        payload["note"] = _clean_note(note)
    if color is not None:
        payload["color"] = _clean_color(color)
    if not payload:
        return None
    supabase.table(TABLE).update(payload) \
        .eq("id", annotation_id).eq("created_by", teacher_id).execute()
    return payload


def remove_annotation(supabase, annotation_id, teacher_id):
    """Remove one mark, leaving the pupil's answer exactly as it was."""
    _owned(supabase, annotation_id, teacher_id)
    supabase.table(TABLE).delete() \
        .eq("id", annotation_id).eq("created_by", teacher_id).execute()
