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

    def test_the_extension_is_the_wrapped_client(self, app):
        """A wrapper nobody installs is a wrapper that fixes nothing.

        Every call site in the app reaches the database through
        ``current_app.extensions["supabase"]``, so this is the one line that decides
        whether the 676 un-retried call sites are fixed.
        """
        assert isinstance(app.extensions["supabase"], RetryingClient), (
            "the app hands out a raw supabase client, so every call site that is not "
            "hand-wrapped is back to raising Server disconnected")

    def test_get_supabase_returns_that_same_wrapped_client(self, app):
        from app.utils.supabase_client import get_supabase

        with app.app_context():
            assert isinstance(get_supabase(), RetryingClient)


# ── a write whose reply was lost is settled by one read ─────────────────────

class ScriptedSession:
    """The httpx client postgrest calls, scripted: the write is dropped, the read answers.

    ``session`` on a real builder is the object postgrest's ``execute`` writes through
    and the object a read would be issued on, so swapping it in drives a **real**
    builder with no network — which is the only way to test this fairly. A fake builder
    would have to invent ``path``/``params``/``json``, and those are exactly the things
    the confirmation depends on.
    """

    def __init__(self, *, rows=None, reads=None):
        self._rows = rows if rows is not None else []
        self._reads = list(reads or [])
        self.writes = []
        self.reads = []

    def request(self, method, path, **kw):
        """Every write in this file is dropped — that is the fault being answered."""
        self.writes.append((str(method), path, dict(kw.get("params") or {}), kw.get("json")))
        raise dropped()

    def get(self, path, params=None, headers=None):
        self.reads.append((path, list(params or [])))
        if self._reads:
            outcome = self._reads.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome
        return answered(self._rows)


def answered(rows, status: int = 200):
    """A real ``httpx.Response``, so the rows are parsed the way production parses them."""
    return httpx.Response(status, json=rows,
                          request=httpx.Request("GET", "https://example.supabase.co/rest/v1/x"))


def real_write(session, build):
    """A wrapped write on postgrest's own builder, with its session swapped for the script."""
    from supabase import create_client

    query = build(create_client("https://example.supabase.co", "a.b.c"))
    query.session = session
    return wrap_query(query)


def a_patch(client):
    return client.table("profiles").update({"full_name": "Ani"}).eq("id", "u1")


def a_delete(client):
    return client.table("classes").delete().eq("id", "c1")


class TestAWriteWhoseReplyWasLostIsConfirmedByARead:
    """A dropped connection does not say whether the statement ran. A read does.

    Reported live as ``Gagal: Server disconnected`` on an admin CRUD page: the row had
    been written, the reply was lost, and the operator was told the change failed — so
    they did it again. Re-sending the write is the one thing that must not happen (it is
    how an insert lands twice), which leaves the read: the same recovery this repo had
    already hand-written twice, in ``user_preferences.save`` and ``analysis_share.create``.
    """

    def test_a_patch_that_landed_is_reported_as_done(self):
        session = ScriptedSession(rows=[{"id": "u1", "full_name": "Ani"}])
        out = real_write(session, a_patch).execute()
        assert out.data == [{"id": "u1", "full_name": "Ani"}], out.data
        assert len(session.writes) == 1, (
            "the write was re-sent — a dropped reply does not say the statement ran")
        assert len(session.reads) == 1, "nothing asked whether the write landed"
        assert session.reads[0][1] == [("id", "eq.u1")], session.reads[0][1]

    def test_a_patch_that_did_not_land_is_still_a_failure(self):
        session = ScriptedSession(rows=[{"id": "u1", "full_name": "Budi"}])
        with pytest.raises(httpx.RemoteProtocolError):
            real_write(session, a_patch).execute()

    def test_a_partly_applied_patch_is_not_reported_as_done(self):
        """One statement is atomic, so a row that does not carry the patch means the
        statement did not run — reporting the others as done would be a guess."""
        session = ScriptedSession(rows=[{"id": "u1", "full_name": "Ani"},
                                        {"id": "u2", "full_name": "Budi"}])
        with pytest.raises(httpx.RemoteProtocolError):
            real_write(session, a_patch).execute()

    def test_a_delete_that_landed_is_reported_as_done(self):
        session = ScriptedSession(rows=[])
        out = real_write(session, a_delete).execute()
        assert out.data == [], out.data
        assert session.reads[0][1][:1] == [("id", "eq.c1")], session.reads[0][1]
        assert ("limit", "1") in session.reads[0][1], (
            "the confirming read was not bounded, so a delete could pull the whole table")

    def test_a_delete_that_did_not_land_is_still_a_failure(self):
        session = ScriptedSession(rows=[{"id": "c1"}])
        with pytest.raises(httpx.RemoteProtocolError):
            real_write(session, a_delete).execute()

    def test_the_confirming_read_is_itself_retried(self):
        """The recovery is a read, and a read is the thing that is retried here."""
        session = ScriptedSession(rows=[{"id": "u1", "full_name": "Ani"}],
                                  reads=[dropped()])
        assert real_write(session, a_patch).execute().data[0]["full_name"] == "Ani"
        assert len(session.reads) == 2, "the confirming read was not retried"

    def test_a_timestamp_written_in_another_spelling_still_confirms(self):
        """The caller writes an ISO string; Postgres echoes its own. Comparing the two
        as text would report a write that landed as lost — the very bug being fixed."""
        written = "2026-09-29T15:26:08.498522+00:00"
        session = ScriptedSession(rows=[{"id": "u1",
                                         "password_changed_at": "2026-09-29T15:26:08.498522Z"}])
        out = real_write(
            session,
            lambda c: c.table("profiles").update({"password_changed_at": written}).eq("id", "u1"),
        ).execute()
        assert out.data, "a timestamp in another spelling was read as a lost write"


class TestWhatIsNeverConfirmed:
    """The confirmation is only offered where it means something."""

    def test_an_insert_is_never_confirmed(self):
        """An insert has no filter, so there is no row to ask about — and the insert
        that landed twice because it was re-sent is the reason writes are not retried."""
        session = ScriptedSession(rows=[{"id": "x"}])
        with pytest.raises(httpx.RemoteProtocolError):
            real_write(session, lambda c: c.table("classes").insert({"name": "7A"})).execute()
        assert session.reads == [], "an insert was confirmed by a read it cannot make"

    def test_an_unfiltered_update_is_never_confirmed(self):
        """With no filter the only honest read is the whole table, and a guess about a
        table-wide update is worse than the error."""
        session = ScriptedSession(rows=[{"full_name": "Ani"}])
        with pytest.raises(httpx.RemoteProtocolError):
            real_write(session, lambda c: c.table("profiles").update({"full_name": "Ani"})).execute()
        assert session.reads == [], "an unfiltered update triggered a table-wide read"

    def test_a_patch_that_moves_the_column_it_filters_on_is_never_confirmed(self):
        """``update({"status": "done"}).eq("status", "pending")`` — after the write the
        filter matches nothing, so the read would report a landed write as lost. The
        invigilation retake decision has exactly this shape."""
        session = ScriptedSession(rows=[])
        with pytest.raises(httpx.RemoteProtocolError):
            real_write(session, lambda c: c.table("exam_retake_requests")
                       .update({"status": "approved"}).eq("status", "pending")).execute()
        assert session.reads == [], "a conditional update was confirmed by a filter it moved"

    def test_a_real_api_error_is_still_raised_at_once(self):
        from postgrest.exceptions import APIError

        session = ScriptedSession()
        session.request = lambda method, path, **kw: (_ for _ in ()).throw(
            APIError({"message": "column does not exist"}))
        with pytest.raises(APIError):
            real_write(session, a_patch).execute()
        assert session.reads == [], "a server answer was second-guessed with a read"
