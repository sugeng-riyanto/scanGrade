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

import pytest
import werkzeug.routing
from flask import Flask

from app import create_app

#: The config every test app is built with. ``TestingConfig`` keeps the suite
#: offline and side-effect free; nothing here should construct a real one.
TESTING_CONFIG = "app.config.TestingConfig"


def build_app(config: str = TESTING_CONFIG) -> Flask:
    """A new app, for the tests whose subject is construction itself."""
    return create_app(config)


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
