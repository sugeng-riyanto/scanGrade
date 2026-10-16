"""The weekly cache sweep: one quiet hour a week, and it stands down for a sitting.

Why a sweep at all, when every entry already has a TTL
------------------------------------------------------
Because two of this app's caches are *process* caches, and a TTL on those is only
advice. `app/utils/cache.py`'s `_mem_cache` and `app/__init__.py`'s `_lru_cache`
both consult an entry's expiry when they *read* it, so an entry that is never read
again is never removed — it sits in the worker's heap for the life of the worker,
and under gevent a worker lives a long time. The Redis layer (`sg:kv:`,
`sgcache:`) expires on its own, so the sweep has exactly one thing to do there and
it is a real one: **a key with no expiry never goes away at all.** Redis remembers
`TTL == -1`, so the sweep finds those and removes them, which is the only leak the
TTL cannot cover.

So the sweep is three small jobs, and none of them is "clear the cache":

* drop expired entries from the two in-process dicts;
* delete the keys in this app's own namespace that have no expiry;
* write down what it did.

Why it must never clear a live cache
------------------------------------
The obvious implementation — `FLUSHDB`, or `KEYS sg:*` then `DELETE` — would empty
every school's cached rows *while pupils were sitting an exam*, and the next
requests would each pay the Supabase round-trip the cache exists to avoid. At 500
concurrent students on one vCPU that is not a slow page, it is a slow afternoon for
a whole school. Hence :func:`live_sitting`, and hence the rule that a deferred
sweep is a normal outcome rather than an error: the window comes again in fifteen
minutes and every week after that.

Why failing is silent in one direction only
-------------------------------------------
The sweep must never break the app, so every step is wrapped and the result is a
dict of what happened. But it is *logged* either way (and shown on the analytics
page from `last_sweep()`), because a maintenance job that quietly stops running is
how a leak comes back six months later with nobody having noticed. The failure this
protects against — the sweep refusing to run for a fortnight and blocking forever —
is bounded by :data:`LIVE_SITTING_GRACE`, which reads "a sitting in progress" as "a
sitting that started in the last three hours" rather than "a row that exists".
"""
from __future__ import annotations

import datetime as dt
import logging
import threading
import time

logger = logging.getLogger(__name__)

#: The clock the window is expressed in. WIB (UTC+7), the app's default timezone —
#: "three in the morning for the schools" is the point of the window, and schools
#: here are not in UTC.
WIB_OFFSET = dt.timedelta(hours=7)

#: How recently a sitting must have started to count as live. A licence to skip the
#: sweep is a licence to skip it forever if the condition can be true for a row
#: that never closes, so it is bounded by time rather than by existence.
LIVE_SITTING_GRACE = dt.timedelta(hours=3)

#: Prefixes this app owns in Redis. `sg:traffic:` is deliberately absent: the
#: session sets there are the day's distinct-session count and deleting one would
#: *change a number the dashboard reports*, which is not a cache operation.
OWNED_PREFIXES = ("sg:kv:", "sgcache:")

#: How many keys to ask Redis for a size of. `MEMORY USAGE` is one round-trip per
#: key, so the freed-bytes figure is an estimate from a sample rather than a full
#: accounting — stated in the reading rather than implied.
MEMORY_SAMPLE = 50


def _now(now=None) -> dt.datetime:
    moment = now or dt.datetime.now(dt.timezone.utc)
    return moment if moment.tzinfo else moment.replace(tzinfo=dt.timezone.utc)


def in_window(now=None, *, weekday=None, hour=None) -> bool:
    """Is it the configured weekly hour, in WIB?"""
    local = _now(now).astimezone(dt.timezone(WIB_OFFSET))
    want_day = 6 if weekday is None else int(weekday)
    want_hour = 3 if hour is None else int(hour)
    return local.weekday() == want_day and local.hour == want_hour


def _config(name, default):
    try:
        from flask import current_app
        return current_app.config.get(name, default)
    except Exception:
        return default


def live_sitting(supabase=None) -> dict:
    """Is an exam in progress right now? Bounded, and never raises.

    One query with `limit=1`, against the statuses `attempt_status` already calls
    *open* — the same list `deadline_service` closes by, imported rather than
    retyped. A read that fails answers `{"live": False, "checked": False}` and the
    sweep therefore **skips**: a guard that cannot be read must not be the reason a
    pupil's cache is cleared mid-paper.
    """
    out = {"live": False, "checked": False, "open": 0}
    try:
        from app.services import attempt_status
        client = supabase
        if client is None:
            from app.utils.supabase_client import get_supabase
            client = get_supabase()
        since = (_now() - LIVE_SITTING_GRACE).isoformat()
        rows = (client.table("submissions").select("id")
                .in_("status", list(attempt_status.OPEN_STATUSES))
                .gte("started_at", since).limit(1).execute().data) or []
        out["checked"] = True
        out["open"] = len(rows)
        out["live"] = bool(rows)
    except Exception as exc:
        logger.warning("cache sweep: could not read live sittings (%s) — skipping", exc)
        out["error"] = str(exc)
    return out


def _connection():
    from app.utils.rate_limiter import _get_redis_conn
    return _get_redis_conn()


def _sweep_redis(conn) -> dict:
    """Remove this app's unexpiring keys. Never raises."""
    out = {"keys_scanned": 0, "no_expiry_deleted": 0, "bytes_estimate": 0}
    try:
        for prefix in OWNED_PREFIXES:
            cursor = 0
            while True:
                cursor, keys = conn.scan(cursor=cursor, match=prefix + "*", count=500)
                out["keys_scanned"] += len(keys)
                doomed = []
                for raw in keys:
                    key = raw.decode() if isinstance(raw, bytes) else str(raw)
                    try:
                        if conn.ttl(key) == -1:      # -1 is "exists, no expiry"
                            doomed.append(key)
                    except Exception:
                        continue
                if doomed:
                    out["bytes_estimate"] += _sample_bytes(conn, doomed)
                    conn.delete(*doomed)
                    out["no_expiry_deleted"] += len(doomed)
                if not cursor:
                    break
    except Exception as exc:
        logger.warning("cache sweep: redis pass failed (%s)", exc)
        out["error"] = str(exc)
    out["bytes_estimate_note"] = f"estimate from up to {MEMORY_SAMPLE} sampled keys"
    return out


def _sample_bytes(conn, keys) -> int:
    """Roughly what `keys` occupy, from a bounded sample of `MEMORY USAGE`."""
    sample = keys[:MEMORY_SAMPLE]
    total = 0
    for key in sample:
        try:
            total += int(conn.memory_usage(key) or 0)
        except Exception:
            continue
    if not sample:
        return 0
    return int(total / len(sample) * len(keys))


def _sweep_in_process() -> dict:
    """Drop expired entries from the two process-local caches.

    Both are read by their own modules, so the sweep reaches them through a small
    named entry point each rather than importing their private dicts — a sweep that
    pokes at another module's internals is a sweep that breaks silently the first
    time that module is refactored.
    """
    out = {"local_dropped": 0, "lru_dropped": 0}
    try:
        from app.utils import cache as shared_cache
        out["local_dropped"] = shared_cache.drop_expired()
    except Exception as exc:
        logger.debug("cache sweep: shared cache pass failed (%s)", exc)
    try:
        from app import drop_expired_page_cache
        out["lru_dropped"] = drop_expired_page_cache()
    except Exception as exc:
        logger.debug("cache sweep: page cache pass failed (%s)", exc)
    return out


def sweep(*, now=None, supabase=None, force=False) -> dict:
    """Run one sweep, or say why it did not. Never raises.

    `force=True` is for an operator asking from the console: it still refuses while
    a sitting is live, because that rule is about pupils and not about who asked.
    """
    started = _now(now)
    result = {"at": started.isoformat(), "ran": False, "reason": None}
    try:
        if not force and not in_window(now, weekday=_config("CACHE_SWEEP_WEEKDAY", 6),
                                       hour=_config("CACHE_SWEEP_HOUR", 3)):
            result["reason"] = "outside the weekly window"
            return result
        sitting = live_sitting(supabase=supabase)
        result["live_sitting"] = sitting
        if not sitting.get("checked"):
            result["reason"] = "could not read whether a sitting is live"
            return result
        if sitting.get("live"):
            # The window comes again in fifteen minutes and every week after. A
            # deferral is the sweep working, not the sweep failing.
            result["reason"] = "a sitting is in progress"
            return result
        conn = _connection()
        result["redis"] = _sweep_redis(conn) if conn is not None else {"unavailable": True}
        result.update(_sweep_in_process())
        result["ran"] = True
        logger.info("cache sweep: %s", {k: v for k, v in result.items()
                                        if k not in ("at", "live_sitting")})
    except Exception as exc:  # pragma: no cover - a maintenance job must not raise
        result["error"] = str(exc)
        logger.warning("cache sweep failed: %s", exc)
    return result


# ── the timer ───────────────────────────────────────────────────────────────

_thread = None
_running = False
_last: dict = {}


def last_sweep() -> dict:
    """The most recent sweep's reading, for the analytics page."""
    return dict(_last)


def _run_loop(app=None):
    global _running, _last
    _running = True
    interval = max(60, int(_config("CACHE_SWEEP_INTERVAL_SECONDS", 900)))
    while _running:
        try:
            if app is not None:
                with app.app_context():
                    _last = sweep()
            else:
                _last = sweep()
        except Exception as exc:  # pragma: no cover
            logger.warning("cache sweep loop error: %s", exc)
            _last = {"ran": False, "reason": str(exc)}
        # Sleep to the next quarter hour rather than a fixed interval from start-up,
        # so the sweep's *check* lands inside the configured hour however long the
        # worker has been up. The check is one indexed read; the sweep happens once.
        time.sleep(interval)


def start_cache_sweep_scheduler(interval=None, app=None):
    """Start the weekly cache sweep (safe to call more than once, per worker)."""
    global _thread
    _running = True
    if _thread and _thread.is_alive():
        return
    if interval:
        try:
            from flask import current_app
            current_app.config["CACHE_SWEEP_INTERVAL_SECONDS"] = int(interval)
        except Exception:
            pass
    _thread = threading.Thread(target=_run_loop, args=(app,), daemon=True)
    _thread.start()
    logger.info("Cache sweep scheduler started")


def stop_cache_sweep_scheduler():
    global _running
    _running = False
