"""How many Supabase round-trips one request spent — and it says so in a header.

Why this exists
---------------
The deploy's release performance gate compares a release with the release before it
on **response time**. Response time is a symptom: it says a page got slower, not what
made it slower. A page that gains ten queries can still answer inside the latency
slack on a quiet box, and then be the page that falls over at 500 concurrent
students. The number of round-trips is the *cause*, it is an integer rather than a
distribution, and it is exactly what an N+1 pattern multiplies.

So every request counts the queries it sends to the data client, and the count leaves
with the response:

    X-Supabase-Roundtrips: 7
    X-Supabase-Queries: 6
    X-Supabase-Rows: 120

Three numbers, because one of them answers two questions badly. Round-trips are
**attempts**: a retried read costs two, which is deliberate, and it means the number
moves when the *box* is having a bad afternoon — measured on production, same release,
byte-identical pages, `/teacher/dashboard` reporting 1 attempt one day and 3 the next.
A gate that scores that as a ratio refuses a release for the transport. So the counts
are separated:

* `X-Supabase-Queries` is what the render **issued**. That is what a release changes:
  an N+1 moves this one, and nothing else does.
* `X-Supabase-Roundtrips` is what the database **served**, retries included. It is the
  cost and the health signal, and it is reported rather than scored.
* `X-Supabase-Rows` is how much **data** the render read. Bytes and queries both grow
  when a school grows, and this is the only number that can say whether they grew
  because the data did or because the code did — measured the same way: page bytes
  were identical to the byte across those two runs, so a page that grew is evidence
  of nothing on its own.

The load harness records that header per endpoint and the gate compares the worst
student/teacher page against the last release that passed, next to the page's byte
size. A release that adds three queries to the student dashboard, or that ships a
200 KB script onto it, is refused before it reaches students — rather than being
blamed on the database some Friday.

What it counts, and what it deliberately does not
-------------------------------------------------
It counts queries on the **data client** (`get_supabase()`), which is where a page
render spends its round-trips: every `table(...)...execute()` and every `rpc(...)`,
including a retry, because a retry is a round-trip the database really served. Auth
calls (`get_auth_client()`) are not counted: they happen at sign-in, not while a page
renders, and the smoke test is what holds the sign-in path.

The count is carried in a :mod:`contextvars` context, which is scoped to the
greenlet or thread serving the request — gunicorn runs `gevent`, and greenlet gives
each one its own context (verified on this project's greenlet 3.5.5, and asserted in
`tests/unit/test_db_roundtrip_meter.py`). `begin()` sets it back to zero at the top
of *every* request, which is what makes a reused thread — the dev server, the test
client — safe as well: the previous request's total can still be in the context, and
it is overwritten before any hook or view reads it. It therefore does **not** follow work
handed to a `ThreadPoolExecutor` or to a `gevent.spawn`ed greenlet, because those
start with a fresh context. That boundary is real, it is tested rather than assumed,
and it is acceptable here: the app's two fan-outs (`teacher.py`'s bulk score
recalculation and the OMR worker) are bulk operations behind a POST, not page
renders, which is what the gate measures. A meter that silently reported a lower
number for a page would be worse than no meter, so a page render — where every query
is made inline — is the case this is built for and the case the tests pin.
"""

from __future__ import annotations

import contextvars

#: The headers the harness reads. Named for the questions they answer, not for the
#: module, because they are part of the app's response contract now.
HEADER = "X-Supabase-Roundtrips"
QUERY_HEADER = "X-Supabase-Queries"
ROW_HEADER = "X-Supabase-Rows"

_spent: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "supabase_roundtrips", default=None
)
_asked: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "supabase_queries", default=None
)
_read: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "supabase_rows", default=None
)


def begin() -> None:
    """Arm the meter for this request, at zero.

    Called once at the top of a request so a page that spends *nothing* reports 0
    rather than "unknown": the two are different answers, and a header that is
    missing on a cheap page would read as a harness fault.
    """
    _spent.set(0)
    _asked.set(0)
    _read.set(0)


def trip(n: int = 1) -> None:
    """One round-trip to Supabase is about to be made (or was retried).

    A no-op when nothing armed the meter — a Celery task, a script, an import-time
    call — so this can sit on the client wrapper without every caller needing to
    know whether a request is in flight.
    """
    current = _spent.get()
    if current is not None:
        _spent.set(current + n)


def query(n: int = 1) -> None:
    """One query is about to be issued, whatever the transport does with it.

    Counted once per query, at the moment it is asked for — so a retry does not
    make the render look like it issued two, which is the whole point of keeping
    this apart from :func:`trip`.
    """
    current = _asked.get()
    if current is not None:
        _asked.set(current + n)


def read_rows(n: int) -> None:
    """`n` records came back from a query the render issued.

    Counted once per query for the same reason: a retry re-reads the same rows,
    and reporting twice the data because the transport hiccuped would say the
    school doubled.
    """
    current = _read.get()
    if current is not None and n > 0:
        _read.set(current + int(n))


def spent() -> int | None:
    """Round-trips this request has spent, or None if the meter was never armed."""
    return _spent.get()


def issued() -> int | None:
    """Queries this request asked for, or None if the meter was never armed."""
    return _asked.get()


def rows_read() -> int | None:
    """Records this request read, or None if the meter was never armed."""
    return _read.get()


def header_value() -> str | None:
    """The header's value, or None when there is nothing to report."""
    value = _spent.get()
    return None if value is None else str(value)


def queries_header_value() -> str | None:
    value = _asked.get()
    return None if value is None else str(value)


def rows_header_value() -> str | None:
    value = _read.get()
    return None if value is None else str(value)
