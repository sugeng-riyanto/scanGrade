"""Regression guard for the UU PDP data-retention scheduler.

purge_all() resolves its Supabase client from current_app, so the background
loop MUST push an application context. Before this was fixed every purge pass
died with "Working outside of application context" and retention was silently
never enforced — which is a compliance problem, not just a logging one.
"""

import threading

import flask
import pytest


def test_retention_loop_runs_inside_app_context(app, monkeypatch):
    from app.services import data_retention_service as drs

    contexts = []
    ran = threading.Event()

    def fake_purge_all():
        contexts.append(flask.has_app_context())
        ran.set()
        return {}

    monkeypatch.setattr(drs, "purge_all", fake_purge_all)
    monkeypatch.setattr(drs, "_retention_thread", None)
    monkeypatch.setattr(drs, "_running", False)

    drs.start_retention_scheduler(interval=3600, app=app)
    try:
        assert ran.wait(5), "retention loop never executed a purge pass"
    finally:
        drs.stop_retention_scheduler()
        monkeypatch.setattr(drs, "_retention_thread", None)

    assert contexts == [True], "purge ran outside the Flask application context"


def test_purge_all_without_app_context_raises():
    """Documents why the scheduler must pass app= into the loop."""
    from app.services import data_retention_service as drs

    with pytest.raises(RuntimeError):
        drs.purge_all()
