"""Tiny Redis-backed JSON cache with an in-process fallback.

Supabase round-trips cost ~100-165 ms each from this deployment, and several of
them were being paid on *every* authenticated request (token validation, profile
lookup, subscription gate). Caching them for a few seconds collapses that to
near zero for repeat requests while staying short enough that role or billing
changes propagate quickly.

Every operation fails open: if Redis is unavailable or errors, callers simply
fall back to doing the real work, so a cache outage degrades performance rather
than breaking requests.
"""
import json
import logging
import threading
import time

logger = logging.getLogger(__name__)

_PREFIX = "sg:kv:"

# In-process fallback, used only when Redis is not reachable. Bounded so a long
# lived worker can't grow without limit.
_local = {}
_local_lock = threading.Lock()
_LOCAL_MAX = 1024


def _redis():
    """Reuse the rate limiter's pooled, health-checked connection."""
    try:
        from app.utils.rate_limiter import _get_redis_conn
        return _get_redis_conn()
    except Exception:
        return None


def _local_get(key):
    with _local_lock:
        entry = _local.get(key)
    if entry and entry[0] > time.time():
        return entry[1]
    return None


def cache_get(key):
    """Return the cached value, or None on miss/expiry/error."""
    full = _PREFIX + key
    conn = _redis()
    if conn is not None:
        try:
            raw = conn.get(full)
            return json.loads(raw) if raw else None
        except Exception as e:
            logger.debug("kv cache get failed: %s", e)
            return None
    return _local_get(full)


def cache_set(key, value, ttl):
    """Store a JSON value for ``ttl`` seconds. A non-positive ttl disables it."""
    if not ttl or ttl <= 0:
        return
    full = _PREFIX + key
    conn = _redis()
    if conn is not None:
        try:
            conn.setex(full, int(ttl), json.dumps(value))
            return
        except Exception as e:
            logger.debug("kv cache set failed: %s", e)

    with _local_lock:
        if len(_local) >= _LOCAL_MAX:
            now = time.time()
            for k in [k for k, v in _local.items() if v[0] <= now]:
                _local.pop(k, None)
            if len(_local) >= _LOCAL_MAX:
                _local.clear()
        _local[full] = (time.time() + ttl, value)


def cache_delete(key):
    """Drop a cached value (e.g. right after the underlying row changed)."""
    full = _PREFIX + key
    conn = _redis()
    if conn is not None:
        try:
            conn.delete(full)
        except Exception as e:
            logger.debug("kv cache delete failed: %s", e)
    with _local_lock:
        _local.pop(full, None)
