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

#: The header the harness reads. Named for the question it answers, not for the
#: module, because it is part of the app's response contract now.
HEADER = "X-Supabase-Roundtrips"

_spent: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "supabase_roundtrips", default=None
)


def begin() -> None:
    """Arm the meter for this request, at zero.

    Called once at the top of a request so a page that spends *nothing* reports 0
    rather than "unknown": the two are different answers, and a header that is
    missing on a cheap page would read as a harness fault.
    """
    _spent.set(0)


def trip(n: int = 1) -> None:
    """One round-trip to Supabase is about to be made (or was retried).

    A no-op when nothing armed the meter — a Celery task, a script, an import-time
    call — so this can sit on the client wrapper without every caller needing to
    know whether a request is in flight.
    """
    current = _spent.get()
    if current is not None:
        _spent.set(current + n)


def spent() -> int | None:
    """Round-trips this request has spent, or None if the meter was never armed."""
    return _spent.get()


def header_value() -> str | None:
    """The header's value, or None when there is nothing to report."""
    value = _spent.get()
    return None if value is None else str(value)
