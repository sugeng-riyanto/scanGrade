"""Celery app for async task processing (OMR, etc.)."""
import os
from celery import Celery

celery_app = Celery(
    "scangrade",
    broker=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
    backend=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Jakarta",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    result_expires=300,
)

# Import task modules to register them with Celery
import app.services.omr_tasks  # noqa: register OMR tasks
import app.services.ai_grading  # noqa: register AI grading (for future async tasks)


# ── Which commit this worker is running ───────────────────────────────────────
# worker-commit:start
#
# The app publishes the commit it is serving on `/health`, and the deploy refuses a
# release whose reload did not take. This worker is the *other* process holding the
# same checkout, it has no HTTP surface, and it imports its task modules once at
# start-up — so reloading the app leaves it running the previous release. That is a
# half-deployed release, and it has already bitten this box: `page_index` was added
# to the OMR task's signature and to its caller in one commit, gunicorn was reloaded
# alone, and every scan then failed with
#
#     process_omr_scan() got an unexpected keyword argument 'page_index'
#
# — the caller new, the worker old, and nothing on the box saying so.
#
# So the worker answers the same question the app does, over the broker it already
# uses. `build_info.snapshot()` is the answer that already exists: it is the commit
# whose code this process loaded, resolved at import, not whatever the checkout
# points at now. `deploy/worker_commit_gate.py` broadcasts this command and compares
# the replies with the merged commit.
#
# A control command, not an `inspect` one, because the `celery inspect` CLI collects
# its subcommand names when Celery is imported — before this code has registered
# anything — so a custom command is reachable from the Python API (`control.broadcast`)
# and not from the CLI. The gate uses the API for exactly that reason.
from app.utils import build_info  # noqa: E402

try:
    from celery.worker.control import control_command  # noqa: E402
except Exception:  # pragma: no cover - celery ships this; a broken install is a box
    # problem, and a worker that cannot import it must still start and run tasks.
    control_command = None

if control_command is not None:
    @control_command(name="served_commit")
    def served_commit(state):
        """The commit this worker has in memory, for the deploy's worker gate."""
        return build_info.snapshot()
# worker-commit:end
