"""The traffic rollup's jobs, as Celery tasks — for an operator, not for a timer.

Where the periodic work actually runs
------------------------------------
**Not here.** This app has no Celery *beat* process: `app/celery_app.py` declares no
`beat_schedule`, nothing installs a beat unit, and the five existing periodic jobs
(cleanup, data retention, deadline sweep, period reconcile, deploy alerts) are all
daemon threads started in `create_app` behind `START_BACKGROUND_SCHEDULERS`. The
rollup and the weekly cache sweep follow that convention, in
:mod:`app.services.traffic_rollup_service` and
:mod:`app.services.cache_maintenance_service`, because a new process to supervise on
a 1 vCPU box — with its own failure mode, its own restart policy and its own way of
serving stale code — is a large price for two functions.

So what are these for? Exactly the cases a timer cannot cover, and each of them is a
real one from this box's history:

* **the hours before the timer existed.** The rollup tables start empty, and
  gunicorn's `/var/log/scangrade/access.log` already holds days of traffic. A
  backfill is a one-shot, not a schedule.
* **a box that was down.** Nothing flushes a buffer that never existed; the log is
  still there and only a backfill can recover it.
* **an operator asking now.** A weekly window is a guess about when the box is quiet;
  somebody who has looked at the heatmap may know better.

Each task is a thin call into the service, and each is **safe to run twice** — the
backfill re-adds to a rollup keyed by (period, group, school), so running it over the
same log twice double-counts. That is stated here rather than papered over with a
dedupe marker, because the honest instruction is one the operator can follow:
backfill a log file once, and note the byte offset if it must be repeated. The
retention and sweep tasks are idempotent outright.

Registering them costs nothing when the broker is absent: `celery_app` is imported
for its task registry, and nothing in the request path touches it.
"""
from __future__ import annotations

import logging

from app.celery_app import celery_app

logger = logging.getLogger(__name__)

#: Where gunicorn writes, from `gunicorn.conf.py`. Defaulted here rather than typed
#: into a command line so the two cannot disagree.
DEFAULT_ACCESS_LOG = "/var/log/scangrade/access.log"


@celery_app.task(name="traffic.rollup_backfill")
def traffic_rollup_backfill(path: str = DEFAULT_ACCESS_LOG, limit: int | None = None):
    """Load an access log into the rollup tables. Run once per file."""
    from app.services import traffic_rollup_service as traffic
    outcome = traffic.rollup_from_access_log(path, limit=limit)
    logger.info("traffic backfill %s: %s", path, outcome)
    return outcome


@celery_app.task(name="traffic.rollup_retention")
def traffic_rollup_retention(keep_hourly_days: int | None = None,
                             keep_daily_days: int | None = None):
    """Apply the retention window to the rollup tables."""
    from app.services import traffic_rollup_service as traffic
    outcome = traffic.purge(keep_hourly_days=keep_hourly_days,
                            keep_daily_days=keep_daily_days)
    logger.info("traffic retention: %s", outcome)
    return outcome


@celery_app.task(name="cache.weekly_sweep")
def cache_weekly_sweep(force: bool = True):
    """Run the cache sweep out of hours, or on request.

    `force=True` skips the *window* check, never the sitting guard: the guard is
    about pupils, and an operator running this by hand during an exam would be doing
    the thing the guard exists to prevent. The result says which of the two
    happened.
    """
    from app.services import cache_maintenance_service as sweep
    outcome = sweep.sweep(force=force)
    logger.info("cache sweep (task): %s", outcome)
    return outcome
