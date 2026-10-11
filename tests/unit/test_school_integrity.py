"""Rows whose `school_id` disagrees with the school of the people they point at.

Reported three times in one week, each a slightly different symptom of one fault:
*"Kenapa 306 mapel. Harusnya hanya mapel sesuai NPSN"*, a class list that included
another school's subjects, a page that answered a school question about a pupil.
Every one of those was a row whose `school_id` said one school while the row it
pointed at said another — and every one was found by a human reading a number that
looked wrong, days later.

A check that only runs when someone suspects something is not a check. So this is
one read-only sweep over the four pairs that matter, and it is deliberately
**install-wide**: a *per-school* filter is the one thing it must not have, because
the row it is looking for is precisely the row that claims another school — filter
by one school and the check discards its own evidence.

The other decision worth stating: a read that fails is **not** a clean report.
Every other reader in this repository turns a failure into an empty list, which is
right for a timeline or a code list — a page that 500s because its evidence is
missing is worse than one that says it has none. Here the opposite is true. An
operator who is told "no cross-school rows" because the query itself failed has
been told the one thing that must never be a guess, so the result separates
`ok: False` (the sweep did not run) from `ok: True, findings: []` (it ran and the
data is clean).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SERVICE = ROOT / "app" / "services" / "school_integrity.py"
SUPER_ROUTES = ROOT / "app" / "routes" / "super_admin.py"
DASHBOARD = ROOT / "app" / "templates" / "super_admin" / "dashboard.html"


# ── a PostgREST stand-in, recording what it was asked ───────────────────────

class _Res:
    def __init__(self, data):
        self.data = data
        self.count = len(data or [])


class _Not:
    """The `not_.is_(...)` chain, which the pupil read needs to skip NULLs."""

    def __init__(self, query):
        self._q = query

    def is_(self, column, value):
        self._q._filters.append((column, ("__not_is__", value)))
        return self._q


class _Query:
    def __init__(self, db, table):
        self.db = db
        self.table = table
        self._filters = []
        self._select = None
        self._raise = None

    def select(self, columns="*", **kw):
        self._select = columns
        return self

    def eq(self, column, value):
        self._filters.append((column, value))
        return self

    def in_(self, column, values):
        self._filters.append((column, ("__in__", list(values))))
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    @property
    def not_(self):
        return _Not(self)

    def _match(self, row) -> bool:
        for column, value in self._filters:
            if isinstance(value, tuple) and value and value[0] == "__not_is__":
                if row.get(column) is None:
                    return False
            elif isinstance(value, tuple) and value and value[0] == "__in__":
                if str(row.get(column)) not in {str(v) for v in value[1]}:
                    return False
            elif str(row.get(column)) != str(value):
                return False
        return True

    def execute(self):
        self.db.log.append(("select", self.table, dict(self._filters), self._select))
        if self._raise:
            raise self._raise
        if self.db.break_table == self.table:
            raise RuntimeError("connection reset")
        return _Res([dict(r) for r in self.db.tables.get(self.table, [])
                     if self._match(r)])


class _DB:
    def __init__(self, break_table=None, **tables):
        self.tables = {name: [dict(r) for r in rows] for name, rows in tables.items()}
        self.log = []
        self.break_table = break_table

    def table(self, name):
        return _Query(self, name)


# ── fixture data: two schools, and rows that cross them ─────────────────────

SCH_A, SCH_B = "school-a", "school-b"


def _clean():
    return _DB(
        classes=[{"id": "c1", "name": "7A", "school_id": SCH_A}],
        subjects=[{"id": "s1", "name": "Math", "school_id": SCH_A}],
        class_subjects=[{"id": "p1", "class_id": "c1", "subject_id": "s1",
                         "school_id": SCH_A, "is_active": True}],
        teacher_assignments=[{"id": "a1", "class_id": "c1", "subject_id": "s1",
                              "school_id": SCH_A, "status": "active"}],
        profiles=[{"id": "u1", "class_id": "c1", "school_id": SCH_A}],
    )


def _kinds(out):
    return sorted({f["kind"] for f in out["findings"]})


# ── the check exists, and is a check ───────────────────────────────────────

class TestTheCheck:
    def test_the_service_exists(self):
        assert SERVICE.exists(), "there is no cross-school integrity check"
        from app.services import school_integrity
        assert callable(school_integrity.cross_school_findings)

    def test_a_clean_install_reports_clean(self):
        from app.services import school_integrity
        out = school_integrity.cross_school_findings(_clean())
        assert out["ok"] is True
        assert out["findings"] == []
        assert out["errors"] == []

    def test_it_reads_every_pair_that_can_span_two_schools(self):
        from app.services import school_integrity
        db = _clean()
        school_integrity.cross_school_findings(db)
        tables = {t for _op, t, _f, _c in db.log}
        for table in ("classes", "subjects", "class_subjects",
                      "teacher_assignments", "profiles", "exams", "submissions",
                      "student_subject_levels"):
            assert table in tables, f"the sweep never reads `{table}`"

    def test_it_writes_nothing(self):
        from app.services import school_integrity
        db = _clean()
        school_integrity.cross_school_findings(db)
        assert all(op == "select" for op, *_ in db.log), (
            "an integrity check that writes can change the data it is measuring")

    def test_it_is_install_wide_on_purpose(self):
        """The one thing it must not do: filter by a single school.

        A row whose `school_id` says school B while it points at school A's class is
        *found by* looking at A's class and *discarded by* filtering the sweep on B.
        So no read may carry a hard-coded school filter.
        """
        src = SERVICE.read_text(encoding="utf-8")
        body = src.split("def cross_school_findings(", 1)[1]
        assert "school_id" in body or "school" in body
        assert not re.search(r'\.eq\(\s*"school_id"', body), (
            "the sweep filters its own reads by one school, which is how it loses "
            "the very row it exists to find")


# ── a class whose pupils are in another school ─────────────────────────────

class TestAClassAndItsPupils:
    def test_a_pupil_from_another_school_in_the_class_is_flagged(self):
        from app.services import school_integrity
        db = _clean()
        db.tables["profiles"] = [{"id": "u1", "class_id": "c1", "school_id": SCH_B}]
        out = school_integrity.cross_school_findings(db)
        assert out["ok"] is True and out["findings"], "cross-NPSN pupil not flagged"
        assert found_kinds(out, "class") == ["class_pupil_mismatch"]

    def test_the_finding_names_the_class_and_both_schools(self):
        from app.services import school_integrity
        db = _clean()
        db.tables["profiles"] = [{"id": "u1", "class_id": "c1", "school_id": SCH_B}]
        f = school_integrity.cross_school_findings(db)["findings"][0]
        assert f["class_id"] == "c1"
        assert str(f["class_school_id"]) == SCH_A
        assert f["pupil_id"] == "u1"
        assert str(f["pupil_school_id"]) == SCH_B

    def test_a_pupil_with_no_school_is_not_a_mismatch(self):
        """NULL is unknown, not a contradiction — flagging it floods a real box."""
        from app.services import school_integrity
        db = _clean()
        db.tables["profiles"] = [{"id": "u1", "class_id": "c1", "school_id": None}]
        out = school_integrity.cross_school_findings(db)
        assert out["findings"] == [], (
            "a pupil with no school on file was reported as a cross-school row")

    def test_a_class_with_no_school_is_not_a_mismatch(self):
        from app.services import school_integrity
        db = _clean()
        db.tables["classes"] = [{"id": "c1", "name": "7A", "school_id": None}]
        out = school_integrity.cross_school_findings(db)
        assert out["findings"] == []

    def test_only_pupils_who_have_a_class_are_read(self):
        """The read is bounded to the rows that can express the relation."""
        from app.services import school_integrity
        db = _clean()
        school_integrity.cross_school_findings(db)
        profile_reads = [f for op, t, f, _c in db.log if t == "profiles"]
        assert profile_reads, "profiles were never read"
        assert any(f.get("class_id") == ("__not_is__", "null") for f in profile_reads), (
            "every account in the install is pulled to compare a handful of pupils")


def found_kinds(out, needle):
    return sorted({f["kind"] for f in out["findings"] if needle in f["kind"]})


def _find(out, kind):
    return [f for f in out["findings"] if f["kind"] == kind]


# ── an exam, and the class it points at ───────────────────────────────────

class TestAnExamAndItsClass:
    def test_an_exam_naming_another_schools_class_is_flagged(self):
        from app.services import school_integrity
        db = _clean()
        db.tables["exams"] = [{"id": "e1", "title": "Math", "class_id": "c1",
                               "school_id": SCH_B}]
        out = school_integrity.cross_school_findings(db)
        assert "exam_school_mismatch" in _kinds(out), (
            "an exam in one school points at another school's class")

    def test_the_exam_finding_names_the_exam_and_both_schools(self):
        from app.services import school_integrity
        db = _clean()
        db.tables["exams"] = [{"id": "e1", "title": "Math", "class_id": "c1",
                               "school_id": SCH_B}]
        f = _find(school_integrity.cross_school_findings(db), "exam_school_mismatch")[0]
        assert f["exam_id"] == "e1"
        assert str(f["exam_school_id"]) == SCH_B
        assert str(f["class_school_id"]) == SCH_A
        assert f["class_id"] == "c1"

    def test_an_exam_in_its_classs_school_is_not_flagged(self):
        from app.services import school_integrity
        db = _clean()
        db.tables["exams"] = [{"id": "e1", "title": "Math", "class_id": "c1",
                               "school_id": SCH_A}]
        assert _find(school_integrity.cross_school_findings(db),
                     "exam_school_mismatch") == []

    def test_an_exam_with_no_class_is_not_a_mismatch(self):
        """A paper not tied to a class cannot contradict a class — NULL is unknown."""
        from app.services import school_integrity
        db = _clean()
        db.tables["exams"] = [{"id": "e1", "title": "Math", "class_id": None,
                               "school_id": SCH_B}]
        assert school_integrity.cross_school_findings(db)["findings"] == []


# ── a submission joins an exam and a pupil ────────────────────────────────

class TestASubmissionAcrossSchools:
    def _db(self):
        db = _clean()
        db.tables["exams"] = [{"id": "e1", "title": "Math", "class_id": "c1",
                               "school_id": SCH_A}]
        db.tables["submissions"] = [{"id": "sub1", "exam_id": "e1",
                                     "student_id": "u1"}]
        return db

    def test_a_submission_for_another_schools_exam_is_flagged(self):
        from app.services import school_integrity
        db = self._db()
        db.tables["profiles"] = [{"id": "u1", "class_id": "c1", "school_id": SCH_B}]
        out = school_integrity.cross_school_findings(db)
        assert "submission_school_mismatch" in _kinds(out), (
            "a pupil in one school sat another school's paper")

    def test_the_finding_pairs_the_exams_school_with_the_pupils(self):
        from app.services import school_integrity
        db = self._db()
        db.tables["profiles"] = [{"id": "u1", "class_id": "c1", "school_id": SCH_B}]
        f = _find(school_integrity.cross_school_findings(db),
                  "submission_school_mismatch")[0]
        assert f["submission_id"] == "sub1"
        assert str(f["exam_school_id"]) == SCH_A
        assert str(f["pupil_school_id"]) == SCH_B

    def test_a_submission_inside_one_school_is_not_flagged(self):
        from app.services import school_integrity
        db = self._db()
        out = school_integrity.cross_school_findings(db)
        assert _find(out, "submission_school_mismatch") == []

    def test_a_submission_whose_pupil_school_is_unknown_is_not_flagged(self):
        from app.services import school_integrity
        db = self._db()
        db.tables["profiles"] = [{"id": "u1", "class_id": "c1", "school_id": None}]
        assert _find(school_integrity.cross_school_findings(db),
                     "submission_school_mismatch") == []


# ── a pupil's subject level, and the pupil and class it names ─────────────

class TestASubjectLevelAcrossSchools:
    def test_a_level_naming_another_schools_pupil_is_flagged(self):
        from app.services import school_integrity
        db = _clean()
        db.tables["profiles"] = [{"id": "u1", "class_id": "c1", "school_id": SCH_B}]
        db.tables["student_subject_levels"] = [
            {"id": "lv1", "student_id": "u1", "subject_id": "s1",
             "class_id": "c1", "school_id": SCH_A}]
        out = school_integrity.cross_school_findings(db)
        field = [f for f in _find(out, "subject_level_school_mismatch")
                 if f["field"] == "pupil"]
        assert field, "a level in one school names another school's pupil"

    def test_a_level_naming_another_schools_class_is_flagged(self):
        from app.services import school_integrity
        db = _clean()
        db.tables["classes"] = [{"id": "c1", "name": "7A", "school_id": SCH_B}]
        db.tables["student_subject_levels"] = [
            {"id": "lv1", "student_id": None, "subject_id": "s1",
             "class_id": "c1", "school_id": SCH_A}]
        out = school_integrity.cross_school_findings(db)
        field = [f for f in _find(out, "subject_level_school_mismatch")
                 if f["field"] == "class"]
        assert field, "a level in one school names another school's class"

    def test_the_level_finding_names_the_level_and_the_schools(self):
        from app.services import school_integrity
        db = _clean()
        db.tables["profiles"] = [{"id": "u1", "class_id": "c1", "school_id": SCH_B}]
        db.tables["student_subject_levels"] = [
            {"id": "lv1", "student_id": "u1", "subject_id": "s1",
             "class_id": "c1", "school_id": SCH_A}]
        f = [x for x in _find(school_integrity.cross_school_findings(db),
                              "subject_level_school_mismatch")
             if x["field"] == "pupil"][0]
        assert f["level_id"] == "lv1"
        assert str(f["level_school_id"]) == SCH_A
        assert str(f["pupil_school_id"]) == SCH_B

    def test_a_level_inside_one_school_is_not_flagged(self):
        from app.services import school_integrity
        db = _clean()
        db.tables["student_subject_levels"] = [
            {"id": "lv1", "student_id": "u1", "subject_id": "s1",
             "class_id": "c1", "school_id": SCH_A}]
        assert _find(school_integrity.cross_school_findings(db),
                     "subject_level_school_mismatch") == []

    def test_a_level_with_no_pupil_and_no_class_is_not_flagged(self):
        from app.services import school_integrity
        db = _clean()
        db.tables["student_subject_levels"] = [
            {"id": "lv1", "student_id": None, "subject_id": "s1",
             "class_id": None, "school_id": SCH_B}]
        assert _find(school_integrity.cross_school_findings(db),
                     "subject_level_school_mismatch") == []


# ── a subject offered to, or assigned across, two schools ──────────────────

class TestASubjectAndItsPupils:
    def test_a_pair_naming_another_schools_subject_is_flagged(self):
        from app.services import school_integrity
        db = _clean()
        db.tables["subjects"] = [{"id": "s1", "name": "Math", "school_id": SCH_B}]
        out = school_integrity.cross_school_findings(db)
        assert "pair_school_mismatch" in _kinds(out), (
            "a class in one school was offering a subject that belongs to another")
        f = [x for x in out["findings"] if x["kind"] == "pair_school_mismatch"][0]
        assert f["table"] == "class_subjects"
        assert str(f["subject_school_id"]) == SCH_B
        assert str(f["pair_school_id"]) == SCH_A

    def test_a_pair_naming_another_schools_class_is_flagged(self):
        from app.services import school_integrity
        db = _clean()
        db.tables["classes"] = [{"id": "c1", "name": "7A", "school_id": SCH_B}]
        out = school_integrity.cross_school_findings(db)
        assert "pair_school_mismatch" in _kinds(out), (
            "a subject in one school was mapped onto another school's class")

    def test_a_teacher_assignment_spanning_two_schools_is_flagged(self):
        """The table the \"306 mapel\" count came from: a cross-school row here is
        exactly the mixture that kept reappearing."""
        from app.services import school_integrity
        db = _clean()
        db.tables["subjects"] = [{"id": "s1", "name": "Math", "school_id": SCH_B}]
        out = school_integrity.cross_school_findings(db)
        assert "assignment_school_mismatch" in _kinds(out), (
            "a teacher is assigned to teach another school's subject")

    def test_the_assignment_finding_names_the_assignment(self):
        from app.services import school_integrity
        db = _clean()
        db.tables["subjects"] = [{"id": "s1", "name": "Math", "school_id": SCH_B}]
        f = [x for x in school_integrity.cross_school_findings(db)["findings"]
             if x["kind"] == "assignment_school_mismatch"][0]
        assert f["assignment_id"] == "a1"
        assert f["field"] == "subject"


# ── the honest half: a failed read is never "clean" ────────────────────────

class TestAFailedReadIsNotACleanReport:
    def test_a_broken_read_says_the_sweep_did_not_run(self):
        from app.services import school_integrity
        out = school_integrity.cross_school_findings(_DB(break_table="profiles"))
        assert out["ok"] is False, (
            "the sweep reported a clean install when it could not read the pupils "
            "— the one answer that must never be a guess")
        assert out["errors"], "no error was recorded"
        assert out["findings"] == []

    def test_the_error_names_the_table_that_failed(self):
        from app.services import school_integrity
        out = school_integrity.cross_school_findings(_DB(break_table="classes"))
        assert any("classes" in str(e) for e in out["errors"])

    def test_a_partial_failure_still_reports_what_it_did_see(self):
        """A finding already proven stays reported, with `ok: False` beside it."""
        from app.services import school_integrity
        db = _clean()
        db.tables["profiles"] = [{"id": "u1", "class_id": "c1", "school_id": SCH_B}]
        db.break_table = "subjects"
        out = school_integrity.cross_school_findings(db)
        assert out["ok"] is False
        assert out["findings"], "a real finding was dropped because a later read failed"


# ── bounded output ─────────────────────────────────────────────────────────

class TestTheOutputIsBounded:
    def test_findings_are_capped(self):
        from app.services import school_integrity
        db = _clean()
        db.tables["profiles"] = [{"id": f"u{i}", "class_id": "c1", "school_id": SCH_B}
                                 for i in range(50)]
        out = school_integrity.cross_school_findings(db, limit=5)
        assert len(out["findings"]) == 5
        assert out["truncated"] is True

    def test_a_short_list_is_not_flagged_as_truncated(self):
        from app.services import school_integrity
        out = school_integrity.cross_school_findings(_clean(), limit=5)
        assert out["truncated"] is False

    def test_it_reports_how_much_it_looked_at(self):
        from app.services import school_integrity
        out = school_integrity.cross_school_findings(_clean())
        assert out["checked"]["classes"] == 1
        assert out["checked"]["profiles"] == 1


# ── the operator actually sees it ──────────────────────────────────────────

class TestTheOperatorSeesIt:
    def test_the_dashboard_reads_the_check(self):
        src = SUPER_ROUTES.read_text(encoding="utf-8")
        body = src.split("def dashboard(", 1)[1].split("\ndef ", 1)[0]
        assert "school_integrity" in body, (
            "the check runs nowhere, so cross-NPSN rows stay invisible again")
        assert "integrity" in body, "the result never reaches the template"

    def test_the_dashboard_caches_the_sweep(self):
        """Five whole-table reads must not ride every dashboard load."""
        src = SUPER_ROUTES.read_text(encoding="utf-8")
        body = src.split("def dashboard(", 1)[1].split("\ndef ", 1)[0]
        assert "ttl(" in body, "the sweep is not cached"

    def test_the_template_renders_the_findings(self):
        html = DASHBOARD.read_text(encoding="utf-8")
        assert "integrity" in html, "the dashboard template ignores the sweep"
        assert "findings" in html, "the findings are not listed"

    def test_the_template_is_bilingual(self):
        html = DASHBOARD.read_text(encoding="utf-8")
        block = html.split("integrity", 1)[1][:3000]
        assert "t('" in block, "the integrity panel carries no i18n helper"

    def test_the_panel_says_when_the_sweep_itself_failed(self):
        """`ok: False` needs its own sentence, or it reads as \"all clear\"."""
        html = DASHBOARD.read_text(encoding="utf-8")
        assert re.search(r"integrity\.ok", html), (
            "the panel does not distinguish \"clean\" from \"could not check\"")
