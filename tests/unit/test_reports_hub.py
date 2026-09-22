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


def _render(app, role="guru", supabase=None, report=None, learners=None):
    """The page as the route hands it to Jinja.

    `supabase` is passed straight through so a case can change the *data* — a
    paper still being marked, say — without also having to remember to rebuild
    every argument from it.
    """
    supabase = supabase if supabase is not None else _scope_database(role=role)
    report = report if report is not None else _report(supabase, role=role)
    learners = (learners if learners is not None
                else analysis_scope.learners_in_scope(supabase, report["rows"]))
    with _signed_in(app, role=role):
        html = app.jinja_env.get_template("teacher/reports.html").render(
            report=report, learners=learners,
            learner_keys=[f"{row['name']} {row['exam_title']}".lower()
                          for row in learners],
            learner_cap=analysis_scope.MAX_LEARNERS,
            learners_truncated=len(learners) >= analysis_scope.MAX_LEARNERS)
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
        sent = {"exam_id", "exam_title", "student_id", "name", "mark", "status",
                "submitted_at"}
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
        keys = [f"{row['name']} {row['exam_title']}".lower() for row in learners]
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
