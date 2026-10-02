"""Marking 200 essays is not 200 forms — it is one question, marked 200 times.

Fase 0 read the two pages that exist today. `/teacher/grading/<exam_id>` marks one
teacher's whole paper at a time; `/teacher/grade-question/<exam_id>/<n>` is the
per-question queue the brief calls the default, and it is **broken**: a stray
closing `</div>` after the shortcut hint closes the page container early, so every
student card renders outside it. The per-question page is also the one that lacks
the three things a long marking session needs — a reusable comment, a "come back
to this one" flag, and a record of what a mark used to be.

This suite pins the assist layer those three need:

* a **comment bank** owned by the teacher, ordered by how often it was applied,
  carrying an optional mark deduction so flagging and subtracting are one action;
* a **review flag** that is one row per (paper, question) — setting it twice is
  still one flag;
* a **score audit** that writes a row **only when the mark actually changed**, so
  re-saving an untouched paper does not manufacture a history of edits.

Everything is scoped to the caller: a comment is applied only by the teacher who
wrote it, and a flag is set only on a paper the caller may grade.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVICE = ROOT / "app" / "services" / "grading_assist.py"
MIGRATION = ROOT / "supabase" / "migrations" / "052_grading_assist.sql"
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
        self.order_by = a
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
        if self.table in self.store.fail:
            raise RuntimeError("boom")
        rows = self.store.rows.setdefault(self.table, [])
        if self.mode == "insert":
            payload = self.payload if isinstance(self.payload, list) else [self.payload]
            added = []
            for item in payload:
                row = dict(item)
                row.setdefault("id", f"r{len(rows) + len(added) + 1}")
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
    def __init__(self, rows=None, fail=()):
        self.rows = {k: [dict(r) for r in v] for k, v in (rows or {}).items()}
        self.fail = set(fail)
        self.calls = []

    def table(self, name):
        return _Query(self, name)


def _service():
    import importlib
    return importlib.import_module("app.services.grading_assist")


# ── the schema: one row per fact, not one row per save ───────────────────────

class TestMigration:
    def test_declares_the_three_tables(self):
        sql = MIGRATION.read_text(encoding="utf-8")
        for table in ("grading_comment_bank", "grading_flag", "grading_audit_log"):
            assert f"CREATE TABLE IF NOT EXISTS public.{table}" in sql, table

    def test_a_paper_gets_one_flag_per_question(self):
        sql = MIGRATION.read_text(encoding="utf-8")
        # Without a unique key, "flag it, then flag it again" is two rows and the
        # review list counts a paper twice.
        assert re.search(r"UNIQUE INDEX[^;]*grading_flag[^;]*\(attempt_id, question_index\)",
                         sql, re.S), "grading_flag needs a unique (attempt, question) key"

    def test_the_bank_is_indexed_by_owner(self):
        sql = MIGRATION.read_text(encoding="utf-8")
        assert "grading_comment_bank (teacher_id" in sql


# ── the bank ─────────────────────────────────────────────────────────────────

class TestCommentBank:
    def test_rejects_an_empty_comment_without_writing(self):
        sb = FakeSupabase()
        try:
            _service().add_comment(sb, "t1", "   ")
        except ValueError:
            pass
        else:
            raise AssertionError("an empty comment must be refused")
        assert not sb.rows.get("grading_comment_bank")

    def test_rejects_a_deduction_outside_the_mark_scale(self):
        sb = FakeSupabase()
        try:
            _service().add_comment(sb, "t1", "Typo berulang", score_delta=-140)
        except ValueError:
            pass
        else:
            raise AssertionError("a -140 deduction must be refused")
        assert not sb.rows.get("grading_comment_bank")

    def test_a_new_comment_starts_unused(self):
        sb = FakeSupabase()
        row = _service().add_comment(sb, "t1", "Argumen belum dijelaskan",
                                     subject="Fisika", question_index=2, score_delta=-5)
        assert row["body"] == "Argumen belum dijelaskan"
        assert row["uses"] == 0
        assert row["score_delta"] == -5
        assert row["question_index"] == 2

    def test_listing_is_scoped_to_the_owner_and_frequency_order(self):
        sb = FakeSupabase(rows={"grading_comment_bank": [
            {"id": "a", "teacher_id": "t1", "body": "jarang", "uses": 1},
            {"id": "b", "teacher_id": "t1", "body": "sering", "uses": 9},
            {"id": "c", "teacher_id": "t2", "body": "milik guru lain", "uses": 99},
        ]})
        rows = _service().list_bank(sb, "t1", subject="Fisika", question_index=2)
        assert [r["id"] for r in rows] == ["b", "a"]

    def test_applying_someone_elses_comment_is_refused(self):
        sb = FakeSupabase(rows={"grading_comment_bank": [
            {"id": "c", "teacher_id": "t2", "body": "milik guru lain", "uses": 3},
        ]})
        try:
            _service().apply_comment(sb, "t1", "c")
        except LookupError:
            pass
        else:
            raise AssertionError("a teacher must not apply another teacher's comment")
        assert sb.rows["grading_comment_bank"][0]["uses"] == 3

    def test_applying_counts_the_use_and_returns_the_body(self):
        sb = FakeSupabase(rows={"grading_comment_bank": [
            {"id": "c", "teacher_id": "t1", "body": "Kesimpulan lemah",
             "uses": 4, "score_delta": -3},
        ]})
        applied = _service().apply_comment(sb, "t1", "c")
        assert applied["body"] == "Kesimpulan lemah"
        assert applied["score_delta"] == -3
        assert sb.rows["grading_comment_bank"][0]["uses"] == 5


# ── the flag ─────────────────────────────────────────────────────────────────

class TestReviewFlag:
    def test_flagging_twice_is_still_one_flag(self):
        sb = FakeSupabase()
        svc = _service()
        svc.set_flag(sb, "sub1", 3, "t1", reason="ragu")
        svc.set_flag(sb, "sub1", 3, "t1", reason="masih ragu")
        rows = sb.rows["grading_flag"]
        assert len(rows) == 1
        assert rows[0]["reason"] == "masih ragu"

    def test_a_flag_records_the_question_it_belongs_to(self):
        sb = FakeSupabase()
        row = _service().set_flag(sb, "sub1", 7, "t1")
        assert row["attempt_id"] == "sub1"
        assert row["question_index"] == 7

    def test_clearing_removes_only_that_question(self):
        sb = FakeSupabase(rows={"grading_flag": [
            {"id": "f1", "attempt_id": "sub1", "question_index": 3, "teacher_id": "t1"},
            {"id": "f2", "attempt_id": "sub1", "question_index": 7, "teacher_id": "t1"},
        ]})
        _service().clear_flag(sb, "sub1", 3, "t1")
        left = sb.rows["grading_flag"]
        assert [r["question_index"] for r in left] == [7]

    def test_listing_flags_answers_per_paper_and_question(self):
        sb = FakeSupabase(rows={"grading_flag": [
            {"attempt_id": "sub1", "question_index": 3, "teacher_id": "t1"},
            {"attempt_id": "sub1", "question_index": 7, "teacher_id": "t1"},
        ]})
        flags = _service().flags_for(sb, "sub1")
        assert set(flags) == {3, 7}


# ── the audit ────────────────────────────────────────────────────────────────

class TestScoreAudit:
    def test_saving_an_unchanged_mark_writes_no_history(self):
        sb = FakeSupabase()
        written = _service().record_score_change(sb, "sub1", 2, "t1", 80, 80)
        assert written is None
        assert not sb.rows.get("grading_audit_log")

    def test_a_real_change_records_both_marks(self):
        sb = FakeSupabase()
        written = _service().record_score_change(sb, "sub1", 2, "t1", 80, 65)
        assert written["old_score"] == 80
        assert written["new_score"] == 65
        assert sb.rows["grading_audit_log"][0]["question_index"] == 2

    def test_first_mark_records_from_nothing(self):
        sb = FakeSupabase()
        written = _service().record_score_change(sb, "sub1", 2, "t1", None, 70)
        assert written is not None
        assert written["old_score"] is None


# ── the routes: scoped, guarded, and reachable ───────────────────────────────

class TestRouteWiring:
    def _src(self):
        return ROUTES.read_text(encoding="utf-8")

    def test_the_bank_routes_are_registered(self):
        src = self._src()
        assert "/api/grading-assist/bank" in src
        assert "/api/grading-assist/flag" in src

    def test_the_bank_routes_require_a_teacher(self):
        src = self._src()
        for needle in ("def grading_assist_bank", "def grading_assist_apply",
                       "def grading_assist_flag"):
            # Every one of them sits under `@teacher_or_admin_required`.
            window = src.split(needle)[0].rsplit("@teacher_bp.route", 1)[-1]
            assert "teacher_or_admin_required" in window, needle

    def test_the_flag_route_guards_the_submission_it_touches(self):
        src = self._src()
        window = src.split("def grading_assist_flag", 1)[1][:1200]
        assert "_guard_submission" in window, \
            "a flag may only be set on a paper the caller may grade"

    def test_saving_a_question_records_the_change(self):
        src = self._src()
        window = src.split("def grade_question_save", 1)[1][:2500]
        assert "record_score_change" in window
        assert "grading_assist" in src


# ── the page: the marking contract the brief asks for ────────────────────────

class TestMarkingPage:
    def _page(self):
        return PAGE.read_text(encoding="utf-8")

    def test_the_stray_closing_div_is_gone(self):
        # The bug Fase 0 found: the container closes before the cards, so every
        # student card renders outside the graded page body.
        page = self._page()
        assert "</div>\n    </div>\n\n    <!-- Loading -->" not in page

    def test_the_answer_is_read_at_a_comfortable_size(self):
        page = self._page()
        # A 12px answer is the thing the brief calls out; the answer body must not
        # be pinned at `text-xs`.
        assert "answer-body" in page and "text-base" in page

    def test_the_score_control_stays_in_view(self):
        page = self._page()
        assert "sticky" in page.split("answer-body")[0], \
            "the score control must be sticky so it never scrolls away"

    def test_the_comment_bank_is_reachable_from_the_page(self):
        page = self._page()
        assert "bank" in page.lower()
