"""One exam, one session page — and the deliberate absence of a verdict.

Three surfaces described the same sitting: the live room (`proctoring`), the
co-occurrence patterns (`cheat-analysis`), and a neutral session layer whose data
migration 035 had been writing since before anything read it. Two of them were
pages with their own JSON endpoints; the third had **no reader at all** —
`attempt_summary`, `attempt_session_events` and `exam_class_baseline` were written
on every submit and read by nobody.

The merged page is one per exam, and what it must get right is not "it renders".
It is that it says what happened without saying what it meant, in four checkable
ways:

* **one page and one API**, with the two duplicated JSON endpoints gone and the
  two old page URLs redirecting — a teacher's bookmark still lands somewhere;
* **no verdict in the copy**: no "curang", "menyontek", "mencurigakan", "pelaku",
  "guilty". The existing cheat-analysis page says *semakin mencurigakan* and marks
  a group "Aman"; that framing is what this replaces;
* **every observation carries its own rebuttal** — evidence, the class comparison
  the evidence is against, a plausible innocent explanation, a confidence, and a
  human follow-up — because a number a reader cannot argue with is a verdict
  wearing a chart;
* **a shared disturbance excuses the individuals inside it**, and a class too small
  to have a distribution produces no per-student observation at all.

Measured against the code before this test existed: four endpoints existed
(`proctoring`, `proctoring-data`, `cheat-analysis`, `cheat-data`), no route read
the 035 tables, and the only two anti-cheat pages in the app opened by default on
whichever student the database returned first.
"""
import ast
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import session_review

ROOT = Path(__file__).resolve().parents[2]
TEACHER_SRC = ROOT / "app" / "routes" / "teacher.py"
TEMPLATES = ROOT / "app" / "templates" / "teacher"
PAGE = TEMPLATES / "session_review.html"
RESULTS = TEMPLATES / "results.html"
BASE = ROOT / "app" / "templates" / "base.html"

#: Words that decide something about a student rather than describe the sitting.
VERDICTS = ("curang", "menyontek", "mencurigakan", "pelaku", "tersangka",
            "kecurangan", "cheater", "cheating", "guilty", "suspect")

NEW_PAGE = "/teacher/exams/<exam_id>/sessions"
NEW_API = "/teacher/api/exams/<exam_id>/sessions-data"
OLD_PAGES = ("/teacher/exams/<exam_id>/proctoring",
             "/teacher/exams/<exam_id>/cheat-analysis")
OLD_APIS = ("/teacher/api/exams/<exam_id>/proctoring-data",
            "/teacher/api/exams/<exam_id>/cheat-data")


def _source(path=TEACHER_SRC) -> str:
    return path.read_text(encoding="utf-8")


def _rules(app) -> dict[str, object]:
    return {rule.rule: rule for rule in app.url_map.iter_rules()}


def _function(source: str, name: str) -> ast.FunctionDef:
    return next(node for node in ast.walk(ast.parse(source))
                if isinstance(node, ast.FunctionDef) and node.name == name)


def _decorators(source: str, name: str) -> str:
    node = _function(source, name)
    if not node.decorator_list:
        return ""
    lines = source.splitlines()
    first = min(d.lineno for d in node.decorator_list)
    return "\n".join(lines[first - 1:node.lineno - 1])


def _bodies(app) -> str:
    """Every function body in the teacher blueprint, with its own prose removed."""
    return "\n".join(node.name for node in ast.walk(ast.parse(_source()))
                     if isinstance(node, ast.FunctionDef))


# ── 1. one page, one API ─────────────────────────────────────────────────────

class TestTheRoomIsOnePlaceNow:
    def test_the_page_and_the_api_exist(self, app):
        rules = _rules(app)
        assert NEW_PAGE in rules, "the merged session page has no route"
        assert NEW_API in rules, "the merged session page has no data endpoint"

    def test_both_are_get_only(self, app):
        """Read-only is the whole design: nothing on this page can penalise a
        student, so neither route may answer a write."""
        rules = _rules(app)
        for path in (NEW_PAGE, NEW_API):
            methods = rules[path].methods - {"HEAD", "OPTIONS"}
            assert methods == {"GET"}, f"{path} answers {sorted(methods)}"

    def test_the_duplicated_data_endpoints_are_gone(self, app):
        rules = _rules(app)
        for path in OLD_APIS:
            assert path not in rules, (
                f"{path} still exists: the merged API is supposed to replace it")

    def test_the_old_pages_redirect_rather_than_vanish(self, app):
        """A bookmark is a promise. Both old URLs have to reach the new page."""
        rules = _rules(app)
        source = _source()
        assert OLD_PAGES[0] in rules and OLD_PAGES[1] in rules, (
            "an old URL was deleted outright instead of redirecting")
        for name in ("exam_proctoring", "cheat_analysis"):
            body = ast.get_source_segment(source, _function(source, name)) or ""
            assert "redirect(" in body, f"{name} does not redirect anywhere"
            assert "301" in body, (
                f"{name} redirects with a temporary code: a moved page is permanent")
            assert "render_template" not in body, (
                f"{name} still renders a page of its own")

    def test_the_two_old_templates_are_deleted(self):
        assert PAGE.exists(), "the merged page has no template"
        for dead in ("proctoring.html", "cheat_analysis.html"):
            assert not (TEMPLATES / dead).exists(), f"{dead} is still in the tree"

    def test_nothing_points_at_the_dead_endpoints(self):
        offenders = []
        for path in ROOT.joinpath("app").rglob("*.html"):
            text = path.read_text(encoding="utf-8")
            for old in ("/proctoring", "/cheat-analysis", "proctoring-data", "cheat-data"):
                if old in text:
                    offenders.append(f"{path.relative_to(ROOT)} -> {old}")
        assert not offenders, offenders


# ── 2. the copy describes, it does not judge ─────────────────────────────────

class TestThePageDoesNotDecideAnything:
    def test_the_page_says_what_was_seen_without_a_verdict(self):
        text = PAGE.read_text(encoding="utf-8").lower()
        found = [word for word in VERDICTS if word in text]
        assert not found, (
            f"the page decides something about a student: {found}. An observation "
            "is a description plus its own rebuttal, never a label")

    def test_the_data_the_page_reads_is_not_named_after_a_verdict(self):
        """The endpoint's own keys end up in the DOM and in the JSON, so a key
        called `suspect_students` is the same verdict by another route."""
        source = _source()
        body = ast.get_source_segment(source, _function(source, "exam_sessions_data")) or ""
        lowered = body.lower()
        found = [word for word in VERDICTS if word in lowered]
        assert not found, f"the data endpoint names a verdict: {found}"

    def test_the_page_carries_the_false_positive_warning(self):
        """The reader has to be told, on the page, that this is not proof."""
        text = PAGE.read_text(encoding="utf-8").lower()
        assert "korelasi" in text or "tidak membuktikan" in text, (
            "the page shows patterns with no statement that a pattern is not proof")

    def test_the_page_explains_how_to_read_itself(self):
        text = PAGE.read_text(encoding="utf-8").lower()
        assert "cara membaca" in text, (
            "a page of statistics a teacher cannot interpret is a page that "
            "invites the wrong conclusion")

    def test_the_roster_is_in_its_own_order(self, app):
        """No reader may be handed a "worst first" list: the default order is the
        roster's, so the page cannot be skimmed as a ranking."""
        source = _source()
        body = ast.get_source_segment(source, _function(source, "exam_sessions_data")) or ""
        assert "sort" not in body, (
            "the endpoint sorts its students: the order has to come from the roster, "
            "not from a suspicion score")


# ── 3. what a reader is handed ───────────────────────────────────────────────

def _summary(student_id: str, **metrics) -> dict:
    base = {"student_id": student_id, "attempt_id": f"a-{student_id}",
            "away_count": 0, "away_total_ms": 0, "away_max_ms": 0,
            "away_short_count": 0, "away_long_count": 0, "away_unknown_count": 0,
            "sync_gap_count": 0, "offline_ms": 0, "answer_change_count": 0,
            "effective_active_ms": 3_600_000, "events_seen": 1, "truncated": False,
            "clock_drift_max_ms": 0, "clock_suspect": False,
            "sources": ["violation_logs", "attempt_session_events"]}
    base.update(metrics)
    return base


def _baseline(pairs: dict) -> list[dict]:
    return [{"metric": metric, "p50": value, "p90": value, "n": 30,
             "small_sample": False} for metric, value in pairs.items()]


class TestEveryObservationCarriesItsOwnRebuttal:
    def test_an_observation_has_all_six_parts(self):
        facts = session_review.observations(
            summaries=[_summary("s1", away_count=9)], baseline=_baseline({"away_count": 0}),
            names={"s1": "Ayu"}, sample_size=30)
        assert facts, "a student far from a baseline of zero produced no observation"
        for fact in facts:
            assert set(fact) >= {"key", "title", "evidence", "comparison",
                                 "explanation", "confidence", "follow_up"}, fact
            assert fact["explanation"], "an observation without a plausible innocent reading"
            assert fact["comparison"], "an observation without the class it is compared to"
            assert fact["follow_up"], "an observation with no human next step"

    def test_every_key_is_a_description(self):
        facts = session_review.observations(
            summaries=[_summary("s1", away_count=9)], baseline=_baseline({"away_count": 0}),
            names={"s1": "Ayu"}, sample_size=30)
        for fact in facts:
            lowered = f"{fact['key']} {fact['title']}".lower()
            assert not [word for word in VERDICTS if word in lowered], fact

    def test_confidence_is_one_of_three_words(self):
        facts = session_review.observations(
            summaries=[_summary("s1", away_count=9)], baseline=_baseline({"away_count": 0}),
            names={"s1": "Ayu"}, sample_size=30)
        assert all(f["confidence"] in session_review.CONFIDENCE for f in facts)

    def test_a_shared_disturbance_downgrades_everyone_inside_it(self):
        """The case a per-student ranking gets wrong: the whole class is equally
        odd, and nobody would have seen the flags on the others' papers.

        One student here is genuinely far above the class *as well*, because a
        fixture in which nobody stands out proves nothing about downgrading: the
        first version of this test added nothing but ordinary rows, produced no
        individual observation at all, and passed with the downgrade deleted.
        """
        summaries = [_summary(f"s{i}", away_count=4) for i in range(30)]
        summaries.append(_summary("loud", away_count=40))
        names = {f"s{i}": f"Murid {i}" for i in range(30)}
        names["loud"] = "Murid Menonjol"
        baseline = _baseline({"away_count": 4})
        facts = session_review.observations(
            summaries=summaries, baseline=baseline, names=names, sample_size=31,
            shared_window={"start": "2026-09-21T08:00:00+07:00",
                           "end": "2026-09-21T08:04:00+07:00", "share": 1.0})
        shared = [f for f in facts if f["key"] == session_review.SHARED_KEY]
        assert shared, "a class that shares one minute produced no shared observation"
        assert shared[0]["confidence"] in ("medium", "high")
        individuals = [f for f in facts if f["key"] != session_review.SHARED_KEY]
        assert individuals, (
            "the fixture produced no individual observation, so a deleted downgrade "
            "would be invisible")
        for fact in individuals:
            assert fact["confidence"] == "low", (
                "an individual was left at full confidence inside a shared "
                f"disturbance: {fact}")

    def test_a_class_too_small_for_a_distribution_gets_no_individual_claim(self):
        """Five students is not a distribution, and the app already refuses to
        compare one: the seed's own `small_class` is the subject of that rule."""
        facts = session_review.observations(
            summaries=[_summary(f"s{i}", away_count=9) for i in range(5)],
            baseline=_baseline({"away_count": 0}),
            names={f"s{i}": f"Murid {i}" for i in range(5)}, sample_size=5)
        assert not [f for f in facts if f["key"] != session_review.SMALL_KEY], (
            "a class of five produced per-student observations")
        assert [f for f in facts if f["key"] == session_review.SMALL_KEY], (
            "a class below the minimum was not told so")


# ── 4. one arithmetic, and the one that already exists ───────────────────────

class TestThePageReusesTheApp:
    def test_the_reader_is_built_on_the_summary_the_writer_wrote(self):
        source = (ROOT / "app" / "services" / "session_review.py").read_text(encoding="utf-8")
        assert "from app.services import attempt_summary" in source or \
               "from app.services.attempt_summary import" in source, (
            "the page measures the data itself instead of reading the stored summary")
        assert not re.search(r"def (percentile|median|summarize)\s*\(", source), (
            "a second implementation of the app's statistics")

    def test_the_old_pages_are_not_still_registered_anywhere(self):
        names = _bodies(None)
        for dead in ("exam_proctoring_data", "cheat_analysis_data"):
            assert dead not in names, f"{dead} is still defined"


# ── 4b. the reader actually finds a shared moment ────────────────────────────
#
# The pure function above is told about a window. This is the half that has to
# *find* one, and it is the half that was wrong: `attempt_session_events` carries
# an `attempt_id` and no `exam_id`, so the first version's read would 400 on every
# exam, degrade to empty under its own exception handler, and leave the
# shared-disturbance rule permanently silent while the page looked healthy.

class _FakeQuery:
    def __init__(self, table, rows, calls):
        self.table, self.rows, self.calls = table, rows, calls
        self.filters = []

    def select(self, *a, **k):
        return self

    def eq(self, column, value):
        self.filters.append((column, value))
        return self

    def in_(self, column, values):
        self.filters.append((column, list(values)))
        return self

    def execute(self):
        self.calls.append((self.table, list(self.filters)))
        rows = list(self.rows)
        for column, value in self.filters:
            if isinstance(value, list):
                rows = [row for row in rows if row.get(column) in value]
            else:
                rows = [row for row in rows if row.get(column) == value]
        return SimpleNamespace(data=rows)


class _FakeSupabase:
    """PostgREST enough to answer the five reads `review_for_exam` makes."""

    def __init__(self, tables):
        self.tables = tables
        self.calls = []

    def table(self, name):
        return _FakeQuery(name, self.tables.get(name, []), self.calls)


def _exam_reads() -> dict:
    return {
        "profiles": [{"id": "s1", "full_name": "Ayu", "role": "murid",
                      "class_id": "c1"},
                     {"id": "s2", "full_name": "Budi", "role": "murid",
                      "class_id": "c1"}],
        "submissions": [{"id": "a1", "exam_id": "e1", "student_id": "s1",
                         "status": "submitted", "answers": {},
                         "submitted_at": "2026-09-21T08:20:00Z"},
                        {"id": "a2", "exam_id": "e1", "student_id": "s2",
                         "status": "submitted", "answers": {},
                         "submitted_at": "2026-09-21T08:21:00Z"}],
        "attempt_summary": [_summary("s1", attempt_id="a1"),
                            _summary("s2", attempt_id="a2")],
        "attempt_session_events": [
            {"attempt_id": "a1", "server_at": "2026-09-21T08:00:00Z",
             "kind": "went_offline"},
            {"attempt_id": "a2", "server_at": "2026-09-21T08:00:40Z",
             "kind": "went_offline"}],
        "violation_logs": [],
    }


class TestTheSharedMomentIsFoundRatherThanDescribed:
    def test_the_window_is_found_from_this_exams_own_events(self):
        client = _FakeSupabase(_exam_reads())
        review = session_review.review_for_exam(
            client, {"id": "e1", "title": "UH", "class_ids": ["c1"],
                     "total_questions": 5})
        assert review["shared_window"], (
            "no shared window was found, so the class-wide rule never fires")
        assert review["shared_window"]["share"] == 1.0
        assert any(f["key"] == session_review.SHARED_KEY
                   for f in review["observations"]), (
            "the window was found but the page was never told about it")

    def test_the_event_read_is_keyed_on_the_attempt_not_the_exam(self):
        """The column the table does not have cannot be the one it is asked by."""
        client = _FakeSupabase(_exam_reads())
        session_review.review_for_exam(
            client, {"id": "e1", "title": "UH", "class_ids": ["c1"],
                     "total_questions": 5})
        event_reads = [filters for table, filters in client.calls
                       if table == "attempt_session_events"]
        assert event_reads, "this exam's events were never read at all"
        assert any(column == "attempt_id" for filters in event_reads
                   for column, _ in filters), "the event table was not read by attempt_id"
        assert not any(column == "exam_id" for filters in event_reads
                       for column, _ in filters), (
            "the event table was read by an `exam_id` column it does not have, so the "
            "read 400s, degrades to empty, and the shared-window rule never fires")

    def test_the_room_comes_back_in_the_rosters_order(self):
        review = session_review.review_for_exam(
            _FakeSupabase(_exam_reads()),
            {"id": "e1", "title": "UH", "class_ids": ["c1"], "total_questions": 5})
        assert [row["name"] for row in review["room"]] == ["Ayu", "Budi"]


# ── 5. nothing reaches the page by accident ──────────────────────────────────

class TestTheRouteIsGuardedAndTidied:
    @pytest.mark.parametrize("func", ["exam_sessions", "exam_sessions_data"])
    def test_the_merged_routes_are_behind_the_same_guard(self, func):
        decorators = _decorators(_source(), func)
        assert "teacher_or_admin_required" in decorators, (
            f"{func} is not behind the role guard")
        assert "require_school_access" in decorators or "_guard_exam" in (
            ast.get_source_segment(_source(), _function(_source(), func)) or ""), (
            f"{func} does not check that the exam is the caller's")

    def test_the_results_page_links_to_the_one_surviving_place(self):
        text = RESULTS.read_text(encoding="utf-8")
        assert "/sessions" in text, "the results page does not link to the session page"
        for dead in ("/proctoring", "/cheat-analysis"):
            assert dead not in text, f"the results page still links to {dead}"

    def test_the_breadcrumb_knows_the_page(self):
        text = BASE.read_text(encoding="utf-8")
        assert "'sessions'" in text, "the breadcrumb has no label for the session page"
