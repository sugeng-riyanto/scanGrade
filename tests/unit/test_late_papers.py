"""A late paper is a recorded fact, and a fact nobody is shown is not a record.

`submissions.submitted_late` is written by the submit route when the answers arrive
after the sitting's deadline, past the grace (`app/utils/exam_window.py`). It was
recorded and displayed nowhere, so a teacher had to open the database to tell a
timed paper from one that came in twenty minutes late — the one thing a marking
screen exists to save them from.

Three surfaces carry the mark now, in both themes and both languages. Each is
checked for the two ways this kind of change fails quietly:

* **the condition reads the stored column.** Recomputing `is_late` from the exam's
  current window would make the record follow a later edit to the exam — a teacher
  who lengthens the duration after the fact would erase the evidence. The mark must
  read `submitted_late`, and no template may reach for the arithmetic.
* **the mark renders, and only for a late paper.** The pages are rendered, not
  grepped: the mobile card and the desktop row are separate markup, so a badge
  added to one of them is a badge half the teachers never see.

The label is a `t()` pair rather than a literal, because these pages render inside
the toggle: a hardcoded "Terlambat" would be the only string on the row that does
not follow the reader, and `deploy/i18n_coverage.py` would count it as debt.
"""
import contextlib
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "app" / "templates"
TABLE = (TEMPLATES / "teacher" / "_results_table.html").read_text(encoding="utf-8")
RESULTS = (TEMPLATES / "teacher" / "results.html").read_text(encoding="utf-8")
GRADE = (TEMPLATES / "teacher" / "grade_detail.html").read_text(encoding="utf-8")
PRINT = (TEMPLATES / "teacher" / "print_exam_report.html").read_text(encoding="utf-8")
TEACHER = (ROOT / "app" / "routes" / "teacher.py").read_text(encoding="utf-8")

#: The pair, spelled the one way this app writes a pair.
LABEL = "t('Terlambat','Late')"

#: Each surface, and the token that proves it reads the *stored* flag. The two
#: screens that mark one paper read the row's column; the results header reads the
#: count `_exam_results` took from the same column.
MARK = {
    "teacher/_results_table.html": (TABLE, "s.submitted_late"),
    "teacher/results.html": (RESULTS, "stats.late"),
    "teacher/grade_detail.html": (GRADE, "submission.submitted_late"),
}

SURFACES = {name: source for name, (source, _) in MARK.items()}

_COMMENT = re.compile(r"\{#.*?#\}|<!--.*?-->", re.S)

#: A translation call, and not `feedback.get('scores')`. A bare `"t('" in src`
#: search is what this had first, and it failed on Jinja's own dict access — the
#: defect the i18n gate's own history warns about in as many words.
_PAIR_CALL = re.compile(r"(?<![\w.])(?:sgT|t)\('")


def code(name: str) -> str:
    """A surface with its comments removed.

    A comment that *names* the window arithmetic is documentation, not a call — and
    a guard that fails on its own explanation is the kind that gets deleted, which
    is how a real check stops running.
    """
    return _COMMENT.sub("", SURFACES[name])


@pytest.fixture(scope="module")
def app():
    from app import create_app
    return create_app("app.config.TestingConfig")


def _row(name, late):
    """One submission row, with the columns the screens actually read."""
    return {
        "id": f"sub-{name}",
        "student_id": f"stu-{name}",
        "student_name": name,
        "status": "graded",
        "score": 80,
        "final_score": 76,
        "penalty": 4,
        "submitted_at": "2026-09-19T02:00:00+00:00",
        "started_at": "2026-09-19T01:00:00+00:00",
        "submitted_late": late,
        "answers": {"_nisn": "1234"} if late else {},
        "teacher_feedback": {"scores": {}, "comments": {}, "overlay_pages": {}},
    }


EXAM = {
    "id": "exam-1",
    "title": "Fisika",
    "subject": "Fisika",
    "total_questions": 2,
    "question_types": {"0": "mcq", "1": "essay"},
    "question_weights": {"0": 50, "1": 50},
    "answer_key": {"0": "A"},
    "question_pages": {},
    "pdf_page_urls": [],
}
STUDENT = {"id": "stu-Ani", "full_name": "Ani Uji", "phone": "1"}


@contextlib.contextmanager
def _signed_in(app, path):
    """A request context carrying everything base.html reads for a teacher.

    A context manager, and not just a `push()`: an unpopped context stays on the
    stack for the rest of the pytest session, where "there is no app context" is
    the premise of other suites — `test_retention_scheduler`
    (`test_purge_all_without_app_context_raises`) went red with this file's first
    version in the run.
    """
    from flask import g
    with app.test_request_context(path):
        g.user_id = "tea-1"
        g.user_name = "Guru Uji"
        g.user_email = "guru@example.test"
        g.user_role = "guru"
        g.tz_offset = 7
        g.show = {}
        yield


def _render_table(app, rows, is_scan_section=False):
    with _signed_in(app, "/teacher/results"):
        return app.jinja_env.get_template("teacher/_results_table.html").render(
            sub_list=rows, is_scan_section=is_scan_section)


def _render_results(app, stats, rows):
    with _signed_in(app, "/teacher/results?exam_id=exam-1"):
        return app.jinja_env.get_template("teacher/results.html").render(
            submissions=rows, stats=stats, exam_id="exam-1", exams=[EXAM],
            exam=EXAM, scan_subs=[], online_subs=rows)


def _render_marking(app, late):
    with _signed_in(app, "/teacher/grade/sub-Ani"):
        return app.jinja_env.get_template("teacher/grade_detail.html").render(
            submission=_row("Ani", late), exam=EXAM, exam_id="exam-1",
            student=STUDENT)


def _stats(late):
    return {"avg": 78.0, "max": 80, "min": 76, "count": 2, "passed": 2,
            "pass_rate": 100, "threshold": 70, "late": late}


# ── the condition is the stored flag, not a recomputation ────────────────────

class TestTheMarkReadsTheStoredFlag:
    @pytest.mark.parametrize("name", sorted(MARK))
    def test_the_condition_reads_the_stored_flag(self, name):
        _, token = MARK[name]
        assert token in SURFACES[name], (
            f"{name} does not read {token!r}, so late papers have no mark here")

    @pytest.mark.parametrize("name", sorted(MARK))
    def test_no_template_recomputes_lateness(self, name):
        """A recomputation would follow a later edit to the exam's window.

        The deadline is arithmetic over `end_at`/`duration_minutes`/`started_at`,
        all of which a teacher can still change. The record of when a paper
        arrived must not move with them, so the screens read the column the submit
        route wrote.
        """
        body = code(name)
        assert "is_late(" not in body, (
            f"{name} recomputes lateness instead of showing the recorded flag")
        assert "exam_window" not in body, (
            f"{name} reaches for the window arithmetic to decide a display")

    def test_the_route_counts_the_same_column_it_shows(self):
        """The header chip and the per-row badges must not be able to disagree."""
        assert '"late": sum(1 for row in subs if row.get("submitted_late"))' in TEACHER, (
            "stats['late'] is not counted from submitted_late, so the header count "
            "and the row badges come from two different readings")


# ── the flag is visible, and only when it is set ─────────────────────────────

class TestTheResultsListShowsIt:
    def test_a_late_paper_is_marked_and_an_on_time_one_is_not(self, app):
        html = _render_table(app, [_row("Ani", True), _row("Budi", False)])

        assert LABEL in html, "the late mark never rendered"
        assert html.count("Terlambat") == 2, (
            "the late paper should be marked in both halves of the table — the "
            f"mobile card and the desktop row — and it appears {html.count('Terlambat')} time(s)")

    def test_nothing_is_marked_when_nothing_is_late(self, app):
        html = _render_table(app, [_row("Ani", False), _row("Budi", False)])

        assert LABEL not in html, ("an on-time paper was marked late — a badge that "
                                   "is always there is a badge nobody reads")

    def test_the_mark_sits_with_the_student_it_belongs_to(self, app):
        """The row is where the reader is, not a footnote at the bottom."""
        html = _render_table(app, [_row("Ani", False), _row("Budi", True)])

        for marked in re.finditer("Terlambat", html):
            before = html[:marked.start()]
            assert "Budi" in before.rsplit("Ani", 1)[-1], (
                "a mark rendered under a student it does not flag")

    def test_every_mark_binds_the_label_as_a_pair(self, app):
        """Two halves, two pairs.

        Counting only that *a* pair appears is satisfied by one half — which is
        how a label added to one of the two tables and forgotten in the other
        passes a check that looked thorough.
        """
        html = _render_table(app, [_row("Ani", True), _row("Budi", False)])

        assert html.count(LABEL) == html.count("Terlambat") == 2, (
            "a mark is rendered as a literal, so it stays Indonesian in English mode")

    def test_the_scan_half_marks_its_rows_too(self, app):
        """Scan and online submissions render through the same partial — a guard on
        one half only would let the other lose the mark."""
        html = _render_table(app, [_row("Ani", True)], is_scan_section=True)

        assert LABEL in html


class TestTheHeaderCountsThem:
    def test_the_count_is_shown_when_there_are_late_papers(self, app):
        html = _render_results(app, _stats(1), [_row("Ani", True), _row("Budi", False)])

        assert "terlambat" in html, (
            "the results header does not surface how many papers were late")
        assert "'1 ' + t('terlambat','late')" in html, (
            "the header count is not the count of late papers")

    def test_nothing_is_claimed_when_none_are_late(self, app):
        html = _render_results(app, _stats(0), [_row("Ani", False), _row("Budi", False)])

        assert "terlambat" not in html, (
            "an exam with no late papers claims a late count")

    def test_the_count_is_bilingual_and_follows_the_toggle(self):
        assert "t('terlambat','late')" in RESULTS, (
            "the header count is a literal, so it stays Indonesian in English")

    def test_the_count_says_which_language_it_follows(self, app):
        """`test_content_lang.py`, stated where the next author will look.

        The results page declares `content_lang = 'id'`, so the document is
        Indonesian whatever the reader chose — and copy inside it that *does*
        follow the toggle has to carry `:lang=\"lang\"` on its own element, or a
        screen reader announces English words as Indonesian. The first version of
        this chip did not, and the full suite caught it.
        """
        html = _render_results(app, _stats(1), [_row("Ani", True)])
        # The chip, found by its own tooltip: the nearest `<span` that opens before
        # it is the element whose copy follows the toggle.
        titled_at = html.index(':title="t(\'Kertas')
        opening = html.index(">", html.rindex("<span", 0, titled_at))
        tag = html[html.rindex("<span", 0, titled_at):opening]
        assert ':lang="lang"' in tag, (
            "the count follows the toggle without saying so: " + tag[:160])



class TestTheMarkingPageShowsIt:
    def test_the_paper_being_marked_carries_the_mark(self, app):
        html = _render_marking(app, late=True)

        assert "Terlambat" in html, "the marking page does not flag the paper it is showing"
        assert html.count("Terlambat") == 1

    def test_an_on_time_paper_carries_none(self, app):
        html = _render_marking(app, late=False)

        assert "Terlambat" not in html

    def test_the_mark_sits_beside_the_student_it_belongs_to(self, app):
        html = _render_marking(app, late=True)

        marked = html.index("Terlambat")
        assert "Ani Uji" in html[:marked], (
            "the mark rendered away from the student's own name")
        assert marked - html.index("Ani Uji") < 700, (
            "the mark is on the wrong part of the page for a teacher to connect "
            "it to the paper")

    def test_the_same_word_as_the_list(self):
        """Two screens naming one fact differently is how a teacher has to
        translate between pages. The list binds it as a pair because that partial
        is all-pairs by design; this page writes it as copy, because it declares
        `content_lang = 'id'` and a pair here would be the one string that
        switched."""
        assert "Terlambat" in TABLE and "Terlambat" in GRADE
        assert LABEL in TABLE

    def test_the_marking_page_stays_a_pinned_page(self):
        """The rule the gate enforces, stated where the next author will look.

        `deploy/i18n_coverage.py` fails a page that newly pins its language while
        carrying pairs — the pin freezes copy the toggle would otherwise switch, so
        English words would render under an Indonesian `lang`. A `t()` added here
        for the badge alone would do exactly that.
        """
        assert "content_lang = 'id'" in GRADE
        assert not _PAIR_CALL.search(code("teacher/grade_detail.html")), (
            "grade_detail.html declares its copy Indonesian and now carries a t() "
            "pair — translate the whole page or leave it pinned")


class TestTheFiledSheetShowsIt:
    def test_a_late_paper_is_marked_on_the_printed_roster(self, app):
        html = app.jinja_env.get_template("teacher/print_exam_report.html").render(
            exam=EXAM,
            roster=[_row("Ani", True), _row("Budi", False)],
            stats=_stats(1),
            class_names=[],
            school={"name": "SMP Uji", "address": "Jl. Uji", "city": "Bandung"},
            teacher_name="Guru Uji",
            printed_on="19 Sep 2026",
            pass_mark=70,
        )

        assert "terlambat" in html, "the printed roster has no late mark"
        assert html.count("terlambat") == 1, (
            "the printed roster marks more papers late than there are late papers")
        # And it is styled rather than left to inherit the table's body colour: an
        # unstyled span on a filed sheet is a word nobody notices.
        assert ".late" in PRINT and "color: #b45309" in PRINT
