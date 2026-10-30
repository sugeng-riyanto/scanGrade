"""A removal is checked *before* it is written — so what starts in between?

`teacher_assignments.save` refuses a removal with a live paper (409) unless the
admin confirms. But the check runs before the updates, and a paper minted in that
window — by a teacher who was still authorised then — is legitimate: it must not
fail the save, and it must not vanish silently either. So a removal that passed a
clean pre-check re-reads the live papers afterwards and reports anything that
started mid-save as `started_during_save`.

With `confirm_remove=True` there was no clean baseline (the admin already
accepted live papers), so nothing is reported there — the key means exactly
\"new since the check\", never \"live\".
"""
from __future__ import annotations

from pathlib import Path

from app.services import teacher_assignments as ta

ROOT = Path(__file__).resolve().parents[2]

SCHOOL = "school-1"
TEACHER = "teacher-1"
YEAR = "2026/2027"


def _live_exam(eid="e9", title="Quiz dadakan"):
    return {"id": eid, "title": title, "teacher_id": TEACHER,
            "subject_id": "s1", "school_id": SCHOOL, "class_ids": ["c1"],
            "status": "active", "is_published": True}


class _Q:
    def __init__(self, db, table):
        self.db = db
        self.table = table
        self.rows = list(db.tables.get(table, []))
        self._update = None

    def select(self, *a, **k):
        return self

    def eq(self, col, val):
        self.rows = [r for r in self.rows if str(r.get(col)) == str(val)]
        return self

    def in_(self, col, vals):
        wanted = {str(v) for v in vals}
        self.rows = [r for r in self.rows if str(r.get(col)) in wanted]
        return self

    def limit(self, n):
        self.rows = self.rows[:n]
        return self

    def update(self, payload):
        self._update = payload
        return self

    def upsert(self, rows, on_conflict=None):
        self.db.writes.append(("upsert", self.table, rows, on_conflict))
        return self

    def execute(self):
        from types import SimpleNamespace

        if self._update is not None:
            self.db.writes.append(("update", self.table, self._update))
            return SimpleNamespace(data=[])
        if self.table == "exams" and self.db.exams_script:
            return SimpleNamespace(data=self.db.exams_script.pop(0))
        return SimpleNamespace(data=self.rows)


class _Db:
    """Enough PostgREST for `save`, with scripted `exams` reads.

    `exams_script` answers the 1st, 2nd, … `exams` read in order, so a test can
    mint a paper *between* the pre-check and the re-check. `None` (the default)
    reads the table normally.
    """

    def __init__(self, exams_script=None):
        self.tables = {
            "school_years": [{"id": "y1", "name": YEAR, "is_active": True,
                              "school_id": SCHOOL}],
            "classes": [{"id": "c1", "school_id": SCHOOL}],
            "subjects": [{"id": "s1", "school_id": SCHOOL}],
            "profiles": [{"id": TEACHER, "school_id": SCHOOL, "role": "guru"}],
            "teacher_assignments": [
                {"teacher_id": TEACHER, "class_id": "c1", "subject_id": "s1",
                 "school_id": SCHOOL, "status": "active", "school_year": YEAR}],
            "class_subjects": [],
            "exams": [],
        }
        self.writes = []
        self.exams_script = list(exams_script) if exams_script else None

    def table(self, name):
        return _Q(self, name)


def test_a_paper_minted_mid_save_is_reported_not_lost():
    db = _Db(exams_script=[[], [_live_exam()]])
    ok, result = ta.save(db, SCHOOL, TEACHER, [], year_name=YEAR)
    assert ok, result
    assert result["removed_count"] == 1
    assert [w for w in db.writes if w[0] == "update"], "the removal still applied"
    assert [e["title"] for e in result.get("started_during_save", [])] == ["Quiz dadakan"]


def test_a_quiet_save_reports_nothing_new():
    db = _Db(exams_script=[[], []])
    ok, result = ta.save(db, SCHOOL, TEACHER, [], year_name=YEAR)
    assert ok, result
    assert "started_during_save" not in result


def test_a_confirmed_removal_reports_no_baseline():
    # The admin already accepted live papers, so there is no "new since the
    # check" to report — the key stays off rather than relabelling old papers.
    db = _Db()
    db.tables["exams"] = [_live_exam()]
    ok, result = ta.save(db, SCHOOL, TEACHER, [], year_name=YEAR, confirm_remove=True)
    assert ok, result
    assert "started_during_save" not in result
