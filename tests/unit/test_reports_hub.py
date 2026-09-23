"""The index between the menu and the documents.

A report is per exam — one class, one paper — so a sidebar can offer the *idea* of
one and never "the report for this exam": the choice is the reader's. `/teacher/reports`
is where that choice is made cheap, and what makes it real is that the doors it
draws lead where the app actually answers, for exactly the rows the caller may
open. Three ways that fails quietly, and all three are held here:

* **a door that 404s** — an address the routes do not serve, or one built for a
  learner the row cannot name (the learner route answers 404 for a paper with no
  profile);
* **a menu entry for a role with no scope** — the guard admits a role
  `analysis_scope` does not know, and the page renders an empty report with no
  error anywhere;
* **a second computation** — the index recomputing the numbers it prints, so the
  figure beside a door disagrees with the document behind it.

The page is rendered rather than grepped where a grep would pass a blank cell, and
the fixture report is the *service's own* output, so the template cannot read a
field the reader never sends.
"""
from __future__ import annotations

import contextlib
import pathlib
import re

import pytest

from app.services import analysis_scope

ROOT = pathlib.Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "app" / "templates"
PAGE = (TEMPLATES / "teacher" / "reports.html").read_text(encoding="utf-8")
BASE = (TEMPLATES / "base.html").read_text(encoding="utf-8")
TEACHER = (ROOT / "app" / "routes" / "teacher.py").read_text(encoding="utf-8")

ROLES = ("super_admin", "admin_sekolah", "guru", "murid")
HUB = "/teacher/reports"


# ── a fake database, so the fixtures are the service's own output ────────────

class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    """One PostgREST chain, recorded. Only what these two reads actually use."""

    def __init__(self, rows, log, table):
        self._rows = rows
        self._log = log
        self._table = table
        self._eq = []
        self._in = []
        self._order = None
        self._limit = None
        self._columns = None

    def select(self, columns):
        self._columns = columns
        self._log.append((self._table, "select", columns))
        return self

    def eq(self, column, value):
        self._eq.append((column, value))
        return self

    def in_(self, column, values):
        self._in.append((column, list(values)))
        self._log.append((self._table, "in_", column, list(values)))
        return self

    def order(self, column, desc=False):
        self._order = (column, desc)
        self._log.append((self._table, "order", column))
        return self

    def limit(self, count):
        self._limit = count
        return self

    def execute(self):
        rows = list(self._rows)
        for column, value in self._eq:
            rows = [row for row in rows if str(row.get(column)) == str(value)]
        for column, values in self._in:
            wanted = {str(value) for value in values}
            rows = [row for row in rows if str(row.get(column)) in wanted]
        if self._order:
            column, desc = self._order
            rows.sort(key=lambda row: str(row.get(column) or ""), reverse=bool(desc))
        if self._limit is not None:
            rows = rows[:self._limit]
        return _Result(rows)


class _Fake:
    """A stand-in Supabase, with the queries it was asked for in `log`.

    `fail` names a table whose *second* call raises, which is how a partial
    failure is reproduced: the first chunk of papers loads and the next one does
    not.
    """

    def __init__(self, tables, fail=None):
        self.tables = tables
        self.log = []
        self.fail = dict(fail or {})
        self.seen = {}

    def table(self, name):
        self.seen[name] = self.seen.get(name, 0) + 1
        if self.fail.get(name) == self.seen[name]:
            raise RuntimeError(f"{name} is unavailable")
        return _Query(self.tables.get(name, []), self.log, name)


EXAM = {
    "id": "exam-1",
    "title": "Mid Semester 1",
    "subject": "Fisika",
    "teacher_id": "tea-1",
    "school_id": "sch-1",
    "created_at": "2026-06-01T08:00:00+00:00",
    "total_questions": 2,
    "question_types": {"0": "mcq", "1": "mcq"},
    "question_weights": {"0": 50.0, "1": 50.0},
    "answer_key": {"0": "A", "1": "B"},
    "passing_score": 70,
    "status": "published",
}

PAPERS = [
    {"exam_id": "exam-1", "student_id": "stu-1", "answers": {"0": "A", "1": "B"},
     "teacher_feedback": {}, "score": 100.0, "final_score": 100.0,
     "status": "published", "submitted_at": "2026-06-02T09:00:00+00:00"},
    {"exam_id": "exam-1", "student_id": "stu-2", "answers": {"0": "A", "1": "A"},
     "teacher_feedback": {}, "score": 50.0, "final_score": 50.0,
     "status": "published", "submitted_at": "2026-06-02T09:30:00+00:00"},
    # A scan the app could not hang on a profile: it was sat, so it is listed,
    # and it has no page, so it gets no door.
    {"exam_id": "exam-1", "student_id": None, "answers": {"0": "A", "1": "B"},
     "teacher_feedback": {}, "score": 100.0, "final_score": 100.0,
     "status": "published", "submitted_at": "2026-06-02T10:00:00+00:00"},
]


def _scope_database(role="guru", papers=None, exams=None):
    return _Fake({
        "exams": [dict(EXAM)] if exams is None else exams,
        "submissions": list(PAPERS if papers is None else papers),
        "profiles": [{"id": "stu-1", "full_name": "Ani"},
                     {"id": "stu-2", "full_name": "Budi"},
                     {"id": "tea-1", "full_name": "Guru Uji"}],
        "schools": [{"id": "sch-1", "name": "SMP Uji"}],
    })


#: A box wide enough for the two filters to mean something: two schools, three
#: teachers, an exam each. A scope of one school cannot offer a school choice, so
#: the interesting cases all need a fixture wider than it — this is the shape a
#: super admin's page has, and the reason the filters exist at all.
WIDE_EXAMS = (
    {**EXAM, "id": "exam-a", "title": "Fisika 1", "teacher_id": "tea-1",
     "school_id": "sch-1", "created_at": "2026-06-03T08:00:00+00:00"},
    {**EXAM, "id": "exam-b", "title": "Fisika 2", "teacher_id": "tea-2",
     "school_id": "sch-1", "created_at": "2026-06-02T08:00:00+00:00"},
    {**EXAM, "id": "exam-c", "title": "Kimia 1", "teacher_id": "tea-3",
     "school_id": "sch-2", "created_at": "2026-06-01T08:00:00+00:00"},
)

WIDE_PAPERS = (
    {**PAPERS[0], "exam_id": "exam-a", "student_id": "stu-a",
     "submitted_at": "2026-06-03T09:00:00+00:00"},
    {**PAPERS[0], "exam_id": "exam-b", "student_id": "stu-b",
     "submitted_at": "2026-06-02T09:00:00+00:00"},
    {**PAPERS[0], "exam_id": "exam-c", "student_id": "stu-c",
     "submitted_at": "2026-06-01T09:00:00+00:00"},
)


def _wide_database(exams=None, papers=None):
    """Two schools and three teachers: who the filters are for."""
    return _Fake({
        "exams": [dict(exam) for exam in (WIDE_EXAMS if exams is None else exams)],
        "submissions": list(WIDE_PAPERS if papers is None else papers),
        "profiles": [{"id": "stu-a", "full_name": "Ani"},
                     {"id": "stu-b", "full_name": "Budi"},
                     {"id": "stu-c", "full_name": "Citra"},
                     {"id": "tea-1", "full_name": "Guru Uji"},
                     {"id": "tea-2", "full_name": "Guru Dua"},
                     {"id": "tea-3", "full_name": "Guru Tiga"},
                     {"id": "adm-1", "full_name": "Admin Uji"}],
        "schools": [{"id": "sch-1", "name": "SMP Uji"},
                    {"id": "sch-2", "name": "SMA Uji"}],
    })


def _report(supabase=None, role="guru"):
    supabase = supabase or _scope_database(role=role)
    return analysis_scope.report(supabase, role, "tea-1", "sch-1")


@contextlib.contextmanager
def _signed_in(app, role="guru", path="/teacher/reports"):
    from flask import g
    with app.test_request_context(path):
        g.user_id = "tea-1"
        g.user_name = "Guru Uji"
        g.user_email = "guru@example.test"
        g.user_role = role
        g.user_school_id = "sch-1"
        g.tz_offset = 7
        g.show = {}
        yield


def _render(app, role="guru", supabase=None, report=None, learners=None,
            choices=None, school_filter="", teacher_filter=""):
    """The page as the route hands it to Jinja.

    `supabase` is passed straight through so a case can change the *data* — a
    paper still being marked, say — without also having to remember to rebuild
    every argument from it. The choices, the filters and the search keys are built
    the way the route builds them, from the same data, so a rendering test is
    measuring the page and not a second hand-written copy of its arguments.
    """
    supabase = supabase if supabase is not None else _scope_database(role=role)
    report = report if report is not None else _report(supabase, role=role)
    learners = (learners if learners is not None
                else analysis_scope.learners_in_scope(supabase, report["rows"]))
    choices = (choices if choices is not None
               else analysis_scope.scope_choices(supabase, role, "tea-1", "sch-1"))
    with _signed_in(app, role=role):
        html = app.jinja_env.get_template("teacher/reports.html").render(
            report=report, learners=learners,
            learner_keys=[f"{row['name']} {row['exam_title']} {row['school']} "
                          f"{row['teacher']}".lower() for row in learners],
            learner_cap=analysis_scope.MAX_LEARNERS,
            learners_truncated=len(learners) >= analysis_scope.MAX_LEARNERS,
            scope_choices=choices, school_filter=school_filter,
            teacher_filter=teacher_filter)
    return {"html": html, "report": report, "learners": learners}


@pytest.fixture(scope="module")
def rendered(app):
    return _render(app)


def _served(app, endpoint, **params):
    """The address the app serves for a route, blueprint prefix and all."""
    rule = next((r for r in app.url_map.iter_rules()
                 if r.endpoint.endswith(endpoint)), None)
    assert rule, f"the route {endpoint} is gone"
    return rule.build(params, append_unknown=False)[1]


# ── who may open the index at all ────────────────────────────────────────────

class TestTheDoorIsGatedToTheRolesThatHaveAScope:
    def test_the_hub_is_guarded_by_exactly_the_scoped_roles(self):
        """One list, two jobs: the guard and `analysis_scope` must name the same
        roles. A role the page admits but the scope does not know renders an empty
        report and no error — and the sidebar would happily offer it."""
        match = re.search(r'@teacher_bp\.route\("/reports"\)\s*\n'
                          r'@role_required\(([^)]*)\)\s*\n'
                          r'def reports_hub\(', TEACHER)
        assert match, "the reports hub is gone, or is reachable by any role"
        assert set(re.findall(r'"(\w+)"', match.group(1))) == \
            set(analysis_scope.SCOPE_LABELS), (
                "the roles the hub admits are not the roles that have a scope")

    def test_a_student_has_no_scope_at_all(self):
        assert "murid" not in analysis_scope.SCOPE_LABELS
        assert not analysis_scope.exams_in_scope(_Fake({}), "murid", "stu-1", None)

    @pytest.mark.parametrize("role", ["guru", "admin_sekolah", "super_admin"])
    def test_every_scoped_role_gets_its_own_scope_words(self, role):
        pair = analysis_scope.scope_pair(role)
        assert pair.get("id") and pair.get("en"), (
            f"the {role} hub would print an empty scope label")


# ── the menu entries ─────────────────────────────────────────────────────────

def _sidebar_blocks():
    """Each role's branch of the desktop sidebar, the way `test_sidebar_nav`
    reads it: one `<nav>`, one branch per role."""
    opening = re.compile(r"<nav\b[^>]*>").search(BASE)
    end = BASE.find("</nav>", opening.end())
    parts = re.split(r"\{%\s*(?:el)?if\s+g\.user_role\s*==\s*'(\w+)'\s*%\}",
                     BASE[opening.end():end])
    return dict(zip(parts[1::2], parts[2::2]))


class TestTheSidebarOffersIt:
    """The entry the request was actually about: without it the page exists and
    nobody reaches it."""

    def test_every_scoped_role_gets_exactly_one_entry(self):
        blocks = _sidebar_blocks()
        for role in ("guru", "admin_sekolah", "super_admin"):
            assert blocks[role].count(f'href="{HUB}"') == 1, (
                f"the {role} sidebar offers the reports index "
                f"{blocks[role].count(f'href=\"{HUB}\"')} times")

    def test_a_student_is_not_offered_a_page_it_cannot_open(self):
        """The one entry that would 404/403: a menu item for a role the guard
        refuses is a dead end with no explanation."""
        assert f'href="{HUB}"' not in _sidebar_blocks()["murid"]

    def test_it_sits_under_the_reports_section(self):
        """A door in the wrong section is a door in a strange place: the reports
        belong beside the analytics they report on, not under Settings."""
        for role, chunk in _sidebar_blocks().items():
            if f'href="{HUB}"' not in chunk:
                continue
            before = chunk.split(f'href="{HUB}"', 1)[0]
            section = re.findall(
                r"nav-section-title[^>]*>\s*<span[^>]*x-text=\"t\('([^']+)'", before)
            assert section[-1] in ("Laporan", "Reports"), (
                f"the {role} reports entry is filed under {section[-1]!r}")

    def test_the_entry_is_bilingual(self):
        """A literal would be the one menu line that stops following the EN/ID
        button — and the sidebar is where that is most visible."""
        for role in ("guru", "admin_sekolah", "super_admin"):
            link = _sidebar_blocks()[role].split(f'href="{HUB}"', 1)[1]
            label = link.split("</a>", 1)[0]
            assert re.search(r"t\('[^']+','[^']+'\)", label), (
                f"the {role} reports entry is not a bilingual pair: {label}")


# ── the page ─────────────────────────────────────────────────────────────────

class TestItRendersBothHalves:
    def test_no_jinja_placeholder_survives(self, rendered):
        assert "{{" not in rendered["html"] and "{%" not in rendered["html"]
        assert "Undefined" not in rendered["html"]

    def test_one_row_per_exam_and_one_per_paper(self, rendered):
        assert len(re.findall(r'data-report-row="', rendered["html"])) == \
            len(rendered["report"]["rows"])
        assert len(re.findall(r'data-learner-row="', rendered["html"])) == \
            len(rendered["learners"])
        assert rendered["learners"], "the fixture has no papers to index"

    def test_the_numbers_are_the_scope_report_s_own(self, rendered):
        """The index prints what `_scope_report` computed — the same object the
        statistics page renders and the PDF is built from. A mean recomputed here
        is a mean that disagrees with the document behind the door."""
        row = rendered["report"]["rows"][0]
        assert row["count"] == len(PAPERS), "the fixture paper count is not three"
        assert f">{row['mean']}<" in rendered["html"]
        assert f">{row['median']}<" in rendered["html"]
        assert f"data-report-row=\"{row['id']}\"" in rendered["html"]

    def test_a_scope_with_nothing_marked_prints_no_average_at_all(self, app):
        """A dash, not a zero: `0` over four cards reads as a school that scored
        nothing rather than one that has not sat anything yet."""
        supabase = _scope_database(papers=[])
        rendered = _render(app, supabase=supabase, report=_report(supabase))
        cards = rendered["html"].split('data-report-section="global"', 1)[0]
        values = re.findall(
            r'font-extrabold (?:text-slate-800|text-emerald-600) mt-1">([^<]*)<',
            cards)
        assert values == ["1", "0", "—", "—"], (
            f"an unmarked scope reports {values}; a zero is a measurement")

    def test_both_halves_are_on_the_page(self, rendered):
        html = rendered["html"]
        assert 'data-report-section="global"' in html
        assert 'data-report-section="individual"' in html


# ── the doors ────────────────────────────────────────────────────────────────

class TestEveryDoorIsAnAddressTheAppServes:
    def test_the_class_door_is_the_filed_report_s_own_route(self, app, rendered):
        row = rendered["report"]["rows"][0]
        served = _served(app, "exam_analysis_report", exam_id=row["id"])
        assert f'href="{served}"' in rendered["html"], (
            "the class door does not lead to the filed report route")

    def test_the_learner_door_is_the_learner_report_s_own_route(self, app, rendered):
        linked = [row for row in rendered["learners"] if row["student_id"]]
        assert linked, "the fixture has no linkable learner"
        served = _served(app, "exam_analysis_student",
                         exam_id=linked[0]["exam_id"],
                         student_id=linked[0]["student_id"])
        assert f'href="{served}"' in rendered["html"]

    def test_a_paper_with_no_profile_is_listed_without_a_door(self, rendered):
        """It was sat, and the totals above count it: a list that drops it
        contradicts the count printed on the same page. And the learner route
        answers 404 for it, so the door would lead nowhere."""
        assert len(re.findall(r'data-learner-row="', rendered["html"])) == \
            len(rendered["learners"]) == 3
        assert 'data-learner-report' in rendered["html"]
        assert len(re.findall(r'<a[^>]*data-learner-report', rendered["html"])) == 2
        assert "/report/student/None" not in rendered["html"]

    def test_a_paper_that_is_not_published_says_which_it_is(self, app):
        """A blank mark beside a child reads as a zero. `status` is what tells the
        reader whether the number is final — which is why the row is sent one, and
        why this is asserted on the render rather than on the source: a row that
        *mentions* the field but never shows it passes a substring check."""
        for status, wanted, unwanted in (("submitted", "Ungraded", "Graded"),
                                        ("graded", "Graded", "Ungraded")):
            supabase = _scope_database(
                papers=[dict(PAPERS[0], status=status, final_score=None)])
            rendered = _render(app, supabase=supabase, report=_report(supabase))
            assert f"'{wanted}'" in rendered["html"], (
                f"a {status} paper does not say so")
            assert f"'{unwanted}'" not in rendered["html"], (
                f"a {status} paper is labelled {unwanted}")

    def test_a_published_paper_carries_no_state_at_all(self, rendered):
        """The other end: published is the ordinary case, and a chip on every row
        is a column of noise that stops meaning anything."""
        assert "Belum dikoreksi" not in rendered["html"]
        assert "Sudah dikoreksi" not in rendered["html"]

    def test_the_downloads_carry_the_reader_s_language(self, rendered):
        """"The files are built on the server, so a link that did not carry the
        choice would hand an English reader an Indonesian report."""
        for kind in ("pdf", "csv"):
            assert f"/download.{kind}?lang=' + lang" in PAGE, (
                f"the {kind} link does not carry the chosen language")


# ── the copy the template reads must be copy the reader sends ────────────────

class TestTheTemplateOnlyReadsWhatTheScopeSends:
    """The defect this catches: a template reading a field the service never
    builds renders a blank cell — or the string "None" — and no test that greps
    for the field name would notice."""

    def test_the_exam_row_reads_are_the_report_s_own_keys(self):
        sent = set(EXAM_ROW_KEYS)
        reads = set(re.findall(r"(?<![\w.])e\.(\w+)", PAGE))
        reads |= set(re.findall(r"(?<![\w.])e\['(\w+)'\]", PAGE))
        assert reads <= sent, f"the exam row reads {sorted(reads - sent)}"

    def test_the_learner_row_reads_are_the_scope_s_own_keys(self):
        # `MAX_LEARNERS` is read through `learner_cap`, not through a row.
        # `school` and `teacher` are on the row from the exam it belongs to: a
        # reader whose scope spans more than one school cannot tell three papers
        # apart without them, and that reader is the one the filters are for.
        sent = {"exam_id", "exam_title", "student_id", "name", "mark", "status",
                "submitted_at", "school", "teacher"}
        reads = set(re.findall(r"(?<![\w.])row\.(\w+)", PAGE))
        assert reads <= sent, f"the learner row reads {sorted(reads - sent)}"
        assert sent <= reads, (
            f"the learner row never shows {sorted(sent - reads)}")

    def test_the_service_really_builds_those_exam_keys(self):
        """Both halves of the contract, from the other side: the list above is
        the *service's* output, not a second hand-written copy that happens to
        agree with the template."""
        report = _report()
        assert report["rows"], "the fixture exam produced no row"
        assert set(report["rows"][0]) == set(EXAM_ROW_KEYS)

    def test_the_learner_keys_search_the_rendered_list_one_to_one(self, app):
        """The filter indexes the rows by position, so a key list that drifted
        out of step would filter the wrong child out — silently."""
        supabase = _scope_database()
        report = _report(supabase)
        learners = analysis_scope.learners_in_scope(supabase, report["rows"])
        keys = [f"{row['name']} {row['exam_title']} {row['school']} "
                f"{row['teacher']}".lower() for row in learners]
        assert len(keys) == len(learners)
        assert all(key == key.lower() for key in keys), (
            "the browser is asked to fold the case of every row on each keystroke")


#: The keys `analysis_scope.report` builds into each row, pinned so the template's
#: reads can be compared against them.
EXAM_ROW_KEYS = (
    "id", "title", "subject", "teacher", "school", "passing", "items", "flagged",
    "holes", "unkeyed", "alpha", "kr20", "count", "mean", "median", "min", "max",
    "sd", "pass_pct",
)


# ── the learner read, batched ────────────────────────────────────────────────

class TestTheLearnerListIsReadTheWayTheClaimIsMade:
    def test_it_chunks_by_the_shared_chunk_size(self):
        exams = [dict(EXAM, id=f"exam-{i}") for i in range(60)]
        papers = [{"exam_id": f"exam-{i}", "student_id": f"stu-{i}",
                   "final_score": 70.0, "status": "published",
                   "submitted_at": f"2026-06-{i % 28 + 1:02d}T09:00:00+00:00"}
                  for i in range(60)]
        supabase = _Fake({"submissions": papers,
                          "profiles": [{"id": f"stu-{i}", "full_name": f"M{i}"}
                                       for i in range(60)]})
        rows = analysis_scope.learners_in_scope(supabase, exams)
        chunks = [entry for entry in supabase.log
                  if entry[:2] == ("submissions", "in_")]
        assert len(rows) == 60
        assert [len(entry[3]) for entry in chunks] == \
            [analysis_scope.CHUNK, analysis_scope.CHUNK,
             len(exams) - 2 * analysis_scope.CHUNK], (
                "sixty exams are not read in chunks of twenty-five")
        assert all(len(entry[3]) <= analysis_scope.CHUNK for entry in chunks)

    def test_the_order_is_applied_once_over_the_whole_list(self):
        """`order()` inside a chunked `.in_` sorts each chunk against itself, so
        the newest paper is only newest *within its chunk*."""
        supabase = _scope_database()
        supabase.tables["submissions"] = list(reversed(PAPERS))
        rows = analysis_scope.learners_in_scope(supabase, [dict(EXAM)])
        assert [row["submitted_at"] for row in rows] == \
            ["2026-06-02", "2026-06-02", "2026-06-02"]
        assert rows[0]["student_id"] is None, (
            "the newest paper is not first: the order was left to the query")
        assert ("submissions", "order") not in [
            entry[:2] for entry in supabase.log], (
                "the query is asked to order what it will be chunked and re-merged")

    def test_one_unreadable_chunk_does_not_empty_the_list(self):
        exams = [dict(EXAM, id=f"exam-{i}") for i in range(30)]
        papers = [{"exam_id": f"exam-{i}", "student_id": f"stu-{i}",
                   "final_score": 70.0, "status": "published",
                   "submitted_at": "2026-06-02T09:00:00+00:00"}
                  for i in range(30)]
        supabase = _Fake({"submissions": papers,
                          "profiles": [{"id": f"stu-{i}", "full_name": f"M{i}"}
                                       for i in range(30)]},
                         fail={"submissions": 2})
        rows = analysis_scope.learners_in_scope(supabase, exams)
        assert len(rows) == 25, (
            "a failed chunk took the papers that did load with it")

    def test_an_empty_scope_reads_nothing_at_all(self):
        supabase = _Fake({})
        assert analysis_scope.learners_in_scope(supabase, []) == []
        assert supabase.log == []

    def test_the_cap_is_the_service_s_own_number(self):
        exams = [dict(EXAM)]
        papers = [{"exam_id": "exam-1", "student_id": f"stu-{i}",
                   "final_score": 70.0, "status": "published",
                   "submitted_at": "2026-06-02T09:00:00+00:00"}
                  for i in range(20)]
        supabase = _Fake({"submissions": papers, "profiles": []})
        rows = analysis_scope.learners_in_scope(supabase, exams, limit=5)
        assert len(rows) == 5
        assert analysis_scope.MAX_LEARNERS >= 100


# ── the route hands the page everything the page renders ─────────────────────

class TestTheRouteFillsThePage:
    def test_it_hands_the_page_the_scope_report_and_the_learners(self, app,
                                                                 monkeypatch):
        """Every other test here renders the template with its arguments handed
        in, so a route that stopped passing one would print nothing and raise
        nothing: the door would be gone and the page would look finished."""
        import inspect

        import app.routes.teacher as teacher

        supabase = _scope_database()
        report = _report(supabase)
        monkeypatch.setattr(teacher, "get_supabase", lambda: supabase)
        monkeypatch.setattr(teacher, "_scope_report",
                            lambda *a, **k: dict(report))
        handed = {}
        monkeypatch.setattr(teacher, "render_template",
                            lambda name, **kw: handed.update({"name": name, **kw})
                            or "")

        with _signed_in(app, "guru"):
            inspect.unwrap(teacher.reports_hub)()

        assert handed["name"] == "teacher/reports.html"
        assert handed["report"]["rows"] == report["rows"]
        assert handed["learners"], "the route handed the page no learners"
        assert len(handed["learner_keys"]) == len(handed["learners"]), (
            "the filter's keys and the rows it filters are different lengths")
        assert handed["learner_cap"] == analysis_scope.MAX_LEARNERS
        assert handed["learners_truncated"] is False
        # The filters, always handed over — including when the scope offers no
        # choice, because the template branches on the choices and would raise on
        # a page that never offered any.
        assert set(handed["scope_choices"]) == {"schools", "teachers"}, (
            "the page was handed no dropdown contents")
        assert handed["school_filter"] == "" and handed["teacher_filter"] == ""
        assert all(not row["school"] or row["school"].lower() in key
                   for row, key in zip(handed["learners"], handed["learner_keys"])), (
            "the search box does not cover the school name the row prints")

    def test_a_choice_the_page_never_offered_is_not_shown_as_the_choice(
            self, app, monkeypatch):
        """The form must not claim a parameter did something it did not. An id the
        scope does not offer leaves the whole scope on screen, so the page shows
        "all" beside it — and prints the ordinary empty state rather than the one
        about the corner you asked for."""
        import inspect

        import app.routes.teacher as teacher

        supabase = _scope_database()
        monkeypatch.setattr(teacher, "get_supabase", lambda: supabase)
        monkeypatch.setattr(teacher, "cache_get", lambda key: None)
        monkeypatch.setattr(teacher, "cache_set",
                            lambda key, value, ttl=None: None)
        handed = {}
        monkeypatch.setattr(teacher, "render_template",
                            lambda name, **kw: handed.update({"name": name, **kw})
                            or "")
        with _signed_in(app, "guru",
                        path="/teacher/reports?school_id=another-school"):
            inspect.unwrap(teacher.reports_hub)()

        assert handed["school_filter"] == "", (
            "the page would mark a choice no option offers")
        assert len(handed["learners"]) == 3, (
            "a parameter the scope does not offer changed what the reader sees")
        assert set(handed["report"]["totals"]) >= {
            "exams", "participants", "mean", "pass_rate"}, (
                "the KPI strip has no numbers to print")

    def test_the_hub_and_the_statistics_page_share_one_report(self):
        """`_scope_report` is the cached, scope-keyed report. A second reader
        would be a second cache key, a second batch of queries over forty exams —
        and two numbers for one exam."""
        body = TEACHER.split("def reports_hub(", 1)[1].split("\n@teacher_bp", 1)[0]
        assert "_scope_report(" in body
        assert "analysis_scope.report(" not in body, (
            "the hub computes its own report instead of reading the cached one")


# ── it fits a phone ─────────────────────────────────────────────────────────

class TestItFitsASmallScreen:
    def test_the_learner_list_scrolls_instead_of_growing_forever(self):
        assert "max-h-[70vh] overflow-y-auto" in PAGE, (
            "four hundred papers push the class reports off the page")

    def test_the_exam_row_wraps_rather_than_squeezing(self):
        assert "flex flex-col lg:flex-row lg:items-center" in PAGE, (
            "the exam row keeps its columns on a phone")

    def test_the_kpi_grid_starts_at_two_columns(self):
        assert "grid-cols-2 lg:grid-cols-4" in PAGE

    def test_the_door_strips_wrap(self):
        assert PAGE.count("flex flex-wrap gap-1.5") >= 1
        assert "flex flex-wrap items-center gap-2 sm:gap-3" in PAGE


# ── the school and teacher filters ───────────────────────────────────────────
#
# A super admin's scope is every exam on the box, so the paper list is every paper
# on the box: the one page in the app where "narrow it down before you scroll" is
# a request rather than a preference. Two rules make the two controls honest, and
# both are held here: a choice can only be one the caller's own scope contains
# (which is what makes "may I filter by this?" and "may I see this?" one
# question), and the narrowing happens *before* the cap rather than over a list
# that was already cut.

class TestTheChoicesTheScopeOffers:
    def test_a_scope_of_one_school_offers_no_choice_because_there_is_none(self, app):
        """A teacher's page is unchanged: the control is drawn where it can do
        something and nowhere else."""
        html = _render(app, role="guru")["html"]
        assert "data-scope-filter" not in html, (
            "a one-school scope was given dropdowns with one option each")
        assert "All schools" not in html

    def test_the_two_roles_between_the_ends_get_what_varies(self):
        wide = _wide_database()
        super_admin = analysis_scope.scope_choices(wide, "super_admin", "sa-1", None)
        assert [o["name"] for o in super_admin["schools"]] == ["SMA Uji", "SMP Uji"], (
            "the options are not sorted by label, so the order a reader sees "
            "depends on which exam happened to be read first")
        assert len(super_admin["teachers"]) == 3
        admin = analysis_scope.scope_choices(wide, "admin_sekolah", "adm-1", "sch-1")
        assert [o["name"] for o in admin["schools"]] == ["SMP Uji"], (
            "an admin of one school was offered another school")
        assert [o["name"] for o in admin["teachers"]] == ["Guru Dua", "Guru Uji"]

    def test_a_teacher_is_offered_only_their_own(self):
        choices = analysis_scope.scope_choices(_wide_database(), "guru", "tea-2", "sch-1")
        assert [o["name"] for o in choices["teachers"]] == ["Guru Dua"]
        assert [o["name"] for o in choices["schools"]] == ["SMP Uji"]

    def test_a_school_with_no_name_on_file_offers_no_blank_option(self):
        """A blank option is a filter a reader would have to guess at; the papers
        stay visible under "all" instead."""
        wide = _wide_database()
        wide.tables["schools"] = [{"id": "sch-1", "name": "SMP Uji"}]
        choices = analysis_scope.scope_choices(wide, "super_admin", "sa-1", None)
        assert [o["id"] for o in choices["schools"]] == ["sch-1"]
        assert len(analysis_scope.exams_in_scope(wide, "super_admin", "sa-1", None)) == 3


class TestTheFiltersNarrowTheScope:
    def test_a_school_choice_narrows_the_rows_and_the_totals_together(self):
        data = analysis_scope.report(_wide_database(), "super_admin", "sa-1", None,
                                     school_filter="sch-2")
        assert [row["id"] for row in data["rows"]] == ["exam-c"]
        assert data["totals"]["exams"] == 1 and data["totals"]["participants"] == 1, (
            "the rows narrowed and the totals did not: one scope, two sizes")

    def test_a_teacher_choice_narrows_inside_the_school(self):
        data = analysis_scope.report(_wide_database(), "super_admin", "sa-1", None,
                                     school_filter="sch-1", teacher_filter="tea-2")
        assert [row["id"] for row in data["rows"]] == ["exam-b"]

    def test_a_choice_the_scope_does_not_offer_is_not_a_filter(self):
        """The security-relevant direction: an id from another school, typed into
        the address by hand, must not narrow anything — and must not be a way to\
        ask about that school either."""
        data = analysis_scope.report(_wide_database(), "admin_sekolah", "adm-1",
                                     "sch-1", school_filter="sch-2",
                                     teacher_filter="tea-3")
        assert {row["school"] for row in data["rows"]} == {"SMP Uji"}
        assert len(data["rows"]) == 2, (
            "a parameter the page never offered changed what the caller sees")

    def test_a_blank_choice_is_the_whole_scope(self):
        data = analysis_scope.report(_wide_database(), "super_admin", "sa-1", None,
                                     school_filter="", teacher_filter="")
        assert len(data["rows"]) == 3

    def test_the_learners_are_narrowed_before_the_cap(self):
        """The cap is the newest `MAX_LEARNERS` papers *of the scope*, so a filter
        applied after it would list one school's papers only if they happened to be
        among the newest four hundred on the box."""
        older = [{"exam_id": "exam-c", "student_id": None, "final_score": 70.0,
                  "status": "published",
                  "submitted_at": f"2026-05-{i % 28 + 1:02d}T09:00:00+00:00"}
                 for i in range(analysis_scope.MAX_LEARNERS + 10)]
        newer = [{"exam_id": "exam-a", "student_id": None, "final_score": 70.0,
                  "status": "published",
                  "submitted_at": f"2026-06-30T09:{i % 60:02d}:00+00:00"}
                 for i in range(analysis_scope.MAX_LEARNERS + 10)]
        db = _wide_database(papers=older + newer)
        data = analysis_scope.report(db, "super_admin", "sa-1", None,
                                     school_filter="sch-2")
        assert [row["id"] for row in data["rows"]] == ["exam-c"]
        papers = analysis_scope.learners_in_scope(db, data["rows"])
        assert {row["exam_id"] for row in papers} == {"exam-c"}, (
            "the narrowed school's papers were cut by a cap that ran before the "
            "filter did")
        assert len(papers) == analysis_scope.MAX_LEARNERS

    def test_the_route_narrows_on_the_server_rather_than_in_the_browser(self):
        body = TEACHER.split("def reports_hub(", 1)[1].split("\n@teacher_bp", 1)[0]
        assert "school_filter=school_filter" in body \
            and "teacher_filter=teacher_filter" in body, (
                "the route reads the two choices and does not act on them")
        assert 'learners_in_scope(supabase, data["rows"])' in body, (
            "the paper list is built from something other than the narrowed scope")

    def test_every_filter_the_service_names_is_one_the_route_reads(self):
        """One list, two jobs: the names the page's form posts and the names the
        route reads have to be the ones `SCOPE_FILTERS` declares."""
        for parameter, _, _, _ in analysis_scope.SCOPE_FILTERS:
            assert f'name="{parameter}"' in PAGE, (
                f"the form has no {parameter} field to post the choice with")
            assert f'request.args.get("{parameter}")' in TEACHER, (
                f"the route does not read {parameter}")


class TestTwoChoicesAreTwoCacheEntries:
    def test_the_cache_key_carries_the_two_choices(self, app, monkeypatch):
        """A report narrowed to one school is not the report for the whole scope.
        Serving one as the other prints the wrong totals beside the right rows, and
        the date range already taught this codebase that lesson."""
        import app.routes.teacher as teacher

        keyed = []
        monkeypatch.setattr(teacher, "cache_get", lambda key: None)
        monkeypatch.setattr(teacher, "cache_set",
                            lambda key, value, ttl=None: keyed.append(key))
        supabase = _wide_database()
        with _signed_in(app, "super_admin"):
            teacher._scope_report(supabase, "id")
            teacher._scope_report(supabase, "id", school_filter="sch-1")
            teacher._scope_report(supabase, "id", school_filter="sch-2")
        assert len(set(keyed)) == 3, (
            "two different scopes share one cache entry")


class TestThePageDrawsTheFilters:
    def test_a_super_admin_sees_both_and_a_note_about_what_they_narrow(self, app):
        html = _render(app, role="super_admin", supabase=_wide_database())["html"]
        assert 'data-scope-filter="school_id"' in html
        assert 'data-scope-filter="teacher_id"' in html
        schools = html.split('name="school_id"', 1)[1].split("</select>", 1)[0]
        teachers = html.split('name="teacher_id"', 1)[1].split("</select>", 1)[0]
        assert "SMA Uji" in schools and "SMP Uji" in schools, (
            "the school dropdown does not list the scope's schools")
        assert "Guru Dua" in teachers and "Ani" not in teachers, (
            "the teacher dropdown is filled from something other than the "
            "teachers'")
        assert "narrow this whole page" in html, (
            "two controls that change the numbers, the exams and the papers are "
            "shown without saying that they do")

    def test_the_chosen_option_is_the_selected_one(self, app):
        html = _render(app, role="super_admin", supabase=_wide_database(),
                       school_filter="sch-2")["html"]
        assert re.search(r'<option value="sch-2"[^>]*selected', html), (
            "the form does not show which school the page is about")
        assert 'value="" x-text' in html, (
            "there is no way back to the whole scope")

    def test_a_filter_on_its_own_still_offers_a_way_back(self, app):
        """The Clear link was conditional on the date range before there was
        anything else to clear."""
        html = _render(app, role="super_admin", supabase=_wide_database(),
                       school_filter="sch-2")["html"]
        assert f'href="{HUB}?lang=' in html, (
            "a narrowed scope with no way back to the whole one")
        assert f'href="{HUB}?lang=' not in _render(app, role="guru")["html"], (
            "a Clear link over nothing")

    def test_the_learner_caption_names_the_origin_only_when_it_separates(self, app):
        """Three exams can share one title, so on a super admin's list the school
        and the teacher are the only thing telling the papers apart — and on a
        teacher's own list they are the same two words on every row."""
        narrow = _render(app, role="guru")["html"]
        wide = _render(app, role="super_admin", supabase=_wide_database())["html"]

        def learner_half(html):
            return html.split('data-report-section="individual"', 1)[1]

        assert "SMP Uji" in learner_half(wide) and "Guru Uji" in learner_half(wide)
        caption = learner_half(wide).split("Fisika 1", 1)[1][:200]
        assert "SMP Uji" in caption and "Guru Uji" in caption, (
            "the row does not say which school and which teacher it came from")
        assert caption.index("SMP Uji") < caption.index("Guru Uji"), (
            "the caption is not exam, then school, then teacher")
        assert "SMP Uji" not in learner_half(narrow)

    def test_an_empty_filtered_list_says_which_of_the_two_it_is(self, app):
        """\"There is nothing here\" and \"the corner you asked for is empty\" are
        fixed by doing different things."""
        filtered = _render(app, role="super_admin", supabase=_wide_database(),
                           learners=[], school_filter="sch-2")["html"]
        unfiltered = _render(app, role="super_admin", supabase=_wide_database(),
                             learners=[])["html"]
        assert "No paper in the school or teacher you chose" in filtered
        assert "No papers to open in this scope yet" in unfiltered
