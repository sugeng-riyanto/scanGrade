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

That throttle has since moved again, and not because of a defect in this release:
the decision left the worker's own memory for the shared store, so that three
gevent workers count one limit instead of three. The table this file used to
exercise by name is gone, and its tests moved with it — see
``tests/unit/test_sync_throttle_shared.py``. What stays here is what this file is
really about: the module must not read a name nothing binds, and the dead route
must not come back.

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


# ── the throttle's own tests now live with the shared store ──────────────────
#
# Three tests used to sit here — "a sync after the prune window is answered", "the
# prune does not take the entry it just recorded", "a second call inside the window
# is refused" — each reaching for `_sync_last` / `_sync_last_cleanup`. That table no
# longer exists: the decision moved into the shared store so that three gevent
# workers count one limit instead of three. All three properties are asserted
# against the new home in `tests/unit/test_sync_throttle_shared.py`; nothing was
# dropped, and one of them got stronger there (a *forgotten* worker memory is the
# case the old table could not see).


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
