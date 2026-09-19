"""How many Supabase round-trips a render spent, and who is allowed to say so.

Why this exists
---------------
The deploy's release performance gate compares a release with the release before it
on **response time**. That number is a symptom: it says a page got slower, not what
made it slower, and a page can gain ten queries and still answer inside the latency
slack on a quiet box — then be the page that falls over at 500 concurrent students.
Round-trips are the cause, they are an integer, and they are what an N+1 multiplies.

So every request counts the queries it sends and says so in a response header:

    X-Supabase-Roundtrips: 7

These tests hold the four things that make that number trustworthy:

1. it is on **every** response, including a page that spent nothing (0, not missing —
   "unknown" and "free" are different answers, and a missing header would read as a
   harness fault);
2. it counts **attempts**, so a retried read costs two — a release that makes retries
   routine is a release that pays the database twice;
3. it is armed before any other request hook runs, so no query is spent outside the
   count;
4. it is scoped to the greenlet/thread serving the request, and does **not** follow
   work handed to another thread. That boundary is real; it is asserted here rather
   than assumed, and it is acceptable because the app's fan-outs are bulk POST
   operations, not the page renders the gate measures.
"""
import contextvars
import importlib.util
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import httpx

ROOT = Path(__file__).resolve().parents[2]

from app import create_app                                  # noqa: E402
from app.utils import query_meter                           # noqa: E402
from app.utils.cache import cache_delete, cache_get, cache_set   # noqa: E402
from app.utils.supabase_retry import RetryingClient         # noqa: E402


def _load_harness():
    """The load harness, by path — it lives at the repo root, not in a package."""
    spec = importlib.util.spec_from_file_location("loadtest_concurrent",
                                                  ROOT / "loadtest_concurrent.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["loadtest_concurrent"] = module
    spec.loader.exec_module(module)
    return module


harness = _load_harness()


# ── a fake data client, so a real request can query something ────────────────

class FakeQuery:
    """A postgrest builder: chainable, and scripted to fail on cue."""

    def __init__(self, method="GET", outcomes=None):
        self.http_method = method
        self.calls = 0
        self._outcomes = list(outcomes or [])

    def _step(self, *a, **k):
        return self

    select = _step
    insert = _step
    update = _step
    eq = _step
    order = _step
    limit = _step

    def execute(self):
        self.calls += 1
        outcome = self._outcomes.pop(0) if self._outcomes else SimpleNamespace(data=[])
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class FakeSupabase:
    """One query object, handed out for whatever table is asked for."""

    def __init__(self, query):
        self.query = query

    def table(self, name):
        return self.query

    def rpc(self, name, params=None):
        return self.query


def app_with(routes):
    """A testing app whose data client is a fake, plus the routes to poke at it."""
    app = create_app("testing")
    app.config["WTF_CSRF_ENABLED"] = False
    for rule, view in routes:
        app.add_url_rule(rule, view.__name__, view)
    return app


def query_of(app, method="GET", outcomes=None):
    """Install a fake data client and return the query object behind it."""
    query = FakeQuery(method, outcomes)
    app.extensions["supabase"] = RetryingClient(FakeSupabase(query))
    return query


# ── 1. the number is always on the response ─────────────────────────────────

class TestTheHeader:
    def test_a_page_that_spent_nothing_reports_zero_not_nothing(self):
        app = app_with([("/__free", lambda: "ok")])
        resp = app.test_client().get("/__free")
        assert resp.headers.get(query_meter.HEADER) == "0", (
            "a missing header on a cheap page reads as a broken harness; the answer "
            "has to be 0")

    def test_a_render_that_queries_the_database_reports_what_it_spent(self):
        holder = {}

        def probe():
            get = holder["app"].extensions["supabase"]
            get.table("profiles").select("id").execute()
            get.table("exams").select("id").execute()
            get.table("submissions").select("id").execute()
            return "ok"

        app = app_with([("/__three", probe)])
        holder["app"] = app
        query_of(app)
        resp = app.test_client().get("/__three")
        assert resp.status_code == 200
        assert resp.headers.get(query_meter.HEADER) == "3"

    def test_the_count_is_on_the_response_a_student_would_actually_get(self):
        """Not just a probe route: the real pages carry it too."""
        app = app_with([])
        resp = app.test_client().get("/auth/login")
        value = resp.headers.get(query_meter.HEADER)
        assert value is not None and value.isdigit(), (
            f"/auth/login did not report its round-trips: {value!r}")


# ── 2. an attempt is a round-trip, so a retry costs two ──────────────────────

class TestAttemptsAreCounted:
    def test_a_retried_read_costs_two_roundtrips(self):
        app = app_with([])

        def probe():
            get = app.extensions["supabase"]
            get.table("exams").select("id").execute()
            return "ok"

        app.add_url_rule("/__retry", "probe_retry", probe)
        query_of(app, "GET", [httpx.RemoteProtocolError("Server disconnected")])
        resp = app.test_client().get("/__retry")
        assert resp.status_code == 200, "the read should have healed itself"
        assert resp.headers.get(query_meter.HEADER) == "2", (
            "the retry is a round-trip the database served, so it has to be counted")

    def test_a_write_costs_one_and_is_not_retried(self):
        app = app_with([])

        def probe():
            get = app.extensions["supabase"]
            get.table("submissions").insert({"score": 10}).execute()
            return "ok"

        app.add_url_rule("/__write", "probe_write", probe)
        query = query_of(app, "POST", [httpx.RemoteProtocolError("Server disconnected")])
        resp = app.test_client().get("/__write")
        assert resp.status_code >= 500, "a failed write still reports the failure"
        assert query.calls == 1, "a write was re-sent after a dropped connection"
        assert resp.headers.get(query_meter.HEADER) == "1", (
            "the attempt happened, so it is a round-trip regardless of the outcome")


# ── 3. nothing is spent outside the count ────────────────────────────────────

class TestTheMeterIsArmedFirst:
    def test_a_hook_registered_after_the_app_sees_an_armed_meter(self):
        """`begin()` must run before every other hook, or the first query is free."""
        seen = {}
        app = create_app("testing")

        @app.before_request
        def later_hook():
            # Registered last, so it runs after init_request. If arming happened
            # anywhere later, this would see None — and so would every query the
            # hook itself made.
            seen["spent"] = query_meter.spent()

        app.test_client().get("/auth/login")
        assert seen["spent"] == 0, (
            f"the meter was not armed before the other request hooks: {seen['spent']!r}")

    def test_the_meter_is_a_no_op_outside_a_request(self):
        """A Celery task or a script queries the same client with no request in flight.

        Run on a thread of its own: the test client drives requests in *this*
        thread's context, so `begin()` from an earlier test would still be visible
        here. A fresh context is what "no request in flight" actually means.
        """
        seen = {}

        def fresh_context():
            seen["before"] = query_meter.spent()
            query_meter.trip()      # must not raise, and must not accumulate
            seen["after"] = query_meter.spent()
            seen["header"] = query_meter.header_value()

        thread = threading.Thread(target=fresh_context)
        thread.start()
        thread.join()

        assert seen["before"] is None, "an unarmed meter must not report a count"
        assert seen["after"] is None
        assert seen["header"] is None, (
            "nothing to report must be None, so the header is only sent when it means "
            "something")


# ── 4. the boundary, asserted rather than assumed ────────────────────────────

class TestTheScopeOfTheCount:
    def test_the_count_is_isolated_between_contexts(self):
        """Two greenlets/threads must not share one counter.

        gunicorn runs `gevent`, and this is the property that makes a per-request
        number possible at all rather than a number for the whole worker.
        """
        cv = contextvars.ContextVar("probe", default=None)
        cv.set(1)
        seen = {}

        def other():
            seen["start"] = cv.get()      # a fresh context, not the parent's
            cv.set(99)

        import greenlet
        greenlet.greenlet(other).switch()
        assert seen["start"] is None, (
            "greenlets share a contextvars context on this interpreter, so the meter "
            "would report the whole worker's queries as one request's")
        assert cv.get() == 1

    def test_work_handed_to_another_thread_is_not_counted(self):
        """The documented boundary: a ThreadPoolExecutor task is a different context.

        Not a defect to fix — the app's fan-outs are bulk POST work, and the gate
        measures page renders. This test exists so the boundary is a fact with a test
        behind it rather than a sentence in a docstring.
        """
        app = app_with([])

        def probe():
            get = app.extensions["supabase"]
            get.table("a").select("id").execute()                 # inline: counted

            def in_thread():
                get.table("b").select("id").execute()             # elsewhere: not

            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(in_thread).result()
            return "ok"

        app.add_url_rule("/__fan", "probe_fan", probe)
        query_of(app)
        resp = app.test_client().get("/__fan")
        assert resp.headers.get(query_meter.HEADER) == "1", (
            "the header must count the render's own queries, not every query anywhere")


# ── 4b. a cache hit still reports what the page costs ────────────────────────

class TestACacheHitStillReportsTheCost:
    """A warm cache must not make a page look free.

    Measured on the running app before this: `/student/dashboard` answered 12
    requests in 14 seconds with **0** queries every time, because the 30-second entry
    was warmed by the login redirect and never expired. The gate would have been
    blind to the busiest page a student has. So the entry remembers what it cost to
    build, and a hit replays it — the header answers "what does this page cost when
    it does its work", which is the same number warm or cold.
    """

    def _routes(self, key):
        def fill():
            get = fill.app.extensions["supabase"]
            get.table("exams").select("id").execute()
            get.table("submissions").select("id").execute()
            cache_set(key, {"rows": []}, ttl=60)
            return "filled"

        def hit():
            cached = cache_get(key)
            return "hit" if cached is not None else "miss"

        def drop():
            cache_delete(key)
            return "dropped"

        return fill, hit, drop

    def test_a_hit_reports_the_cost_the_entry_was_built_with(self):
        key = f"probe:{uuid.uuid4().hex}"
        fill, hit, _ = self._routes(key)
        app = app_with([("/__fill", fill), ("/__hit", hit)])
        fill.app = app
        query_of(app)
        client = app.test_client()

        assert client.get("/__fill").headers.get(query_meter.HEADER) == "2"
        after = client.get("/__hit")
        assert after.status_code == 200
        assert after.headers.get(query_meter.HEADER) == "2", (
            "a cache hit reported 0, so a release that adds queries to a cached page "
            "would pass the gate unnoticed")

    def test_a_miss_reports_nothing_and_a_deleted_entry_stops_reporting_it(self):
        key = f"probe:{uuid.uuid4().hex}"
        fill, hit, drop = self._routes(key)
        app = app_with([("/__fill", fill), ("/__hit", hit), ("/__drop", drop)])
        fill.app = app
        query_of(app)
        client = app.test_client()

        assert client.get("/__hit").headers.get(query_meter.HEADER) == "0"
        client.get("/__fill")
        client.get("/__drop")
        stale = client.get("/__hit")
        assert stale.headers.get(query_meter.HEADER) == "0", (
            "a deleted entry kept reporting its cost, so the number would outlive the "
            "cache it described")

    def test_an_entry_built_outside_a_request_reports_nothing(self):
        """A Celery task can warm the cache too, and it has no request to charge."""
        key = f"probe:{uuid.uuid4().hex}"
        rebuilt_outside_a_request(key)

        def hit():
            return "hit" if cache_get(key) is not None else "miss"

        app = app_with([("/__hit", hit)])
        query_of(app)
        resp = app.test_client().get("/__hit")
        assert resp.headers.get(query_meter.HEADER) == "0", (
            "an unmeasured build must not be reported as free *or* as a cost")

    def test_an_entry_rebuilt_outside_a_request_does_not_inherit_an_old_cost(self):
        """`cache_delete` has to drop the cost along with the value.

        Otherwise a key that was measured once, deleted, and then refilled where no
        meter is armed — a Celery task, a warmer script — keeps reporting the old
        entry's cost. That is a number about code that may no longer exist, and it is
        the shape the gate would then refuse a release for.
        """
        key = f"probe:{uuid.uuid4().hex}"
        fill, hit, drop = self._routes(key)
        app = app_with([("/__fill", fill), ("/__hit", hit), ("/__drop", drop)])
        fill.app = app
        query_of(app)
        client = app.test_client()

        assert client.get("/__fill").headers.get(query_meter.HEADER) == "2"
        client.get("/__drop")
        rebuilt_outside_a_request(key)

        assert client.get("/__hit").headers.get(query_meter.HEADER) == "0", (
            "the entry was rebuilt without a meter, so it has no cost to report")


def rebuilt_outside_a_request(key):
    """Refill a key on a thread of its own, where no meter is armed.

    A thread, not an inline call: the test client drives its requests in *this*
    thread's context, so an inline `cache_set` would still see the `0` left behind by
    the previous request and record a cost of zero rather than none — and a test that
    cannot tell "no cost" from "a cost of zero" cannot catch the stale-cost defect
    this is here for.
    """
    thread = threading.Thread(target=cache_set, args=(key, {"rows": []}), kwargs={"ttl": 60})
    thread.start()
    thread.join()


# ── 5. the two halves agree on the header's name ─────────────────────────────

class TestTheHarnessReadsWhatTheAppWrites:
    def test_the_harness_declares_the_apps_header(self):
        assert harness.ROUNDTRIP_HEADER == query_meter.HEADER, (
            "the harness reads its own copy of the header name so it can run without "
            "importing the app; the copy has drifted and the gate would measure nothing")


# ── 6. what the harness records, and what the gate will see ──────────────────

class _Resp:
    def __init__(self, status=200, body=b"x" * 100, roundtrips=None):
        self.status_code = status
        self.content = body
        self.headers = {} if roundtrips is None else {harness.ROUNDTRIP_HEADER: str(roundtrips)}


class TestTheHarnessRecordsCost:
    def _results_with(self, entries):
        r = harness.Results()
        for key, resp in entries:
            r.rec(key, 0.0, resp)
        return r

    def test_a_page_records_its_bytes_and_its_queries(self):
        r = self._results_with([("GET /student/dashboard", _Resp(body=b"y" * 4096,
                                                                 roundtrips=7))])
        row = harness.summary(r, 1.0, 1)["per_endpoint"]["GET /student/dashboard"]
        assert row["bytes_p50"] == 4096
        assert row["roundtrips_p50"] == 7

    def test_a_page_that_did_not_answer_200_records_no_cost(self):
        """A 302 or a 500 has a body of nothing.

        Averaging those in would let a page that broke look *cheap* — the exact
        measurement that hides the defect it was taken to find.
        """
        r = self._results_with([("GET /student/dashboard", _Resp(status=500, body=b"",
                                                                 roundtrips=0))])
        row = harness.summary(r, 1.0, 1)["per_endpoint"]["GET /student/dashboard"]
        assert row["bytes_p50"] is None
        assert row["roundtrips_p50"] is None, (
            "a failed page must read as not-measured, never as zero queries")

    def test_a_missing_header_is_not_read_as_zero_queries(self):
        r = self._results_with([("GET /student/dashboard", _Resp(body=b"x" * 10))])
        row = harness.summary(r, 1.0, 1)["per_endpoint"]["GET /student/dashboard"]
        assert row["bytes_p50"] == 10
        assert row["roundtrips_p50"] is None, (
            "an app too old to send the header must not look like a free page")

    def test_heaviest_page_is_the_largest_payload(self):
        r = self._results_with([("GET /student/dashboard", _Resp(body=b"x" * 100)),
                                ("GET /teacher/exams", _Resp(body=b"x" * 900))])
        key, row = harness.heaviest_page(harness.summary(r, 1.0, 1))
        assert key == "GET /teacher/exams" and row["bytes_p50"] == 900

    def test_a_page_that_never_answered_is_not_the_heaviest(self):
        r = self._results_with([("GET /student/dashboard", _Resp(status=500, body=b"")),
                                ("GET /teacher/exams", _Resp(body=b"x" * 42))])
        key, _ = harness.heaviest_page(harness.summary(r, 1.0, 1))
        assert key == "GET /teacher/exams"

    def test_nothing_to_grade_reads_as_none_not_as_a_pass(self):
        assert harness.heaviest_page({"per_endpoint": {}}) is None
        assert harness.heaviest_page({"per_endpoint": {"/health": {"bytes_p50": 3}}}) is None, (
            "the gate is about signed-in pages, not every endpoint the run touched")
