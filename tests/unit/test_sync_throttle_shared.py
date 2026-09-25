"""The sync throttle has to count the same for every worker.

`POST /api/student/sync-draft` is the endpoint an offline-first paper leans on: the
page syncs roughly every 20 s, with a 3 s minimum between light payloads and 10 s
between canvas ones. The throttle that answers "too soon" lived in a dict in the
worker's own memory — `_sync_last`, read and written by `_check_rate_limit`.

With one process that is invisible, which is why it survived. With the three gevent
workers this box runs, each worker keeps its own clock and its own table, so the
same student can sync three times as often as the limit says — and *which* worker
answers a request is not something the client controls, so the effective limit is
neither 3 s nor 1 s but a lottery. The code even said so ("the throttle is still
per-process … a fair-use guard, not a boundary"); the fair-use part is exactly what
three workers break.

So the decision moves to the shared store the rate limiter already uses, through
the one operation that is correct under concurrency: Redis `SET NX EX`. Set-if-
absent is atomic, so two workers cannot both read "no recent write" and both write;
and the key's own TTL is what opens the window again, so there is no second table
to prune and no cleanup branch that can be entered only once every ten minutes. A
get-then-set pair would have been a race dressed up as a limit.

What must **not** change: the throttle still fails *open*. If Redis is unreachable
the request is answered from the in-process fallback — per-worker again, and the
docstring says so — because a cache outage must never stop a paper from being
saved. That is the same policy the rest of `app/utils/rate_limiter.py` follows.
"""
from __future__ import annotations

import pathlib
import re

import pytest

from app.routes import api as api_module
from app.utils import rate_limiter as rl

API_PY = (pathlib.Path(__file__).resolve().parents[2]
          / "app" / "routes" / "api.py")

#: The window the route asks for on a light payload; the boundary tests use the
#: same number so the two cannot drift.
WINDOW = 3

#: Every name a process-local copy of this decision could hide in. The point of
#: the helper below is that it does not need to know which one is live: it empties
#: whatever exists, which is what "a different worker" amounts to.
LOCAL_STATE = ("_sync_last", "_sync_last_cleanup", "_claims", "_limits")


class FakeRedis:
    """The two operations the shared store is allowed to use, and no more.

    ``set`` records whether it was called with ``nx`` and what TTL it was given, so
    a test can assert the atomicity and the window rather than trusting them. Reads
    and writes are made against an injectable clock so a window can be walked past
    without sleeping.
    """

    def __init__(self, clock):
        self.clock = clock
        self.store = {}
        self.ops = []

    def _live(self, name):
        entry = self.store.get(name)
        if not entry:
            return None
        value, expires_at = entry
        if expires_at is not None and self.clock[0] >= expires_at:
            return None
        return value

    def set(self, name, value, nx=False, ex=None):  # noqa: A003 — Redis' own name
        self.ops.append({"name": name, "nx": nx, "ex": ex})
        if nx and self._live(name) is not None:
            return None
        expires_at = (self.clock[0] + ex) if ex else None
        self.store[name] = (value, expires_at)
        return True


def forget_every_process_local_copy():
    """Empty whatever the throttle keeps in this process.

    This is the unit-test stand-in for "a different worker picked up the request":
    same shared store, none of the memory the first call happened to fill.
    """
    for module in (api_module, rl):
        for name in LOCAL_STATE:
            state = getattr(module, name, None)
            if isinstance(state, dict):
                state.clear()


@pytest.fixture
def shared(monkeypatch):
    """A shared store with a clock, and a clean slate of local state."""
    clock = [1000.0]
    conn = FakeRedis(clock)
    monkeypatch.setattr(rl, "_get_redis_conn", lambda: conn)
    monkeypatch.delenv("LOAD_TEST", raising=False)
    forget_every_process_local_copy()
    yield conn, clock
    forget_every_process_local_copy()


def check(user="u-1", exam="e-1", interval=WINDOW):
    return api_module._check_rate_limit(user, exam, min_interval=interval)


# ── one limit, three workers ─────────────────────────────────────────────────

class TestOneLimitForEveryWorker:
    def test_the_second_call_inside_the_window_is_refused(self, shared):
        assert check() is True
        assert check() is False

    def test_a_forgotten_worker_memory_does_not_reset_the_limit(self, shared):
        """This is the defect, stated as a test.

        Before the decision moved, emptying the worker's own table *was* the reset
        — which is what a second worker effectively is. Now the first worker's
        answer is in the shared store, so the second is refused even though its own
        memory has never heard of the key.
        """
        assert check() is True
        forget_every_process_local_copy()
        assert check() is False, (
            "a fresh worker was allowed a second sync inside the window, so the "
            "limit is still being counted per process"
        )

    def test_the_refusal_came_from_the_shared_store(self, shared):
        conn, _ = shared
        check()
        forget_every_process_local_copy()
        check()

        assert len(conn.ops) == 2, (
            "the shared store was not asked about both calls, so something else "
            "answered the second one"
        )
        assert all(op["nx"] is True for op in conn.ops), (
            "the shared write is not set-if-absent, so two workers can both win it"
        )

    def test_nothing_local_is_consulted_when_the_store_answers(self, shared):
        check()
        assert rl._claims == {}, (
            "the answer was also recorded in this process, which is the per-worker "
            "table this change removes"
        )

    def test_the_window_opens_again_after_the_interval(self, shared):
        _, clock = shared
        assert check() is True
        assert check() is False
        clock[0] += WINDOW + 0.01
        assert check() is True

    def test_the_key_lives_exactly_as_long_as_the_limit_lifts(self, shared):
        conn, _ = shared
        check(interval=7)
        assert conn.ops[0]["ex"] == 7, (
            "the shared key's TTL is not the window, so the limit and the expiry "
            "can disagree"
        )


class TestBucketsArePerStudentAndExam:
    def test_two_students_on_one_exam_do_not_share_a_bucket(self, shared):
        assert check(user="u-1", exam="e-1") is True
        assert check(user="u-2", exam="e-1") is True

    def test_one_student_on_two_exams_does_not_share_a_bucket(self, shared):
        assert check(user="u-1", exam="e-1") is True
        assert check(user="u-1", exam="e-2") is True

    def test_the_same_pair_still_shares_one(self, shared):
        assert check(user="u-1", exam="e-1") is True
        assert check(user="u-1", exam="e-1") is False


class TestItStillFailsOpen:
    def test_without_a_shared_store_the_window_is_counted_locally(self, monkeypatch):
        """Redis down: the request is answered, not refused.

        The fallback is per-worker again, which is the old behaviour — deliberately,
        because refusing to save a paper because a cache is unreachable is worse
        than a loose limit.
        """
        monkeypatch.setattr(rl, "_get_redis_conn", lambda: None)
        forget_every_process_local_copy()
        assert check(user="u-9", exam="e-9") is True
        assert check(user="u-9", exam="e-9") is False

    def test_a_broken_shared_store_does_not_refuse_the_sync(self, monkeypatch):
        class Exploding:
            def set(self, *a, **k):
                raise RuntimeError("redis is on fire")

        monkeypatch.setattr(rl, "_get_redis_conn", lambda: Exploding())
        forget_every_process_local_copy()
        assert check(user="u-8", exam="e-8") is True


class TestTheOlderRegressionsStillHold:
    """The two properties `test_sync_path_integrity.py` pinned, carried over.

    That file's subject — a per-worker table whose maintenance branch ran once
    every 600 s and raised `NameError` after ten minutes — no longer exists, so
    the pruning now belongs to the store's TTL and there is no branch to break.
    What it protected is worth keeping as a property of the throttle itself.
    """

    def test_a_claim_after_a_long_idle_period_is_answered_not_raised(self, shared):
        _, clock = shared
        clock[0] += 7200  # two hours with nothing claimed
        assert check(user="u-old", exam="e-old") is True

    def test_maintenance_never_clears_another_students_window(self, monkeypatch):
        """The bound-prune must age the table, not empty it.

        A prune that cleared the table instead of dropping its stale rows would
        reset every student mid-sitting — a worse failure than the unbounded growth
        it exists to avoid, and it would only show up on the one worker that
        happened to hit the bound. Written after a mutation that did exactly that
        survived the first version of this test, because that version only checked
        the claim being added and not the window already running.
        """
        monkeypatch.setattr(rl, "_get_redis_conn", lambda: None)
        forget_every_process_local_copy()

        assert check(user="u-a", exam="e-a") is True  # a live window
        rl._claims.update({f"sgl:claim:stale-{i}": 0.0 for i in range(rl._CLAIM_MAX)})

        assert check(user="u-b", exam="e-b") is True  # this claim forces the prune
        assert "sgl:claim:sync:u-b:e-b" in rl._claims, (
            "the prune took the claim it had just recorded"
        )
        assert check(user="u-a", exam="e-a") is False, (
            "the prune cleared another student's window, so the limit vanished for "
            "everyone the moment the table hit its bound"
        )


# ── the route is wired to it ─────────────────────────────────────────────────

class TestTheRouteUsesIt:
    SOURCE = API_PY.read_text(encoding="utf-8-sig")

    def test_the_worker_local_table_is_gone(self):
        assert "_sync_last" not in self.SOURCE, (
            "app/routes/api.py still keeps the sync throttle in a per-process dict"
        )

    def test_the_throttle_goes_through_the_shared_claim(self):
        body = self.SOURCE[self.SOURCE.index("def _check_rate_limit"):]
        body = body[:body.index("\n@")] if "\n@" in body else body
        assert re.search(r"\bclaim\(", body), (
            "the sync throttle does not go through the shared claim, so the limit "
            "is still the worker's own"
        )

    def test_the_two_intervals_are_unchanged(self):
        """3 s for a light payload, 10 s for canvas — the numbers the page expects."""
        assert "min_interval=3 if is_light else 10" in self.SOURCE

    def test_it_is_called_before_any_work_is_done(self):
        """A throttled sync must cost a round-trip to the store, not to Supabase."""
        route = self.SOURCE[self.SOURCE.index("def student_sync_draft"):]
        assert route.index("_check_rate_limit(") < route.index("_redis_lock(")
