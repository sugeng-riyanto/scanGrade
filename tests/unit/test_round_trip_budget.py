"""The round-trip budget, held by a test.

One Supabase call costs roughly 100-165 ms from this deployment, so the number of
calls a page makes *is* its latency budget — and the same row was being paid for
two, three and four times on the pages a load test drives hardest:

* the student dashboard read `profiles` four times (three of them for a
  ``class_id`` the session already carried) and `submissions` three times;
* the exam list asked for the retracted set and the answered set in two queries
  over the same rows;
* `teacher_assignments` was counted twice with an identical filter, the second
  answer overwriting the first, for a number every student in a school shares.

None of that is visible in a test that only checks a page renders, so this file
checks the two things that make the fix real and keep it: the caches behave, and
the duplicated shapes are gone from the route bodies.

The static half is deliberate. A behavioural test would need a logged-in
session and the live database; a regex over the route body is what fails on the
next person who pastes a third `select("exam_id")` back in.
"""
import re
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "app"


# ── a supabase stand-in that records what was asked ─────────────────────────


class _Result:
    def __init__(self, data, count=None):
        self.data = data
        self.count = count


class _Query:
    def __init__(self, log, table):
        self.log = log
        self.table = table
        self._single = False
        self._count = None

    def select(self, *_cols, **kw):
        self.log.append(self.table)
        if kw.get("count"):
            self._count = 3
        return self

    def eq(self, *_a, **_k):
        return self

    def in_(self, *_a, **_k):
        return self

    def neq(self, *_a, **_k):
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def single(self):
        self._single = True
        return self

    def execute(self):
        if self._count is not None:
            return _Result(data=[], count=self._count)
        return _Result(data={"ok": True} if self._single else [], count=None)


class _Client:
    def __init__(self, log):
        self.log = log

    def table(self, name):
        return _Query(self.log, name)


@pytest.fixture
def app():
    from app import create_app
    return create_app("app.config.TestingConfig")


@pytest.fixture
def fake_db():
    """Patch the client the cache helpers import at call time."""
    log = []
    with patch("app.utils.supabase_client.get_supabase", lambda: _Client(log)):
        yield log


# ── the caches ──────────────────────────────────────────────────────────────


class TestTheTwoLifetimes:
    def test_memo_answers_the_second_caller_without_running_again(self, app):
        from app.utils.req_cache import memo

        runs = []

        def factory():
            runs.append(1)
            return {"row": True}

        with app.test_request_context("/"):
            assert memo("k", factory) == {"row": True}
            assert memo("k", factory) == {"row": True}
        assert len(runs) == 1, "the second reader paid for the row again"

    def test_memo_does_not_survive_the_request(self, app):
        """A per-request store must not become a per-process one.

        The cache this replaced keyed a module-level dict by `id(request)` and
        never cleared it: it grew for the life of the worker, and could hand a
        request another request's row when CPython reused the address.
        """
        from app.utils.req_cache import memo

        runs = []
        for _ in range(2):
            with app.test_request_context("/"):
                memo("shared-key", lambda: runs.append(1) or 1)
        assert len(runs) == 2, "a value from one request was served to the next"

    def test_ttl_is_shared_across_requests_and_invalidate_drops_it(self, fake_db):
        from app.utils.req_cache import invalidate_school, school_row

        first = school_row("school-a")
        second = school_row("school-a")
        assert first == second
        assert fake_db == ["schools"], f"one row cost {len(fake_db)} queries"

        invalidate_school("school-a")
        school_row("school-a")
        assert len(fake_db) == 2, "a write must not leave the old row cached"

    def test_the_school_row_serves_the_header_and_the_feature_flag(self, fake_db):
        """Two readers, one query — this is where three selects became one."""
        from app.utils.req_cache import school_row

        row = school_row("school-b")
        assert isinstance(row, dict)
        assert len(fake_db) == 1

    def test_the_subject_count_is_one_query_for_the_whole_school(self, fake_db):
        from app.utils.req_cache import school_subject_count

        assert school_subject_count("school-c") == 3
        assert school_subject_count("school-c") == 3
        assert fake_db == ["teacher_assignments"], f"got {fake_db}"


# ── the route bodies ────────────────────────────────────────────────────────


def _route_body(source, path):
    """The text of the view function registered for `path`."""
    start = source.index(f'@student_bp.route("{path}")')
    nxt = source.find("\n@student_bp.route", start + 10)
    return source[start:nxt if nxt > 0 else len(source)]


class TestTheDashboardAsksOnce:
    @pytest.fixture(scope="class")
    def source(self):
        return (ROOT / "app" / "routes" / "student.py").read_text(encoding="utf-8")

    @pytest.fixture(scope="class")
    def dashboard(self, source):
        return _route_body(source, "/dashboard")

    def test_it_does_not_re_read_the_profile_for_the_class_id(self, dashboard):
        assert 'table("profiles")' not in dashboard, (
            "the session carries class_id and school_id — reading the profile here "
            "is a round-trip for a column the token check already fetched")

    def test_it_reads_the_submissions_once(self, dashboard, source):
        assert 'table("submissions")' not in dashboard, (
            "every submissions read on this page goes through the one helper")
        assert dashboard.count("_student_submissions(") == 1, (
            "the available list, the retracted set and the result cards come from "
            "one read: `status` is on the row")
        helper = source[source.index("def _student_submissions("):]
        helper = helper[:helper.index("@student_bp.route")]
        assert helper.count('table("submissions")') == 1

    def test_it_counts_the_school_subjects_once(self, dashboard):
        assert dashboard.count('table("teacher_assignments")') == 0, (
            "the count is per school, so it belongs in the shared cache rather "
            "than in the request (it used to be asked twice with one filter)")

    def test_it_does_not_fetch_school_columns_it_never_renders(self, dashboard):
        assert 'table("schools")' not in dashboard, (
            "the school name/logo/features come from the cached school row")


class TestTheExamListAsksOnce:
    @pytest.fixture(scope="class")
    def exam_list(self):
        source = (ROOT / "app" / "routes" / "student.py").read_text(encoding="utf-8")
        return _route_body(source, "/exams")

    def test_it_does_not_re_read_the_profile(self, exam_list):
        assert 'table("profiles")' not in exam_list

    def test_it_asks_for_the_answered_set_once(self, exam_list):
        assert 'select("exam_id")' not in exam_list, (
            "the answered set and the retracted set were two queries over the "
            "same rows; `status` is on the row, so one read answers both")

    def test_it_selects_only_the_columns_the_card_renders(self, exam_list):
        assert 'table("exams").select("*")' not in exam_list, (
            "`select(\"*\")` shipped the whole exam row — including question_canvas "
            "— into every student's page context")


# ── the shapes that must not come back ──────────────────────────────────────


def _code_only(path):
    """The file's code, with comments and string literals removed.

    Both of the notes explaining why `id(request)` is not a cache key would
    otherwise trip a guard that searches for the text.
    """
    import tokenize

    tokens = []
    with path.open("rb") as fh:
        for tok in tokenize.tokenize(fh.readline):
            if tok.type in (tokenize.COMMENT, tokenize.STRING, tokenize.NL):
                continue
            tokens.append(tok.string)
    return "".join(tokens)


class TestTheLeakShapeIsGone:
    def test_no_module_level_cache_keyed_by_request_address(self):
        """`id(request)` is not a cache key.

        CPython reuses addresses, so the entry can be another request's data, and
        nothing ever evicts it — the dict grows for the life of the worker.
        """
        offenders = [str(path.relative_to(ROOT)) for path in APP.rglob("*.py")
                     if "id(request)" in _code_only(path)]
        assert not offenders, (
            "cache keyed by the request object's address — use g (per request) or "
            "a row id (shared) instead:\n  " + "\n  ".join(offenders))


class TestEveryCacheHasAnInvalidator:
    """A cache whose writer does not invalidate is a stale page after an edit."""

    WRITERS = ["app/routes", "app/services"]

    @pytest.mark.parametrize("name", [
        "invalidate_school",
        "invalidate_class",
        "invalidate_boards",
        "invalidate_teacher_assignments",
    ])
    def test_it_is_called_somewhere_outside_its_own_module(self, name):
        callers = []
        for folder in self.WRITERS:
            for path in (APP / folder.split("/", 1)[1]).rglob("*.py"):
                if name in path.read_text(encoding="utf-8", errors="replace"):
                    callers.append(str(path.relative_to(ROOT)))
        assert callers, (
            f"{name}() is never called: whatever writes that row leaves every "
            f"reader looking at the old one until the TTL expires")


def test_the_shared_rows_are_not_cached_forever():
    """A school's row may be reused across a class period, not across a term."""
    from app.utils import req_cache

    assert 0 < req_cache.SCHOOL_TTL <= 600
    assert 0 < req_cache.CLASS_TTL <= 1800
    assert 0 < req_cache.ASSIGNMENT_TTL <= 300
    assert 0 < req_cache.BOARD_TTL <= 120
