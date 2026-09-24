"""The sync path has to still answer a request an hour into a sitting.

Measured on the released code
-----------------------------
``_check_rate_limit`` — the throttle ``POST /api/student/sync-draft`` runs first —
pruned its stale-entry table through three module names that do not exist:
``_sync_lock_mutex`` and ``_sync_locks`` here, and ``_get_sync_lock`` in the
neighbouring route. Nothing defines them anywhere in the repository.

Two consequences, and a code review sees neither:

* **The prune runs once per 600 s**, so the first ten minutes of every worker's
  life are clean and then *every* sync from that worker raises ``NameError`` and
  answers 500 — until systemd restarts the worker. The one endpoint an
  offline-first exam leans on is the one that breaks, and it breaks only once the
  sitting has run long enough that nobody connects it to the release.
* ``POST /api/student/auto-save`` called ``_get_sync_lock`` on its first line, so
  it answered 500 for every caller. Nothing has ever called it — the page syncs
  through ``sync-draft`` — which is exactly why the crash was never seen: a dead
  path advertising a second way to save a paper that could not save one.

What this release does: the live route keeps its throttle and loses the ghost
prune, and the dead route is **deleted rather than repaired**. A second write path
that nothing calls is a second path that can drift, and ``sync-draft`` is the one
the exam page uses and the one that carries the exam-window rule.

The template-side guards live in ``test_anti_cheat_violations.py`` and
``test_away_events.py``; these are about the server path's ability to stay up.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from app.routes import api as api_module

API_PY = pathlib.Path(__file__).resolve().parents[2] / "app" / "routes" / "api.py"

#: The names the released code read and nothing ever bound. Kept in one place so
#: the guard below and the prose above cannot disagree about what was removed.
GHOSTS = ("_sync_lock_mutex", "_sync_locks", "_get_sync_lock")


# ── the throttle survives its own cleanup ────────────────────────────────────

def test_the_prune_survives_the_cleanup_interval(monkeypatch):
    """A sync after the prune window must be answered, not raised.

    This is the failing case the release fixes: the branch is entered only once
    per ``_SYNC_CLEANUP_INTERVAL``, so it stayed invisible to every short test and
    to every fresh worker.
    """
    monkeypatch.setattr(api_module, "_sync_last", {"old-user:old-exam": 0.0})
    # Zero means "the last prune was at the epoch", i.e. it is due now.
    monkeypatch.setattr(api_module, "_sync_last_cleanup", 0.0)

    allowed = api_module._check_rate_limit("u-1", "e-1", min_interval=5)

    assert allowed is True, "the request inside the window should be allowed"


def test_the_prune_still_drops_entries_older_than_an_hour(monkeypatch):
    """Removing the ghost branch must not remove the cleanup it was tangled with."""
    monkeypatch.setattr(api_module, "_sync_last", {"old-user:old-exam": 0.0})
    monkeypatch.setattr(api_module, "_sync_last_cleanup", 0.0)

    api_module._check_rate_limit("u-2", "e-2", min_interval=5)

    assert "old-user:old-exam" not in api_module._sync_last, (
        "an entry untouched for more than an hour should have been pruned"
    )
    assert "u-2:e-2" in api_module._sync_last, (
        "the prune must not take the entry it just recorded"
    )


def test_the_throttle_still_refuses_a_second_call_inside_the_window(monkeypatch):
    """The throttle is the reason this helper exists; it must keep working."""
    monkeypatch.setattr(api_module, "_sync_last", {})
    monkeypatch.setattr(api_module, "_sync_last_cleanup", api_module.time.time())

    assert api_module._check_rate_limit("u-3", "e-3", min_interval=60) is True
    assert api_module._check_rate_limit("u-3", "e-3", min_interval=60) is False


# ── no ghost names may come back ─────────────────────────────────────────────

def _names_in(path: pathlib.Path) -> set[str]:
    """Every identifier the module *reads*, which is what a NameError is made of."""
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    read: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            read.add(node.id)
        # `with _mutex:` and `del _locks[k]` read the name too.
        if isinstance(node, ast.Attribute):
            continue
    return read


@pytest.mark.parametrize("ghost", GHOSTS)
def test_no_ghost_lock_name_is_read_by_the_module(ghost):
    """The guard against reintroducing it: the module must not read the name at all."""
    assert ghost not in _names_in(API_PY), (
        f"`{ghost}` is read by app/routes/api.py but nothing binds it — a "
        "NameError that only fires once the cleanup interval has passed"
    )


def test_the_sync_path_still_uses_the_live_lock():
    """The replacement is the lock the neighbouring route already uses."""
    read = _names_in(API_PY)
    assert "_redis_lock" in read, (
        "the sync path should lock with _redis_lock, the lock sync-draft already uses"
    )


# ── the dead route is gone, the live one remains ─────────────────────────────

def _registered(app) -> set[str]:
    return {rule.rule for rule in app.url_map.iter_rules()}


def test_the_dead_sync_route_is_not_registered(app):
    """`/api/student/auto-save` had no caller and could not save a paper."""
    assert "/api/student/auto-save" not in _registered(app)


def test_the_live_sync_route_is_still_registered(app):
    """The page's own sync endpoint is the one that must survive."""
    assert "/api/student/sync-draft" in _registered(app)
