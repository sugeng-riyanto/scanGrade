"""Eligibility to sync is read from the row the sync writes — no worker keeps a clock.

`POST /api/student/sync-draft` is the endpoint an offline-first paper leans on: the
page syncs roughly every 20 s, with a 3 s minimum between light payloads and 10 s
between canvas ones. Where that "too soon?" answer comes from has now moved twice,
and the second move is the one that removes the state altogether.

*Originally* it was a dict in the worker's own memory (`_sync_last`), read and
written by `_check_rate_limit`. With one process that is invisible; with the three
gevent workers this box runs, each worker kept its own clock and its own table, so
the same student could sync three times as often as the limit says — and *which*
worker answers a request is not something the client controls, so the effective
limit was neither 3 s nor 1 s but a lottery. That dict also carried a maintenance
branch entered once per 600 s that raised `NameError` once a sitting had run ten
minutes; `tests/unit/test_sync_path_integrity.py` holds that half.

*Then* it was a Redis claim (`rate_limiter.claim`, one atomic `SET NX EX`) — shared
and correct under concurrency, but a **second** clock for one guard, and it still
kept a per-process fallback table (`_claims`) for the case where the cache was
unreachable: the same defect, just off the happy path.

*Now* the throttle keeps no state at all, because the decision is derived from the
row the sync is about to write: `submissions.updated_at` — the last time anything
wrote that paper. The row lives in the shared database, so three workers read one
number by construction: there is no table to forget and no cache to be unreachable,
and nothing is left that could be counted per process. What makes this cheap is that
the sync **already** reads that row — it deep-merges the incoming answers into the
stored ones — so the read the guard needs is a read the endpoint was already paying
for. The guard no longer prevents that read; it prevents the write, which is the
part that amplifies (a JSONB rewrite plus WAL on every call).

Three properties are why this is safe to trust, and each is a guard here.

* **The decision is a pure function of ``(row, now)``.** Two calls from one process
  answer the same thing, so there is no state for a fresh worker to be missing —
  which is the defect this whole line of work is about, now impossible to state.
* **It fails open.** No row, no stamp, an unreadable stamp, or a stamp in the
  future all mean *allow*. A fair-use guard that a cache outage, a schema drift or a
  clock skew could turn into a refused save is worse than the writes it prevents.
* **The route stamps the row it judges.** The sync writes `updated_at` itself, so
  the number keeps moving even on a box whose `submissions` trigger is missing
  (`supabase/schema.sql:299` declares one; where it exists it discards our value in
  favour of the database's own `NOW()`, which only makes the stamp more truthful).
"""
from __future__ import annotations

import datetime as dt
import pathlib
import re

import pytest
from types import SimpleNamespace

from app.routes import api as api_module
from app.utils import rate_limiter as rl

ROOT = pathlib.Path(__file__).resolve().parents[2]
API_PY = ROOT / "app" / "routes" / "api.py"
RL_PY = ROOT / "app" / "utils" / "rate_limiter.py"

#: The windows the route asks for. The boundary tests use the same numbers so the
#: two cannot drift apart unseen.
LIGHT_WINDOW = 3
CANVAS_WINDOW = 10

#: Every name a process-local copy of *this* decision could hide in: the dict the
#: throttle kept, the cleanup stamp beside it, and the claim's fallback table that
#: replaced them. None of them may come back.
THROTTLE_STATE = ("_sync_last", "_sync_last_cleanup", "_claims")

#: Everything a worker could be holding, this guard's tables included. `_limits`
#: belongs to the *other* limiter (logins and the like) and is not this throttle's
#: business; it is cleared only to model a worker that has forgotten everything.
LOCAL_STATE = THROTTLE_STATE + ("_limits",)


def forget_every_process_local_copy():
    for module in (api_module, rl):
        for name in LOCAL_STATE:
            state = getattr(module, name, None)
            if isinstance(state, dict):
                state.clear()


@pytest.fixture(autouse=True)
def _no_state_leaks_between_tests():
    """Start and finish each test with whatever memory a worker could hold, gone.

    Not cosmetic: with a process-local table in the path, an earlier test leaves a
    claim that makes a later one pass *within the same three seconds* — the same
    cross-worker leak this file is about, showing up as an order-dependent test.
    """
    forget_every_process_local_copy()
    yield
    forget_every_process_local_copy()


def row_written(seconds_ago, now=10_000):
    """A submission row whose last write was ``seconds_ago`` before ``now``."""
    stamp = dt.datetime.fromtimestamp(now - seconds_ago, tz=dt.timezone.utc)
    return {"id": "sub-1", "status": "draft", "answers": {},
            "started_at": stamp.isoformat(), "updated_at": stamp.isoformat()}


# ── the decision itself ──────────────────────────────────────────────────────

def allowed(row, now=10_000, interval=LIGHT_WINDOW):
    return api_module._check_rate_limit(row, now, min_interval=interval)


def test_a_row_written_inside_the_window_is_refused():
    assert allowed(row_written(1)) is False


def test_the_window_opens_again_after_the_interval():
    assert allowed(row_written(4)) is True


def test_the_boundary_is_the_interval_itself():
    """Exactly `interval` seconds after the write is late enough — no off-by-one."""
    assert allowed(row_written(LIGHT_WINDOW)) is True
    assert allowed(row_written(LIGHT_WINDOW - 1)) is False


def test_the_two_windows_are_the_numbers_the_page_expects():
    """3 s for a light payload, 10 s for canvas: the route passes both."""
    for window in (LIGHT_WINDOW, CANVAS_WINDOW):
        assert allowed(row_written(window - 1), interval=window) is False
        assert allowed(row_written(window), interval=window) is True


def test_a_sitting_with_no_row_yet_is_allowed():
    """The first sync of a sitting creates the row; there is nothing to judge by."""
    assert allowed(None) is True
    assert allowed({}) is True


def test_a_row_without_a_stamp_is_allowed():
    assert allowed({"id": "sub-1", "status": "draft", "answers": {}}) is True


def test_an_unreadable_stamp_is_allowed():
    """A value this code cannot read is not evidence of a recent write."""
    assert allowed(dict(row_written(1), updated_at="not a timestamp")) is True


def test_the_stamp_is_read_however_the_row_returns_it():
    """PostgREST hands back an ISO string; a row built in Python holds a datetime."""
    now = 10_000
    iso = dt.datetime.fromtimestamp(now - 1, tz=dt.timezone.utc).isoformat()
    zulu = iso.replace("+00:00", "Z")
    naive = iso.replace("+00:00", "")
    aware = dt.datetime.fromtimestamp(now - 1, tz=dt.timezone.utc)
    naive_dt = dt.datetime.fromtimestamp(now - 1, tz=dt.timezone.utc).replace(tzinfo=None)

    for stamp in (iso, zulu, naive, aware, naive_dt):
        assert allowed(dict(row_written(1), updated_at=stamp), now=now) is False, stamp


def test_a_stamp_in_the_future_does_not_refuse_the_sync():
    """Clock skew, not evidence — including a skew *inside* the window.

    The database's `NOW()` and this process's clock are two clocks, so a stamp
    slightly ahead of the request is ordinary. Refusing on it would stall every
    sync on such a box for the length of the skew; allowing it costs at most the
    extra writes this guard exists to trim.

    The one-second case is the one that matters, and it is why this test exists in
    two sizes: a `-30` s stamp is comfortably outside every window and stays
    allowed even under an `abs(age)` rule, so on its own it would let "treat skew as
    evidence" pass unnoticed. A cell whose stamp runs one second fast is the case
    that rule would refuse.
    """
    assert allowed(row_written(-1)) is True, "a stamp one second ahead is skew"
    assert allowed(row_written(-30)) is True


def test_a_sync_after_a_long_idle_period_is_answered_not_raised():
    """The regression `test_sync_path_integrity.py` was written for.

    Its old shape was a maintenance branch — entered once per 600 s — that raised
    `NameError` after ten minutes of a sitting. There is no branch now: the answer
    comes from a row that has simply not been written for two hours.
    """
    assert allowed(row_written(7200)) is True


def test_two_students_do_not_share_a_bucket():
    """Each sitting carries its own stamp, so one student's sync is not another's."""
    assert allowed(row_written(60)) is True
    assert allowed(row_written(1)) is False


# ── no state, in this process or any other ───────────────────────────────────

def test_the_decision_is_a_pure_function_of_the_row_and_the_clock():
    """Calling it twice must not change what it says.

    This is the defect stated as a property. A throttle with memory answers the
    second call differently from the first *because* of that memory; a pure
    function cannot, and so cannot disagree with the other two workers.
    """
    row = row_written(60)

    first = allowed(row)
    forget_every_process_local_copy()
    second = allowed(row)

    assert first is True
    assert second is True, (
        "the decision changed between two identical calls, so something is being "
        "remembered — which is what makes the limit per-worker"
    )


def test_forgetting_a_worker_memory_does_not_reset_the_limit(monkeypatch):
    """A fresh worker, a row already written, and the same refusal.

    With the state in a per-worker table, emptying that table *was* the reset —
    which is what a second gevent worker effectively is. The row is not in this
    process, so it cannot be emptied.
    """
    now = 10_000
    row = row_written(1, now=now)

    assert allowed(row, now=now) is False
    forget_every_process_local_copy()
    assert allowed(row, now=now) is False, (
        "a fresh worker was allowed a second sync inside the window, so the limit "
        "is still counted per process"
    )


def test_nothing_in_this_process_records_the_decision():
    """The tables this guard used to live in must be gone, not merely unused."""
    allowed(row_written(1))
    for module in (api_module, rl):
        for name in THROTTLE_STATE:
            assert not isinstance(getattr(module, name, None), dict), (
                f"{module.__name__}.{name} still exists; there is no state to keep"
            )


def test_the_shared_store_claim_is_gone_with_its_fallback():
    """`claim` had exactly one caller, and that caller judges by the row now.

    A leave-behind would not fail loudly: it would simply be dead weight with a
    per-process table inside it, waiting for someone to reuse.
    """
    source = RL_PY.read_text(encoding="utf-8-sig")
    assert not re.search(r"^def claim\(", source, re.M), (
        "rate_limiter.claim is still defined, but the throttle no longer calls it"
    )
    assert "_claims" not in source, "the per-process claim fallback is still there"


# ── the route: it judges by the row it writes, and writes nothing when refused ─

def test_the_route_asks_by_the_row_it_already_read():
    route = API_PY.read_text(encoding="utf-8-sig")
    route = route[route.index("def student_sync_draft"):]
    route = route[:route.index("\n@api_bp.route")] if "\n@api_bp.route" in route else route

    assert "updated_at" in route, (
        "the sync does not read the column it judges eligibility by"
    )
    assert re.search(r"_check_rate_limit\(\s*sub\b", route), (
        "the route does not pass the submission row it read, so the decision is "
        "being made from something other than the row"
    )


def test_the_route_still_uses_the_two_numbers_the_page_expects():
    """3 s between light payloads, 10 s between canvas ones, set at the call site.

    The page syncs every ~20 s and every ~60 s, so both are floors rather than
    schedules — but they are the numbers the endpoint has always enforced, and a
    change to them should be a decision rather than a side effect.
    """
    source = API_PY.read_text(encoding="utf-8-sig")
    assert "min_interval=10 if not is_light else 3" in source


def test_the_check_happens_after_the_row_read_and_before_the_write():
    """The order is the design: the row is the clock, and a refusal costs no write."""
    route = API_PY.read_text(encoding="utf-8-sig")
    route = route[route.index("def student_sync_draft"):]
    route = route[:route.index("\n@api_bp.route")] if "\n@api_bp.route" in route else route

    check = route.index("_check_rate_limit(")
    read = route.index('table("submissions").select(')
    write = route.index('table("submissions").update(')

    assert read < check, "the decision is made before the row it depends on is read"
    assert check < write, (
        "a refused sync still reaches the write, so the guard costs a round-trip "
        "and saves nothing"
    )


# ── the same claim, driven through the route ─────────────────────────────────

class FakeResponse:
    def __init__(self, data):
        self.data = data


class FakeTable:
    """Chainable stand-in for one PostgREST table that remembers its writes."""

    def __init__(self, rows=None):
        self.rows = [dict(r) for r in (rows or [])]
        self._mode = "select"
        self._filters = []
        self._payload = None
        self._limit = None
        self._single = False
        self._maybe = False
        self.writes = []

    def select(self, *a, **k):
        self._mode = "select"
        return self

    def eq(self, col, val):
        self._filters.append((col, val))
        return self

    def limit(self, n):
        self._limit = n
        return self

    def single(self):
        self._single = True
        return self

    def maybe_single(self):
        self._single, self._maybe = True, True
        return self

    def insert(self, data):
        self._mode, self._payload = "insert", data
        return self

    def update(self, data):
        self._mode, self._payload = "update", data
        return self

    def _match(self):
        return [r for r in self.rows
                if all(r.get(c) == v for c, v in self._filters)]

    def execute(self):
        if self._mode == "select":
            rows = self._match()
            self._filters = []
            if self._limit:
                rows = rows[: self._limit]
            if self._single:
                # postgrest hands back one row (not a list) for `single()`, and
                # `None` — not a response — for `maybe_single()` that matched nothing.
                if not rows:
                    return None if self._maybe else FakeResponse(None)
                return FakeResponse(rows[0])
            return FakeResponse(rows)

        payload = dict(self._payload)
        self.writes.append((self._mode, payload))
        if self._mode == "insert":
            row = dict(payload)
            row.setdefault("id", f"sub-{len(self.rows) + 1}")
            self.rows.append(row)
            self._filters = []
            return FakeResponse([row])

        rows = self._match()
        for r in rows:  # a real UPDATE moves the stored row, stamp included
            r.update(payload)
        self._filters = []
        return FakeResponse(rows)


class FakeSupabase:
    def __init__(self, tables):
        self._tables = dict(tables)

    def table(self, name):
        return self._tables.setdefault(name, FakeTable())


EXAM = {
    "id": "exam-1", "duration_minutes": 60, "total_questions": 5,
    "answer_key": {}, "question_types": {}, "question_weights": {},
    "max_attempts": 1, "publish_mode": "manual", "is_published": True,
    "status": "active", "start_at": None, "end_at": None,
    "auto_submit_on_window_end": False,
}

NOW = 10_000


@pytest.fixture
def sitting(monkeypatch):
    """One draft sitting at a pinned instant, with the lock and the store faked."""
    started = dt.datetime.fromtimestamp(NOW - 300, tz=dt.timezone.utc).isoformat()
    tables = {
        "exams": FakeTable([EXAM]),
        "submissions": FakeTable([{
            "id": "sub-1", "exam_id": "exam-1", "student_id": "stu-1",
            "status": "draft", "answers": {}, "started_at": started,
            "updated_at": started,
        }]),
    }
    yield FakeSupabase(tables)

    forget_every_process_local_copy()


def sync(supa, monkeypatch, app, clock=None, light=True):
    """Drive the real view, as a logged-in student, with the clock pinned."""
    from flask import g

    app.extensions["supabase"] = supa
    monkeypatch.setattr(api_module, "_redis_lock", lambda key: object())
    monkeypatch.setattr(api_module, "_release_lock", lambda conn, key: None)
    monkeypatch.setattr(api_module, "exam_sitting_allowed",
                        lambda sb, exam, eid, sid: (True, ""))
    monkeypatch.setattr(api_module, "time", SimpleNamespace(time=lambda: (clock or [NOW])[0]))

    payload = {"exam_id": "exam-1", "answers": {"q1": "a"}, "light": light,
               "started_at": NOW}
    with app.test_request_context("/api/student/sync-draft", method="POST", json=payload):
        g.user_id, g.user_role = "stu-1", "murid"
        raw = api_module.student_sync_draft.__wrapped__()
    return raw.get_json()


def test_the_row_decides_not_this_processes_memory(sitting, monkeypatch, app):
    """Same process, same clock — and the answer follows the stored row.

    The previous mechanism answered from a table this worker had just written, so
    a row that says "last written two minutes ago" would still have been refused.
    That difference is the whole change, and it is invisible from one call.
    """
    subs = sitting.table("submissions")

    sync(sitting, monkeypatch, app)
    subs.rows[0]["updated_at"] = dt.datetime.fromtimestamp(
        NOW - 120, tz=dt.timezone.utc).isoformat()
    again = sync(sitting, monkeypatch, app)

    assert "throttled" not in again, (
        "the row says the last write was two minutes ago and this process's memory "
        "disagreed, so the memory is what is deciding"
    )
    assert len(subs.writes) == 2


def test_a_sync_after_the_window_writes_again(sitting, monkeypatch, app):
    subs = sitting.table("submissions")
    clock = [NOW]

    sync(sitting, monkeypatch, app, clock=clock)
    clock[0] += LIGHT_WINDOW + 1
    again = sync(sitting, monkeypatch, app, clock=clock)

    assert "throttled" not in again
    assert len(subs.writes) == 2


def test_the_canvas_payload_waits_longer_than_a_light_one(sitting, monkeypatch, app):
    subs = sitting.table("submissions")
    clock = [NOW]

    sync(sitting, monkeypatch, app, clock=clock)
    clock[0] += LIGHT_WINDOW + 1  # past the light window, inside the canvas one

    assert sync(sitting, monkeypatch, app, clock=clock, light=False).get("throttled") is True
    assert len(subs.writes) == 1


def test_a_fresh_worker_cannot_reset_the_window(sitting, monkeypatch, app):
    """The defect, through the route: empty this process, and it still refuses —
    and the refusal costs no write."""
    subs = sitting.table("submissions")

    sync(sitting, monkeypatch, app)
    forget_every_process_local_copy()
    again = sync(sitting, monkeypatch, app)

    assert again.get("throttled") is True, (
        "a fresh worker was allowed a second sync inside the window"
    )
    assert len(subs.writes) == 1, (
        "the refused sync still wrote to the row, so the guard saved nothing"
    )


def test_the_write_moves_the_stamp_the_guard_reads(sitting, monkeypatch, app):
    """The stamp has to move with each sync, or the row stops being a clock.

    Asserted on the payload rather than on a trigger existing: `updated_at` is
    maintained by the database where `supabase/schema.sql:299` is in force, and
    written here where it is not. Either way the next sync reads a fresh stamp.
    """
    subs = sitting.table("submissions")
    before = subs.rows[0]["updated_at"]

    sync(sitting, monkeypatch, app)

    mode, payload = subs.writes[-1]
    assert mode == "update"
    assert "answers" in payload
    assert "updated_at" in payload, (
        "the sync did not stamp the row, so eligibility would never move on a box "
        "whose submissions trigger is missing"
    )
    assert subs.rows[0]["updated_at"] != before


def test_a_brand_new_sitting_is_allowed_to_start(sitting, monkeypatch, app):
    """No row yet: the first sync creates one, stamp and all."""
    subs = sitting.table("submissions")
    subs.rows = []

    resp = sync(sitting, monkeypatch, app)

    assert "throttled" not in resp
    mode, payload = subs.writes[-1]
    assert mode == "insert"
    assert "updated_at" in payload
