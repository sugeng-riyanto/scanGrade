"""The suite runs on one app; these are the properties that keep that safe.

Reported as a cost problem: ``create_app`` was built once per test *file* — and, in
a dozen files, once per *test* — so the same app and the same sixty-odd routes were
thrown away and built again for every case. ``tests/conftest.py`` builds it once per
session now.

That is only worth having if it cannot rot, so the properties are pinned here: the
object handed out is the same one in every test, what a test does to it is invisible
to the next, the limiter is not carrying one test's spending into another, the app
the suite builds never dials a network, and no test file goes back to building its
own.
"""
import ast
import re
from pathlib import Path

import pytest
import werkzeug.routing

from app.config import DevelopmentConfig, TestingConfig
from tests.conftest import (app_instance, build_app, manage_app_refusal,
                            require_bootable_manage_app)

ROOT = Path(__file__).resolve().parents[2]

#: Set by the mutation test below, read by the one after it. The pair means
#: something together; alone, the second one proves the app is already clean.
_MUTATED = []


def _counts(register: dict) -> dict:
    """Callables filed under each key.

    Counted rather than compared: the identity of a hook differs between two
    builds (the limiter's own bound method, one closure per CSRF registration),
    the shape of the register does not — and a leaked hook is a shape that grew.
    """
    return {key: len(value) for key, value in register.items()}


def _handler_counts(spec: dict) -> dict:
    return {blueprint: {code: len(handlers) for code, handlers in by_code.items()}
            for blueprint, by_code in spec.items()}


@pytest.fixture(scope="session")
def handed_out():
    """Every app object a test was handed, in the order the tests asked."""
    return []


# ── 1. one app, handed to every test ────────────────────────────────────────

class TestOneAppForTheRun:
    def test_the_app_is_the_shared_instance(self, app):
        assert app is app_instance(), (
            "a test was handed an app of its own, so the suite is paying for a "
            "build it does not need — ask for `app`, and reach for build_app() only "
            "when what you are testing is construction itself")

    def test_a_later_test_is_handed_the_same_object(self, app, handed_out):
        """Checked against the whole history, so it cannot pass by luck of order."""
        handed_out.append(id(app))
        assert len(set(handed_out)) == 1, (
            f"the tests so far were handed {len(set(handed_out))} different apps — "
            "something is building one per test")

    def test_the_unshadowable_name_is_the_same_object(self, app, app_session):
        assert app is app_session


# ── 2. what one test does to it, the next one cannot see ────────────────────

class TestWhatATestTouchesIsPutBack:
    def test_registering_on_the_shared_app_is_refused(self, app):
        """Why ``build_app()`` exists at all, and it is not a style rule.

        Flask refuses ``add_url_rule``, ``before_request`` and ``errorhandler``
        once the app has answered a request — and the shared app answered its
        first one minutes ago, in another file. A test that needs its own rule
        needs its own app.
        """
        app.test_client().get("/")   # one request is all it takes

        with pytest.raises(AssertionError, match="no longer be called"):
            app.add_url_rule("/_too_late", "too_late", lambda: "ok")

    def test_a_test_may_do_anything_to_the_app(self, app):
        """Every way a test can leave a mark on the app it was handed.

        The registers are edited directly rather than through `before_request`
        and `add_url_rule`: those refuse to run this late (see above), and what
        the hygiene has to survive is the *state* a test can leave behind.
        """
        app.config["SMTP_EMAIL"] = "leaked@example.test"
        app.extensions["leaked"] = object()
        app.jinja_env.globals["leaked"] = lambda: None
        app.jinja_env.filters["leaked"] = lambda value: value
        app.before_request_funcs.setdefault(None, []).append(lambda: None)
        app.error_handler_spec.setdefault(None, {}).setdefault(418, lambda e: ("teapot", 418))
        app.view_functions["leaked_route"] = lambda: "ok"
        app.url_map.add(werkzeug.routing.Rule("/_leaked_route", endpoint="leaked_route"))

        assert app.test_client().get("/_leaked_route").status_code == 200
        _MUTATED.append(True)

    def test_the_next_test_cannot_tell(self, app):
        """Runs after the test above, which is what makes it mean something.

        On its own it passes by construction — there is nothing to have leaked —
        so it is the pair, in one file, that fails when the hygiene goes.
        """
        assert app.config["SMTP_EMAIL"] == TestingConfig.SMTP_EMAIL, (
            "a config a test set was left in place for the next one")
        assert "leaked" not in app.extensions
        assert "leaked" not in app.jinja_env.globals
        assert "leaked" not in app.jinja_env.filters
        assert app.test_client().get("/_leaked_route").status_code == 404, (
            "a route a test registered is still answering")
        assert _MUTATED, "the mutation test did not run before this one"

    def test_the_registers_match_a_fresh_build(self, app):
        """The registers a test is least likely to think about.

        Compared against a build of its own rather than a remembered count: the
        question is not "is it the same number of hooks" but "is this the app the
        suite would have had if the test above had never run".
        """
        fresh = build_app()

        assert "leaked_route" not in app.view_functions
        assert _counts(app.before_request_funcs) == _counts(fresh.before_request_funcs)
        assert _counts(app.after_request_funcs) == _counts(fresh.after_request_funcs)
        assert _counts(app.teardown_request_funcs) == _counts(fresh.teardown_request_funcs)
        assert _counts(app.url_value_preprocessors) == _counts(fresh.url_value_preprocessors)
        assert _handler_counts(app.error_handler_spec) == _handler_counts(fresh.error_handler_spec)


# ── 3. one test's spending is not the next one's ────────────────────────────

class TestTheLimitIsNotCarriedBetweenTests:
    def test_a_test_may_spend_the_limit(self, app):
        storage = app.extensions["limiter"].storage
        storage.incr("spent-by-a-test", expiry=60)
        assert storage.get("spent-by-a-test") == 1

    def test_the_next_test_starts_at_zero(self, app):
        # This storage reads a spent key as its count and a missing one as 0.
        assert app.extensions["limiter"].storage.get("spent-by-a-test") == 0, (
            "a limit spent in one test walked the next one up the ladder")


# ── 4. the app the suite builds never dials out ─────────────────────────────

class TestTheAppTheSuiteBuildsIsOffline:
    def test_testing_config_names_its_limiter_storage(self):
        assert TestingConfig.RATELIMIT_STORAGE_URI == "memory://", (
            "the test config has to *name* the limiter's storage: an empty "
            "REDIS_URL loses to the one in the checkout's .env")

    def test_an_environment_redis_cannot_reach_the_suite(self, monkeypatch):
        """The checkout's own .env sets REDIS_URL, and a test run used to dial it.

        Recorded rather than raised: the limiter block catches everything, so an
        exception here would only be a warning — and the four seconds spent
        failing to connect were the most expensive thing in the suite.
        """
        import redis

        dialled = []

        def _record(*args, **kwargs):
            dialled.append(args or kwargs)
            raise RuntimeError("no Redis on this machine, as intended")

        monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6379/0")
        monkeypatch.setattr(redis.Redis, "from_url", staticmethod(_record))

        built = build_app()

        assert dialled == [], "a test app tried to reach Redis"
        assert built.extensions["limiter"]._storage_uri == "memory://"


# ── 5. and no test file goes back to building its own ───────────────────────

class TestTheSuiteHasOneWayToBuildAnApp:
    """``tests/conftest.py`` is the only place in the suite that knows how a test
    app is made.

    Not tidiness: a file that builds its own pays for the build per test, which is
    the cost this fixture exists to remove. ``build_app()`` is there for the tests
    whose subject *is* construction — they ask for it by name, so the exceptions
    are visible at the call site instead of hidden in an import.
    """

    def _files(self):
        # This file has to write the pattern down to look for it, so it is the one
        # test file the search cannot be run against.
        mine = {"conftest.py", Path(__file__).name}
        return [path for path in sorted(ROOT.joinpath("tests").rglob("*.py"))
                if path.name not in mine]

    def test_no_test_file_imports_the_factory(self):
        offenders = [
            path.relative_to(ROOT).as_posix()
            for path in self._files()
            if re.search(r"from app import create_app|import create_app\b",
                         path.read_text(encoding="utf-8"))
        ]
        assert not offenders, (
            f"{offenders} build their own app by importing create_app — use "
            "`app` from tests/conftest.py, or build_app() if construction is what "
            "the test is about")

    def test_every_file_that_builds_one_says_so_at_the_call_site(self):
        builders = [
            path.relative_to(ROOT).as_posix()
            for path in self._files()
            if re.search(r"\bbuild_app\(", path.read_text(encoding="utf-8"))
        ]
        assert builders, "nothing in the suite builds an app any more?"
        assert "tests/unit/test_armament_refusal.py" in builders, (
            "the armament tests have to build their own — the refusal happens "
            "during construction")


# ── 6. the one part of the suite that needs the checkout's environment ──────

#: The files that execute `manage.py`. Asserted rather than assumed: the sweep
#: below cannot report a file it does not visit, and a pattern that matches
#: nothing passes silently.
MANAGE_LOADERS = {
    "tests/unit/test_account_creation_orphans.py",
    "tests/unit/test_anticheat_seed.py",
    "tests/unit/test_demo_school_scope.py",
    "tests/unit/test_demo_subscription.py",
    "tests/unit/test_school_officials.py",
}

#: The file whose test *asserts about the recorded reason* while denying the read
#: of a `.env`, and the two calls each shape has to make before what it needs.
ENV_DENIERS = {"tests/unit/test_env_and_email.py"}
MANAGE_GUARD = "require_bootable_manage_app"
ENV_GUARD = "require_a_findable_env_file"


def _parents(tree) -> dict:
    """A map from every node to its parent."""
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    return parents


def _holder(node, tree, parents):
    """The function this node is in, or the module when it is in none.

    Holder, not file: these guards skip *the test that needs the environment*, so
    a call in the next function over protects nothing — and one at module level in
    a file whose loads are elsewhere protects nothing either.
    """
    holder = node if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else tree
    walker = node
    while walker in parents:
        walker = parents[walker]
        if isinstance(walker, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return walker
    return holder


def _guarded(node, holder, guard) -> bool:
    """Is `guard()` called in the holder, before this node — or inside it?

    A load that is a *statement* (an import, a loader call) has to be preceded by
    the guard, because a statement runs where it is written. A load that is a whole
    *function* is the registered test itself, so a guard anywhere in its body is in
    time: pytest runs the body top to bottom and the guard's skip ends it.
    """
    for candidate in ast.walk(holder):
        if not isinstance(candidate, ast.Call):
            continue
        name = getattr(candidate.func, "attr", None) or getattr(candidate.func, "id", None)
        if name == guard and (node is holder or candidate.lineno < node.lineno):
            return True
    return False


def _called(node) -> str:
    """The name a call goes through, for a plain or a dotted call."""
    return (getattr(node.func, "attr", None) or getattr(node.func, "id", None) or "")


def _loads_manage(node) -> bool:
    """Does this node *execute* `manage.py`, as opposed to reading its text?

    Three shapes, and they are the three the suite has: `import manage`, `from
    manage import ...`, and the file loader that names it. Reading the module as
    text needs no environment — three other files parse `manage.py` as data — and
    is deliberately not a load.
    """
    if isinstance(node, ast.Import):
        return any(alias.name == "manage" for alias in node.names)
    if isinstance(node, ast.ImportFrom):
        return node.module == "manage"
    if isinstance(node, ast.Call):
        return _called(node) == "spec_from_file_location" and \
            "manage" in ast.unparse(node).lower()
    return False


def _denies_an_env_read(node) -> bool:
    """Does this function deny the read of a `.env` and check what was recorded?

    Three markers, all of them the test's own: it replaces `open` (a `setattr` in
    this process, an assignment inside the script a subprocess runs), the path it
    refuses is a `.env`, and what it goes on to check is `DOTENV_ERROR`. Narrow on
    purpose — the sibling test in the same file denies a `.env`-named file too, but
    it hands `load_env_file()` an explicit path inside `tmp_path`, needs no `.env`
    anywhere, and would be a false positive that teaches the reader to ignore this.
    """
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return False
    text = ast.unparse(node)
    return (
        ".env" in text
        and "DOTENV_ERROR" in text
        and re.search(r"setattr\([^)]*[\"']open[\"']|open\s*=\s*deny", text) is not None
    )


def _unguarded(path: Path, is_load, guard):
    """Every *is_load* in *path* with no *guard()* call before it."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    parents = _parents(tree)
    found = []
    for node in ast.walk(tree):
        if not is_load(node):
            continue
        holder = _holder(node, tree, parents)
        if not _guarded(node, holder, guard):
            found.append((getattr(holder, "name", "<module>"), node.lineno))
    return found


def _count_nodes(path: Path, is_load) -> int:
    """How many *is_load* sites this file has, counted without the sweep."""
    return sum(1 for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
               if is_load(node))


def _files_where(is_load) -> set:
    return {path.relative_to(ROOT).as_posix()
            for path in sorted(ROOT.joinpath("tests").rglob("*.py"))
            if any(is_load(node)
                   for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))))}


class TestTheEnvironmentGateOnManageDotPy:
    """`manage.py` boots a real app at import time, and `create_app` refuses to
    boot without `SUPABASE_URL`/`SUPABASE_SERVICE_KEY`, which a developer's box
    takes from the checkout's `.env` — a gitignored file, so a fresh worktree has
    none. From the outside that read as a broken suite: ten errors at fixture
    setup and eighteen failures raised inside the import, every one of them an
    environment rather than a defect.

    `require_bootable_manage_app()` is the answer, and these are the properties
    that keep it honest — it fires when the app could not boot, it stays out of
    the way when it could, and no file can execute `manage.py` without asking it
    first. The last one is the one that matters: the tests it protects are the
    ones a future file will forget to guard.
    """

    def test_it_fires_when_the_app_would_be_refused(self, monkeypatch):
        monkeypatch.setattr(DevelopmentConfig, "SUPABASE_URL", "")
        with pytest.raises(pytest.skip.Exception) as refusal:
            require_bootable_manage_app()
        message = str(refusal.value)
        assert "SUPABASE_URL" in message, (
            "the skip does not say which variable this checkout is missing: " + message)
        assert ".env" in message and ".env.example" in message, (
            "the skip does not say which file supplies it or how to get one: " + message)

    def test_it_does_nothing_when_the_app_would_boot(self, monkeypatch):
        """The other direction, and the one that keeps the tests alive: a gate that
        always fires would delete every test it was added to protect, silently.

        Caught rather than merely not raised, and turned into a *failure*: a skip
        here is a green run with a test missing, which is the one defect a suite
        cannot report about itself.
        """
        monkeypatch.setattr(DevelopmentConfig, "SUPABASE_URL", "http://127.0.0.1:54321")
        monkeypatch.setattr(DevelopmentConfig, "SUPABASE_SERVICE_KEY", "test-key")
        assert manage_app_refusal() == ""
        try:
            require_bootable_manage_app()
        except pytest.skip.Exception as skipped:
            pytest.fail(f"the guard skipped a checkout whose app boots: {skipped}")

    def _sweep(self, is_load, guard):
        return [f"{path.relative_to(ROOT).as_posix()}:{line} in {name}()"
                for path in sorted(ROOT.joinpath("tests").rglob("*.py"))
                for name, line in _unguarded(path, is_load, guard)]

    def test_no_file_executes_manage_without_asking_first(self):
        """The guard that matters most: the tests it protects are the ones a file
        added later will forget to guard."""
        offenders = self._sweep(_loads_manage, MANAGE_GUARD)
        assert not offenders, (
            f"{offenders} execute `manage.py` without calling "
            f"`{MANAGE_GUARD}()` first, so a checkout with no `.env` reports them "
            "as environment errors rather than skipping them")

    def test_no_file_denies_an_env_read_without_asking_first(self):
        offenders = self._sweep(_denies_an_env_read, ENV_GUARD)
        assert not offenders, (
            f"{offenders} make the read of a `.env` fail without calling "
            f"`{ENV_GUARD}()` first, so a checkout with no `.env` is reported as a "
            "failure about a file that was never there")

    def test_the_two_sweeps_find_the_sites_they_are_supposed_to(self):
        """Without this, a pattern that matches nothing passes both sweeps above."""
        loaders = _files_where(_loads_manage)
        deniers = _files_where(_denies_an_env_read)
        assert loaders == MANAGE_LOADERS, (
            f"the files that execute `manage.py` are {sorted(loaders)}; the list in "
            f"MANAGE_LOADERS says {sorted(MANAGE_LOADERS)} — update it *with* the "
            "guard, not instead of it")
        assert deniers == ENV_DENIERS, (
            f"the files that deny a `.env` read are {sorted(deniers)}; the list in "
            f"ENV_DENIERS says {sorted(ENV_DENIERS)} — update it *with* the guard, "
            "not instead of it")

    def test_the_sweeps_report_every_site_they_visit(self):
        """With a guard nobody calls, every load has to be reported.

        Without this, a sweep whose loop never yields passes the sweeps above
        silently — the one defect that is invisible *because* it reports nothing.
        """
        missing = []
        for path in sorted(ROOT.joinpath("tests").rglob("*.py")):
            for is_load in (_loads_manage, _denies_an_env_read):
                sites = _count_nodes(path, is_load)
                reported = len(_unguarded(path, is_load, "__a_guard_nobody_calls__"))
                if reported != sites:
                    missing.append(
                        f"{path.relative_to(ROOT).as_posix()} ({is_load.__name__}): "
                        f"visits {sites}, reports {reported}")
        assert not missing, (
            f"{missing} — a sweep that reports fewer sites than it visits passes "
            "every other check here while checking nothing")
