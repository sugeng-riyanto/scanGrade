"""The Celery half of AI essay grading: one suggestion, outside the request.

Provider latency is seconds and unbounded, the box is one vCPU, and a teacher
marking 200 essays must not wait on a request that holds a gunicorn worker. So
the route only *queues* a row and returns; this worker builds the prompt, calls
the provider, validates the reply and stores it. The row's status is the
progress a page polls, which survives a worker restart — a task id alone would
not.

The worker has no Flask application of its own. It builds one, once per process,
with two differences from the app the box serves:

* `START_BACKGROUND_SCHEDULERS = False` — the cleanup, retention and alert loops
  belong to the web process. A worker that started them would run a destructive
  retention purge, and its alerts would duplicate the app's.
* `DEPLOY_PROBE = False` — the armament question is asked of the process a
  release is about to serve, not of this one.

The task is registered by `app/celery_app.py`, the same place the OMR tasks are,
so the deploy's worker-commit gate covers it.
"""
from __future__ import annotations

import logging

from app.celery_app import celery_app
from app.config import ProductionConfig

logger = logging.getLogger("app")


class WorkerConfig(ProductionConfig):
    """The config a task process constructs: production, without the loops."""

    START_BACKGROUND_SCHEDULERS = False
    DEPLOY_PROBE = False


_APP = None


def worker_app():
    """The app context this worker runs tasks in — built once per process."""
    global _APP
    if _APP is None:
        from app import create_app

        _APP = create_app(WorkerConfig)
    return _APP


def _run(suggestion_id):
    from app.services.ai_grading_suggestion import run_suggestion
    from app.utils.auth import get_supabase

    return run_suggestion(get_supabase(), suggestion_id)


@celery_app.task(bind=True, name="ai.request_essay_suggestion", max_retries=0)
def request_essay_suggestion(self, suggestion_id):
    """Grade one stored suggestion row.

    `max_retries=0` is deliberate: a provider timeout is retried by the teacher
    with one click, and an automatic retry would bill the same essay twice
    without anyone asking. The row records the failure and the page says so.
    """
    app = worker_app()
    with app.app_context():
        try:
            return _run(suggestion_id)
        except Exception as exc:  # a task must always answer, never die silently
            logger.exception("AI suggestion task %s crashed", suggestion_id)
            # `run_suggestion` already records its own failures; this is the
            # belt for the case where it could not (e.g. the table read failed).
            try:
                from app.services.ai_grading_suggestion import _fail
                from app.utils.auth import get_supabase

                _fail(get_supabase(), suggestion_id,
                      "Layanan AI gagal memproses jawaban ini. Coba lagi.")
            except Exception:
                pass
            return {"status": "failed", "error": str(exc)[:200]}
