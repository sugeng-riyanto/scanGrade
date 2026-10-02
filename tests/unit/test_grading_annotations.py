"""Marking a typed answer means pointing at the words, not describing them later.

The comment bank in `grading_assist` is the marker's *own* phrase reused across
papers. An annotation is the other half: a mark **on this child's words** — this
sentence is the argument, that claim contradicts itself, this one earns the mark.

The whole design rests on one rule: **the pupil's answer is never edited.** A
highlight is a row that names a range of the answer's text; the text itself is
untouched, so removing the mark restores exactly what the child wrote and no
report can ever show a word they did not type. That is why the range is
`start_offset`/`end_offset` into the answer as stored, and why the service writes
to `grading_annotation` and to nothing else.

What this suite pins:

* the vocabulary is closed — an unknown kind or colour is refused, not stored;
* a range is a range — negative, inverted or beyond the answer is refused before
  anything is written;
* a mark belongs to the teacher who made it — editing or removing someone else's
  is refused, on the same rule the comment bank uses;
* the answer is read-only — no code path here writes to `submissions`.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVICE = ROOT / "app" / "services" / "grading_annotations.py"
MIGRATION = ROOT / "supabase" / "migrations" / "053_grading_annotation.sql"
ROUTES = ROOT / "app" / "routes" / "teacher.py"
PAGE = ROOT / "app" / "templates" / "teacher" / "grade_question.html"


# ── a fake that really filters, so a missing scope is a real refusal ─────────

class _Resp:
    def __init__(self, data=None):
        self.data = data
        self.count = len(data) if isinstance(data, list) else None


class _Query:
    def __init__(self, store, table):
        self.store, self.table = store, table
        self.payload = None
        self.filters = []
        self.mode = "select"

    def select(self, *a, **k):
        self.mode = "select"
        return self

    def insert(self, payload):
        self.mode, self.payload = "insert", payload
        return self

    def update(self, payload):
        self.mode, self.payload = "update", payload
        return self

    def delete(self):
        self.mode = "delete"
        return self

    def eq(self, column, value):
        self.filters.append((column, value))
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def maybe_single(self):
        return self

    def single(self):
        return self

    def _matches(self, row):
        for key, value in self.filters:
            if str(row.get(key)) != str(value):
                return False
        return True

    def execute(self):
        self.store.calls.append((self.table, tuple(self.filters), self.payload))
        rows = self.store.rows.setdefault(self.table, [])
        if self.mode == "insert":
            payload = self.payload if isinstance(self.payload, list) else [self.payload]
            added = []
            for item in payload:
                row = dict(item)
                row.setdefault("id", f"a{len(rows) + len(added) + 1}")
                rows.append(row)
                added.append(row)
            return _Resp(added)
        if self.mode == "update":
            hit = [r for r in rows if self._matches(r)]
            for row in hit:
                row.update(self.payload)
            return _Resp(hit)
        if self.mode == "delete":
            hit = [r for r in rows if self._matches(r)]
            self.store.rows[self.table] = [r for r in rows if r not in hit]
            return _Resp(hit)
        return _Resp([dict(r) for r in rows if self._matches(r)])


class FakeSupabase:
    def __init__(self, rows=None):
        self.rows = {k: [dict(r) for r in v] for k, v in (rows or {}).items()}
        self.calls = []

    def table(self, name):
        return _Query(self, name)


def _svc():
    import importlib
    return importlib.import_module("app.services.grading_annotations")


# ── the schema ───────────────────────────────────────────────────────────────

class TestMigration:
    def test_declares_the_table(self):
        sql = MIGRATION.read_text(encoding="utf-8")
        assert "CREATE TABLE IF NOT EXISTS public.grading_annotation" in sql

    def test_the_kind_vocabulary_is_closed_in_the_database(self):
        sql = MIGRATION.read_text(encoding="utf-8")
        check = re.search(r"kind TEXT[^,]*CHECK[^)]*\)", sql)
        assert check, "kind must carry a CHECK"
        for kind in ("highlight", "strike", "comment"):
            assert kind in check.group(0), kind

    def test_it_is_indexed_by_paper_and_question(self):
        sql = MIGRATION.read_text(encoding="utf-8")
        assert "grading_annotation (attempt_id, question_index" in sql


# ── the range is a range ─────────────────────────────────────────────────────

class TestRangeValidation:
    def test_a_zero_length_range_is_refused(self):
        sb = FakeSupabase()
        try:
            _svc().add_annotation(sb, "sub1", 2, "t1", "highlight", 5, 5)
        except ValueError:
            pass
        else:
            raise AssertionError("an empty range must be refused")
        assert not sb.rows.get("grading_annotation")

    def test_an_inverted_range_is_refused(self):
        sb = FakeSupabase()
        try:
            _svc().add_annotation(sb, "sub1", 2, "t1", "highlight", 9, 4)
        except ValueError:
            pass
        else:
            raise AssertionError("end before start must be refused")

    def test_a_range_past_the_answer_is_refused(self):
        sb = FakeSupabase()
        try:
            _svc().add_annotation(sb, "sub1", 2, "t1", "highlight", 0, 400,
                                  text_length=120)
        except ValueError:
            pass
        else:
            raise AssertionError("a range past the answer must be refused")
        assert not sb.rows.get("grading_annotation")

    def test_a_negative_start_is_refused(self):
        sb = FakeSupabase()
        try:
            _svc().add_annotation(sb, "sub1", 2, "t1", "highlight", -3, 5)
        except ValueError:
            pass
        else:
            raise AssertionError("a negative offset must be refused")


# ── a closed vocabulary ──────────────────────────────────────────────────────

class TestVocabulary:
    def test_an_unknown_kind_is_refused(self):
        sb = FakeSupabase()
        try:
            _svc().add_annotation(sb, "sub1", 2, "t1", "scribble", 0, 5)
        except ValueError:
            pass
        else:
            raise AssertionError("an unknown kind must be refused")
        assert not sb.rows.get("grading_annotation")

    def test_an_unknown_colour_is_refused(self):
        sb = FakeSupabase()
        try:
            _svc().add_annotation(sb, "sub1", 2, "t1", "highlight", 0, 5,
                                  color="chartreuse")
        except ValueError:
            pass
        else:
            raise AssertionError("an unknown colour must be refused")

    def test_the_three_kinds_are_stored(self):
        sb = FakeSupabase()
        svc = _svc()
        for kind in ("highlight", "strike", "comment"):
            row = svc.add_annotation(sb, "sub1", 2, "t1", kind, 0, 5)
            assert row["kind"] == kind
        assert {r["kind"] for r in sb.rows["grading_annotation"]} == {
            "highlight", "strike", "comment"}


# ── a mark is the marker's own ───────────────────────────────────────────────

class TestOwnership:
    def test_listing_returns_marks_in_reading_order(self):
        sb = FakeSupabase(rows={"grading_annotation": [
            {"id": "b", "attempt_id": "sub1", "question_index": 2, "start_offset": 30,
             "end_offset": 40, "created_by": "t1"},
            {"id": "a", "attempt_id": "sub1", "question_index": 2, "start_offset": 4,
             "end_offset": 9, "created_by": "t1"},
            {"id": "c", "attempt_id": "sub1", "question_index": 5, "start_offset": 0,
             "end_offset": 3, "created_by": "t1"},
        ]})
        rows = _svc().list_annotations(sb, "sub1", 2)
        assert [r["id"] for r in rows] == ["a", "b"]

    def test_editing_someone_elses_mark_is_refused(self):
        sb = FakeSupabase(rows={"grading_annotation": [
            {"id": "a", "attempt_id": "sub1", "question_index": 2,
             "created_by": "t2", "note": "asli"},
        ]})
        try:
            _svc().update_annotation(sb, "a", "t1", note="diubah")
        except LookupError:
            pass
        else:
            raise AssertionError("a mark must not be edited by another teacher")
        assert sb.rows["grading_annotation"][0]["note"] == "asli"

    def test_removing_someone_elses_mark_is_refused(self):
        sb = FakeSupabase(rows={"grading_annotation": [
            {"id": "a", "attempt_id": "sub1", "question_index": 2, "created_by": "t2"},
        ]})
        try:
            _svc().remove_annotation(sb, "a", "t1")
        except LookupError:
            pass
        else:
            raise AssertionError("a mark must not be removed by another teacher")
        assert sb.rows["grading_annotation"]

    def test_the_owner_can_edit_and_remove(self):
        sb = FakeSupabase(rows={"grading_annotation": [
            {"id": "a", "attempt_id": "sub1", "question_index": 2, "created_by": "t1",
             "note": None},
        ]})
        svc = _svc()
        row = svc.update_annotation(sb, "a", "t1", note="argumennya kuat")
        assert row["note"] == "argumennya kuat"
        svc.remove_annotation(sb, "a", "t1")
        assert sb.rows["grading_annotation"] == []


# ── the pupil's words are never written to ──────────────────────────────────

class TestTheAnswerStaysUntouched:
    def test_the_service_never_writes_to_submissions(self):
        src = SERVICE.read_text(encoding="utf-8")
        assert 'table("submissions")' not in src, (
            "an annotation lives beside the answer; it must never edit it")
        assert "grading_annotation" in src

    def test_an_annotation_records_the_range_not_the_text(self):
        sb = FakeSupabase()
        row = _svc().add_annotation(sb, "sub1", 2, "t1", "comment", 12, 28,
                                    note="klaim tanpa bukti")
        assert row["start_offset"] == 12
        assert row["end_offset"] == 28
        assert "text" not in row  # the words themselves are the pupil's, not ours


# ── the routes ───────────────────────────────────────────────────────────────

class TestRouteWiring:
    def _src(self):
        return ROUTES.read_text(encoding="utf-8")

    def test_the_annotation_routes_are_registered(self):
        src = self._src()
        assert "/api/grading-assist/annotations" in src

    def test_every_annotation_route_requires_a_teacher(self):
        src = self._src()
        for fn in ("grading_annotation_list", "grading_annotation_add",
                   "grading_annotation_update", "grading_annotation_delete"):
            window = src.split("def " + fn, 1)[0].rsplit("@teacher_bp.route", 1)[-1]
            assert "teacher_or_admin_required" in window, fn

    def test_the_write_routes_guard_the_paper_they_touch(self):
        src = self._src()
        for fn in ("grading_annotation_add", "grading_annotation_update",
                   "grading_annotation_delete"):
            window = src.split("def " + fn, 1)[1][:1400]
            assert "_guard_submission" in window, fn


# ── the page contract ────────────────────────────────────────────────────────

class TestMarkingPage:
    def _page(self):
        return PAGE.read_text(encoding="utf-8")

    def test_the_answer_renders_as_selectable_segments(self):
        page = self._page()
        assert "segmentsFor" in page, "the answer must render as annotation segments"
        assert "answer-body" in page

    def test_the_toolbar_names_a_layer_of_the_scale(self):
        page = self._page()
        assert "sg-layer-float" in page, (
            "a floating annotation toolbar must name a line of the layer scale")

    def test_the_three_marks_are_offered(self):
        page = self._page()
        for kind in ("highlight", "strike", "comment"):
            assert kind in page, kind

    def test_the_floating_palette_carries_score_buttons(self):
        page = self._page()
        toolbar = page.split("sg-layer-float", 1)[1][:3000]
        assert "setQuickScore" in toolbar or "scoreVal" in toolbar, (
            "the floating palette must carry score buttons for typed answers")
