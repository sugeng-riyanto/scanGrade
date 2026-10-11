"""Every filesystem read this code does *at import* has to report, not raise.

Two failures of this shape had already happened: `app/config.py`'s python-dotenv read
(a `.env` the process user could not open took gunicorn's worker boot, the Celery
worker and every `manage.py` command down) and `wsgi.py`'s own `load_dotenv()`. Both
were found by hand. This file is the sweep that finds the rest, and the guards for the
two that were still open:

* **the temp directory.** `tempfile.gettempdir()` opens a file in each candidate
  directory to prove it is writable and raises `FileNotFoundError` when none is. Both
  marker modules — `auth_health` and `lock_health` — computed their default marker path
  from it in their module body, so a full or read-only `/tmp` was an import-time death
  for three processes at once, in the two modules whose whole contract is that no state
  problem reaches a student's save.
* **a module's own path.** `Path(__file__).resolve()` is a probe and raises on a
  symlink loop (`RuntimeError`) or an unreadable link (`OSError`); four modules
  resolved their repository root that way at import.

The subprocess cases are the real ones: the failure was *a process that never started*,
so a test that imports the already-loaded module proves nothing. The last class is a
source sweep, because a sweep is how the third instance gets caught before an operator
does.
"""
import ast
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]

#: The one module allowed to touch the filesystem to find out whether it can: every
#: other module goes through it, which is what makes it the only place to audit.
SAFE_MODULE = "app/utils/import_safety.py"


def _run(script: str, *env: tuple[str, str]) -> subprocess.CompletedProcess:
    environment = {**os.environ, "PYTHONPATH": str(ROOT), "PYTHONIOENCODING": "utf-8"}
    for key, value in env:
        environment[key] = value
    return subprocess.run([sys.executable, "-c", script], cwd=str(ROOT),
                          capture_output=True, text=True, timeout=300, env=environment)


def _denied_temp_dir() -> str:
    """A script prelude that makes `tempfile.gettempdir()` answer the way a full disk does.

    Patched on the module rather than by pointing `TMPDIR` somewhere hostile: the real
    raise comes out of `tempfile._get_default_tempdir()` *after* it has tried every
    candidate, and reaching that state in a test would mean filling a disk.
    """
    return (
        "import tempfile\n"
        "def _no_temp(*a, **k):\n"
        "    raise FileNotFoundError(2, 'No usable temporary directory found in'\n"
        "                            \" ['/tmp', '/var/tmp', '/usr/tmp', '/']\")\n"
        "tempfile.gettempdir = _no_temp\n"
    )


class TestTheTempDirectoryIsAReading:
    """A box with no usable `/tmp` still starts, and says what it could not do."""

    def test_auth_health_imports_and_reports_the_missing_temp_directory(self):
        script = _denied_temp_dir() + (
            "import app.utils.auth_health as h\n"
            "print('TEMP_DIR_ERROR=' + h.TEMP_DIR_ERROR)\n"
            "print('DEFAULT_STATE_FILE=' + repr(h.DEFAULT_STATE_FILE))\n"
            "print('STATE_FILE=' + repr(h.state_file()))\n"
            "print('BEFORE_KEY=' + h.state()['key'])\n"
            "h.record_retry('database error creating new user')\n"
            "s = h.state()\n"
            "print('KEY=' + s['key'])\n"
            "print('RETRIES=' + str(s['worker_retries']))\n"
            "print('MARKER=' + s['marker']['key'])\n"
            "print('STATE_FILE_FIELD=' + repr(s['state_file']))\n"
        )
        done = _run(script, ("SCANGRADE_AUTH_STATE_FILE", ""))
        assert done.returncode == 0, (
            "an unusable temp directory still takes the process down at import — which "
            "is how the worker and every gunicorn worker would die:\n"
            + (done.stderr or "")[-2000:])
        assert "TEMP_DIR_ERROR=FileNotFoundError" in done.stdout, (
            "the import survived but the reason was not recorded: " + done.stdout)
        assert "DEFAULT_STATE_FILE=''" in done.stdout, (
            "a default path was published for a temp directory that does not exist: "
            + done.stdout)
        assert "STATE_FILE=None" in done.stdout, (
            "`state_file()` did not answer None, so a marker would be written (or read) "
            "at a path that is not a file — `Path('')` is the current directory: "
            + done.stdout)
        assert "BEFORE_KEY=unavailable" in done.stdout, (
            "a box that cannot keep the marker reported something other than "
            "`unavailable` — `clean` would be a claim it cannot make: " + done.stdout)
        assert "KEY=recorded" in done.stdout, (
            "a worker with retries of its own stopped being the amber case once the "
            "marker had nowhere to live, so the more urgent truth was lost: "
            + done.stdout)
        assert "RETRIES=1" in done.stdout, (
            "the per-worker count was lost along with the marker: " + done.stdout)
        assert "MARKER=unavailable" in done.stdout, (
            "the marker's own reading did not say it is unavailable: " + done.stdout)
        assert "STATE_FILE_FIELD=''" in done.stdout, (
            "the reading still names a state file, so the card would show a path that "
            "is not used: " + done.stdout)

    def test_lock_health_imports_and_reports_the_missing_temp_directory(self):
        script = _denied_temp_dir() + (
            "import app.utils.lock_health as h\n"
            "print('TEMP_DIR_ERROR=' + h.TEMP_DIR_ERROR)\n"
            "print('DEFAULT_STATE_FILE=' + repr(h.DEFAULT_STATE_FILE))\n"
            "print('STATE_FILE=' + repr(h.state_file()))\n"
            "h.record_fallback('boom')\n"
            "s = h.state(probe=lambda: (None, None, 'not_asked'))\n"
            "print('FALLBACKS=' + str(s['worker_fallbacks']))\n"
            "print('MARKER=' + s['marker']['key'])\n"
            "print('STATE_FILE_FIELD=' + repr(s['state_file']))\n"
        )
        done = _run(script, ("SCANGRADE_LOCK_STATE_FILE", ""))
        assert done.returncode == 0, (
            "an unusable temp directory still takes the process down at import:\n"
            + (done.stderr or "")[-2000:])
        assert "TEMP_DIR_ERROR=FileNotFoundError" in done.stdout, done.stdout
        assert "STATE_FILE=None" in done.stdout, done.stdout
        assert "FALLBACKS=1" in done.stdout, (
            "the count was lost with the marker: " + done.stdout)
        assert "MARKER=unavailable" in done.stdout, done.stdout

    def test_the_marker_is_never_written_when_there_is_nowhere_to_write_it(self):
        """`None` must be a *reading*, not a path that happens to be empty.

        `pathlib.Path("")` is `.` — the process's working directory — so a module that
        kept building a Path from the empty default would try to create a marker *in the
        checkout*, which is the other thing these two modules must never do (the deploy
        runner refuses a dirty tree).
        """
        script = _denied_temp_dir() + (
            "import os, pathlib\n"
            "import app.utils.lock_health as h\n"
            "print('CWD=' + os.getcwd())\n"
            "h.record_fallback('boom')\n"
            "h.record_shared()\n"
            "leftovers = [p.name for p in pathlib.Path('.').glob('scangrade-*.json')]\n"
            "print('LEFTOVERS=' + repr(leftovers))\n"
        )
        done = _run(script, ("SCANGRADE_LOCK_STATE_FILE", ""))
        assert done.returncode == 0, (done.stderr or "")[-2000:]
        assert "LEFTOVERS=[]" in done.stdout, (
            "a marker was written into the working directory: the empty default became "
            "`Path('.')` instead of `None`: " + done.stdout)


class TestAModulesOwnPathIsAReading:
    """`Path(__file__).resolve()` can raise; the repository root must survive it."""

    def test_build_info_resolves_tolerantly(self):
        script = (
            "import pathlib\n"
            "real = pathlib.Path.resolve\n"
            "def _loop(self, *a, **k):\n"
            "    if str(self).replace('\\\\', '/').endswith('app/utils/build_info.py'):\n"
            "        raise RuntimeError('Symlink loop from %r' % (str(self),))\n"
            "    return real(self, *a, **k)\n"
            "pathlib.Path.resolve = _loop\n"
            "import app.utils.build_info as b\n"
            "print('CODE_ROOT=' + pathlib.Path(b.CODE_ROOT).name)\n"
            "print('SNAPSHOT=' + str(b.snapshot()['available']))\n"
        )
        done = _run(script)
        assert done.returncode == 0, (
            "a symlink loop where this module's own file sits takes the import down:\n"
            + (done.stderr or "")[-2000:])
        assert f"CODE_ROOT={ROOT.name}" in done.stdout, (
            "the repository root was not recovered from the unresolved path: "
            + done.stdout)
        assert "SNAPSHOT=True" in done.stdout, (
            "the module imported but no longer answers from the checkout: " + done.stdout)

    def test_every_repository_root_goes_through_the_one_helper(self):
        """Four modules need the same directory; each resolves it the same way.

        A source guard rather than four subprocesses: the failure is a *probe* that can
        raise, and what makes it report is that the call is `import_safety.resolved`
        and not `.resolve()`.
        """
        users = [
            "app/services/capacity_service.py",
            "app/utils/armament.py",
            "app/utils/build_info.py",
            "app/utils/checkout_integrity.py",
        ]
        for relative in users:
            text = (ROOT / relative).read_text(encoding="utf-8")
            assert "import_safety.resolved(" in text, (
                f"{relative} no longer resolves its root through "
                "`import_safety.resolved`, so a symlink loop is an import-time death")
            unresolved = ("Path(__file__).resolve()" in text
                          or "pathlib.Path(__file__).resolve()" in text)
            assert not unresolved, (
                f"{relative} resolves `__file__` itself, which is the probe this guard "
                "exists to keep out of a module body")


class TestNoModuleReadsTheFilesystemAtImport:
    """The sweep: a module body may not probe the filesystem without a reason.

    It walks the module bodies (not function bodies, which do not run at import) of
    everything a deployed process loads, and refuses a `gettempdir()`/`resolve()` that
    is not inside a `try` and is not in the one module written for it. That is how the
    next instance of this is found — by the suite, rather than by an operator whose
    worker will not start.
    """

    CALLS = {"gettempdir", "getcwd", "expanduser", "resolve", "realpath", "readlink"}

    class _ModuleScope(ast.NodeVisitor):
        def __init__(self):
            self.calls: list[ast.Call] = []

        def visit_FunctionDef(self, node):
            return

        def visit_AsyncFunctionDef(self, node):
            return

        def visit_Lambda(self, node):
            return

        def visit_Call(self, node):
            self.calls.append(node)
            self.generic_visit(node)

    @classmethod
    def _unguarded(cls, tree: ast.Module) -> list[tuple[str, int]]:
        scope = cls._ModuleScope()
        scope.visit(tree)
        parents = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parents[child] = node

        def in_try(node) -> bool:
            current = node
            while current in parents:
                parent = parents[current]
                if isinstance(parent, ast.Try) and (
                        current in parent.body
                        or any(current in handler.body for handler in parent.handlers)):
                    return True
                current = parent
            return False

        found = []
        for call in scope.calls:
            fn = call.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
            if name in cls.CALLS and not in_try(call):
                found.append((name, call.lineno))
        return found

    def test_no_module_body_probes_the_filesystem_outside_the_helper(self):
        offenders = []
        files = sorted((ROOT / "app").rglob("*.py")) + [ROOT / "wsgi.py"]
        for path in files:
            relative = path.relative_to(ROOT).as_posix()
            if relative == SAFE_MODULE:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8-sig", errors="replace"))
            for name, line in self._unguarded(tree):
                offenders.append(f"{relative}:{line} {name}()")
        assert offenders == [], (
            "these module bodies probe the filesystem at import without a reason "
            "around them, so a full disk or a symlink loop is an import that never "
            "finishes: " + ", ".join(offenders)
            + f" — route each one through {SAFE_MODULE}")
