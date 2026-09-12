"""Simple Redis-backed cache with in-memory fallback.

Usage:
    from app.utils.cache import cache_get, cache_set
    val = cache_get("dashboard:stats:teacher_id")
    if val is None:
        val = expensive_query()
        cache_set("dashboard:stats:teacher_id", val, ttl=60)
"""
import os
import json
import time
import threading
import logging

logger = logging.getLogger(__name__)

# In-memory fallback cache (per-process)
_mem_cache = {}
_mem_lock = threading.Lock()
_mem_max = 200  # max entries

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


def cache_get(key):
    """Get value from cache. Returns None on miss."""
    r = _get_redis()
    if r:
        try:
            raw = r.get("sgcache:" + key)
            if raw:
                return json.loads(raw)
        except Exception:
            pass
    # Fallback: in-memory
    with _mem_lock:
        entry = _mem_cache.get(key)
        if entry and entry[1] > time.time():
            return entry[0]
        if entry:
            del _mem_cache[key]
    return None


def cache_set(key, value, ttl=60):
    """Set value in cache with TTL in seconds."""
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
        except Exception:
            pass
    with _mem_lock:
        _mem_cache.pop(key, None)
