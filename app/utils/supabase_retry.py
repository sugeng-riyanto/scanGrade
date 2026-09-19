"""Every read on the database client, retried when the transport fails under it.

Why this exists
---------------
Supabase answers over HTTP/2 on a connection the client keeps alive, and the server
closes that connection. The client does not notice, so the *next* query is written to
a socket that is already gone and postgrest raises

    httpx.RemoteProtocolError: Server disconnected

on a query that is perfectly correct. Measured against the live project: 4 of 10
identical ``exams`` reads failed this way.

That one fault shows up in two places that look unrelated:

* a teacher's results page answering **500** with nothing wrong anywhere — measured on
  the running server, ``app/routes/teacher.py`` raised it twice in 28 seconds while the
  data was fine;
* the release performance gate counting **5xx against a baseline that had none**, which
  rolls a healthy release back for a fault that predates it. That is how a page that
  500s occasionally turns into deploys that fail occasionally.

The remedy was already here — ``read_with_retry`` in ``app/utils/helpers.py`` — but it
was applied at three call sites out of 679, and the pages nobody had retried yet were
exactly the ones that failed. A rule that has to be remembered at every call site is a
rule that will be applied at three.

So the retry belongs on the client, not on the call site: wrap it once and
``get_supabase().table(...)...execute()`` retries by default. The hand-written
``read_with_retry`` sites keep working unchanged — a retry inside a retry costs one
extra attempt and changes no answer.

Only reads are retried
----------------------
A dropped connection does not say whether the server ran the statement, so re-sending a
write can apply it twice. The builder carries ``http_method``, so a GET/HEAD query —
every ``select`` — is retried and a POST/PATCH/DELETE is not. A write that loses its
connection still reports the failure, which is the safe direction to be wrong in.

Nothing here changes what a caller sees except that ``Server disconnected`` stops
reaching it. A genuine API error (a bad column, an RLS refusal) is not a transport
error and is raised on the first attempt, unchanged.

One more thing happens here: every attempt is counted, so the page a request renders
reports how many round-trips it cost. That is free at this seam — the wrapper already
sees each query — and it is the number the release performance gate compares, because
round-trips are the cause of which response time is only the symptom. See
``app/utils/query_meter.py``.
"""

from __future__ import annotations

from app.utils import query_meter
from app.utils.helpers import read_with_retry

# Methods whose request can be sent again without changing the database. HEAD is
# included because postgrest uses it for counts, and it is a read by definition.
_IDEMPOTENT = ("GET", "HEAD")


def is_read(query) -> bool:
    """Is this postgrest query safe to run a second time?"""
    return getattr(query, "http_method", None) in _IDEMPOTENT


def wrap_query(obj):
    """Wrap a query builder; pass anything else through untouched.

    The test is "has an ``execute``", not "has an ``http_method``": the object
    ``table()`` returns is a plain ``SyncRequestBuilder`` that carries **no**
    ``http_method`` — the method only appears on the builder ``select()``/``insert()``
    produce. Requiring ``http_method`` here left ``table()`` unwrapped, and because
    that first step was unwrapped every later step was too, so the wrapper did nothing
    at all on the real client while passing every test written against a fake that had
    ``http_method`` from the start. Whether a query may be retried is decided in
    ``is_read`` at ``execute()`` time, which is the only moment the method is known.

    postgrest's chained methods return the builder itself, so wrapping the result of
    each step is what keeps a multi-step chain wrapped all the way to ``execute()``. A
    method that returns something that is not a builder — ``csv()`` returns text — is
    handed back as it is.
    """
    if hasattr(obj, "execute"):
        return RetryingQuery(obj)
    return obj


class RetryingQuery:
    """One postgrest query, a retry for the transport failing under it, and a count.

    The count is a round-trip per *attempt*, not per query: a retry is a round-trip
    the database really served, and a release that quietly makes retries routine is
    a release that costs the same database twice. Wrapping the callable is how the
    attempt gets counted, because `read_with_retry` re-invokes it for every try.
    """

    __slots__ = ("_query",)

    def __init__(self, query) -> None:
        self._query = query

    def _counted_execute(self):
        query_meter.trip()
        return self._query.execute()

    def execute(self):
        if is_read(self._query):
            return read_with_retry(self._counted_execute)
        query_meter.trip()
        return self._query.execute()

    def __getattr__(self, name):
        attribute = getattr(self._query, name)
        if not callable(attribute):
            return attribute

        def step(*args, **kwargs):
            return wrap_query(attribute(*args, **kwargs))

        return step


class RetryingClient:
    """The Supabase client, with every read on it retried.

    Only ``table`` and ``rpc`` build queries; everything else — ``auth``, ``storage``,
    ``postgrest``, ``functions`` — is reached unchanged through ``__getattr__``, so
    this is a narrow wrap rather than a reimplementation of the client.
    """

    __slots__ = ("_client",)

    def __init__(self, client) -> None:
        self._client = client

    def table(self, *args, **kwargs):
        # Wrapped unconditionally, not through wrap_query: the object table() returns
        # carries no http_method, and an unwrapped first step unwraps the whole chain.
        return RetryingQuery(self._client.table(*args, **kwargs))

    def rpc(self, *args, **kwargs):
        return RetryingQuery(self._client.rpc(*args, **kwargs))

    def __getattr__(self, name):
        return getattr(self._client, name)
