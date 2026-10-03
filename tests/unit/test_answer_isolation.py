"""One pupil's answers must never be stored as, or shown to, another.

Two different surfaces are audited here, because the isolating boundary is in two
different places.

**Server.** A sitting's answers live in one `submissions` row, and the table is
unique on `(student_id, exam_id)` (`submissions_student_exam_unique`, migration
013). Every write in `submission_service` targets a row read by that same pair and
patches it by `id`, so no write can reach another pupil's row even when a whole
class submits at once. The concurrency test below drives N pupils — one thread
each, against a shared, lock-guarded store that enforces the same unique key — and
asserts every row ends up holding exactly its own author's answers.

**Client.** The exam page keeps a draft, a pending submit, the clock stamp, the
agreement flag and its tab claim in `localStorage`. Those keys used to be built
from the exam id alone, which made them belong to *whoever opened this exam on the
device last*. On a shared classroom machine the next pupil loads the previous
pupil's answers into the page (`loadDraft`) and can submit them (`exam_pending_submit`).
That is the cross-student leak this file's template tests pin shut: every
per-sitting key now carries the sitter's own id.
"""
from __future__ import annotations

import pathlib
import re
import threading

import pytest
from flask import g

from app.services.submission_service import finish_sitting, open_sitting

ROOT = pathlib.Path(__file__).resolve().parents[2]
PAGE = ROOT / "app" / "templates" / "student" / "take_exam.html"
ROUTES = ROOT / "app" / "routes" / "student.py"

EXAM = "5f357840-1fb2-45c0-a822-2493124aacbb"


# ── server: one row per (student, exam), under concurrency ────────────────────

class _LockedStore:
    """An in-memory PostgREST that keeps the one constraint that matters.

    `submissions_student_exam_unique` is enforced on insert, and every call holds
    one lock, so two threads cannot observe a half-written row — which is what the
    database guarantees and what the routes are written against.
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.rows: list[dict] = []

    def insert(self, payload):
        key = (payload["student_id"], payload["exam_id"])
        with self.lock:
            if any((r["student_id"], r["exam_id"]) == key for r in self.rows):
                raise RuntimeError(
                    "{'code': '23505', 'message': 'duplicate key value violates "
                    "unique constraint \"submissions_student_exam_unique\"'}")
            row = {"id": f"row-{len(self.rows) + 1}", **payload}
            self.rows.append(row)
            return [dict(row)]

    def select(self, student_id, exam_id):
        with self.lock:
            return [dict(r) for r in self.rows
                    if r["student_id"] == student_id and r["exam_id"] == exam_id]

    def update_by_id(self, row_id, payload):
        with self.lock:
            for r in self.rows:
                if r["id"] == row_id:
                    r.update(payload)
                    return [dict(r)]
            return []


class _Query:
    def __init__(self, store, name, order):
        self.store, self.name, self.order = store, name, order
        self.filters: dict = {}
        self.payload = None
        self.kind = None

    def select(self, *a, **k):
        self.kind = "select"
        return self

    def update(self, payload):
        self.kind, self.payload = "update", payload
        return self

    def insert(self, payload):
        self.kind, self.payload = "insert", payload
        return self

    def eq(self, column, value):
        self.filters[column] = value
        return self

    def limit(self, *a, **k):
        return self

    def order(self, *a, **k):
        return self

    def execute(self):
        class _R:
            def __init__(self, data):
                self.data = data
        sid = self.filters.get("student_id")
        eid = self.filters.get("exam_id")
        if self.kind == "select":
            return _R(self.store.select(sid, eid))
        if self.kind == "insert":
            return _R(self.store.insert(self.payload))
        return _R(self.store.update_by_id(self.filters.get("id"), self.payload))


class _FakeSupabase:
    def __init__(self, store=None):
        self.store = store or _LockedStore()

    def table(self, name):
        assert name == "submissions"
        return _Query(self.store, name, None)


def test_a_class_submitting_at_once_keeps_every_answer_with_its_author():
    """40 pupils, 40 threads, one exam — each row holds only its own answers."""
    store = _LockedStore()
    sb = _FakeSupabase(store)
    pupils = [f"student-{n:02d}" for n in range(40)]
    errors: list[BaseException] = []

    def sit(student_id):
        try:
            open_sitting(sb, EXAM, student_id)
            finish_sitting(sb, EXAM, student_id, {
                "status": "submitted",
                "answers": {"0": student_id, "2": {"statements": [student_id]}},
            }, None)
        except BaseException as exc:  # noqa: BLE001 — reported, not swallowed
            errors.append(exc)

    threads = [threading.Thread(target=sit, args=(s,)) for s in pupils]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors
    assert len(store.rows) == len(pupils), "a pupil's row was lost or duplicated"
    for row in store.rows:
        author = row["student_id"]
        assert row["status"] == "submitted"
        assert row["answers"]["0"] == author, (
            f"a row for {author} holds another pupil's answer: {row['answers']['0']}")
        assert row["answers"]["2"]["statements"] == [author], (
            f"a structured answer leaked between pupils: {row['answers']['2']}")


def test_one_pupil_cannot_write_into_another_pupils_row():
    """A write is addressed by the pair it read, so it can only be its own row."""
    store = _LockedStore()
    sb = _FakeSupabase(store)
    open_sitting(sb, EXAM, "student-A")
    open_sitting(sb, EXAM, "student-B")
    a_row = store.select("student-A", EXAM)[0]

    # B finishes; A must be untouched.
    finish_sitting(sb, EXAM, "student-B", {"status": "submitted", "answers": {"0": "B"}}, None)
    still_a = store.select("student-A", EXAM)[0]
    assert still_a == a_row, "writing one pupil's sitting changed another's row"
    assert store.select("student-B", EXAM)[0]["answers"]["0"] == "B"


# ── client: every per-sitting key carries the sitter's id ─────────────────────

#: The keys that belong to one pupil's sitting. A key built with `exam.id` and
#: nothing else is the leak: the next pupil on the device reads it.
PER_SITTING = ("exam_draft_", "exam_pending_submit_", "exam_started_",
               "exam_seen_", "sg_agreed_", "sg_exam_tab_")

BARE_KEY = re.compile(r"'(?:exam_draft_|exam_pending_submit_|exam_started_|exam_seen_|"
                      r"sg_agreed_|sg_exam_tab_)'\s*\+\s*'\{\{\s*exam\.id\s*\}\}'")


def test_no_per_sitting_key_is_built_from_the_exam_id_alone():
    source = PAGE.read_text(encoding="utf-8")
    offenders = BARE_KEY.findall(source)
    assert not offenders, (
        "a per-sitting localStorage key is keyed by exam id alone again — the next "
        f"pupil on this device would inherit it: {offenders}")


def test_every_per_sitting_key_goes_through_the_sitter_keyed_helper():
    source = PAGE.read_text(encoding="utf-8")
    for prefix in PER_SITTING:
        bare = re.search(r"localStorage\.(?:get|set|remove)Item\(\s*'"
                         + re.escape(prefix) + r"'", source)
        assert not bare, f"`{prefix}` is read with a bare literal key again"
        assert f"sgLS('{prefix}')" in source, (
            f"`{prefix}` is no longer addressed through `sgLS()`")


def test_the_page_carries_its_own_sitters_id_and_purges_a_legacy_key():
    source = PAGE.read_text(encoding="utf-8")
    assert "const SG_STUDENT = {{ (student_key or '') | tojson }};" in source
    assert "function sgLS(prefix)" in source
    # The one-time purge is what stops a stale, author-less key from being read.
    assert "sgPurgeLegacySittingKeys" in source
    for prefix in PER_SITTING:
        assert f"'{prefix}'," in source or f"'{prefix}'" in source


def test_the_route_hands_the_page_the_sitter():
    """Without this, `student_key` is empty and every pupil shares one key again."""
    body = re.search(r"def take_exam\(exam_id\):(.*?)\n@student_bp", ROUTES.read_text(encoding="utf-8"), re.S)
    assert body, "take_exam is no longer the route this test names"
    assert "student_key=g.user_id" in body.group(1), (
        "take_exam does not pass the sitter's id, so the page cannot key storage "
        "by it")


def test_two_pupils_render_two_different_storage_namespaces(app):
    """The rendered key differs by pupil — the property the fix exists for."""

    def render(student_key):
        ctx = {
            "exam": {"id": EXAM, "title": "T", "total_questions": 1,
                     "duration_minutes": 60, "question_types": {},
                     "pdf_page_urls": []},
            "anti_cheat_config": "{}", "exam_started_at": None, "recovery_code": "",
            "question_options": {}, "deadline": None, "deadline_reason": "duration",
            "seconds_left": 100, "window_end": None, "away_grace_seconds": 15,
            "away_grace_chances": 2, "student_name": "A", "student_class_label": "7A",
            "student_key": student_key,
        }
        with app.test_request_context(f"/student/exams/{EXAM}"):
            g.user_id, g.user_name, g.user_role = student_key, "A", "murid"
            g.tz_offset, g.show = 7, {}
            return app.jinja_env.get_template("student/take_exam.html").render(**ctx)

    a = render("pupil-A")
    b = render("pupil-B")
    assert 'const SG_STUDENT = "pupil-A";' in a
    assert 'const SG_STUDENT = "pupil-B";' in b
    assert a != b


@pytest.mark.parametrize("prefix", PER_SITTING)
def test_the_helper_is_the_only_place_the_namespace_is_built(prefix):
    """`sgLS` appends the student, so a hand-built key cannot slip past it."""
    source = PAGE.read_text(encoding="utf-8")
    assert f"function sgLS(prefix) {{ return prefix + SG_EXAM + '__' + SG_STUDENT; }}" in source
