"""One app for the whole run, and the hygiene that makes sharing it safe.

``create_app`` is the most expensive thing the suite does: the routes, the Jinja
environment, the limiter, CORS, the template loaders — measured at ~0.8s once the
limiter stopped dialling a Redis that is not there (see ``RATELIMIT_STORAGE_URI``
in ``TestingConfig``). The suite used to pay that per test *file* and, in a dozen
files, per *test*: the same app, the same sixty-odd routes, thrown away and built
again for every case. This file builds it once per **session**; every test that
asks for ``app`` is handed that same object.

Reuse is only safe because nothing is left behind. ``_shared_app_hygiene``
snapshots the app's whole mutable surface — config, extensions, the Jinja globals
and filters, the request hooks, the error handlers, the registered routes, the
rate limiter's counters — before the test and puts it back exactly as it was
after it. A test cannot hand the next one a mutated app any more than a fresh
build could.

Two ways in:

* ``app`` — **the** app. Asked for the way every test already asks for it.
* ``build_app()`` — a genuinely new app, for the tests whose subject *is*
  construction: a box's armament, the schedulers, a config read at import time,
  or a rule registered before the app has answered its first request (Flask
  refuses that on an app that has — and a shared app always has).
"""

# The interpreter floor, asked about *this run* rather than about this box: below
# 3.12 the hook at the bottom compiles the files the run targets and refuses only
# a run that would die at collection, naming them. An import-time refusal was the
# first shape of it, and it refused every run — including the seven theme tests
# `deploy/theme_gate.sh` runs on the box, which is how a release quarantined
# itself. See the module.
import python_requires

import collections
import os
import sys
from pathlib import Path

# The shared git-scrub lives under `tests/`, which is importable as a package from
# the repository root — put that root on the path before naming it, so importing
# this file does not depend on how pytest was invoked.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
import werkzeug.routing
from flask import Flask

from app import create_app
from tests.unit.git_env import GIT_REPO_ENV

#: The config every test app is built with. ``TestingConfig`` keeps the suite
#: offline and side-effect free; nothing here should construct a real one.
TESTING_CONFIG = "app.config.TestingConfig"


def build_app(config: str = TESTING_CONFIG) -> Flask:
    """A new app, for the tests whose subject is construction itself."""
    return create_app(config)


# ── the checkout's environment, and the tests whose subject needs one ────────
#
# `manage.py` is not a data file to read; it is a module that boots a real app at
# import time, and the seed these tests check cannot be reached without it. That
# works on a developer's box because the checkout's `.env` supplies
# `SUPABASE_URL` and `SUPABASE_SERVICE_KEY`, and `create_app` refuses to boot
# without them. The file is gitignored — so a fresh worktree (`git worktree add`,
# a clone, `deploy/dirty_snapshot.py --run`) has none — and the run then reports
# **ten errors** at fixture setup and **eighteen failures** raised inside the
# import. Seventeen of the failures, and all ten errors, are only the environment
# the checkout does not have. The eighteenth was a real defect the pile was hiding:
# `get_config` stringified the config class it was handed, so a test that asked for
# `TestingConfig` was silently given `DevelopmentConfig` — which boots against the
# checkout's `.env` and cannot boot without one (see the fix in `app/config.py`).
#
# The right report for that is a skip that names the file and the variables: the
# same run on a box with a `.env` has all of these tests, and most of what is
# *in* these files reads `manage.py` as text and needs no environment at all, so
# gating the whole module would throw away the tests the environment cannot touch.

def manage_app_refusal() -> str:
    """What `import manage` would be refused for here, or ``""`` when it would not.

    Asked of the app's own rule rather than of a list of names kept here:
    `manage.py` boots `app.config.DevelopmentConfig`, and `create_app` answers the
    same question by calling its `validate()`. So this is the very check the
    import would fail, made before doing the import — and a variable added to the
    required set joins this answer without being named in this file.
    """
    from app.config import DevelopmentConfig

    try:
        DevelopmentConfig.validate()
    except RuntimeError as exc:
        return str(exc)
    return ""


def require_bootable_manage_app() -> None:
    """Call before executing `manage.py`, in the function that does so.

    Named apart from the refusal so the tests can hold both directions: it skips
    when the app could not boot, and does nothing at all when it could — a gate
    that always fires would silently delete every test it was added to protect.
    """
    refusal = manage_app_refusal()
    if refusal:
        pytest.skip(
            f"needs a bootable app to read `manage.py`'s seed: {refusal} — "
            f"`manage.py` boots a real app at import time and this checkout has no "
            f"environment to boot one with (`.env` is gitignored, so a fresh "
            f"worktree has none; copy `.env.example` to `.env` to run this)")


def require_a_findable_env_file() -> None:
    """Call before a test that has to *deny* the reading of a `.env`.

    `test_env_and_email` runs a subprocess in which every `open()` of a
    `.env`-shaped path fails, and asserts that the import survived it with the
    reason recorded. With no such file for python-dotenv to resolve, the read is
    never attempted, the reason stays empty, and the test fails while asserting a
    behaviour it had no file to exercise. Asked of python-dotenv's own resolution
    — the same `find_dotenv()` `load_env_file()` rides on — so a `.env` above the
    worktree counts, and nothing here restates where it looks.
    """
    from dotenv import find_dotenv

    if not find_dotenv():
        pytest.skip(
            "needs a `.env` to deny: the test makes the read of one fail and asks "
            "that the import survives it, so a tree python-dotenv finds no `.env` "
            "in has nothing to exercise — the read never happens")


# ── the interpreter floor, asked about *this run* ─────────────────────────────
#
# Not at import, and not about "this box": a run that targets files this
# interpreter can parse is allowed even below the floor, because the floor exists
# for the files that use 3.12 syntax. See `python_requires`.

def pytest_configure(config) -> None:
    refusal = python_requires.refusal_for_run(python_requires.run_targets(config))
    if refusal:
        raise pytest.UsageError(refusal)


#: The shared app, once it exists. A list rather than a bare global so the
#: memo can be read without a ``global`` statement.
_BUILT: "list[Flask]" = []


def app_instance() -> Flask:
    """The shared app, built on first use.

    Also the way in for the helpers that take no fixtures — a module-level
    ``run_import(...)`` has nothing to ask pytest for.
    """
    if not _BUILT:
        _BUILT.append(build_app())
    return _BUILT[0]


@pytest.fixture(scope="session")
def app_session() -> Flask:
    """The app every test in the run shares, under a name no file can shadow."""
    return app_instance()


@pytest.fixture(scope="session")
def app(app_session) -> Flask:
    """The shared app, under the name every test already asks for it.

    Session-scoped rather than function-scoped on purpose: a module- or
    class-scoped fixture that renders a page once for its whole file may keep
    depending on ``app``. What isolates one test from the next is
    ``_shared_app_hygiene``, not the scope of this fixture — asking for it twice
    in two tests hands out the same object either way.
    """
    return app_session


# ── what a test may touch, and how to put it back ────────────────────────────
#
# Each of these is a register the app fills at import or on first use, and each
# one is a way a test can leak into the next if it is not restored. Structural
# copies rather than ``copy.deepcopy``: everything here is dicts of dicts of
# lists of functions, and deepcopying functions is both slower and — for a bound
# method — not always the same object back.

_MAPPINGS = (
    "before_request_funcs",
    "after_request_funcs",
    "teardown_request_funcs",
    "teardown_appcontext_funcs",
    "url_value_preprocessors",
    "url_default_functions",
    "template_context_processors",
    "error_handler_spec",
)

_JINJA_REGISTERS = ("globals", "filters", "tests")


def _structural_copy(value):
    """A copy that keeps the container's *kind*, not only its contents.

    Flask's registers are ``defaultdict``s whose missing keys have to answer: an
    error-handler lookup asks for ``error_handler_spec[blueprint][code]`` for every
    blueprint on the request, including the ones with no handler at all. Restoring
    a flattened plain-dict copy of that register turned a 404 into a
    ``KeyError: 'public'`` on requests no test had made.
    """
    if isinstance(value, collections.defaultdict):
        copied = collections.defaultdict(value.default_factory)
        copied.update({key: _structural_copy(item) for key, item in value.items()})
        return copied
    if isinstance(value, dict):
        return {key: _structural_copy(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_structural_copy(item) for item in value]
    return value


def _clone_rule(rule: werkzeug.routing.Rule) -> werkzeug.routing.Rule:
    """A rule that can be bound to a map of its own.

    A ``Rule`` remembers the map it was bound to and refuses a second one, so a
    saved rule cannot simply be handed to a new map.
    """
    return werkzeug.routing.Rule(
        rule.rule,
        endpoint=rule.endpoint,
        methods=rule.methods,
        subdomain=rule.subdomain,
        strict_slashes=rule.strict_slashes,
        merge_slashes=rule.merge_slashes,
        defaults=dict(rule.defaults or {}),
        build_only=rule.build_only,
        redirect_to=rule.redirect_to,
        alias=rule.alias,
        host=rule.host,
        websocket=rule.websocket,
    )


def _map_like(url_map, rules) -> werkzeug.routing.Map:
    """A map carrying exactly *rules*, with the app's own routing settings."""
    return werkzeug.routing.Map(
        rules=[_clone_rule(rule) for rule in rules],
        default_subdomain=url_map.default_subdomain,
        strict_slashes=url_map.strict_slashes,
        merge_slashes=url_map.merge_slashes,
        redirect_defaults=url_map.redirect_defaults,
        converters=dict(url_map.converters),
        sort_parameters=url_map.sort_parameters,
        sort_key=url_map.sort_key,
        host_matching=url_map.host_matching,
    )


def _snapshot(app: Flask) -> dict:
    return {
        "config": dict(app.config),
        "extensions": dict(app.extensions),
        "jinja": {name: _structural_copy(getattr(app.jinja_env, name))
                  for name in _JINJA_REGISTERS},
        "registers": {name: _structural_copy(getattr(app, name))
                      for name in _MAPPINGS},
        "view_functions": dict(app.view_functions),
        "rules": tuple(app.url_map.iter_rules()),
        "endpoints": {rule.endpoint for rule in app.url_map.iter_rules()},
    }


def _restore(app: Flask, snapshot: dict) -> None:
    app.config.clear()
    app.config.update(snapshot["config"])

    app.extensions.clear()
    app.extensions.update(snapshot["extensions"])

    for name, saved in snapshot["jinja"].items():
        register = getattr(app.jinja_env, name)
        register.clear()
        register.update(_structural_copy(saved))

    for name, saved in snapshot["registers"].items():
        setattr(app, name, _structural_copy(saved))

    app.view_functions.clear()
    app.view_functions.update(snapshot["view_functions"])

    # Only when a test actually registered something: rebuilding the map means
    # cloning every rule, and that is not worth a per-test cost for the 99% of
    # tests that never touch the routes.
    if {rule.endpoint for rule in app.url_map.iter_rules()} != snapshot["endpoints"]:
        app.url_map = _map_like(app.url_map, snapshot["rules"])

    # A limit counted in one test is a limit the next one did not spend. Reset
    # rather than kept: the memory storage is per-process, so without this a
    # test that flooded a route would walk the next one further up the ladder.
    limiter = app.extensions.get("limiter")
    if limiter is not None:
        try:
            limiter.reset()
        except Exception:                                    # pragma: no cover
            pass


@pytest.fixture(autouse=True)
def _shared_app_hygiene(app_session):
    """Put the shared app back the way this test found it."""
    snapshot = _snapshot(app_session)
    yield
    _restore(app_session, snapshot)


@pytest.fixture(scope="session", autouse=True)
def _git_spawns_resolve_their_own_repository():
    """Run the whole suite with git's repository variables cleared.

    Git hands a hook an absolute ``GIT_DIR``/``GIT_INDEX_FILE``, and every
    subprocess inherits them — so a test that runs ``git`` against a scratch repo
    in ``tmp_path`` wrote into the checkout's index, and the tree it had just
    dirtied failed the gate that ran it (see ``tests/unit/git_env.py``). Clearing
    them from ``os.environ`` for the session means a spawn that forgets ``env=``
    still resolves its own ``cwd``. Session-scoped and autouse, so it is in place
    before any test, and put back afterwards so the run leaves nothing changed.
    """
    saved = {name: os.environ[name] for name in GIT_REPO_ENV if name in os.environ}
    for name in GIT_REPO_ENV:
        os.environ.pop(name, None)
    try:
        yield
    finally:
        os.environ.update(saved)
