"""The draft lock rides the app's pooled Redis, and degrades instead of refusing.

``POST /api/student/sync-draft`` takes a lock on every call — the lock is what
keeps two writers from merging into one JSONB ``answers`` column at once — and it
did that in a way that had three separate costs:

* **a client per call.** ``Redis.from_url(...)`` was built and never closed, so
  the pooled, health-checked, resettable connection that every other Redis user
  in this app rides — the rate limiter, the account limiter, ``kv_cache``, and
  the ``claim`` this lock was meant to share — was bypassed. 500 students syncing
  is 500 TCP connections per pass instead of the pool's twenty handed back.
* **``SETNX`` then ``EXPIRE``.** Two round trips for one decision, and a process
  that died between them left the key behind **with no TTL**: not a stale lock
  but a permanent one, so that student's syncs stayed refused for good rather
  than until the timeout. ``SET ... NX EX`` is one atomic command carrying its
  own expiry.
* **no fallback.** An unreachable store returned ``None``, which the caller reads
  as "someone else holds it" — so every sync answered ``busy: True`` and saved
  nothing at all. Refusing to store the paper is the one outcome a cache outage
  may never produce here.

Each of those is held below: the connection comes from the pool, one atomic
``SET ... NX EX`` carries the expiry, a lock that is genuinely *held* still
refuses, and a store that is missing or raising lets the write through. The
route-level half — that a save actually lands when the store is down — lives in
``tests/unit/test_sync_eligibility.py``, beside the fake PostgREST it needs.
"""
from __future__ import annotations

import inspect
import pathlib

import pytest

from app.routes import api as api_module

ROOT = pathlib.Path(__file__).resolve().parents[2]
API_PY = ROOT / "app" / "routes" / "api.py"

LOCK_KEY = "scan_grade:lock:sync:stu-1:exam-1"


class Conn:
    """A Redis that records what it was asked, and opens no socket.

    ``takes`` is the answer to a `SET ... NX`: True when the key was absent and
    this caller now holds it, falsy when somebody else does.
    """

    def __init__(self, takes=True):
        self.takes = takes
        self.calls = []

    def set(self, key, value, nx=False, ex=None):
        self.calls.append(("set", key, value, nx, ex))
        return self.takes

    def setnx(self, key, value):
        self.calls.append(("setnx", key, value))
        return self.takes

    def expire(self, key, seconds):
        self.calls.append(("expire", key, seconds))
        return True

    def delete(self, key):
        self.calls.append(("delete", key))
        return 1


@pytest.fixture(autouse=True)
def _no_local_state_leaks():
    """A held fallback lock is state; one test must not spend another's."""
    api_module._local_locks.clear()
    yield
    api_module._local_locks.clear()


@pytest.fixture
def store(monkeypatch):
    """Point the lock at a recording connection, as if the pool were healthy."""
    conn = Conn()

    def use(takes=True):
        conn.takes = takes
        return conn

    monkeypatch.setattr(api_module, "_lock_conn", lambda: conn)
    use.conn = conn
    return use


# ── the connection ───────────────────────────────────────────────────────────

class TestTheLockRidesThePool:

    def test_it_does_not_build_its_own_client(self):
        """`from_url` per call is the defect, so the source must not name it.

        Read as source on purpose: a mocked connection cannot show that no
        *second* connection was made, which is the whole complaint — the pool was
        there, and this path ignored it.
        """
        body = inspect.getsource(api_module._redis_lock)
        assert "from_url" not in body, (
            "the lock builds its own Redis client per call again, so the app's pool "
            "is bypassed and every sync opens a connection nothing closes")
        assert "Redis(" not in body, "the lock constructs its own client again"

    def test_it_asks_the_shared_helper_for_the_connection(self):
        """The one place the pooled connection comes from, named explicitly."""
        helper = inspect.getsource(api_module._lock_conn)
        assert "_get_redis_conn" in helper and "rate_limiter" in helper, (
            "the lock does not go through the app's pooled connection helper")

    def test_the_pool_helper_is_the_same_one_every_other_caller_uses(self):
        from app.utils import rate_limiter

        assert callable(rate_limiter._get_redis_conn), (
            "the shared pooled connection the lock names no longer exists")
        source = (ROOT / "app" / "utils" / "rate_limiter.py").read_text(encoding="utf-8")
        assert "ConnectionPool.from_url" in source, (
            "the shared helper stopped pooling, so there is nothing to ride")


# ── the take ─────────────────────────────────────────────────────────────────

class TestTheTakeIsOneAtomicCommand:

    def test_one_set_carries_nx_and_the_expiry(self, store):
        """`SET key 1 NX EX <timeout>` — the expiry and the take, indivisible."""
        handle = api_module._redis_lock("sync:stu-1:exam-1", timeout=7)

        assert handle, "a free lock was not taken"
        assert store.conn.calls == [("set", LOCK_KEY, "1", True, 7)], (
            "the take was not one atomic SET with NX and its expiry: "
            f"{store.conn.calls}")

    def test_no_separate_expire_step_exists_to_be_lost(self, store):
        """The window between `SETNX` and `EXPIRE` is what a crash could fall into.

        Asserted separately from the happy path because the two-step version also
        succeeds — it only fails when a process dies in between, which no test can
        stage. The absence of the second command is the guarantee.
        """
        api_module._redis_lock("sync:stu-1:exam-1", timeout=7)

        kinds = [c[0] for c in store.conn.calls]
        assert "setnx" not in kinds and "expire" not in kinds, (
            f"the lock took two steps again, so a crash between them leaves a key "
            f"with no TTL: {store.conn.calls}")

    def test_a_lock_somebody_holds_is_not_taken(self, store):
        """Refusing while another writer runs is the lock doing its job."""
        store(takes=False)

        assert api_module._redis_lock("sync:stu-1:exam-1") is None, (
            "a held lock was handed out twice")
        assert [c[0] for c in store.conn.calls] == ["set"], (
            "the lock touched the store beyond asking for it")

    def test_release_names_the_key_it_took(self, store):
        handle = api_module._redis_lock("sync:stu-1:exam-1")
        api_module._release_lock(handle, "sync:stu-1:exam-1")

        assert ("delete", LOCK_KEY) in store.conn.calls, (
            "releasing did not delete the key the take created, so the next sync "
            "from this student is refused until the TTL runs out")


# ── the fallback ─────────────────────────────────────────────────────────────

class TestAnUnreachableStoreDoesNotRefuseTheWrite:
    """The half a cache outage makes visible.

    Without it, `None` means "someone else holds it" to the caller, which answers
    `busy: True` — so one Redis blip refuses every sync in the school and stores
    nothing. The fallback is weaker than the shared lock and is documented as
    such; it is not weaker than dropping the paper.
    """

    def test_a_missing_store_still_hands_out_the_lock(self, monkeypatch):
        monkeypatch.setattr(api_module, "_lock_conn", lambda: None)

        assert api_module._redis_lock("sync:stu-1:exam-1"), (
            "an unreachable store refused the lock, so the sync would answer "
            "'busy' and save nothing")

    def test_a_store_that_raises_also_still_hands_out_the_lock(self, monkeypatch):
        def boom():
            raise RuntimeError("connection refused")

        monkeypatch.setattr(api_module, "_lock_conn", boom)

        assert api_module._redis_lock("sync:stu-1:exam-1"), (
            "a raising store refused the lock instead of falling back")

    def test_a_take_that_raises_falls_back_rather_than_refusing(self, monkeypatch):
        class Broken(Conn):
            def set(self, *a, **kw):
                raise RuntimeError("socket closed mid-command")

        monkeypatch.setattr(api_module, "_lock_conn", lambda: Broken())

        assert api_module._redis_lock("sync:stu-1:exam-1"), (
            "a store that failed during the command refused the lock")

    def test_the_fallback_still_serialises_one_writer(self, monkeypatch):
        """In this worker it is a lock, not a pass: the second caller waits."""
        monkeypatch.setattr(api_module, "_lock_conn", lambda: None)

        assert api_module._redis_lock("sync:stu-1:exam-1")
        assert api_module._redis_lock("sync:stu-1:exam-1") is None, (
            "the fallback handed the same lock to two callers, so it serialises "
            "nothing at all")

    def test_a_different_paper_is_a_different_lock(self, monkeypatch):
        monkeypatch.setattr(api_module, "_lock_conn", lambda: None)

        assert api_module._redis_lock("sync:stu-1:exam-1")
        assert api_module._redis_lock("sync:stu-1:exam-2"), (
            "one student's lock blocked their other paper")

    def test_releasing_the_fallback_frees_the_key(self, monkeypatch):
        monkeypatch.setattr(api_module, "_lock_conn", lambda: None)

        handle = api_module._redis_lock("sync:stu-1:exam-1")
        api_module._release_lock(handle, "sync:stu-1:exam-1")

        assert api_module._redis_lock("sync:stu-1:exam-1"), (
            "the fallback lock was never released, so this student's syncs are "
            "refused for the life of the worker")

    def test_the_fallback_table_is_pruned(self, monkeypatch):
        """With the store down for hours this is the only thing accumulating."""
        import time as _time

        monkeypatch.setattr(api_module, "_lock_conn", lambda: None)
        api_module._local_locks["sync:gone:exam-9"] = _time.monotonic() - 1

        api_module._redis_lock("sync:stu-1:exam-1")

        assert "sync:gone:exam-9" not in api_module._local_locks, (
            "an expired fallback lock was kept, so the table grows without bound")


# ── what the page can see about it ───────────────────────────────────────────

class TestTheFallbackIsVisibleToThePage:
    """A fallback that only reaches `journalctl` is a fallback nobody acts on.

    Every lock this route takes either rides the shared store or is held in this
    worker alone, and which one it was decides whether three gevent workers are
    serialising against each other or not. That is a fact about the appliance, so
    it is recorded for the status page — including the path out of this function
    that used to leave no trace at all (`_lock_conn()` returning None, which is
    the *normal* state of a box without a shared store).
    """

    @pytest.fixture(autouse=True)
    def _a_record_of_our_own(self, tmp_path, monkeypatch):
        from app.utils import lock_health

        monkeypatch.setenv("SCANGRADE_LOCK_STATE_FILE", str(tmp_path / "marker.json"))
        lock_health.reset()
        yield lock_health
        lock_health.reset()

    def test_a_take_from_the_shared_store_is_recorded_as_shared(self, store, _a_record_of_our_own):
        api_module._redis_lock("sync:stu-1:exam-1")

        state = _a_record_of_our_own.state(probe=lambda: {"connected": True})
        assert state["worker_ok"] == 1
        assert state["worker_fallbacks"] == 0
        assert state["key"] == _a_record_of_our_own.SHARED

    def test_a_held_lock_is_not_an_outage(self, store, _a_record_of_our_own):
        """The store answered — somebody else holds it. That is the lock working."""
        store(takes=False)

        api_module._redis_lock("sync:stu-1:exam-1")

        state = _a_record_of_our_own.state(probe=lambda: {"connected": True})
        assert state["worker_fallbacks"] == 0, (
            "a held lock was reported as a store outage, so the page would warn "
            "about a cache that is answering perfectly")

    def test_a_missing_store_records_the_fallback(self, monkeypatch, _a_record_of_our_own):
        """The path that used to be silent: no connection, so no log line either."""
        monkeypatch.setattr(api_module, "_lock_conn", lambda: None)

        api_module._redis_lock("sync:stu-1:exam-1")

        state = _a_record_of_our_own.state(probe=lambda: {"connected": False,
                                                          "error": "not_configured"})
        assert state["worker_fallbacks"] == 1, (
            "the lock fell back to this worker and the page can say nothing about it")
        assert state["key"] == _a_record_of_our_own.UNCONFIGURED

    def test_a_take_that_fails_records_the_reason_it_failed(self, monkeypatch,
                                                            _a_record_of_our_own):
        class Broken(Conn):
            def set(self, *a, **kw):
                raise RuntimeError("socket closed mid-command")

        monkeypatch.setattr(api_module, "_lock_conn", lambda: Broken())

        api_module._redis_lock("sync:stu-1:exam-1")

        assert "socket closed mid-command" in (_a_record_of_our_own.state(
            probe=lambda: {"connected": False, "error": "ping_failed"})["worker_reason"] or ""), (
            "the reason the store failed never reached the record, so the page "
            "could only say 'an outage'")

    def test_the_recorded_reason_separates_a_missing_store_from_a_dead_one(
            self, monkeypatch, _a_record_of_our_own):
        """`not configured` and `unreachable` look alike here and are not.

        Both leave the lock in this worker, and they have opposite remedies: one is
        a setting nobody wrote, the other is a server that is down. The page shows
        the store's own word for it, so the word has to survive the trip.
        """
        monkeypatch.setattr(api_module, "_lock_conn", lambda: None)
        monkeypatch.delenv("REDIS_URL", raising=False)

        api_module._redis_lock("sync:stu-1:exam-1")
        assert _a_record_of_our_own.state()["worker_reason"] == "not_configured"

        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
        api_module._redis_lock("sync:stu-1:exam-2")
        assert _a_record_of_our_own.state()["worker_reason"] == "unreachable", (
            "a configured store that does not answer was reported as one nobody "
            "configured, which sends an operator to the wrong fix")

    def test_a_healthy_lock_leaves_no_marker_to_find(self, store, tmp_path,
                                                     _a_record_of_our_own):
        api_module._redis_lock("sync:stu-1:exam-1")

        assert not (tmp_path / "marker.json").exists(), (
            "the healthy path wrote to the filesystem on every sync")


# ── the shape the caller depends on ──────────────────────────────────────────

def test_the_call_site_still_reads_a_falsy_handle_as_held():
    """`None` means held, and that is the only value that may mean it.

    The route is `if not rlock: return busy`, so a fallback that returned a falsy
    handle would swallow every write silently — which is the failure this file
    exists to prevent, wearing the opposite mask.
    """
    source = API_PY.read_text(encoding="utf-8")
    assert "if not rlock:" in source, (
        "the caller no longer tests the handle, so what an unheld lock returns "
        "is no longer pinned by anything")
