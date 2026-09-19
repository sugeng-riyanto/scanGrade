"""Simple Redis-backed cache with in-memory fallback.

Usage:
    from app.utils.cache import cache_get, cache_set
    val = cache_get("dashboard:stats:teacher_id")
    if val is None:
        val = expensive_query()
        cache_set("dashboard:stats:teacher_id", val, ttl=60)

A hit also reports what the page cost
-------------------------------------
The release performance gate compares the Supabase round-trips a page spends against
the last release that passed, because that is the count an N+1 multiplies. A cache
would hide it: a hit spends nothing, so the cache miss is the only render that does
the work, and on a 30-second TTL it may not occur during the run at all. Measured on
the running app, `/student/dashboard` answered 12 requests in 14 seconds with **0**
queries every time — the entry was warmed by the login redirect and never expired.
The gate would have been blind to that page, which is the busiest one a student has.

So `cache_set` remembers the round-trip count the entry was *built* with, and
`cache_get` replays it into the meter on a hit. The header then answers "what does
this page cost when it does its work", which is the number that is stable across
releases and identical whether the cache was warm or cold. It is deliberately not
"what this request spent" — a metric that reads 0 whenever a cache is warm measures
the cache, not the release.
"""
import os
import json
import time
import threading
import logging

from app.utils import query_meter

logger = logging.getLogger(__name__)

# In-memory fallback cache (per-process)
_mem_cache = {}
_mem_lock = threading.Lock()
_mem_max = 200  # max entries

#: What each entry cost to build, keyed like the entry, so a hit can report the
#: page's real cost instead of zero. See the module docstring.
_COST_PREFIX = "sgcachecost:"
_mem_cost = {}

_redis_pool = None
_redis_lock = threading.Lock()
_redis_available = False
_redis_attempted = False


def _get_redis():
    global _redis_pool, _redis_available, _redis_attempted
    if _redis_attempted and not _redis_available:
        return None
    if _redis_pool is not None:
        return _redis_pool
    with _redis_lock:
        if _redis_pool is not None:
            return _redis_pool
        _redis_attempted = True
        try:
            from redis import Redis
            from redis.connection import ConnectionPool
            url = os.environ.get("REDIS_URL", "")
            if not url:
                return None
            pool = ConnectionPool.from_url(url, max_connections=10, decode_responses=True)
            r = Redis(connection_pool=pool)
            r.ping()
            _redis_pool = r
            _redis_available = True
            logger.info("Cache: Redis connected")
            return _redis_pool
        except Exception as e:
            logger.debug("Cache: Redis unavailable: %s", e)
            _redis_available = False
            return None


def _remember_cost(key, ttl):
    """Record what building this entry cost. A no-op outside a request."""
    cost = query_meter.spent()
    if cost is None:
        return
    r = _get_redis()
    if r:
        try:
            r.setex(_COST_PREFIX + key, ttl, cost)
        except Exception:
            pass
    with _mem_lock:
        if len(_mem_cost) >= _mem_max:
            for k, _ in sorted(_mem_cost.items(), key=lambda x: x[1][1])[:_mem_max // 5]:
                del _mem_cost[k]
        _mem_cost[key] = (cost, time.time() + ttl)


def _replay_cost(key):
    """Add the cost this entry was built with to the meter, if it is known.

    Takes no lock while another is held: `threading.Lock` is not reentrant, and this
    is called from `cache_get` after the in-memory lookup has released it.
    """
    cost = None
    r = _get_redis()
    if r:
        try:
            raw = r.get(_COST_PREFIX + key)
            if raw is not None:
                cost = int(raw)
        except Exception:
            pass
    if cost is None:
        with _mem_lock:
            entry = _mem_cost.get(key)
            if entry and entry[1] > time.time():
                cost = entry[0]
            elif entry:
                del _mem_cost[key]
    if cost:
        query_meter.trip(cost)


def cache_get(key):
    """Get value from cache. Returns None on miss."""
    r = _get_redis()
    if r:
        try:
            raw = r.get("sgcache:" + key)
            if raw:
                _replay_cost(key)
                return json.loads(raw)
        except Exception:
            pass
    # Fallback: in-memory. The value is taken out under the lock and the cost
    # replayed after it is released, because `_replay_cost` takes that same lock.
    found = False
    value = None
    with _mem_lock:
        entry = _mem_cache.get(key)
        if entry and entry[1] > time.time():
            value, found = entry[0], True
        elif entry:
            del _mem_cache[key]
    if found:
        _replay_cost(key)
        return value
    return None


def cache_set(key, value, ttl=60):
    """Set value in cache with TTL in seconds, remembering what it cost to build."""
    _remember_cost(key, ttl)
    r = _get_redis()
    if r:
        try:
            r.setex("sgcache:" + key, ttl, json.dumps(value, default=str))
        except Exception:
            pass
    # Also write to in-memory as fallback
    with _mem_lock:
        if len(_mem_cache) >= _mem_max:
            # Evict oldest 20%
            to_evict = sorted(_mem_cache.items(), key=lambda x: x[1][1])[:_mem_max // 5]
            for k, _ in to_evict:
                del _mem_cache[k]
        _mem_cache[key] = (value, time.time() + ttl)


def cache_delete(key):
    """Delete a cache entry."""
    r = _get_redis()
    if r:
        try:
            r.delete("sgcache:" + key)
            r.delete(_COST_PREFIX + key)
        except Exception:
            pass
    with _mem_lock:
        _mem_cache.pop(key, None)
        _mem_cost.pop(key, None)
