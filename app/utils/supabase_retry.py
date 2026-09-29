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

Only reads are retried, and a lost write reply is settled by a read
----------------------------------------------------------------
A dropped connection does not say whether the server ran the statement, so re-sending a
write can apply it twice. The builder carries ``http_method``, so a GET/HEAD query —
every ``select`` — is retried and a POST/PATCH/DELETE is not.

A write that loses its connection used to report the failure outright. It often was not
one: reported as ``Gagal: Server disconnected`` on an admin CRUD page, where the row had
been written, the *reply* was dropped, and the operator was told the change failed — so
they did it again. Re-sending is the one thing that must not happen, which leaves the
read, and this repo had already hand-written that recovery twice
(``user_preferences.save``, ``analysis_share.create``).

So at ``execute()`` time a PATCH or DELETE that fails with a transport error asks one
question — read the rows this filter names and see whether they carry the patch — and
reports success only when the answer is yes. Everything the read cannot settle is
unchanged: an insert (no filter), an unfiltered write (would need a table-wide read), a
patch that moves the column it filters on (the filter no longer matches, so the read
would call a landed write lost), and any genuine ``APIError``, which is the server
answering and is raised at once.

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

import json
from datetime import datetime

import httpx

from app.utils import query_meter
from app.utils.helpers import read_with_retry

# Methods whose request can be sent again without changing the database. HEAD is
# included because postgrest uses it for counts, and it is a read by definition.
_IDEMPOTENT = ("GET", "HEAD")

#: Writes whose *effect* one read can settle. Both carry the filter that names the rows
#: they meant, so there is a row to ask about — and neither is re-sent, because a dropped
#: reply does not say whether the statement ran.
_CONFIRMABLE = ("PATCH", "DELETE")

#: Query-string keys that are not columns of the filtered table. A filter is a mapping
#: from column to operator-and-value, so the keys that are not one are listed here.
_NOT_A_COLUMN = frozenset({"select", "order", "limit", "offset", "on_conflict",
                           "columns", "or", "and", "not", "count"})


def is_read(query) -> bool:
    """Is this postgrest query safe to run a second time?"""
    return getattr(query, "http_method", None) in _IDEMPOTENT


def filtered_columns(query) -> set[str]:
    """The columns this query's filter names."""
    params = getattr(query, "params", None)
    if params is None:
        return set()
    multi = getattr(params, "multi_items", None)
    if multi is None:
        return set()
    return {key for key, _ in multi() if key not in _NOT_A_COLUMN}


def is_confirmable(query) -> bool:
    """Is there a row this write's effect can be asked about?

    Four refusals, and each one is a shape where the read would answer a question the
    operator did not ask:

    * **an insert** has no filter — there is no row to look for, and the payload cannot
      say which of its columns is unique;
    * **an unfiltered update or delete** could only be settled by reading the whole
      table;
    * **a patch that moves the column it filters on** — ``update({"status": "done"})
      .eq("status", "pending")`` — would read back *nothing* after a write that landed,
      because the filter it was written with no longer matches. The retake decision in
      ``app/services/invigilation.py`` has exactly this shape, and confirming it would
      turn a landed write into a reported failure, which is the bug being fixed;
    * **a non-dict payload** is not a column patch at all.
    """
    method = getattr(query, "http_method", None)
    if method not in _CONFIRMABLE:
        return False
    if not getattr(query, "path", None):
        return False
    columns = filtered_columns(query)
    if not columns:
        return False
    if method == "PATCH":
        patch = getattr(query, "json", None)
        if not isinstance(patch, dict) or not patch:
            return False
        if set(patch) & columns:
            return False
    return True


def _as_instant(value):
    """``value`` as a datetime, or ``None`` when it is not a timestamp at all."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def value_matches(stored, written) -> bool:
    """Did the value the read-back found come from the value the caller wrote?

    Text equality is not enough for the two shapes this app writes constantly. A
    timestamp is written from Python as ``2026-09-29T15:26:08.498522+00:00`` and echoes
    as ``…Z`` — comparing the strings would report a write that landed as lost, which is
    the whole bug. A JSON column (``profiles.preferences``) comes back as the same
    structure in a different key order, so it is compared as a parsed structure.
    """
    if stored == written:
        return True
    if isinstance(stored, (dict, list)) and isinstance(written, (dict, list)):
        try:
            return (json.dumps(stored, sort_keys=True, default=str)
                    == json.dumps(written, sort_keys=True, default=str))
        except (TypeError, ValueError):
            return False
    left, right = _as_instant(stored), _as_instant(written)
    return left is not None and right is not None and left == right


def patch_is_present(row, patch) -> bool:
    """Does this row carry every column the patch wrote?"""
    if not isinstance(row, dict):
        return False
    return all(key in row and value_matches(row[key], value)
               for key, value in patch.items())


class ConfirmedWrite:
    """What a confirmed write returns: the rows the lost reply would have carried.

    ``count`` stays ``None`` rather than guessing a number the dropped reply never
    carried, and a confirmed delete returns ``[]`` — its rows are gone, so the
    representation is not recoverable, while the absence of them is the proof.
    """

    __slots__ = ("data", "count")

    def __init__(self, rows) -> None:
        self.data = rows
        self.count = None


def _read_back(query, method: str):
    """The one safe question to ask of a write: what does the row look like now?

    Bounded twice on purpose. The filter is the write's own, so the read returns exactly
    the rows the write meant and not the table; and a delete is read with ``limit=1``
    because one surviving row is the whole answer.
    """
    params = list(query.params.multi_items())
    if method == "DELETE":
        params.append(("limit", "1"))

    def read():
        query_meter.trip()
        response = query.session.get(query.path, params=params,
                                     headers={"Accept": "application/json"})
        if response.status_code >= 400:
            return None
        body = response.json()
        return body if isinstance(body, list) else [body]

    try:
        rows = read_with_retry(read)
    except Exception:                       # the read failed too: not a confirmation
        return None
    if not isinstance(rows, list):
        return None
    query_meter.read_rows(len(rows))
    return rows


def confirm_write(query, error):
    """Settle a write whose reply was lost, or ``None`` when it cannot be settled.

    ``None`` is not a failure — the caller re-raises ``error``, unchanged. It means the
    question has no safe answer, and an operator being told the write failed is the
    direction to be wrong in when the alternative is a guess.
    """
    if not is_confirmable(query):
        return None
    method = "DELETE" if query.http_method == "DELETE" else "PATCH"
    rows = _read_back(query, method)
    if rows is None:
        return None
    if method == "DELETE":
        return ConfirmedWrite([]) if not rows else None
    if rows and all(patch_is_present(row, query.json) for row in rows):
        return ConfirmedWrite(rows)
    return None


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


def rows_in(response) -> int:
    """How many records a postgrest answer carried, for the row counter.

    ``data`` is a list for a select and a single object for ``.single()``/``.single``
    shaped reads, which is one record and not one character per key. Anything else —
    a storage answer, an insert with no ``returning`` — is no rows read, which is the
    honest answer: the render did not read a dataset.
    """
    data = getattr(response, "data", None)
    if isinstance(data, list):
        return len(data)
    return 1 if isinstance(data, dict) else 0


class RetryingQuery:
    """One postgrest query, a retry for the transport failing under it, and counts.

    Three numbers, and the difference between them is the point. A **query** is what
    the render issued — once, here, before anything touches the network. An
    **attempt** is a round-trip the database really served, counted per try because
    `read_with_retry` re-invokes the callable: a release that quietly makes retries
    routine is a release that costs the same database twice, and that stays visible.
    And the **rows** are what came back, counted once per query for the first
    success — the retry re-reads the same records, and counting them twice would say
    the school's data doubled because the transport hiccuped.
    """

    __slots__ = ("_query", "_rows_counted")

    def __init__(self, query) -> None:
        self._query = query
        self._rows_counted = False

    def _counted_execute(self):
        query_meter.trip()
        response = self._query.execute()
        if not self._rows_counted:
            self._rows_counted = True
            query_meter.read_rows(rows_in(response))
        return response

    def execute(self):
        query_meter.query()
        if is_read(self._query):
            return read_with_retry(self._counted_execute)
        query_meter.trip()
        try:
            return self._query.execute()
        except httpx.TransportError as exc:
            # The reply was lost, so the *write* is not re-sent — but its effect can be
            # settled by the one safe question, a read. See ``confirm_write``.
            confirmed = confirm_write(self._query, exc)
            if confirmed is not None:
                return confirmed
            raise

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
