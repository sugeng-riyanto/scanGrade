"""The dropped Supabase connection stops reaching a view.

What this protects
------------------
Supabase closes a keep-alive HTTP/2 connection, the client does not notice, and the
next query is written to a dead socket: ``httpx.RemoteProtocolError: Server
disconnected`` on a query that is correct. Measured with the live project, 4 of 10
identical ``exams`` reads failed this way.

Two consequences, and the second is why this file exists:

* ``/teacher/results`` answered 500 twice in 28 seconds while the data was fine;
* the deploy's release performance gate counts 5xx against a baseline that had none,
  so a healthy release was rolled back for a fault that predates it. Reproduced on
  this machine: two of three runs of the *unchanged* code were refused.

The rule is applied on the client now rather than by hand at each call site, because
at call sites it was applied 3 times out of 679 — and the pages nobody had retried
were exactly the ones that failed. These tests hold each half of that:

* a read retries and the caller never sees the transport error;
* a write does not, because a dropped connection does not say whether the server ran
  the statement, and re-sending an insert can apply it twice;
* the retry survives a chained call (``table().select().eq().execute()``), which is
  the shape every real query has — a retry that only worked on the first step would
  pass a naive test and fix nothing;
* a genuine API error is raised on the first attempt, so this cannot become a blanket
  "try again" that hides a bad column.
"""

import httpx
import pytest

from app.utils.supabase_retry import RetryingClient, RetryingQuery, is_read, wrap_query


class FakeQuery:
    """A postgrest builder stand-in: chainable, and scripted to fail on cue."""

    def __init__(self, method="GET", outcomes=None):
        self.http_method = method
        self.calls = 0
        self._outcomes = list(outcomes or [])

    def _step(self, *a, **k):
        return self

    select = _step
    eq = _step
    order = _step
    limit = _step
    single = _step
    maybe_single = _step
    csv = property(lambda self: "a,b\n1,2\n")

    def execute(self):
        self.calls += 1
        outcome = self._outcomes.pop(0) if self._outcomes else "answered"
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class FakeClient:
    def __init__(self, query):
        self._query = query
        self.auth = "the auth sub-client"

    def table(self, name):
        return self._query

    def rpc(self, name, params=None):
        return self._query


def dropped():
    """The exact exception the live project raises, not a generic one."""
    return httpx.RemoteProtocolError("Server disconnected")


# ── a read heals itself ──────────────────────────────────────────────────────

class TestAReadIsRetried:

    def test_a_get_survives_the_first_dropped_connection(self):
        query = FakeQuery("GET", [dropped(), "the rows"])
        assert wrap_query(query).execute() == "the rows"
        assert query.calls == 2, "the query was not re-sent"

    def test_a_head_count_survives_it_too(self):
        query = FakeQuery("HEAD", [dropped(), "the count"])
        assert wrap_query(query).execute() == "the count"

    def test_the_retry_survives_a_chained_query(self):
        """The real shape: ``table().select().eq().execute()``.

        A wrapper installed only on the object ``table()`` returns would be lost the
        moment ``select()`` handed back the raw builder, so this is the assertion that
        the fix actually covers the call sites it claims to.
        """
        query = FakeQuery("GET", [dropped(), dropped(), "the rows"])
        chain = wrap_query(query).select("id,title").eq("teacher_id", "t-1").order("x")
        assert chain.execute() == "the rows"
        assert query.calls == 3

    def test_a_read_that_keeps_failing_still_reports(self):
        query = FakeQuery("GET", [dropped()] * 10)
        with pytest.raises(httpx.RemoteProtocolError):
            wrap_query(query).execute()
        assert query.calls >= 2, "it gave up without trying again"


# ── a write is not ───────────────────────────────────────────────────────────

class TestAWriteIsNotRetried:

    @pytest.mark.parametrize("method", ["POST", "PATCH", "PUT", "DELETE"])
    def test_a_write_reports_the_failure_on_the_first_attempt(self, method):
        query = FakeQuery(method, [dropped(), "should never be reached"])
        with pytest.raises(httpx.RemoteProtocolError):
            wrap_query(query).execute()
        assert query.calls == 1, (
            "a write was re-sent after a dropped connection — the server may have "
            "already applied it")

    def test_is_read_says_which_way_a_query_goes(self):
        assert is_read(FakeQuery("GET"))
        assert is_read(FakeQuery("HEAD"))
        assert not is_read(FakeQuery("POST"))
        assert not is_read(object()), "an unknown object must not be treated as a read"


# ── nothing else changes ─────────────────────────────────────────────────────

class TestNothingElseChanges:

    def test_a_real_api_error_is_raised_at_once(self):
        from postgrest.exceptions import APIError

        query = FakeQuery("GET", [APIError({"message": "column does not exist"})])
        with pytest.raises(APIError):
            wrap_query(query).execute()
        assert query.calls == 1, "a bad column was retried, which changes no answer"

    def test_the_client_passes_its_other_halves_through(self):
        client = RetryingClient(FakeClient(FakeQuery("GET")))
        assert client.auth == "the auth sub-client"

    def test_a_method_that_is_not_a_query_is_handed_back_unchanged(self):
        """``csv()`` returns text, not a builder — wrapping it would break it."""
        query = FakeQuery("GET")
        assert wrap_query(query).csv == "a,b\n1,2\n"

    def test_wrap_query_leaves_anything_that_is_not_a_query_alone(self):
        """The guard on ``wrap_query`` is that only builders get wrapped.

        A chained step can return something that is not a builder at all — the text
        ``csv()`` produces, a count, ``None`` — and a wrapper that took those would
        break the caller holding an ``int`` or a ``str``.
        """
        assert wrap_query("a,b\n1,2\n") == "a,b\n1,2\n"
        assert wrap_query(None) is None
        assert wrap_query(7) == 7

    def test_table_and_rpc_are_both_wrapped(self):
        client = RetryingClient(FakeClient(FakeQuery("GET", [dropped(), "rows"])))
        assert isinstance(client.table("exams"), RetryingQuery)
        assert client.rpc("score_exam").execute() == "rows"


# ── postgrest's own builders, not a stand-in ────────────────────────────────

class TestTheRealBuilderShape:
    """The wrapper has to work on postgrest's builders, not only on a fake.

    This is the pair of tests that was missing the first time, and their absence is
    the whole reason the first version of this module did nothing: the stand-in above
    has ``http_method`` from the moment it exists, while the real ``table()`` returns a
    ``SyncRequestBuilder`` that carries no ``http_method`` at all. A wrapper that
    demanded one therefore left the first step unwrapped, which left every later step
    unwrapped too — and every fake-based test still passed.

    No network here: a client is constructed against a placeholder project and no query
    is executed, so this is purely the shape of the objects the app really uses.
    """

    @staticmethod
    def _client():
        from supabase import create_client

        # The key only has to satisfy the client's shape check (jwt-ish); nothing
        # connects, and the URL is never dialled.
        return RetryingClient(create_client("https://example.supabase.co", "a.b.c"))

    def test_table_is_wrapped_before_select_is_called(self):
        assert isinstance(self._client().table("exams"), RetryingQuery), (
            "table() returned the raw builder, so no chain built from it is wrapped")

    def test_a_real_select_chain_is_wrapped_and_reads_as_a_read(self):
        chain = self._client().table("exams").select("id").eq("teacher_id", "t").limit(5)
        assert isinstance(chain, RetryingQuery)
        assert is_read(chain._query), "a real select builder is not recognised as a read"

    def test_a_real_insert_is_wrapped_and_is_not_a_read(self):
        chain = self._client().table("exams").insert({"title": "x"})
        assert isinstance(chain, RetryingQuery)
        assert not is_read(chain._query), (
            "a real insert was classified as a read, so a write would be re-sent")

    def test_a_real_rpc_is_wrapped(self):
        assert isinstance(self._client().rpc("score_exam"), RetryingQuery)


# ── the app actually installs it ─────────────────────────────────────────────

class TestTheAppWrapsItsClient:

    def test_the_extension_is_the_wrapped_client(self):
        """A wrapper nobody installs is a wrapper that fixes nothing.

        Every call site in the app reaches the database through
        ``current_app.extensions["supabase"]``, so this is the one line that decides
        whether the 676 un-retried call sites are fixed.
        """
        from app import create_app

        app = create_app("testing")
        assert isinstance(app.extensions["supabase"], RetryingClient), (
            "the app hands out a raw supabase client, so every call site that is not "
            "hand-wrapped is back to raising Server disconnected")

    def test_get_supabase_returns_that_same_wrapped_client(self):
        from app import create_app
        from app.utils.supabase_client import get_supabase

        app = create_app("testing")
        with app.app_context():
            assert isinstance(get_supabase(), RetryingClient)
