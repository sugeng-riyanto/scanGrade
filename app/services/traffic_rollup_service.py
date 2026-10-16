"""Where the platform's own traffic numbers come from, without a second system.

Why this is not an analytics product
------------------------------------
Most of the people this app is for are children, and what they do here is their
schoolwork. So the rule this module is built to is the narrow one: **nothing here
is per-person.** There is no student id, no user id, no IP and no raw cookie in
any row it writes, and no rows are ever sent to a third party — the numbers are
counted on this box and stay on this box. What it keeps is counts: requests per
hour and per day, by route group, by status class, by coarse device class, plus a
*count* of distinct sessions that day (a `SCARD` over hashes, not a list of who).

The detail that would make a child identifiable — which questions they answered,
how long they looked at one — is deliberately absent, and the places it lives
(`submissions.answers`, `violation_logs`, `seb_access_log`) already have their own
readers with their own permissions. Duplicating them here would be a second copy
of a child's record in a table with a different audience.

Why it reuses what is already here
----------------------------------
`/health` and `/metrics/processes` answer the *box*; `X-Supabase-Roundtrips`
answers what one render *cost*; Sentry answers what *broke*. None of them answers
"how much was this used, on which route group, and is it getting slower" — but all
of them are already wired, and this module is built on the same box primitives
rather than beside them: the same Redis (`app.utils.kv_cache`'s connection), the
same `get_supabase()` client, the same in-process scheduler shape as
`cleanup_service`/`data_retention_service`, and the same Sentry install for the
error half (the dashboard links to it; nothing new captures errors).

Two sources, one shape
----------------------
* **Live**: an `after_request` hook calls :func:`note_request` once per request.
  It costs a dict lookup and a few integer adds — no round-trip, no Redis call on
  the hot path except the session set — and the numbers are flushed on a timer.
* **Backfill**: :func:`rollup_from_access_log` reads gunicorn's existing
  `/var/log/scangrade/access.log` (format in `gunicorn.conf.py`) through the same
  accumulator, for the hours before this release existed and for a box where the
  app was down. Its one limit is stated rather than papered over: **the access log
  has no school in it**, so a backfilled row is platform-wide (`school_id` NULL)
  and the per-school panel will simply have nothing to show for those hours.

Why the flush empties the buffer before it sends
------------------------------------------------
`traffic_rollup_add` *adds* a batch (see migration 065). A batch that were sent
twice would therefore double-count. So :func:`flush` takes the buffer out of the
shared dict and clears it *before* the RPC: a send that fails loses one minute of
aggregate statistics, which is the smaller of the two errors by a wide margin.
Requests are attributed to the hour they arrived in, so a lost minute is a small
undercount of a bucket, not a wrong bucket.

What is never invented
----------------------
`unique_sessions` comes from Redis and only from Redis. When Redis is unavailable
the key is simply absent from the batch — it is not written as zero, because a
zero there reads as "nobody used the system today", the one answer the reading
must never make up. The same rule the rest of this repository follows for a
process that cannot be read or `/proc/stat` that cannot be opened.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import logging
import os
import re
import threading
import time
from bisect import bisect_left

logger = logging.getLogger(__name__)

#: Upper bound of each duration bucket, in milliseconds, and the reason there is a
#: `+1`: the last bucket is everything above the last bound, so a 40-second request
#: lands somewhere instead of nowhere. Percentiles are then read out of the counts
#: (see :func:`percentile_from_buckets`) rather than from stored samples — a stored
#: sample list is a per-request record, which is exactly what this module is not.
DURATION_BOUNDS_MS = (
    5, 10, 25, 50, 75, 100, 150, 200, 300, 500, 750, 1000, 1500, 2000, 3000,
    5000, 7500, 10000, 15000, 20000, 30000, 45000, 60000, 90000, 120000,
    180000, 300000, 600000, 1800000, 3600000,
)
DURATION_BUCKET_COUNT = len(DURATION_BOUNDS_MS) + 1

#: Route groups, longest prefix first: a path is filed under the first entry that
#: matches. Ordered rather than a dict because `/teacher/analysis` and
#: `/teacher` both start with `/teacher`, and only the list can express "try the
#: specific one first".
ROUTE_GROUPS = (
    ("/api/scan", "/api/scan"),
    ("/api/student", "/api/student"),
    ("/api/teacher", "/api/teacher"),
    ("/api", "/api"),
    ("/student", "/student"),
    ("/teacher", "/teacher"),
    ("/admin-sekolah", "/admin-sekolah"),
    ("/super-admin", "/super-admin"),
    ("/principal", "/principal"),
    ("/auth", "/auth"),
    ("/guide", "/guide"),
    ("/static", "/static"),
    ("/health", "/health"),
    ("/metrics", "/metrics"),
)

#: Everything not covered above. A named bucket rather than a dropped request,
#: because "how much traffic do we not know how to classify" is itself a useful
#: number on a page whose job is to say where the load is.
OTHER_GROUP = "/other"

#: `sg:traffic:sess:<day>` — a Redis set of *hashes* of visitor keys, never the
#: keys themselves. 48 hours is longer than a day so the final reading of a day
#: can still be taken just after midnight, and short enough that the sets never
#: become a standing record.
SESSION_KEY = "sg:traffic:sess:{}:{}"
SESSION_PLATFORM = "-"
SESSION_TTL = 48 * 3600

_MOBILE_HINTS = ("android", "iphone", "ipad", "ipod", "mobile", "windows phone")
_TABLET_HINTS = ("ipad", "tablet")


def route_group(path: str) -> str:
    """The group a request path belongs to. Never raises, never returns ""."""
    path = path or "/"
    for prefix, group in ROUTE_GROUPS:
        if path.startswith(prefix):
            return group
    return OTHER_GROUP


def device_class(user_agent: str) -> str:
    """`mobile` / `desktop` / `other` — the coarse split, from the UA alone.

    Deliberately coarse: a tablet is a *mobile* device for the panel's question
    ("are our pupils on phones?"), and nothing here is a fingerprint. An empty or
    unhelpful UA is `other`, which is a real answer — curl and the deploy smoke
    test are `other` — rather than being folded into desktop.
    """
    ua = (user_agent or "").lower()
    if not ua:
        return "other"
    if any(h in ua for h in _MOBILE_HINTS):
        return "mobile"
    if any(t in ua for t in ("windows", "macintosh", "linux", "x11", "crkey")):
        return "desktop"
    return "other"


def status_class(status: int) -> str:
    """`2xx` / `3xx` / `4xx` / `5xx`. Anything outside them counts as `5xx`.

    A 1xx or a code a proxy invented is not something this table can express, and
    dropping it would make "requests" and the four classes disagree — a page whose
    whole point is arithmetic that adds up must not have a remainder.
    """
    try:
        code = int(status)
    except (TypeError, ValueError):
        return "status_5xx"
    if 200 <= code <= 299:
        return "status_2xx"
    if 300 <= code <= 399:
        return "status_3xx"
    if 400 <= code <= 499:
        return "status_4xx"
    return "status_5xx"


def bucket_index(duration_ms: float) -> int:
    """Which bucket a duration belongs in. Above the last bound is the last one.

    `bisect_left`, not `bisect_right`, and the difference is one bucket on every
    value that happens to equal a bound: `bisect_right` places a 5000 ms request
    *after* the 5000 ms bound, so it would be reported as the next bucket up and
    every percentile that landed on a round number would read one step slow. Bucket
    `i` means "at or below `DURATION_BOUNDS_MS[i]`", which is what `bisect_left`
    answers. (`bisect_left(0)` is bucket 0 — the fastest bucket, never an error.)
    """
    try:
        value = float(duration_ms)
    except (TypeError, ValueError):
        value = 0.0
    return min(bisect_left(DURATION_BOUNDS_MS, value), DURATION_BUCKET_COUNT - 1)


def percentile_from_buckets(counts, q: float) -> float:
    """The `q`-th percentile of a duration histogram, in milliseconds.

    Bucketed, so the answer is the *upper bound of the bucket the percentile falls
    in* — a p95 of `300` means "the 95th percentile is at or below 300 ms", which
    is what the number is for. Interpolating inside the bucket would look more
    precise and be a guess, because the samples are gone by then.

    Returns 0.0 for an empty histogram rather than raising: a route group with no
    traffic that hour has no latency, and the page shows a gap for it.
    """
    counts = list(counts or [])
    total = sum(counts)
    if total <= 0:
        return 0.0
    target = max(1, int(-(-total * float(q) // 1)))  # ceil(total * q)
    seen = 0
    for index, count in enumerate(counts):
        seen += count
        if seen >= target:
            return float(DURATION_BOUNDS_MS[index]) if index < len(DURATION_BOUNDS_MS) \
                else float(DURATION_BOUNDS_MS[-1])
    return float(DURATION_BOUNDS_MS[-1])


def percentile_series(rows) -> dict:
    """`{"p50": …, "p95": …, "p99": …}` over several rows' histograms combined.

    Summed element-wise first, because a percentile of a percentile is not a
    percentile: combining per-row p95s by averaging them would understate the tail
    exactly when a few slow requests are the point.
    """
    merged = [0] * DURATION_BUCKET_COUNT
    for row in rows or []:
        counts = row.get("duration_bucket_counts") or []
        for index, count in enumerate(counts):
            if index < DURATION_BUCKET_COUNT:
                merged[index] += int(count or 0)
    return {q: percentile_from_buckets(merged, p)
            for q, p in (("p50", 0.50), ("p95", 0.95), ("p99", 0.99))}


# ── the accumulator ─────────────────────────────────────────────────────────

_LOCK = threading.Lock()
_HOUR: dict = {}
_DAY: dict = {}
#: (day, school_key) -> nothing; the values are the Redis sets' identities. A
#: bounded map of the last few days, so a flush just after midnight still writes
#: yesterday's final count instead of freezing it a minute early.
_SESSION_DAYS: dict = {}
_SESSION_DAY_ORDER: list = []
SESSION_DAY_KEEP = 3


def _blank() -> dict:
    return {"requests": 0, "status_2xx": 0, "status_3xx": 0, "status_4xx": 0,
            "status_5xx": 0, "device_mobile": 0, "device_desktop": 0,
            "device_other": 0, "duration_bucket_counts": [0] * DURATION_BUCKET_COUNT,
            "duration_sum_ms": 0.0, "duration_max_ms": 0, "school_id": None}


def _bump(store, key, school_id, status, device, duration_ms):
    entry = store.get(key)
    if entry is None:
        entry = store[key] = _blank()
    entry["requests"] += 1
    entry[status_class(status)] += 1
    entry["device_" + device] += 1
    try:
        ms = float(duration_ms)
    except (TypeError, ValueError):
        ms = 0.0
    entry["duration_bucket_counts"][bucket_index(ms)] += 1
    entry["duration_sum_ms"] += ms
    if ms > entry["duration_max_ms"]:
        entry["duration_max_ms"] = int(ms)
    if school_id and not entry["school_id"]:
        entry["school_id"] = str(school_id)


def note_request(path, status, duration_ms, user_agent="", school_id=None, *, at=None):
    """Record one request. Called from the `after_request` hook; must never raise.

    A failure here would take down the page it is measuring, so every conversion
    is defensive and the whole body is inside a `try`. Losing a request from the
    statistics is a smaller problem than losing the request.
    """
    try:
        moment = at or dt.datetime.now(dt.timezone.utc)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=dt.timezone.utc)
        hour = moment.replace(minute=0, second=0, microsecond=0)
        day = hour.replace(hour=0)
        group = route_group(path)
        device = device_class(user_agent)
        with _LOCK:
            _bump(_HOUR, (hour, group, str(school_id or "")), school_id,
                  status, device, duration_ms)
            _bump(_DAY, (day, group, str(school_id or "")), school_id,
                  status, device, duration_ms)
    except Exception:  # pragma: no cover - the hook itself must never raise
        logger.debug("traffic note_request failed", exc_info=True)


def _session_key(day, school_id) -> str:
    return SESSION_KEY.format(day.strftime("%Y-%m-%d"), school_id or SESSION_PLATFORM)


def note_session(visitor, school_id=None, *, at=None) -> None:
    """Count this visitor as active today — as a *hash*, in Redis, and nowhere else.

    `visitor` is whatever identifies a session (the session token); it is hashed
    with a fixed prefix and never stored or logged. Two commands go out in one
    pipeline (the platform set and, when there is one, the school's), so the hot
    path pays one round-trip to a local Redis.

    Silently does nothing when Redis is unavailable. That is the correct reading:
    the panel then shows no session number for the day rather than a wrong one,
    and no page is slowed by a cache outage.
    """
    try:
        from app.utils.rate_limiter import _get_redis_conn
        conn = _get_redis_conn()
        if conn is None:
            return
        digest = hashlib.sha256(("sg/traffic/" + str(visitor)).encode("utf-8")).hexdigest()
        moment = at or dt.datetime.now(dt.timezone.utc)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=dt.timezone.utc)
        day = moment.replace(hour=0, minute=0, second=0, microsecond=0)
        keys = [_session_key(day, None)]
        if school_id:
            keys.append(_session_key(day, school_id))
        pipe = conn.pipeline()
        for key in keys:
            pipe.sadd(key, digest)
            pipe.expire(key, SESSION_TTL)
        pipe.execute()
        with _LOCK:
            for key in keys:
                identity = (day, key.rpartition(":")[2])
                _SESSION_DAYS.setdefault(identity, True)
                if identity not in _SESSION_DAY_ORDER:
                    _SESSION_DAY_ORDER.append(identity)
            # Only the last few days: writing a *stale* day is pointless work, and
            # the day before yesterday's set is still in Redis for the final read.
            while len(_SESSION_DAY_ORDER) > SESSION_DAY_KEEP * 12:
                _SESSION_DAYS.pop(_SESSION_DAY_ORDER.pop(0), None)
    except Exception:
        logger.debug("traffic note_session failed", exc_info=True)


def _session_rows():
    """`[{period_start, route_group, school_id, unique_sessions}, …]` from Redis.

    One entry per (day, school) actually seen, which is what makes the number a
    *count* rather than a list — `SCARD` never returns a member.
    """
    from app.utils.rate_limiter import _get_redis_conn
    conn = _get_redis_conn()
    if conn is None:
        return []
    with _LOCK:
        identities = list(_SESSION_DAY_ORDER)
    rows = []
    for day, school_key in identities:
        try:
            count = conn.scard(SESSION_KEY.format(day.strftime("%Y-%m-%d"), school_key))
        except Exception:
            continue
        if not count:
            continue
        rows.append({
            "period_start": day.isoformat(),
            "route_group": "",   # the whole day, not one route group
            "school_id": "" if school_key == SESSION_PLATFORM else school_key,
            "unique_sessions": int(count),
        })
    return rows


#: Route group a session row is filed under. The daily table's unique key is
#: (period, group, school), so "the whole day" needs a name; `'*'` cannot collide
#: with a real group because every group starts with `/`.
SESSION_GROUP = "*"


def _take(store) -> dict:
    """Empty the buffer and hand back what was in it, atomically.

    A copy then a `clear()`, under the lock: two workers cannot take the same
    entry, and a request arriving mid-flush lands in the next batch rather than
    being counted twice.
    """
    with _LOCK:
        taken = dict(store)
        store.clear()
        return taken


def flush(*, supabase=None) -> dict:
    """Send everything accumulated since the last call, in one round-trip each.

    Two RPCs: `traffic_rollup_add` for the counters and `traffic_rollup_sessions`
    for the day's distinct-session counts. Two rather than one because they mean
    different things — one is a delta and adds, the other is a total and takes the
    maximum — and folding them together would need a flag inside the SQL to keep
    the load-bearing distinction, which is how a distinction gets lost.

    Returns a small dict for the log and for the tests. Never raises.
    """
    out = {"rows": 0, "sessions": 0, "sent": False, "error": None}
    try:
        buckets_hour = _take(_HOUR)
        buckets_day = _take(_DAY)
        rows = [_row(kind, key, value) for kind, store in (("hour", buckets_hour),
                                                          ("day", buckets_day))
                for key, value in store.items()]
        session_rows = _session_rows()
        out["rows"] = len(rows)
        out["sessions"] = len(session_rows)
        if not rows and not session_rows:
            out["sent"] = True
            return out
        client = supabase
        if client is None:
            from app.utils.supabase_client import get_supabase
            client = get_supabase()
        if rows:
            client.rpc("traffic_rollup_add", {"p_rows": rows}).execute()
        if session_rows:
            for row in session_rows:
                row["route_group"] = SESSION_GROUP
            client.rpc("traffic_rollup_sessions", {"p_rows": session_rows}).execute()
        out["sent"] = True
    except Exception as exc:
        # The buffer is already cleared, on purpose (see the module docstring):
        # one lost minute of aggregate statistics beats a doubled one.
        out["error"] = str(exc)
        logger.warning("traffic rollup flush failed: %s", exc)
    return out


def _row(grain, key, value) -> dict:
    period, group, school_key = key
    return {
        "grain": grain,
        "period_start": period.isoformat(),
        "route_group": group,
        "school_id": school_key,
        "requests": value["requests"],
        "status_2xx": value["status_2xx"],
        "status_3xx": value["status_3xx"],
        "status_4xx": value["status_4xx"],
        "status_5xx": value["status_5xx"],
        "device_mobile": value["device_mobile"],
        "device_desktop": value["device_desktop"],
        "device_other": value["device_other"],
        "duration_bucket_counts": list(value["duration_bucket_counts"]),
        "duration_sum_ms": value["duration_sum_ms"],
        "duration_max_ms": value["duration_max_ms"],
    }


def purge(*, keep_hourly_days=None, keep_daily_days=None, supabase=None) -> dict:
    """Apply the retention window to the rollup tables. Never raises."""
    keep_hourly = int(keep_hourly_days or config_int("TRAFFIC_KEEP_HOURLY_DAYS", 14))
    keep_daily = int(keep_daily_days or config_int("TRAFFIC_KEEP_DAILY_DAYS", 400))
    try:
        client = supabase
        if client is None:
            from app.utils.supabase_client import get_supabase
            client = get_supabase()
        result = client.rpc("traffic_rollup_purge", {
            "p_keep_hourly_days": keep_hourly,
            "p_keep_daily_days": keep_daily,
        }).execute()
        return result.data or {}
    except Exception as exc:
        logger.warning("traffic rollup purge failed: %s", exc)
        return {"error": str(exc)}


def config_int(name, default):
    """A config value, or `default` outside an app context (a script, a test)."""
    try:
        from flask import current_app
        return current_app.config.get(name, default)
    except Exception:
        return int(os.environ.get(name, default) or default)


# ── reading it back ─────────────────────────────────────────────────────────

def _since(days: int) -> str:
    now = dt.datetime.now(dt.timezone.utc).replace(minute=0, second=0, microsecond=0)
    return (now - dt.timedelta(days=max(1, int(days)))).isoformat()


def daily_rows(supabase, days=30) -> list:
    """The daily rollup for the last `days`, oldest first."""
    start = (dt.datetime.now(dt.timezone.utc).replace(hour=0, minute=0, second=0,
                                                      microsecond=0)
             - dt.timedelta(days=max(1, int(days)))).isoformat()
    return (supabase.table("traffic_rollup_daily")
            .select("*").gte("period_start", start)
            .order("period_start").execute().data) or []


def hourly_rows(supabase, days=7) -> list:
    """The hourly rollup for the last `days`, oldest first."""
    return (supabase.table("traffic_rollup_hourly")
            .select("*").gte("period_start", _since(days))
            .order("period_start").execute().data) or []


def _sum(rows, field) -> int:
    return sum(int(r.get(field) or 0) for r in rows)


def build_report(supabase, *, days=30, hourly_days=7) -> dict:
    """Everything the dashboard section renders, from the rollup tables only.

    **Four reads, whatever the range.** The page must not become the load it
    measures, so the work is: one daily read, one hourly read, one settings read
    and one health read — and every panel is computed from those rows in Python.
    No panel may issue its own query; that is the property
    `tests/unit/test_analytics_dashboard.py` pins, because "just one more query"
    is exactly how a seventeen-query page gets built one panel at a time.
    """
    day_rows = daily_rows(supabase, days=days)
    hour_rows = hourly_rows(supabase, days=hourly_days)

    daily = {}

    def _blank_day(stamp):
        return {"date": stamp, "requests": 0, "errors": 0, "status_5xx": 0,
                "status_4xx": 0, "mobile": 0, "desktop": 0, "other": 0,
                "unique_sessions": 0}

    for row in day_rows:
        if row.get("route_group") == SESSION_GROUP:
            continue
        stamp = str(row.get("period_start"))[:10]
        entry = daily.setdefault(stamp, _blank_day(stamp))
        entry["requests"] += int(row.get("requests") or 0)
        entry["status_5xx"] += int(row.get("status_5xx") or 0)
        entry["status_4xx"] += int(row.get("status_4xx") or 0)
        entry["errors"] += int(row.get("status_5xx") or 0)
        entry["mobile"] += int(row.get("device_mobile") or 0)
        entry["desktop"] += int(row.get("device_desktop") or 0)
        entry["other"] += int(row.get("device_other") or 0)

    # The session rows are one per (day, school) under the `*` group; summing the
    # school rows would count a pupil once per school, and taking the platform row
    # alone would lose a day whose platform write failed. `max` over the day's
    # rows is the platform figure and is right in both cases.
    for row in day_rows:
        if row.get("route_group") != SESSION_GROUP:
            continue
        stamp = str(row.get("period_start"))[:10]
        # `setdefault`, not `if stamp in daily`: a day whose only row is a session
        # row would otherwise be dropped from the series entirely, and a gap in a
        # traffic chart is read as an outage rather than as a missing read.
        entry = daily.setdefault(stamp, _blank_day(stamp))
        entry["unique_sessions"] = max(entry["unique_sessions"],
                                       int(row.get("unique_sessions") or 0))

    series = [daily[key] for key in sorted(daily)]
    for entry in series:
        entry["error_rate"] = (entry["errors"] / entry["requests"]
                               if entry["requests"] else 0.0)

    # By route group, over the whole window — and split at the midpoint, because
    # the error card's whole question is "is this worse than it was", which needs
    # the two halves of the same rows rather than a second query.
    midpoint = series[len(series) // 2]["date"] if series else None

    def _half(row):
        return "now" if (midpoint is None
                         or str(row.get("period_start"))[:10] >= midpoint) else "before"

    by_group = {}
    for row in day_rows:
        group = row.get("route_group") or OTHER_GROUP
        if group == SESSION_GROUP:
            continue
        entry = by_group.setdefault(group, {"group": group, "requests": 0,
                                            "errors": 0, "buckets": [],
                                            "now_requests": 0, "now_errors": 0,
                                            "before_requests": 0, "before_errors": 0})
        requests = int(row.get("requests") or 0)
        errors = int(row.get("status_5xx") or 0)
        entry["requests"] += requests
        entry["errors"] += errors
        entry["buckets"].append(row)
        entry[_half(row) + "_requests"] += requests
        entry[_half(row) + "_errors"] += errors
    groups = []
    for entry in by_group.values():
        stats = percentile_series(entry.pop("buckets"))
        entry.update(stats)
        entry["error_rate"] = (entry["errors"] / entry["requests"]
                               if entry["requests"] else 0.0)
        # The comparison pair, both halves of the same window. `error_rate` above is
        # the whole window and is what the table prints; these two are what the trend
        # card compares, because a whole-window rate set against a half-window one
        # shows a rise on a route that has not changed.
        entry["baseline_error_rate"] = (
            entry["before_errors"] / entry["before_requests"]
            if entry["before_requests"] else 0.0)
        entry["now_error_rate"] = (
            entry["now_errors"] / entry["now_requests"]
            if entry["now_requests"] else 0.0)
        groups.append(entry)
    groups.sort(key=lambda g: -g["requests"])

    # Status and device mixes, over the same window.
    status = {"2xx": _sum(day_rows, "status_2xx"), "3xx": _sum(day_rows, "status_3xx"),
              "4xx": _sum(day_rows, "status_4xx"), "5xx": _sum(day_rows, "status_5xx")}
    device = {"mobile": _sum(day_rows, "device_mobile"),
              "desktop": _sum(day_rows, "device_desktop"),
              "other": _sum(day_rows, "device_other")}

    # Per school: requests and the day's distinct sessions, over the window.
    by_school = {}
    for row in day_rows:
        key = row.get("school_id")
        if not key:
            continue
        entry = by_school.setdefault(str(key), {"school_id": str(key), "requests": 0,
                                                "errors": 0, "unique_sessions": 0})
        if row.get("route_group") == SESSION_GROUP:
            entry["unique_sessions"] = max(entry["unique_sessions"],
                                           int(row.get("unique_sessions") or 0))
            continue
        entry["requests"] += int(row.get("requests") or 0)
        entry["errors"] += int(row.get("status_5xx") or 0)
    schools = sorted(by_school.values(), key=lambda s: -s["requests"])

    # The heatmap: hour-of-day x weekday, over the hourly window. This is what
    # answers "when is it safe to deploy", which is why it is here rather than in
    # a notebook: the answer changes every term.
    heat = [[0] * 24 for _ in range(7)]
    for row in hour_rows:
        stamp = row.get("period_start")
        try:
            moment = dt.datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        except ValueError:
            continue
        heat[moment.weekday()][moment.hour] += int(row.get("requests") or 0)
    heat_total = sum(sum(line) for line in heat)
    # The quiet window is an **hour of the day**, summed over the week, not the
    # emptiest single cell. Most cells are empty overnight, so a per-cell minimum is
    # always "Monday 00:00, zero requests" — a true statement that answers nothing.
    # A column sum is the number a deploy window is actually chosen from.
    heat_hours = [sum(heat[d][h] for d in range(7)) for h in range(24)]
    quiet_hour = min(range(24), key=lambda h: heat_hours[h]) if heat_total else None

    # Error trend: this week against the week before, per route group. A ratio of
    # a small number is not a finding, so a group needs a floor of traffic before
    # it can be called "rising" — otherwise one 500 on a quiet route tops the list.
    half = max(1, len(series) // 2)
    recent, previous = series[half:], series[:half]

    return {
        "days": days,
        "hourly_days": hourly_days,
        "first_day": series[0]["date"] if series else None,
        "last_day": series[-1]["date"] if series else None,
        "series": series,
        "hourly": _hourly_series(hour_rows),
        "groups": groups,
        "status": status,
        "device": device,
        "schools": schools,
        "heat": heat,
        "quiet_window": {"hour": quiet_hour, "requests": heat_hours[quiet_hour]
                                        if quiet_hour is not None else 0},
        "totals": {
            "requests": sum(entry["requests"] for entry in series),
            "errors": sum(entry["errors"] for entry in series),
            "sessions": max([entry["unique_sessions"] for entry in series] or [0]),
        },
        "recent": _error_trend(recent),
        "previous": _error_trend(previous),
    }


def _hourly_series(rows) -> list:
    """`[{period_start, requests}, …]` summed over route groups, oldest first."""
    buckets = {}
    for row in rows:
        stamp = str(row.get("period_start"))
        buckets[stamp] = buckets.get(stamp, 0) + int(row.get("requests") or 0)
    return [{"period_start": key, "requests": buckets[key]} for key in sorted(buckets)]


def _error_trend(series) -> dict:
    """Total requests and 5xx for a slice of the daily series."""
    return {"requests": sum(e["requests"] for e in series),
            "errors": sum(e["errors"] for e in series)}


def rising_groups(report, *, floor=200, factor=1.5) -> list:
    """Route groups whose error rate has clearly risen against the earlier half.

    A descriptive reading, never an alarm, and three conditions keep it from
    crying wolf: the group needs `floor` requests *on both sides* of the split
    (a ratio over twelve requests is noise), the recent rate must be non-zero, and
    it must be at least `factor` times the earlier rate with an absolute floor of
    one percent. A route that went 0 % → 0.4 % is not news; one that went 1 % → 4 %
    is, and both numbers are named on the page so the reader can disagree.
    """
    if not report.get("groups"):
        return []
    out = []
    for group in report["groups"]:
        # `floor` on *each half*, because a ratio whose denominator is twelve
        # requests is noise however it is scaled. Comparing per-day rates would need
        # the halves to be the same length, and they are not when the range is odd.
        if int(group.get("now_requests") or 0) < floor:
            continue
        if int(group.get("before_requests") or 0) < floor:
            continue
        rate = float(group.get("now_error_rate") or 0.0)
        baseline_rate = float(group.get("baseline_error_rate") or 0.0)
        if rate < 0.01 or rate < factor * max(baseline_rate, 0.0):
            continue
        requests = int(group.get("requests") or 0)
        out.append({"group": group["group"], "error_rate": rate,
                    "baseline_error_rate": baseline_rate,
                    "requests": requests})
    out.sort(key=lambda g: -g["error_rate"])
    return out


# ── the existing raw source: gunicorn's access log ──────────────────────────

#: `%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s` — the
#: format in `gunicorn.conf.py`. `%(D)s` is **microseconds**, which is what makes
#: this worth parsing at all: the latency panel can be backfilled, not only the
#: counts.
ACCESS_LOG_RE = re.compile(
    r'^(?P<host>\S+)\s+\S+\s+\S+\s+\[(?P<stamp>[^\]]+)\]\s+'
    r'"(?P<request>[^"]*)"\s+(?P<status>\d{3})\s+(?P<bytes>\S+)\s+'
    r'"(?P<referer>[^"]*)"\s+"(?P<agent>[^"]*)"\s+(?P<micros>\d+)'
)
ACCESS_LOG_TIME = "%d/%b/%Y:%H:%M:%S %z"


def parse_access_log_line(line: str):
    """One gunicorn access-log line → `(path, status, duration_ms, user_agent, at)`.

    `None` for a line this format cannot describe — a rotated file's header, a
    truncated tail line, anything else. A parser that guessed at a malformed line
    would put invented numbers in an aggregate, and the whole point of the
    aggregate is that it is trustworthy.
    """
    match = ACCESS_LOG_RE.match((line or "").strip())
    if not match:
        return None
    parts = (match.group("request") or "").split()
    if len(parts) < 2:
        return None
    try:
        moment = dt.datetime.strptime(match.group("stamp"), ACCESS_LOG_TIME)
    except ValueError:
        return None
    try:
        micros = int(match.group("micros"))
    except (TypeError, ValueError):
        micros = 0
    return {
        "path": parts[1],
        "status": int(match.group("status")),
        "duration_ms": micros / 1000.0,
        "user_agent": match.group("agent"),
        "at": moment.astimezone(dt.timezone.utc),
    }


def rollup_from_access_log(path, *, supabase=None, limit=None, flush_every=5000) -> dict:
    """Backfill the rollup from gunicorn's access log, through the same accumulator.

    Reads whatever is there and returns counts; never raises. Lines it cannot parse
    are counted in `skipped` rather than dropped silently, because that number is
    the only evidence a format changed.

    **A backfilled row has no school.** The log carries no session, so
    `school_id` is NULL and the per-school panel stays empty for those hours. That
    is a limitation of the source, stated rather than worked around by guessing.
    """
    out = {"lines": 0, "skipped": 0, "flushes": 0}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            for index, line in enumerate(handle, start=1):
                if limit and index > limit:
                    break
                out["lines"] += 1
                parsed = parse_access_log_line(line)
                if parsed is None:
                    out["skipped"] += 1
                    continue
                note_request(parsed["path"], parsed["status"], parsed["duration_ms"],
                             parsed["user_agent"], None, at=parsed["at"])
                if out["lines"] % flush_every == 0:
                    flush(supabase=supabase)
                    out["flushes"] += 1
        flush(supabase=supabase)
        out["flushes"] += 1
    except OSError as exc:
        out["error"] = str(exc)
    return out


# ── the timer ───────────────────────────────────────────────────────────────

_thread = None
_interval = 60
_running = False
_last: dict = {}


def last_flush() -> dict:
    """When the rollup last ran and what it sent — for the dashboard, not for a log."""
    return dict(_last)


def _run_loop(app=None):
    """Flush this worker's own buffer on a timer, and apply retention every 6 h.

    **One timer per worker, and that is the design, not a duplication.** Each
    worker's buffer holds only the requests *it* served, and `traffic_rollup_add`
    adds, so three workers flushing three buffers is the arithmetic being done
    correctly. A cross-process lock would not make the numbers better; it would
    make two workers' requests wait for the third's tick. The extra cost is two
    round-trips per worker per minute, which is nothing beside what it measures.
    """
    global _running, _last
    _running = True
    last_purge = 0.0
    while _running:
        try:
            if app is not None:
                with app.app_context():
                    _last = {"at": dt.datetime.now(dt.timezone.utc).isoformat(),
                             **flush()}
                    if time.time() - last_purge > 6 * 3600:
                        _last["purge"] = purge()
                        last_purge = time.time()
            else:
                _last = {"at": dt.datetime.now(dt.timezone.utc).isoformat(), **flush()}
            if _last.get("error"):
                logger.warning("traffic rollup: %s", _last["error"])
        except Exception as exc:  # pragma: no cover - a timer must not die
            logger.warning("traffic rollup loop error: %s", exc)
            _last = {"at": dt.datetime.now(dt.timezone.utc).isoformat(),
                     "error": str(exc)}
        time.sleep(max(5, int(cfg_interval())))


def cfg_interval() -> int:
    return config_int("TRAFFIC_ROLLUP_INTERVAL_SECONDS", _interval)


def start_rollup_scheduler(interval=None, app=None):
    """Start the rollup timer (safe to call more than once, per worker)."""
    global _thread, _interval
    if interval:
        _interval = int(interval)
    _running = True
    if _thread and _thread.is_alive():
        return
    _thread = threading.Thread(target=_run_loop, args=(app,), daemon=True)
    _thread.start()
    logger.info("Traffic rollup scheduler started (interval=%ds)", _interval)


def stop_rollup_scheduler():
    global _running
    _running = False
