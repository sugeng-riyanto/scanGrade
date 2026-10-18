"""The weekly cache sweep — and the one thing it must refuse to do.

Two halves, and they are the whole design.

**What it cleans.** A TTL on a *process* cache is only advice: both of this app's
in-memory fallbacks check an entry's expiry when they *read* it, so an entry nobody
reads again is never removed and lives until the worker is recycled — and under
gevent a worker lives a long time. In Redis the TTL does apply, with one hole:
`TTL == -1` means the key exists and never expires, and nothing else in this app
will ever remove it. Those are the three things the sweep does, and each has a test
below that fails if the sweep stops doing it.

**What it must not do.** The obvious implementation — flush the cache, or delete
everything under the app's prefix — would empty every school's cached rows *while
pupils were sitting an exam*, and the next request from each of them would pay the
Supabase round-trip the cache exists to avoid. At 500 concurrent students on one
vCPU that is not a slow page, it is a slow afternoon for a whole school. So a live
sitting defers the sweep, and the deferral is a **normal outcome** rather than an
error: the window comes again in fifteen minutes and every week after that.

The guard is bounded in the one direction that matters. Reading it as "an open row
exists" would let a draft that never closes — a paper exam with no duration, a
browser that died before the sweep noticed — block the sweep forever, and a
maintenance job that silently stops running is how a leak comes back six months
later with nobody having noticed. `LIVE_SITTING_GRACE` reads "in progress" as
"started within the last three hours" instead.
"""
from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import cache_maintenance_service as sweep

ROOT = Path(__file__).resolve().parents[2]

SUNDAY_0300_WIB = dt.datetime(2026, 10, 17, 20, 0, tzinfo=dt.timezone.utc)  # Sun 03:00 WIB


class FakeRedis:
    """`scan`, `ttl`, `delete`, `memory_usage` — and a record of what was deleted."""

    def __init__(self, keys=None, no_expiry=(), ttl=300):
        self.keys = list(keys if keys is not None else [])
        self.no_expiry = set(no_expiry)
        self.default_ttl = ttl
        self.deleted = []

    def scan(self, cursor=0, match=None, count=500):
        # `match` is honoured: a fake that returned every key regardless would let
        # the sweep delete a namespace it never asked for and still pass.
        import fnmatch
        keys = [k for k in self.keys if match is None or fnmatch.fnmatch(k, match)]
        return 0, [k.encode() for k in keys]

    def ttl(self, key):
        name = key.decode() if isinstance(key, bytes) else key
        return -1 if name in self.no_expiry else self.default_ttl

    def delete(self, *keys):
        for key in keys:
            name = key.decode() if isinstance(key, bytes) else key
            self.deleted.append(name)
            self.keys = [k for k in self.keys if k != name]
        return len(keys)

    def memory_usage(self, key):
        return 100


class FakeSitting:
    """The `submissions` read `live_sitting` makes, and nothing else of it."""

    def __init__(self, rows=None, fail=False):
        self.rows = rows or []
        self.fail = fail
        self.filters = []

    def table(self, name):
        assert name == "submissions"
        outer = self

        class Query:
            def select(self, *a, **k):
                return self

            def in_(self, column, values):
                outer.filters.append(("in", column, tuple(values)))
                return self

            def gte(self, column, value):
                outer.filters.append(("gte", column, value))
                return self

            def limit(self, n):
                outer.filters.append(("limit", "limit", n))
                return self

            def execute(self):
                if outer.fail:
                    raise RuntimeError("the database is unreachable")
                return SimpleNamespace(data=outer.rows)

        return Query()


@pytest.fixture(autouse=True)
def _no_redis(monkeypatch):
    """Every test starts with no Redis, and the sweep tolerates that.

    The app's own `_connection` rides on the rate limiter's pooled handle, which in
    the suite points at a Redis that is not there. Tests that want one inject it.
    """
    monkeypatch.setattr(sweep, "_connection", lambda: None)


# ── the window ──────────────────────────────────────────────────────────────

class TestTheWindow:
    def test_three_in_the_morning_wib_is_the_window(self):
        assert sweep.in_window(SUNDAY_0300_WIB) is True

    def test_the_window_is_read_in_wib_not_utc(self):
        """20:00 UTC on Saturday is Sunday 03:00 in Jakarta, and that is the point."""
        assert sweep.in_window(dt.datetime(2026, 10, 17, 20, 0,
                                           tzinfo=dt.timezone.utc)) is True
        assert sweep.in_window(dt.datetime(2026, 10, 17, 3, 0,
                                           tzinfo=dt.timezone.utc)) is False

    def test_the_day_and_hour_come_from_configuration(self):
        assert sweep.in_window(dt.datetime(2026, 10, 15, 5, 15,
                                           tzinfo=dt.timezone.utc),
                               weekday=3, hour=12) is True


# ── the guard ───────────────────────────────────────────────────────────────

class TestTheLiveSittingGuard:
    def test_it_asks_for_the_statuses_the_rest_of_the_app_calls_open(self):
        """One list, imported — not retyped. `deadline_service` closes by the same one."""
        from app.services import attempt_status
        fake = FakeSitting()
        sweep.live_sitting(supabase=fake)
        statuses = [value for kind, column, value in fake.filters if kind == "in"]
        assert statuses and statuses[0] == tuple(attempt_status.OPEN_STATUSES)

    def test_it_bounds_the_question_by_time_so_it_can_never_block_forever(self):
        fake = FakeSitting()
        before = dt.datetime.now(dt.timezone.utc)
        sweep.live_sitting(supabase=fake)
        after = dt.datetime.now(dt.timezone.utc)
        gte = [value for kind, column, value in fake.filters if kind == "gte"]
        assert gte, "a guard that cannot clear itself is a guard that stops the sweep"
        since = dt.datetime.fromisoformat(gte[0])
        # Bound against the actual call interval; timedelta approx rounds to exact
        # equality and fails on normal microseconds of elapsed wall time.
        assert before - sweep.LIVE_SITTING_GRACE <= since <= after - sweep.LIVE_SITTING_GRACE

    def test_it_reads_one_row_and_not_a_count(self):
        fake = FakeSitting()
        sweep.live_sitting(supabase=fake)
        assert [n for kind, _, n in fake.filters if kind == "limit"] == [1]

    def test_an_unreadable_check_answers_not_checked_rather_than_not_live(self):
        state = sweep.live_sitting(supabase=FakeSitting(fail=True))
        assert state["checked"] is False and state["live"] is False
        assert state["error"]

    def test_an_in_progress_sitting_is_reported_live(self):
        state = sweep.live_sitting(supabase=FakeSitting(rows=[{"id": "s-1"}]))
        assert state == {"live": True, "checked": True, "open": 1}


# ── the sweep itself ────────────────────────────────────────────────────────

class TestTheSweep:
    def test_outside_the_window_it_does_nothing_and_says_why(self):
        outcome = sweep.sweep(now=dt.datetime(2026, 10, 14, 4, 0,
                                              tzinfo=dt.timezone.utc))
        assert outcome["ran"] is False
        assert outcome["reason"] == "outside the weekly window"

    def test_a_sitting_in_progress_defers_the_sweep(self):
        """The property the whole module exists for.

        With a sitting live and a Redis full of unexpiring keys, nothing is deleted.
        A sweep that cleared them here would hand every pupil in that exam a cache
        miss on their next page — the cost the cache was built to remove, charged at
        the worst possible moment.
        """
        fake = FakeRedis(keys=["sg:kv:school:a"], no_expiry=["sg:kv:school:a"])
        outcome = sweep.sweep(now=SUNDAY_0300_WIB, supabase=FakeSitting(rows=[{"id": "s"}]))
        assert outcome["ran"] is False
        assert outcome["reason"] == "a sitting is in progress"
        assert fake.deleted == []

    def test_a_forced_sweep_still_defers_for_a_live_sitting(self):
        """`force` is for an operator asking, and the rule is about pupils.

        Whoever asks is not the point: an operator who forces a sweep during an exam
        would be doing the thing the guard exists to prevent, with their name on it.
        """
        outcome = sweep.sweep(now=SUNDAY_0300_WIB, force=True,
                              supabase=FakeSitting(rows=[{"id": "s"}]))
        assert outcome["ran"] is False
        assert outcome["reason"] == "a sitting is in progress"

    def test_an_unreadable_guard_skips_rather_than_clearing(self, monkeypatch):
        monkeypatch.setattr(sweep, "_connection", lambda: FakeRedis(keys=["sg:kv:a"]))
        outcome = sweep.sweep(now=SUNDAY_0300_WIB,
                              supabase=FakeSitting(fail=True))
        assert outcome["ran"] is False
        assert outcome["reason"] == "could not read whether a sitting is live"

    def test_it_deletes_the_keys_that_would_never_expire(self, monkeypatch):
        leaky = FakeRedis(keys=["sg:kv:school:a", "sgcache:report:1", "sg:traffic:sess:x"],
                          no_expiry=["sg:kv:school:a", "sgcache:report:1"])
        monkeypatch.setattr(sweep, "_connection", lambda: leaky)
        outcome = sweep.sweep(now=SUNDAY_0300_WIB, supabase=FakeSitting())
        assert outcome["ran"] is True
        assert sorted(leaky.deleted) == ["sg:kv:school:a", "sgcache:report:1"]

    def test_it_does_not_touch_a_key_outside_this_apps_namespace(self, monkeypatch):
        """`sg:traffic:` is deliberately not swept: deleting a day's session set
        would *change a number the dashboard reports*, which is not a cache
        operation at all."""
        redis = FakeRedis(keys=["sg:traffic:sess:2026-10-17:-"], no_expiry=[
            "sg:traffic:sess:2026-10-17:-"])
        monkeypatch.setattr(sweep, "_connection", lambda: redis)
        outcome = sweep.sweep(now=SUNDAY_0300_WIB, supabase=FakeSitting())
        assert outcome["ran"] is True
        assert redis.deleted == []
        assert not any("traffic" in prefix for prefix in sweep.OWNED_PREFIXES)

    def test_a_key_with_a_ttl_is_left_alone(self, monkeypatch):
        redis = FakeRedis(keys=["sg:kv:school:a"], no_expiry=[])
        monkeypatch.setattr(sweep, "_connection", lambda: redis)
        sweep.sweep(now=SUNDAY_0300_WIB, supabase=FakeSitting())
        assert redis.deleted == [], "Redis expires it; deleting it early is a miss we caused"

    def test_it_drops_the_expired_entries_of_the_process_caches(self):
        outcome = sweep.sweep(now=SUNDAY_0300_WIB, supabase=FakeSitting())
        assert outcome["ran"] is True
        assert "local_dropped" in outcome and "lru_dropped" in outcome

    def test_a_failing_redis_pass_does_not_stop_the_sweep(self, monkeypatch):
        class Broken(FakeRedis):
            def scan(self, cursor=0, match=None, count=500):
                raise RuntimeError("connection reset")

        monkeypatch.setattr(sweep, "_connection", lambda: Broken(keys=["sg:kv:a"]))
        outcome = sweep.sweep(now=SUNDAY_0300_WIB, supabase=FakeSitting())
        assert outcome["ran"] is True, "one failing pass must not abort the others"
        assert outcome["redis"]["error"]

    def test_it_never_raises_even_when_everything_is_broken(self, monkeypatch):
        monkeypatch.setattr(sweep, "_connection",
                            lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        outcome = sweep.sweep(now=SUNDAY_0300_WIB, supabase=FakeSitting())
        assert outcome["error"], "the failure is reported rather than swallowed"
        assert outcome["ran"] is False

    def test_no_redis_at_all_is_a_normal_outcome(self, monkeypatch):
        outcome = sweep.sweep(now=SUNDAY_0300_WIB, supabase=FakeSitting())
        assert outcome["ran"] is True
        assert outcome["redis"] == {"unavailable": True}


# ── the two process caches ──────────────────────────────────────────────────

class TestTheProcessCaches:
    def test_the_shared_cache_drops_only_what_has_expired(self):
        import time as _time

        from app.utils import cache as shared
        with shared._mem_lock:
            shared._mem_cache.clear()
            shared._mem_cost.clear()
        shared.cache_set("fresh", {"v": 1}, ttl=300)
        with shared._mem_lock:
            shared._mem_cache["stale"] = ("gone", _time.time() - 1)
            shared._mem_cost["stale"] = (3, _time.time() - 1)
        assert shared.drop_expired() == 1
        with shared._mem_lock:
            assert "fresh" in shared._mem_cache and "stale" not in shared._mem_cache
            assert "stale" not in shared._mem_cost

    def test_the_page_cache_drops_nothing_younger_than_its_bound(self):
        """Dropping a live entry is a cache miss the sweep invented."""
        import app as app_module
        app_module._lru_cache.clear()
        app_module._lru_cache_ttl.clear()
        app_module.cache_set("page", {"v": 1})
        assert app_module.drop_expired_page_cache() == 0
        app_module._lru_cache_ttl["page"] = 0        # long stale
        assert app_module.drop_expired_page_cache() == 1
        assert app_module._lru_cache == {}


# ── the wiring ──────────────────────────────────────────────────────────────

class TestTheWiring:
    def test_the_sweep_starts_with_the_apps_other_timers(self):
        source = (ROOT / "app" / "__init__.py").read_text(encoding="utf-8")
        assert "start_cache_sweep_scheduler" in source
        block = source.split("START_BACKGROUND_SCHEDULERS", 1)[1]
        assert "start_cache_sweep_scheduler" in block, (
            "a timer that is never started is a maintenance job that never runs")
        assert "START_BACKGROUND_SCHEDULERS" in source.split(
            "start_cache_sweep_scheduler", 1)[0], (
            "it must sit behind the same switch as every other loop, or the deploy "
            "probe and the suite would start a weekly timer against a real database")

    def test_the_default_window_is_the_quiet_hour_the_rollup_reports(self):
        from app.config import Config
        assert Config.CACHE_SWEEP_WEEKDAY == 6      # Sunday
        assert Config.CACHE_SWEEP_HOUR == 3         # 03:00 WIB

    def test_the_sweep_reports_its_result_rather_than_only_logging_it(self):
        source = (ROOT / "app" / "services" / "cache_maintenance_service.py"
                  ).read_text(encoding="utf-8")
        assert "def last_sweep" in source
        assert re.search(r"logger\.info\(\"cache sweep", source), (
            "a maintenance job whose only record is a page nobody opened is a "
            "maintenance job that stops without anyone noticing")
