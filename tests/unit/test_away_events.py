"""A paper that left the exam screen is a fact, and the times belong to the log.

The away-blur — a restored window, another tab, another window beside the paper —
was a *visual*. The student saw their paper blurred, the server was told the kind
that raised it, and that was the end of it. A teacher reading the results list then
saw a penalty number with no answer to the only question it raises: **when?**

Three readings now, and each is checked for the way this kind of change fails
quietly:

* **the moment is the server's.** `created_at`, rendered through the page's own
  `tz` filter. A timestamp recomputed in the browser is the one field a student
  could move, and the point of the record is that it is the school's.
* **the ladder charges the away act like a tab switch** — the kind is in
  `PENALIZED_VIOLATION_TYPES`, and one absence is still one charge.
* **the list and the paper agree.** Both read the rows through
  `anti_cheat_service`, so a teacher who clicks through cannot find a different
  story on the other side.
"""
import contextlib
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import anti_cheat_service as acs

ROOT = Path(__file__).resolve().parents[2]
TEM = ROOT / "app" / "templates"
TABLE = (TEM / "teacher" / "_results_table.html").read_text(encoding="utf-8")
RESULTS = (TEM / "teacher" / "results.html").read_text(encoding="utf-8")
GRADE = (TEM / "teacher" / "grade_detail.html").read_text(encoding="utf-8")
TEACHER = (ROOT / "app" / "routes" / "teacher.py").read_text(encoding="utf-8")

#: 02:12:30 UTC is 09:12:30 in the school's own offset (WIB, +7).
EARLY = "2026-09-21T02:12:30+00:00"
LATER = "2026-09-21T02:31:00+00:00"


class FakeQuery:
    """Chainable postgrest stand-in returning a fixed row set."""

    def __init__(self, rows, fail=False):
        self._rows = rows
        self._fail = fail
        self.filters = {}

    def select(self, *a, **k):
        return self

    def eq(self, column, value):
        self.filters[column] = value
        return self

    def order(self, *a, **k):
        return self

    def execute(self):
        if self._fail:
            raise RuntimeError("connection reset")
        # The filters are applied rather than ignored: a stub that returns every
        # row whatever it was asked for cannot show that a lookup is scoped, and
        # "the student's own events" is exactly that claim.
        rows = [row for row in self._rows
                if all(row.get(key) == value for key, value in self.filters.items())]
        return SimpleNamespace(data=rows)


class FakeSupabase:
    """Table-aware, and it records the filters each lookup was given."""

    def __init__(self, tables, fail=False):
        self._tables = tables
        self._fail = fail
        self.queries = []

    def table(self, name):
        query = FakeQuery(self._tables.get(name, []), fail=self._fail)
        self.queries.append((name, query))
        return query


def event(uid="stu-1", exam="exam-1", kind="focus_lost", at=EARLY, metadata=None):
    row = {"user_id": uid, "exam_id": exam, "violation_type": kind, "created_at": at}
    if metadata is not None:
        row["metadata"] = metadata
    return row


# ── the log is one read, and it never throws at a page ───────────────────────

class TestTheLogIsRead:
    def test_a_metadata_string_is_parsed_and_a_missing_one_is_fine(self):
        """The column is JSONB, but the app has read it back as text before."""
        rows = [event(metadata='{"violation_count": 3, "trigger": "window_blur"}'),
                event(metadata=None)]
        fakes = FakeSupabase({"violation_logs": rows})

        events = acs.events_for_exam(fakes, "exam-1")

        assert events[0]["trigger"] == "window_blur"
        assert events[1]["trigger"] is None

    def test_an_unknown_kind_is_shown_but_not_charged(self):
        """A kind a later release adds must appear, and must not cost points: the
        ladder only knows the kinds it lists, and a report that silently drops a
        recorded event is how a log stops being evidence."""
        fakes = FakeSupabase({"violation_logs": [event(kind="right_click")]})

        event_row = acs.events_for_exam(fakes, "exam-1")[0]

        assert event_row["kind"] == "right_click"
        assert event_row["charged"] is False
        assert event_row["label"] == ("right_click", "right_click")

    def test_the_away_kind_is_charged_like_a_tab_switch(self):
        fakes = FakeSupabase({"violation_logs": [event(kind="focus_lost")]})

        assert acs.events_for_exam(fakes, "exam-1")[0]["charged"] is True
        assert "focus_lost" in acs.PENALIZED_VIOLATION_TYPES

    def test_a_log_that_cannot_be_read_is_an_empty_list(self, app):
        """A report that 500s because its evidence is missing is worse than a
        report that says it has no evidence."""
        with app.app_context():
            assert acs.events_for_exam(FakeSupabase({}, fail=True), "exam-1") == []
            assert acs.events_for_student(FakeSupabase({}, fail=True), "exam-1", "s") == []

    def test_the_students_own_events_are_asked_of_the_database(self):
        """One student's page must not filter the whole sitting in Python."""
        fakes = FakeSupabase({"violation_logs": [event(), event(uid="stu-2")]})

        events = acs.events_for_student(fakes, "exam-1", "stu-1")

        _, query = fakes.queries[-1]
        assert query.filters == {"exam_id": "exam-1", "user_id": "stu-1"}
        assert [e["user_id"] for e in events] == ["stu-1"]


# ── what the list says about one student ─────────────────────────────────────

class TestTheSummary:
    def test_only_charged_events_are_counted_and_the_last_moment_is_kept(self):
        summary = acs.leaving_summary([
            acs._as_event(event(kind="right_click", at=EARLY)),
            acs._as_event(event(kind="tab_switch", at=EARLY)),
            acs._as_event(event(kind="focus_lost", at=LATER)),
        ])

        assert summary["away_count"] == 2
        assert summary["away_last_at"] == LATER
        assert summary["away_last_kind"] == "focus_lost"

    def test_a_paper_that_stayed_has_no_moment_at_all(self):
        """`None`, not a blank string — the template branches on it, and an empty
        string is truthy enough to render an empty badge."""
        summary = acs.leaving_summary([])

        assert summary == {"away_count": 0, "away_last_at": None, "away_last_kind": None}


class TestTheRowsCarryIt:
    def _subs(self):
        return [{"student_id": "stu-1", "id": "sub-1"}, {"student_id": "stu-2", "id": "sub-2"}]

    def test_the_count_and_the_moment_land_on_the_student_they_belong_to(self):
        supabase = FakeSupabase({"violation_logs": [
            event(uid="stu-2", kind="focus_lost", at=LATER),
            event(uid="stu-1", kind="tab_switch", at=EARLY),
            event(uid="stu-1", kind="focus_lost", at=LATER),
        ]})
        stats = {}

        from app.routes.teacher import _attach_leaving
        subs = self._subs()
        _attach_leaving(supabase, "exam-1", subs, stats)

        assert subs[0]["away_count"] == 2 and subs[0]["away_last_at"] == LATER
        assert subs[1]["away_count"] == 1
        assert stats["away"] == 2

    def test_the_header_counts_students_not_events(self):
        """The chip sits beside "N peserta" and reads as a headcount."""
        supabase = FakeSupabase({"violation_logs": [
            event(uid="stu-1", at=EARLY), event(uid="stu-1", at=LATER),
        ]})
        stats = {}

        from app.routes.teacher import _attach_leaving
        subs = self._subs()
        _attach_leaving(supabase, "exam-1", subs, stats)

        assert stats["away"] == 1

    def test_the_results_loader_actually_attaches_it(self):
        """A helper nobody calls is a helper that does nothing.

        The assertion is scoped to `_exam_results`'s own body on purpose: the
        helper's *definition* contains the same call text, so a whole-file search
        is satisfied by the signature and passes even when nothing calls it —
        which is exactly what the mutation harness found here.
        """
        body = TEACHER.split("def _exam_results(", 1)[1].split("\ndef ", 1)[0]
        assert "_attach_leaving(supabase, exam_id, subs, stats)" in body, (
            "the results page builds its rows without the leaving summary")


# ── the surfaces ─────────────────────────────────────────────────────────────

@contextlib.contextmanager
def _signed_in(app, path):
    from flask import g
    with app.test_request_context(path):
        g.user_id = "tea-1"
        g.user_name = "Guru Uji"
        g.user_email = "guru@example.test"
        g.user_role = "guru"
        g.tz_offset = 7
        g.show = {}
        yield


EXAM = {"id": "exam-1", "title": "Fisika", "subject": "Fisika", "total_questions": 2,
        "question_types": {"0": "mcq"}, "question_weights": {"0": 100},
        "answer_key": {"0": "A"}, "question_pages": {}, "pdf_page_urls": []}
STUDENT = {"id": "stu-1", "full_name": "Ani Uji", "phone": "1"}


def _row(name="Ani", count=0, last=None):
    return {
        "id": "sub-1", "student_id": "stu-1", "student_name": name,
        "status": "graded", "score": 80, "final_score": 76, "penalty": 5 if count else 0,
        "submitted_at": EARLY, "answers": {}, "submitted_late": False,
        "away_count": count, "away_last_at": last,
    }


def _render_table(app, rows):
    with _signed_in(app, "/teacher/results"):
        return app.jinja_env.get_template("teacher/_results_table.html").render(
            sub_list=rows, is_scan_section=False)


def _render_results(app, stats, rows):
    with _signed_in(app, "/teacher/results?exam_id=exam-1"):
        return app.jinja_env.get_template("teacher/results.html").render(
            submissions=rows, stats=stats, exam_id="exam-1", exams=[EXAM],
            exam=EXAM, scan_subs=[], online_subs=rows)


def _render_marking(app, events):
    with _signed_in(app, "/teacher/grade/sub-1"):
        return app.jinja_env.get_template("teacher/grade_detail.html").render(
            submission=_row(), exam=EXAM, exam_id="exam-1", student=STUDENT,
            violation_events=events)


class TestTheResultsListSaysWhen:
    def test_a_paper_that_left_the_screen_carries_the_time(self, app):
        html = _render_table(app, [_row("Ani", count=2, last=LATER)])

        assert html.count("fa-eye-slash") == 2, (
            "the record belongs in both halves of the table — the mobile card and "
            f"the desktop row — and it appears {html.count('fa-eye-slash')} time(s)")
        # Counted once per half, not merely present: a substring check passes while
        # one half says something else, which is exactly how this guard was found
        # weak (a mutation that printed `3x` in the desktop row still satisfied
        # `2&times; in html`, because the mobile card was unchanged).
        assert html.count("2&times;") == 2, (
            f"the count should appear in both halves, not {html.count('2&times;')} time(s)")
        assert html.count("09:31") == 2, (
            "the moment is the whole ask, and it belongs in both halves")
        assert "02:31" not in html, "the moment was printed in UTC, not the school's clock"

    def test_a_paper_that_stayed_shows_no_claim_about_it(self, app):
        html = _render_table(app, [_row("Ani")])

        assert "fa-eye-slash" not in html, (
            "a badge that is always there is a badge nobody reads")

    def test_the_header_counts_the_students_who_left(self, app):
        rows = [_row("Ani", count=1, last=EARLY), _row("Budi")]
        html = _render_results(app, {"count": 2, "away": 1, "late": 0}, rows)

        assert "meninggalkan ujian" in html and "left the exam" in html, \
            "the header chip must follow the reader's language like the late one"

    def test_the_header_says_nothing_when_nobody_left(self, app):
        html = _render_results(app, {"count": 2, "away": 0, "late": 0}, [_row("Ani")])

        assert "meninggalkan ujian" not in html


class TestTheMarkingPageShowsTheLog:
    def test_every_event_renders_with_its_moment_and_its_name(self, app):
        events = [acs._as_event(event(kind="tab_switch", at=EARLY)),
                  acs._as_event(event(kind="focus_lost", at=LATER))]
        html = _render_marking(app, events)

        assert "09:12:30" in html and "09:31:00" in html, \
            "both moments belong on the page, in the school's clock"
        assert "Berpindah tab atau aplikasi" in html
        assert "Jendela ujian ditinggalkan" in html
        assert "dihitung ke penalti" in html
        assert "(2 dihitung dari 2 tercatat)" in html

    def test_a_recorded_but_uncharged_event_is_not_dressed_up_as_a_penalty(self, app):
        html = _render_marking(app, [acs._as_event(event(kind="right_click"))])

        assert "hanya dicatat, tidak mengurangi nilai" in html
        assert "(0 dihitung dari 1 tercatat)" in html

    def test_a_paper_that_never_left_says_so_rather_than_nothing(self, app):
        """The distinction a teacher needs is *no record*, which is a fact the
        page has to state, and it is not the same as a page that forgot to look."""
        html = _render_marking(app, [])

        assert "Tidak ada catatan meninggalkan ujian untuk kertas ini." in html
        assert "fa-eye-slash" not in html

    def test_the_route_asks_for_this_paper_only(self):
        assert 'events_for_student(supabase, sub["exam_id"], sub["student_id"])' in TEACHER, \
            "the marking page must read the log for the student it is showing"
        assert "violation_events=violation_events" in TEACHER, \
            "and hand it to the template"

    def test_the_page_copy_stays_indonesian(self):
        """This page pins its language, and `deploy/i18n_coverage.py` refuses a
        `t()` pair on a pinned page: the English half would never render, which is
        a string that switches while `lang` does not."""
        assert "{% set content_lang = 'id' %}" in GRADE
        card = GRADE.split("data-leaving-log", 1)[1].split("</div>", 1)[0]
        assert not re.search(r"(?:sgT|t)\(", card), \
            "the leaving card carries a bilingual pair on a pinned page"
