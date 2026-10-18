"""A switched-off paper keeps the violations it already recorded — and the builder says so.

The school's switch (`exams.anti_cheat_enabled`) governs what happens **from now on**:
`anti_cheat_service.enabled` is the one reader of it, and a switched-off paper records
nothing further, charges nothing further and never locks. What it has *ever* been able
to do is take back a row that is already in `violation_logs`, and the builder said
nothing about that: the card's only sentence was about the future ("no violations are
recorded, nothing is charged, and the sitting is never locked"), so a teacher reading a
paper the school had switched off had no way to know that the marks in front of them —
the events on the marking page, the printed sheet filed with the school — came from a
record that is still there.

That is the defect this file pins, and the reason the warning is about **rows**, not
about penalties. The two numbers are not the same question, and the difference is the
whole point:

  * `count_penalized_violations` answers what the ladder *charges*, and for a
    switched-off paper it answers 0 **without looking** — so a warning built on it would
    say "nothing to keep" about a record full of rows;
  * `recorded_violations` counts what the log *holds*, whatever kind, because every row
    on it is on the marking page (a `orientation_shift` is shown and not charged, and it
    is still on the record).

What the guards below hold, one claim each: the reader counts rows and fails to 0
rather than raising; the builder asks for that number **only** for a switched-off paper
(the round-trip is not paid on every page open); the card shows the warning only when
there is something to keep, and never on a monitored paper; the copy names where the
rows stay and what actually removes them; and — the premise the whole warning rests on —
the rows really do stay, because the only door in the app that deletes them is the one
that deletes the paper.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "app"
SERVICE_PATH = APP / "services" / "anti_cheat_service.py"
ROUTES_PATH = APP / "routes" / "teacher.py"
ADMIN_ROUTES_PATH = APP / "routes" / "admin.py"
FORM_PATH = APP / "templates" / "teacher" / "exam_form.html"

SERVICE = SERVICE_PATH.read_text(encoding="utf-8")
ROUTES = ROUTES_PATH.read_text(encoding="utf-8")
FORM = FORM_PATH.read_text(encoding="utf-8")

OFF_PAPER = {"id": "exam-off", "title": "UH Fisika", "teacher_id": "t-1",
             "school_id": "sch-1", "anti_cheat_enabled": False}
ON_PAPER = {"id": "exam-on", "title": "UH Kimia", "teacher_id": "t-1",
            "school_id": "sch-1", "anti_cheat_enabled": True}


# ── the fake client ──────────────────────────────────────────────────────────

class FakeQuery:
    """Chainable postgrest stand-in, recording every read it is asked for.

    `count="exact"` is honoured rather than ignored: a stub that answered `data` only
    would make `recorded_violations` return 0 for the wrong reason, and the whole file
    would pass against a reader that never counted anything.
    """

    def __init__(self, rows, asked, name="", fail=False):
        self._rows, self._asked, self._name, self._fail = rows, asked, name, fail
        self.filters: dict = {}
        self.selected = ""
        self.want_count = False
        self.ordered = False

    def select(self, columns, *a, **k):
        self.selected = columns
        self.want_count = bool(k.get("count"))
        return self

    def eq(self, column, value):
        self.filters[column] = value
        return self

    def order(self, *a, **k):
        self.ordered = True
        return self

    def limit(self, *a, **k):
        return self

    def execute(self):
        self._asked.append({"table": self._name, "selected": self.selected,
                            "filters": dict(self.filters), "count": self.want_count})
        if self._fail:
            raise RuntimeError("connection reset")
        rows = [row for row in self._rows
                if all(row.get(key) == value for key, value in self.filters.items())]
        return SimpleNamespace(data=rows, count=len(rows))


class FakeSupabase:
    def __init__(self, tables=None, fail=False):
        self.tables, self.fail, self.asked = tables or {}, fail, []

    def table(self, name):
        return FakeQuery(self.tables.get(name, []), self.asked, name=name,
                         fail=self.fail and name == "violation_logs")

    def rows_on(self, table):
        return [entry for entry in self.asked if entry["table"] == table]


def _row(uid="stu-1", exam="exam-off", kind="tab_switch"):
    return {"id": f"{uid}-{kind}", "user_id": uid, "exam_id": exam,
            "violation_type": kind, "created_at": "2026-09-21T02:12:30+00:00",
            "metadata": {"trigger": "visibilitychange"}}


# ── the reader ───────────────────────────────────────────────────────────────

class TestTheRecordedCountIsItsOwnQuestion:
    def test_it_counts_rows_and_not_charges(self):
        """A switched-off paper still holds rows, and this is the reader that says how
        many — the number `count_penalized_violations` refuses to produce."""
        from app.services.anti_cheat_service import recorded_violations
        client = FakeSupabase({"violation_logs": [
            _row(), _row(kind="focus_lost"), _row(kind="orientation_shift"),
            _row(exam="exam-on", uid="stu-9"),
        ]})
        assert recorded_violations(client, "exam-off") == 3, (
            "the count is not scoped to the paper, or it dropped a kind the report shows")

    def test_the_two_readers_answer_differently_on_the_same_paper(self):
        """The defect this warning must not have: 'nothing charged' read as
        'nothing recorded'."""
        from app.services.anti_cheat_service import (count_penalized_violations,
                                                     recorded_violations)
        client = FakeSupabase({"violation_logs": [_row(), _row(kind="focus_lost")]})
        assert recorded_violations(client, "exam-off") == 2
        charged = count_penalized_violations(client, "stu-1", "exam-off",
                                            OFF_PAPER)
        assert charged == 0, charged
        looked = [entry for entry in client.asked if entry["table"] == "violation_logs"]
        assert len(looked) == 1, (
            "the charged count reached the database on a switched-off paper; it is "
            "supposed to answer 0 without a lookup, which is exactly why the warning "
            "cannot be built on it")

    def test_it_does_not_filter_by_kind(self):
        """Every row is on the record; only some of them were charged."""
        body = _function_body(SERVICE, "recorded_violations")
        assert "violation_type" not in body, (
            "the reader filters by kind, so a recorded-but-uncharged row (an "
            "orientation shift) would be missing from the number the card shows")
        assert 'count="exact"' in body, (
            "the reader no longer asks the database for a count")

    def test_a_read_that_fails_is_zero_and_not_an_exception(self):
        """A page whose only sin is warning somebody must still render."""
        from app.services.anti_cheat_service import recorded_violations
        assert recorded_violations(FakeSupabase({}, fail=True), "exam-off") == 0


# ── the route decides whether to ask at all ──────────────────────────────────

def _builder_count(client, exam_row, exam_id):
    from app.routes.teacher import _recorded_violations_for_builder
    return _recorded_violations_for_builder(client, exam_row, exam_id)


class TestTheBuilderAsksOnceAndOnlyWhenItMatters:
    def test_a_monitored_paper_pays_no_round_trip(self):
        """The warning cannot be shown on a monitored paper, so the read is not paid
        on every page open — the round-trip budget in AGENTS.md."""
        client = FakeSupabase({"violation_logs": [_row(), _row()]})
        assert _builder_count(client, ON_PAPER, "exam-on") == 0
        assert client.asked == [], (
            f"a monitored paper read the log anyway: {client.asked}")

    def test_a_switched_off_paper_is_asked_once(self):
        client = FakeSupabase({"violation_logs": [_row(), _row(kind="focus_lost"),
                                                  _row(kind="orientation_shift")]})
        assert _builder_count(client, OFF_PAPER, "exam-off") == 3
        reads = client.rows_on("violation_logs")
        assert len(reads) == 1, f"the count was read {len(reads)} time(s)"
        assert reads[0]["filters"] == {"exam_id": "exam-off"}, reads[0]
        assert reads[0]["count"] is True, (
            "the read is not a count, so every row of a whole sitting travels to "
            "render one number")

    def test_the_builder_is_handed_the_number(self):
        """Source-level, because this is wiring rather than a decision: the edit door
        passes it, and the card is what reads it."""
        assert "recorded_violations=recorded_violation_count," in ROUTES, (
            "the edit door computed the count and did not give it to the page")
        assert "recorded_violation_count = _recorded_violations_for_builder(" in ROUTES, (
            "the edit door no longer asks the one function that decides whether to read")
        assert "_recorded_violations_for_builder(supabase, exam_row, exam_id)" in ROUTES
        # ...and the switch stays the service's to read, not this route's.
        assert 'anti_cheat_enabled") is not False' not in ROUTES, (
            "the route decides the school's switch for itself again")


# ── the card ─────────────────────────────────────────────────────────────────

def _render_form(app, enabled, recorded=None):
    """The builder, rendered the way its route hands it over.

    `recorded=None` is the *new paper* shape: the route passes no count at all, and
    the template must answer 0 rather than claim rows it never read.
    """
    from flask import g

    exam = {"id": "e1", "title": "UH Matematika", "subject": "MTK", "class_ids": ["c1"],
            "pdf_url": None, "status": "draft", "duration_minutes": 60,
            "total_questions": 5, "start_at": None, "end_at": None,
            "question_types": {}, "question_weights": {}, "answer_key": {},
            "question_pages": {}, "question_audio": {}, "question_cognitive": {},
            "anti_cheat_enabled": enabled}
    kwargs = {} if recorded is None else {"recorded_violations": recorded}
    with app.test_request_context("/teacher/exams/e1"):
        g.user_id, g.user_name, g.user_role = "u-1", "Uji", "guru"
        g.user_email, g.tz_offset, g.show = "u@example.test", 7, {}
        g.user_school_id, g.user_class_id = "sch-1", "cls-1"
        return app.jinja_env.get_template("teacher/exam_form.html").render(
            exam=exam, subjects=[], classes=[],
            builder_defaults={"subject_id": None, "class_ids": [],
                              "duration_minutes": 60}, **kwargs)


def _copy_block() -> str:
    """The warning's own markup, so the assertions read the block rather than the file.

    Sliced from the binding that decides it to the end of that `<div>`, because a
    sentence elsewhere on a 3000-line page saying the same words would otherwise
    satisfy a search that means "the card shows this".
    """
    marker = 'x-show="recorded_violations > 0"'
    assert marker in FORM, "the card has no element bound to the recorded count"
    start = FORM.index(marker)
    return FORM[start:start + 2000]


class TestTheCardSaysWhatTheSwitchCannotUndo:
    def test_an_off_paper_with_rows_carries_the_count_and_the_sentence(self, app):
        page = _render_form(app, False, recorded=7)
        assert "recorded_violations: 7," in page, (
            "the number the route read does not reach the card")
        assert "event(s) are already on the record for this paper" in page
        assert "peristiwa sudah tercatat di kertas ini" in page
        # The pair's other half, so the page is bilingual like every other page here.
        assert "switching anti-cheat off does not remove them" in page
        assert "mematikan anti-cheat tidak menghapusnya" in page

    def test_it_names_where_the_rows_are_and_what_removes_them(self):
        """The two facts that make the warning actionable rather than alarming."""
        block = _copy_block()
        assert "marking page" in block and "halaman penilaian" in block, block
        assert "printed sheet" in block and "lembar yang dicetak" in block, block
        assert "deleted" in block and "dihapus" in block, block

    def test_it_does_not_promise_the_record_was_cleared(self):
        """The failure this replaced: a teacher reading 'off' as 'and the marks are
        gone'. Nothing in the block may say the rows were removed, withdrawn or
        refunded."""
        block = _copy_block().lower()
        for claim in ("dihapus otomatis", "automatically removed", "dibatalkan",
                      "reversed", "refunded", "tidak ada catatan"):
            assert claim not in block, f"the warning claims {claim!r}"

    def test_a_monitored_paper_can_never_show_it(self, app):
        """Two conditions, and the outer one is the switch: the block sits inside the
        off-state note, so 'monitored' cannot be warned about at all."""
        assert 'x-show="!enabled" x-cloak class="text-xs font-bold text-rose-700' in FORM
        inner = FORM.index('x-show="recorded_violations > 0"')
        outer = FORM.index('x-show="!enabled" x-cloak class="text-xs font-bold text-rose-700')
        assert outer < inner, (
            "the count line is not inside the off-state note, so a monitored paper "
            "could show a warning about a switch that is on")
        page = _render_form(app, True, recorded=0)
        assert "recorded_violations: 0," in page, (
            "a monitored paper is handed a count, and the route reads none")

    def test_a_page_that_read_nothing_claims_nothing(self, app):
        """The new-paper door passes no count at all; the template must answer 0 rather
        than render an undefined number as the words 'None'."""
        page = _render_form(app, True)
        assert "recorded_violations: 0," in page
        assert "recorded_violations: None" not in page


# ── the premise: the rows really do stay ─────────────────────────────────────

class TestTheRecordStaysBecauseOnlyThePaperTakesItWithHim:
    def test_the_report_reads_the_log_without_asking_about_the_switch(self):
        """If this reader were switch-aware the whole warning would be a lie."""
        from app.services.anti_cheat_service import events_for_exam
        client = FakeSupabase({"violation_logs": [_row(), _row(kind="focus_lost")]})
        events = events_for_exam(client, "exam-off")
        assert len(events) == 2, events
        assert [entry["kind"] for entry in events] == ["tab_switch", "focus_lost"]
        # The reader is handed the paper and nothing else — no settings, no switch.
        assert "anti_cheat_enabled" not in _function_body(SERVICE, "events_for_exam")

    def test_only_the_delete_door_removes_them(self):
        """Both app doors that delete the log are the paper's own delete — so 'the
        record goes only when the paper itself is deleted' is a fact, not a comfort."""
        assert ROUTES.count('table("violation_logs").delete(') == 1, (
            "the teacher's routes delete violation rows somewhere other than the "
            "exam-delete door")
        assert ADMIN_ROUTES_PATH.read_text(encoding="utf-8").count(
            'table("violation_logs").delete(') == 1
        for path in (ROUTES_PATH, ADMIN_ROUTES_PATH):
            body = _function_body(path.read_text(encoding="utf-8"), "delete_exam")
            assert 'table("violation_logs").delete(' in body, (
                f"{path.name}: the log is not deleted by the paper's own delete door")

    def test_the_builder_s_save_never_touches_the_log(self):
        """Saving a paper — including saving one that is switched off — must not be a
        way to clear a record by accident."""
        save = _function_body(ROUTES, "exam_detail")
        assert "violation_logs" not in save, (
            "the builder's save mentions the violation log, and the only thing it "
            "could do to it there is delete it")


# ── helpers ──────────────────────────────────────────────────────────────────

def _function_body(source: str, name: str) -> str:
    """One function's body, from its `def` to the next top-level `def`/decorator.

    Read as text rather than with `ast` because the question asked of it is which
    *table* it names, and a syntax tree would drop the string the answer is in.
    """
    marker = f"def {name}("
    assert marker in source, f"{name} is gone"
    start = source.index(marker)
    rest = source[start + len(marker):]
    ends = [rest.find("\ndef "), rest.find("\n@")]
    cut = min([end for end in ends if end != -1], default=len(rest))
    return rest[:cut]
