# Analytics dashboard — traffic, errors and the weekly cache sweep

`/super-admin/dashboard` carries a **Traffic & errors** section, filled from
pre-computed rollup tables and read by super admins only. This document is the
contract: what is shown, where each number comes from, when it is refreshed, and
what is deliberately *not* collected.

The short version: **counts, aggregate, on this box, and never per person.**

## 1. What is shown

| Panel | Reads | Why it is here |
| --- | --- | --- |
| Requests per day (line) | `traffic_rollup_daily` | The trend. 5xx is the second series, not a separate page |
| Status classes (doughnut) | `traffic_rollup_daily` | How much of the traffic is *not* fine |
| By route group (table) | `traffic_rollup_daily` | Requests, 5xx, p50/p95/p99 per group |
| Peak hours (heatmap) | `traffic_rollup_hourly` | Weekday × hour. **This is what answers "when is it safe to deploy"** |
| Devices | `traffic_rollup_daily` | Mobile/desktop/other. Most pupils are on phones |
| Usage per school | `traffic_rollup_daily` | Adoption across the schools on this box |
| Unique sessions (busiest day) | `traffic_rollup_daily` | How many sessions, never which |
| Worst page queries | the perf gate's own record | `X-Supabase-Queries` for the heaviest page of the last measured release |
| Box health | `/health`'s inputs, in-process | Commit, workers, Redis |
| Error summary | both tables + Sentry | How many (ours) and what (Sentry's) |
| Weekly cache sweep | `cache_maintenance_service.last_sweep()` | When it ran, or why it did not |

The whole section costs **two Supabase reads** — one daily, one hourly — cached for
120 seconds under `traffic_report:<days>`. Every panel is computed in Python from
those rows. `tests/unit/test_analytics_dashboard.py` asserts that equality, because
"one more query per panel" is how a page whose job is to report the load becomes
part of it.

## 2. Where the numbers come from

There are two sources, and only one of them is on the hot path.

**Live — the request hook.** `app/__init__.py`'s `after_request` calls
`traffic_rollup_service.note_request(...)` once per request. It costs a dict lookup
and a few integer adds: nothing is written to the database, and nothing is logged.
It knows the four things the access log cannot: the route group, the status, the
cost, and **which school the request was for**.

**Backfill — gunicorn's access log.** `rollup_from_access_log()` reads
`/var/log/scangrade/access.log` (the format is `access_log_format` in
`gunicorn.conf.py`) through the same accumulator. Its limit is stated rather than
worked around: **the log carries no school**, so a backfilled row is platform-wide
and the per-school panel is empty for those hours.

The log is not the live source because it has no school in it and parsing it on every
flush would be work the request already did. It is worth keeping for the other
reason: it is the only per-request record that survives a restart, which is what a
backfill after an outage needs.

**Not a third party.** No Google Analytics, no Sentry breadcrumb of a pupil's pages,
no CDN-side beacon. The numbers are counted here and stay here.

## 3. Schedules

| Job | Where it runs | Cadence | Defaults |
| --- | --- | --- | --- |
| Rollup flush | `traffic_rollup_service`, daemon thread **in each worker** | `TRAFFIC_ROLLUP_INTERVAL_SECONDS` (60 s) | — |
| Retention | same loop | every 6 h | `TRAFFIC_KEEP_HOURLY_DAYS=14`, `TRAFFIC_KEEP_DAILY_DAYS=400` |
| Weekly cache sweep | `cache_maintenance_service`, daemon thread | checked every `CACHE_SWEEP_INTERVAL_SECONDS` (900 s), runs in the window | `CACHE_SWEEP_WEEKDAY=6` (Sunday), `CACHE_SWEEP_HOUR=3` (WIB) |

**One timer per worker, and that is the design.** Each worker's buffer holds only the
requests *it* served, and `traffic_rollup_add` adds — so three workers flushing three
buffers is the arithmetic being done correctly. A cross-process lock would not improve
the numbers; it would make two workers' requests wait for the third's tick.

**There is no Celery beat on this box, and nothing depends on one.** The three jobs
are also registered as Celery tasks (`app/services/traffic_tasks.py`) for the cases a
schedule cannot cover — the hours before the timer existed, a box that was down, an
operator who has looked at the heatmap and wants to sweep now:

```bash
celery -A app.celery_app call traffic.rollup_backfill --args '["/var/log/scangrade/access.log"]'
celery -A app.celery_app call traffic.rollup_retention
celery -A app.celery_app call cache.weekly_sweep
```

The backfill is **not** idempotent (a rollup keyed by period adds), so run it once per
log file. The other two are.

### Why the sweep's hour

Sunday 03:00 WIB is the default because the heatmap says the box is quietest in the
small hours and because a term-time exam is not sat at three in the morning. It is
configuration rather than a reading, and the page says so: the heatmap is the evidence
the default is argued from, not a value the sweep computes.

### What the sweep actually cleans

1. **Expired entries in the two process caches.** A TTL on an in-memory cache is only
   advice: both of this app's fallbacks check an entry's expiry when they *read* it, so
   an entry nobody reads again is never removed and lives until the worker recycles.
2. **Redis keys in this app's namespaces that have no expiry** (`TTL == -1`). Redis
   expires everything else for us; these are the only ones that leak.
3. What it did, written to the log and to the page.

`sg:traffic:*` is deliberately **not** swept: deleting a day's session set would change
a number the dashboard reports, which is not a cache operation.

### What the sweep refuses

A sweep during a live sitting is deferred, and the deferral is a normal outcome rather
than an error. Clearing a school's cached rows while pupils are sitting an exam costs
each of them the Supabase round-trip the cache exists to avoid — at 500 concurrent
students on one vCPU that is not a slow page, it is a slow afternoon for the school.
The guard reads `attempt_status.OPEN_STATUSES` (the same list `deadline_service` closes
by) bounded to sittings that *started within the last three hours*, so a draft that
never closes cannot block the sweep forever. An unreadable guard skips the sweep.

## 4. Privacy — aggregate, and stated as a property

Most of the people this app is for are children, and what they do here is their
schoolwork. So:

* **No per-person row exists in either rollup table.** No `student_id`, no `user_id`,
  no IP, no cookie and no raw user-agent. The columns are counts, a route *group*, a
  coarse device class (`mobile`/`desktop`/`other`) and a school id.
* **The session number is a count and only a count.** It comes from `SCARD` over a
  Redis set of **hashes** (`sha256`, `sg:traffic:sess:<day>:<school>`, 48 h TTL). The
  set never leaves Redis; in the database the number is written with `greatest()`
  because it is a running total for the day, not a delta. When Redis is unavailable the
  key is **absent** rather than zero — a zero there reads as "nobody used the system
  today", the one answer this reading must never make up.
* **No cookie is minted to make the number move.** Sessions are counted for signed-in
  sessions only. An anonymous visitor to the landing page adds to the request counts
  and to no session count, and is not given an identifier to change that.
* **RLS is on with no policy at all.** No school may read platform-wide traffic, so
  there is no `using` clause that would be correct; `anon` and `authenticated` see 0
  rows and the service key is the only reader. The write functions are
  `security definer`, so their `EXECUTE` is revoked from `PUBLIC` and granted to
  `service_role` only — otherwise an anon key could inject fake counts.
* **The existing audit trails are not duplicated.** `audit_logs`, `violation_logs`,
  `seb_access_log` and `submissions.answers` already have their readers and their
  permissions. Copying any of them here would be a second copy of a child's record in
  a table with a different audience, and a second answer to "who did what".
* **Retention is about disk, not personal data**, because there is none in these
  tables: 14 days of hourly buckets, 400 days of daily ones.

## 5. The error half

Two halves, and neither is a new error tracker:

* **How many** — the rollup's 4xx/5xx counts and their trend, with a route group
  named when its rate is clearly worse than the earlier half of the range (both
  numbers are shown; it is a reading, not an alarm).
* **What** — a link into Sentry, which already receives every unhandled exception and
  every `ScanGradeException` (`app/handlers/error_handlers.py`), with the user, the
  school and the request context attached. The link is derived from `SENTRY_DSN`, or
  set outright with `SENTRY_ISSUES_URL` for a self-hosted or regional Sentry. With no
  DSN the page says there is no link rather than showing one that 404s.

No Sentry API key is needed: the counts are ours and the link is a URL.

## 6. Operating it

```bash
# Apply the schema (065). Dry run first — it is additive DDL.
python deploy/apply_migration.py supabase/migrations/065_traffic_rollup.sql --repo . --no-snapshot
python deploy/apply_migration.py supabase/migrations/065_traffic_rollup.sql --repo . --commit

# Prove it does what the service will ask of it.
python deploy/schema_contract.py --require-applied

# Backfill the log this box already has (once per file).
celery -A app.celery_app call traffic.rollup_backfill
```

Turning the live timer off (a probe run, a debugging box):
`START_BACKGROUND_SCHEDULERS=false`.

### Reading a gap in the data

A flat line is one of three things, and the page says which where it can:

* **no rows at all** — the timer has not run yet, or the migration has not been
  applied; the header says "No rollup data yet";
* **a failed flush** — the batch was dropped (the buffer is emptied *before* the send,
  so a failure loses a minute and never double-counts) and the log carries
  `traffic rollup flush failed: …`;
* **a real quiet period** — the heatmap shows the same hours quiet every day of the
  week, which is a schedule rather than an outage.
