"""Re-derive every exam's assessment-period tag on a timer.

Why this exists
---------------
`exams.assessment_period_id` is *derived*: the period whose dates contain the
paper's window, falling back to the running period. The write doors recompute it
when the paper is saved, and the calendar doors recompute every paper's when the
calendar moves. Neither can see a tag written **outside a door** — by SQL, by a
migration, by a repair script. Those rows keep a stale or dangling tag until a
human happens to edit that paper or that calendar again, and a report grouped by
period then quietly counts a paper under the wrong period.

So the derivation is put on a timer. `retag_all_schools` runs the *same* per-school
rule the doors use, over every school, so the timer and the doors cannot disagree.

Shape
-----
The same daemon-thread loop as the deadline sweep, the retention purge and the
deploy-staleness alert: an application context is pushed for each pass (Supabase
comes from the app config), the loop swallows a failed pass and retries next tick,
and the first pass is deliberately **not** immediate — constructing an app must not
write, so the first sweep waits a full interval.

The interval is a day by default. Nothing here is urgent: the doors already handle
every tag the app writes, and this only catches the ones it did not.
"""
import logging
import threading

logger = logging.getLogger(__name__)

#: A day. The doors already keep the tags current for everything the app writes;
#: this only catches tags written around them, and none of those change by the
#: minute. Cheap enough on a 1 vCPU box — a school list plus two reads per school,
#: once a day.
DEFAULT_INTERVAL_SECONDS = 86400

_thread = None
_stop = threading.Event()


def _interval_seconds() -> int:
    try:
        from flask import current_app

        return int(current_app.config.get("PERIOD_RECONCILE_INTERVAL_SECONDS")
                   or DEFAULT_INTERVAL_SECONDS)
    except (RuntimeError, TypeError, ValueError):
        return DEFAULT_INTERVAL_SECONDS


def reconcile() -> dict:
    """One pass: re-derive every school's tags. Never raises."""
    from app.services import assessment_periods
    from app.utils.auth import get_supabase

    try:
        result = assessment_periods.retag_all_schools(get_supabase())
    except Exception as exc:                                       # noqa: BLE001
        logger.error("period reconcile: pass failed: %s", exc)
        return {"ok": False, "reason": "failed", "schools": 0, "retagged": 0,
                "failed": []}
    if result.get("retagged"):
        logger.info("period reconcile: re-derived %d tag(s) across %d school(s)",
                    result["retagged"], result.get("schools"))
    return result


def _loop(app, interval: int) -> None:
    # The first pass waits a full interval on purpose: `create_app` must not write,
    # and an app that has just started has no tag more stale than it was a minute
    # ago. Same reasoning as the deploy-alert loop.
    while not _stop.wait(interval):
        try:
            with app.app_context():
                reconcile()
        except Exception as exc:                                  # noqa: BLE001
            logger.error("period reconcile: loop error: %s", exc)


def start_period_reconcile_scheduler(interval: int | None = None, app=None) -> None:
    """Start the reconcile loop (safe to call more than once)."""
    global _thread
    if _thread and _thread.is_alive():
        return
    if app is None:
        from flask import current_app

        app = current_app._get_current_object()
    _stop.clear()
    _thread = threading.Thread(target=_loop,
                               args=(app, interval or _interval_seconds()),
                               name="period-reconcile", daemon=True)
    _thread.start()
    logger.info("period reconcile scheduler started (interval=%ss)",
                interval or _interval_seconds())


def stop_period_reconcile_scheduler() -> None:
    global _thread
    _stop.set()
    _thread = None


__all__ = [
    "DEFAULT_INTERVAL_SECONDS",
    "reconcile", "start_period_reconcile_scheduler", "stop_period_reconcile_scheduler",
]
