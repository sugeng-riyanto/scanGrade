"""One reader for the school's switch, and two pages that say which papers are off.

`exams.anti_cheat_enabled` decides whether a paper counts violations at all. Four
callers need that answer — the door that writes a row, the ladder that prices it,
the count stored on the sitting, and the resume-code lock — and each of them asked
the flag for itself. The cost was measurable: the log wrote a row for a paper the
school had switched off while the ladder charged nothing for it, and the two routes
that *write a penalty onto a result* never selected the column at all, so a paper
whose switch was off was priced as if it were monitored (PostgREST reads a column
left out of a select as absent, never as an error, which is why nothing complained).

What this file pins:

* **one guard** — `anti_cheat_service.enabled`, and the *comparison* named nowhere
  else, so a fifth caller cannot re-derive it;
* **the pages** — the exams list and the paper's own builder say an unmonitored paper
  is unmonitored, and the builder no longer re-arms it on save (it posted a hidden
  `value="true"` beside a card that read "Always on" whatever the row held);
* **the audit** — the rows already recorded on switched-off papers can be read as
  data, and the read changes nothing: no score, no penalty, no row.
"""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "app"
SERVICE = (APP / "services" / "anti_cheat_service.py").read_text(encoding="utf-8")
RESUME = (APP / "services" / "resume_code.py").read_text(encoding="utf-8")
API = (APP / "routes" / "api.py").read_text(encoding="utf-8")
STUDENT = (APP / "routes" / "student.py").read_text(encoding="utf-8")
TEACHER = (APP / "routes" / "teacher.py").read_text(encoding="utf-8")
AUDIT_CLI = (ROOT / "deploy" / "audit_unmonitored_violations.py").read_text(encoding="utf-8")
EXAMS = (APP / "templates" / "teacher" / "exams.html").read_text(encoding="utf-8")
FORM = APP / "templates" / "teacher" / "exam_form.html"

#: The decision `enabled()` makes, in the shape only that function may write.
DECISION = '("anti_cheat_enabled") is not False'


# ── the fake client ───────────────────────────────────────────────────────────

class FakeQuery:
    """Chainable postgrest stand-in that applies its filters and records writes."""

    def __init__(self, rows, writes, fail=False, name=""):
        self._rows, self._writes, self._fail, self._name = rows, writes, fail, name
        self.filters = {}
        self.selected = ""

    def select(self, columns, *a, **k):
        self.selected = columns
        return self

    def eq(self, column, value):
        self.filters[column] = value
        return self

    def in_(self, column, values):
        self.filters[column] = list(values)
        return self

    def order(self, *a, **k):
        return self

    def execute(self):
        if self._fail:
            raise RuntimeError("connection reset")
        rows = [row for row in self._rows
                if all(row.get(key) in value if isinstance(value, list)
                       else row.get(key) == value
                       for key, value in self.filters.items())]
        # `count` travels with the rows: `count_penalized_violations` reads an exact
        # count, and a stub without one would make it answer 0 for the wrong reason.
        return SimpleNamespace(data=rows, count=len(rows))

    # every write is recorded and refused: an audit that wrote would be the bug
    def insert(self, *a, **k):
        self._writes.append(("insert", self._name))
        return self

    def update(self, *a, **k):
        self._writes.append(("update", self._name))
        return self

    def delete(self, *a, **k):
        self._writes.append(("delete", self._name))
        return self

    def upsert(self, *a, **k):
        self._writes.append(("upsert", self._name))
        return self


class FakeSupabase:
    def __init__(self, tables, fail=False):
        self.tables, self.fail, self.writes, self.queries = tables, fail, [], []

    def table(self, name):
        query = FakeQuery(self.tables.get(name, []), self.writes,
                          fail=self.fail, name=name)
        self.queries.append((name, query))
        return query


OFF_PAPER = {"id": "exam-off", "title": "UH Fisika", "teacher_id": "t-1",
             "school_id": "sch-1", "anti_cheat_enabled": False}
ON_PAPER = {"id": "exam-on", "title": "UH Kimia", "teacher_id": "t-1",
            "school_id": "sch-1", "anti_cheat_enabled": True}


def _row(uid="stu-1", exam="exam-off", kind="tab_switch", at="2026-09-21T02:12:30+00:00"):
    return {"user_id": uid, "exam_id": exam, "violation_type": kind,
            "created_at": at, "metadata": {"trigger": "visibilitychange"}}


def _report(tables=None, **kwargs):
    from app.services.anti_cheat_service import unmonitored_report
    client = FakeSupabase(tables or {
        "exams": [OFF_PAPER, ON_PAPER],
        "violation_logs": [_row(), _row(kind="focus_lost"), _row(uid="stu-2"),
                           _row(exam="exam-on", uid="stu-3")],
        "profiles": [{"id": "stu-1", "full_name": "Ani"}, {"id": "stu-2", "full_name": "Budi"},
                     {"id": "stu-3", "full_name": "Cita"}],
    }, **kwargs)
    return unmonitored_report(client), client


# ── one guard ─────────────────────────────────────────────────────────────────

class TestTheOneGuard:
    def test_an_explicit_false_is_off_and_everything_else_is_on(self):
        from app.services.anti_cheat_service import enabled
        assert enabled({"anti_cheat_enabled": False}) is False
        assert enabled({"anti_cheat_enabled": True}) is True
        # A row that could not be read is not a school asking for silence.
        assert enabled({}) is True
        assert enabled({"anti_cheat_enabled": None}) is True
        assert enabled(None) is True

    def test_the_decision_is_written_in_exactly_one_place(self):
        assert SERVICE.count(DECISION) == 1, (
            "the switch is compared somewhere other than `enabled()`")
        for name, text in (("api.py", API), ("student.py", STUDENT),
                           ("teacher.py", TEACHER), ("resume_code.py", RESUME)):
            assert DECISION not in text, f"{name} decides the switch for itself again"

    def test_the_refusal_is_the_shape_the_log_endpoint_answers(self):
        from app.services.anti_cheat_service import refusal
        assert refusal({"anti_cheat_enabled": True}) is None
        assert refusal({}) is None
        assert refusal({"anti_cheat_enabled": False}) == {
            "logged": False, "reason": "anti_cheat_disabled"}

    def test_the_log_route_relays_the_service_refusal(self):
        assert "anti_cheat_refusal(exam)" in API, (
            "the log endpoint no longer asks the service for the decision")
        assert '{"logged": False, "reason": "anti_cheat_disabled"}' not in API, (
            "the route builds the refusal itself, which is how the two answers drift")

    def test_the_ladder_charges_nothing_for_a_switched_off_paper(self):
        from app.services.anti_cheat_service import calculate_graduated_penalty
        for count in (0, 1, 2, 3, 9):
            out = calculate_graduated_penalty(count, {"anti_cheat_enabled": False,
                                                      "penalty_per_violation": 5,
                                                      "max_violations": 3})
            assert out["penalty"] == 0 and out["warning"] is False
            assert out["auto_submit"] is False
        # …and the same ladder still charges a monitored one.
        assert calculate_graduated_penalty(2, {"anti_cheat_enabled": True,
                                               "penalty_per_violation": 5})["penalty"] == 5

    def test_the_count_is_zero_for_a_switched_off_paper_without_a_lookup(self):
        from app.services.anti_cheat_service import count_penalized_violations
        client = FakeSupabase({"violation_logs": [_row()]}, fail=True)
        assert count_penalized_violations(client, "stu-1", "exam-off",
                                          {"anti_cheat_enabled": False}) == 0
        assert client.queries == [], (
            "a switched-off paper's count reached the database anyway")
        # The audit's own reading omits the settings on purpose: it counts what is
        # *recorded*, not what is charged.
        assert count_penalized_violations(FakeSupabase({"violation_logs": [_row()]}),
                                          "stu-1", "exam-off") == 1

    def test_the_resume_lock_follows_the_same_switch(self):
        from app.services import resume_code
        assert resume_code.locking_enabled(
            {"lock_pending_resume": True, "anti_cheat_enabled": False}) is False
        assert resume_code.locking_enabled(
            {"lock_pending_resume": True, "anti_cheat_enabled": True}) is True
        assert "from app.services.anti_cheat_service import enabled" in RESUME, (
            "the lock spells the switch out for itself instead of asking the service")

    def test_both_penalty_writers_select_the_column(self):
        """The column-absent trap: a select that omits it reads as *monitored*."""
        assert "question_scoring,anti_cheat_enabled" in STUDENT, (
            "the submit route writes a penalty without reading the switch")
        assert "penalty_per_violation,anti_cheat_enabled" in API, (
            "force_submit writes a penalty without reading the switch")


# ── the two pages ─────────────────────────────────────────────────────────────

def _render_list(app, exam):
    from flask import g
    with app.test_request_context("/teacher/exams"):
        g.user_id, g.user_name, g.user_role = "u-1", "Uji", "guru"
        g.user_email, g.tz_offset, g.show = "u@example.test", 7, {}
        g.user_school_id, g.user_class_id = "sch-1", "cls-1"
        return app.jinja_env.get_template("teacher/exams.html").render(
            exams=[exam], unassigned_ids=set(), ambiguous_ids=set())


def _render_form(app, enabled):
    from flask import g
    exam = {"id": "e1", "title": "UH Matematika", "subject": "MTK", "class_ids": ["c1"],
            "pdf_url": None, "status": "draft", "duration_minutes": 60,
            "total_questions": 5, "start_at": None, "end_at": None,
            "question_types": {}, "question_weights": {}, "answer_key": {},
            "question_pages": {}, "question_audio": {}, "question_cognitive": {},
            "anti_cheat_enabled": enabled}
    with app.test_request_context("/teacher/exams/e1"):
        g.user_id, g.user_name, g.user_role = "u-1", "Uji", "guru"
        g.user_email, g.tz_offset, g.show = "u@example.test", 7, {}
        g.user_school_id, g.user_class_id = "sch-1", "cls-1"
        return app.jinja_env.get_template("teacher/exam_form.html").render(
            exam=exam, subjects=[], classes=[],
            builder_defaults={"subject_id": None, "class_ids": [],
                              "duration_minutes": 60})


class TestThePagesSayWhichSittingsAreUnmonitored:
    def test_the_exams_list_marks_a_switched_off_paper(self, app):
        page = _render_list(app, dict(OFF_PAPER, subject="FIS", status="active",
                                      duration_minutes=60, total_questions=5))
        assert "Anti-cheat off" in page and "Anti-cheat mati" in page, (
            "an unmonitored sitting looks exactly like a monitored one")

    def test_a_monitored_paper_carries_no_such_mark(self, app):
        page = _render_list(app, dict(ON_PAPER, subject="KIM", status="active",
                                      duration_minutes=60, total_questions=5))
        assert "Anti-cheat off" not in page and "Anti-cheat mati" not in page

    def test_the_paper_page_states_it_and_explains_what_it_means(self, app):
        page = _render_form(app, False)
        assert "enabled: false," in page, (
            "the anti-cheat card is hard-coded on, so it can never show an off paper")
        assert "no violations are recorded, nothing is charged" in page
        assert "tidak ada pelanggaran yang dicatat" in page

    def test_a_monitored_paper_seeds_the_card_on(self, app):
        assert "enabled: true," in _render_form(app, True)

    def test_the_builder_posts_no_switch_at_all(self):
        assert 'name="anti_cheat_enabled"' not in FORM.read_text(encoding="utf-8"), (
            "the form still decides the school's switch by posting a value for it")

    def test_the_edit_door_preserves_the_stored_value_through_the_service(self):
        assert "from app.services.anti_cheat_service import enabled as anti_cheat_on" in TEACHER, (
            "the edit door spells the switch out for itself instead of asking the service")
        assert "anti_cheat_enabled = anti_cheat_on(exam_row)" in TEACHER, (
            "the edit door no longer keeps the switch the row already holds")
        assert "anti_cheat_enabled = True   # the school switches a paper off" in TEACHER, (
            "the create door must still default a new paper to monitored")


# ── the audit ─────────────────────────────────────────────────────────────────

class TestTheAudit:
    def test_only_switched_off_papers_are_reported(self):
        report, client = _report()
        assert [paper["id"] for paper in report["papers"]] == ["exam-off"], (
            "the report includes a paper whose anti-cheat is on, or misses the one off")
        assert all(row["exam_id"] == "exam-off" for row in report["rows"])
        # The *filter* is what makes that true, not the filtering of the result: a
        # stub that ignores its filters would make this pass for the wrong reason.
        exams_query = client.queries[0][1]
        assert exams_query.filters.get("anti_cheat_enabled") is False

    def test_the_three_views_agree(self):
        report, _ = _report()
        assert report["total"] == len(report["rows"]) == 3
        assert report["papers"][0]["recorded"] == 3
        assert {line["user_id"] for line in report["pupils"]} == {"stu-1", "stu-2"}
        assert sum(line["recorded"] for line in report["pupils"]) == report["total"]
        assert {line["name"] for line in report["pupils"]} == {"Ani", "Budi"}

    def test_a_pupils_line_carries_what_a_teacher_acts_on(self):
        report, _ = _report()
        ani = next(line for line in report["pupils"] if line["user_id"] == "stu-1")
        assert ani["recorded"] == 2 and ani["charged"] == 2
        assert set(ani["kinds"]) == {"tab_switch", "focus_lost"}
        assert ani["first_at"] and ani["last_at"]
        assert ani["exam_title"] == "UH Fisika"

    def test_a_recorded_but_uncharged_kind_is_counted_separately(self):
        report, _ = _report(tables={
            "exams": [OFF_PAPER],
            "violation_logs": [_row(kind="orientation_shift")],
            "profiles": [{"id": "stu-1", "full_name": "Ani"}],
        })
        line = report["pupils"][0]
        assert line["recorded"] == 1 and line["charged"] == 0
        assert line["kinds"] == ["orientation_shift"]

    def test_nothing_is_written(self):
        report, client = _report()
        assert client.writes == [], f"the audit wrote: {client.writes}"
        assert report["total"] == 3

    def test_a_read_that_fails_is_an_empty_report_and_not_an_exception(self):
        report, _ = _report(fail=True)
        assert report == {"papers": [], "pupils": [], "rows": [], "total": 0}

    def test_a_switched_off_paper_with_no_rows_is_still_named(self):
        report, _ = _report(tables={"exams": [OFF_PAPER], "violation_logs": [],
                                    "profiles": []})
        assert report["papers"] == [{"id": "exam-off", "title": "UH Fisika",
                                     "recorded": 0}]
        assert report["pupils"] == [] and report["total"] == 0

    def test_the_command_only_reads(self):
        # The verbs are matched *with their payload* on purpose: `sys.path.insert(0, …)`
        # is a legitimate line in this file, and a guard that cannot tell the two apart
        # fails on the script being correct.
        for write in (".insert({", ".update({", ".delete()", ".upsert("):
            assert write not in AUDIT_CLI, f"the audit command calls {write}"
        assert ".table(" not in AUDIT_CLI, (
            "the command queries the database itself instead of through the service")
        assert "unmonitored_report(" in AUDIT_CLI, (
            "the command does not call the service that owns the read")
        assert "SUPABASE_SERVICE_KEY" in AUDIT_CLI, (
            "the command does not say where its credentials come from")
